"""LQL: grammar, errors, and the two back ends agreeing.

The previous parser was a regex tokenizer with no NOT and no parentheses (both
were searched for as literal text), the wrong precedence for `a OR b AND c`,
unescaped LIKE metacharacters, and a ClickHouse compilation in which
`status:500` never matched a numeric field. It had no tests of its own.

`RECORDS` and `PARITY` are also run against a real ClickHouse by
tests/test_query_parser_clickhouse.py, so the in-memory evaluator and the SQL
cannot drift apart unnoticed.
"""

from __future__ import annotations

import re

import pytest

from denoiser.query.parser import (
    MAX_QUERY_DEPTH,
    AndNode,
    FieldMatch,
    NotNode,
    OrNode,
    QueryError,
    QuerySyntaxError,
    QueryTooComplex,
    TextMatch,
    compile_to_sql,
    evaluate_in_memory,
    parse_query,
)

RECORDS = [
    {"level": "ERROR", "source": "payment", "message": "connection reset by peer", "status": 503,
     "user": {"id": 42}, "trace_id": "abc", "path": "/api/v1/pay"},
    {"level": "WARN", "source": "payment", "message": "slow query 50% over budget", "status": 200,
     "user": {"id": 7}, "path": "/api/v1/list"},
    {"level": "DEBUG", "source": "auth", "message": "getUser() called", "status": "404", "user.id": "42"},
    {"level": "INFO", "source": "cache", "message": "cache_hit ratio fine", "ok": True},
    {"level": "error", "source": "db", "message": "deadlock detected", "status": 500.0, "path": "/internal"},
]

#: query -> indexes of RECORDS it must match, on both back ends.
PARITY = {
    "": [0, 1, 2, 3, 4],
    "*": [0, 1, 2, 3, 4],
    "level:ERROR": [0, 4],                      # case-insensitive
    "level:error timeout": [],                  # implicit AND
    "status:500": [4],                          # 500.0 is 500
    "status:503": [0],                          # a JSON number, which used to match nothing in SQL
    "status:404": [2],                          # and a JSON string
    "status:>=500": [0, 4],
    "status:>=500 status:<600": [0, 4],
    "user.id:42": [0, 2],                       # nested path, and a literal dotted key
    "user.id:7": [1],
    "trace_id:*": [0],
    "user.id:*": [0, 1, 2],
    "path:/api/*": [0, 1],
    "source:pay*": [0, 1],
    "-level:DEBUG": [0, 1, 3, 4],
    "NOT level:DEBUG": [0, 1, 3, 4],
    "-(level:ERROR OR level:WARN)": [2, 3],
    '"connection reset"': [0],
    'message:"deadlock detected"': [4],
    "50%": [1],                                 # % is literal, not a wildcard
    "cache_hit": [3],                           # _ is literal
    "getUser()": [2],                           # balanced parens inside a word are text
    "(payment OR db) AND level:ERROR": [0, 4],
    "a OR level:WARN AND source:payment": [0, 1, 2, 3, 4],
    "ok:true": [3],
}


class TestGrammar:
    def test_adjacent_terms_are_anded(self):
        assert parse_query("level:ERROR timeout") == AndNode([FieldMatch("level", "ERROR"), TextMatch("timeout")])

    def test_and_binds_tighter_than_or(self):
        """`a OR b AND c` is `a OR (b AND c)`; it used to be `(a OR b) AND c`."""
        assert parse_query("a OR b AND c") == OrNode([TextMatch("a"), AndNode([TextMatch("b"), TextMatch("c")])])

    def test_parentheses_group(self):
        assert parse_query("(a OR b) c") == AndNode([OrNode([TextMatch("a"), TextMatch("b")]), TextMatch("c")])

    @pytest.mark.parametrize("q", ["NOT debug", "-debug"])
    def test_not_and_dash_negate(self, q):
        assert parse_query(q) == NotNode(TextMatch("debug"))

    @pytest.mark.parametrize("q,text", [("-5", "-5"), ("a - b", "-")])
    def test_a_dash_that_is_not_before_a_word_is_text(self, q, text):
        assert TextMatch(text) in (parse_query(q).children if isinstance(parse_query(q), AndNode) else [parse_query(q)])

    def test_lower_case_operators_are_search_words(self):
        assert parse_query("and or not") == AndNode([TextMatch("and"), TextMatch("or"), TextMatch("not")])

    @pytest.mark.parametrize("q,op,value", [
        ("status:>500", ">", "500"), ("status:>=500", ">=", "500"),
        ("status:<1.5", "<", "1.5"), ("status:<=0", "<=", "0"),
    ])
    def test_comparisons(self, q, op, value):
        assert parse_query(q) == FieldMatch("status", value, op)

    def test_star_alone_is_presence(self):
        assert parse_query("trace_id:*") == FieldMatch("trace_id", "*", "exists")

    def test_quoted_field_value_keeps_spaces_and_escapes(self):
        assert parse_query(r'msg:"say \"hi\" now"') == FieldMatch("msg", 'say "hi" now')

    def test_value_may_contain_colons(self):
        assert parse_query("url:http://x:8080/a") == FieldMatch("url", "http://x:8080/a")

    def test_empty_query_matches_everything(self):
        assert parse_query("   ") == TextMatch("")


class TestErrors:
    @pytest.mark.parametrize("q,fragment", [
        ('"unclosed', "Unclosed quote"),
        ("(a OR b", "never closed"),
        ("a)", "Unmatched ')'"),
        ("()", "Empty parentheses"),
        ("AND x", "nothing before it"),
        ("x OR", "nothing after it"),
        ("x AND", "nothing after it"),
        ("NOT", "nothing after it"),
        ("level:", "needs a value"),
        ("status:>abc", "non-number"),
    ])
    def test_malformed_queries_say_what_and_where(self, q, fragment):
        with pytest.raises(QuerySyntaxError, match=re.escape(fragment)):
            parse_query(q)

    def test_every_refusal_is_a_query_error(self):
        """The API maps QueryError to 400; nothing malformed may escape as a 500."""
        assert issubclass(QuerySyntaxError, QueryError) and issubclass(QueryTooComplex, QueryError)

    def test_too_many_terms(self):
        with pytest.raises(QueryTooComplex):
            parse_query(" AND ".join(["level:ERROR"] * 2000))

    def test_too_deep(self):
        with pytest.raises(QueryTooComplex):
            parse_query("(" * (MAX_QUERY_DEPTH + 1) + "a" + ")" * (MAX_QUERY_DEPTH + 1))
        with pytest.raises(QueryTooComplex):
            parse_query("NOT " * (MAX_QUERY_DEPTH + 1) + "a")

    def test_too_long(self):
        with pytest.raises(QueryTooComplex):
            parse_query("a" * 50000)


class TestSql:
    def test_values_and_field_names_are_bound_never_interpolated(self):
        params: dict = {}
        sql = compile_to_sql(parse_query("""user.name:"x' OR 1=1 --" "evil'; DROP" """), params)
        assert "OR 1=1" not in sql and "DROP" not in sql and "user" not in sql
        assert "x' OR 1=1 --" in params.values()

    def test_like_metacharacters_are_escaped(self):
        params: dict = {}
        compile_to_sql(parse_query("50%_off"), params)
        assert params["p0"] == "%50\\%\\_off%"

    def test_wildcard_becomes_like(self):
        params: dict = {}
        sql = compile_to_sql(parse_query("path:/api/*"), params)
        assert "ILIKE" in sql and params["p1"] == "/api/%"


@pytest.mark.parametrize("query,expected", sorted(PARITY.items()))
def test_in_memory_matches(query, expected):
    assert [i for i, r in enumerate(RECORDS) if evaluate_in_memory(parse_query(query), r)] == expected


class TestSavedQueriesAreValidated:
    def test_monitor_with_a_malformed_query_is_refused(self):
        from pydantic import ValidationError

        from denoiser.api.monitors import MonitorCreateSchema

        with pytest.raises(ValidationError, match="never closed"):
            MonitorCreateSchema(name="m", query="(level:ERROR")

    def test_saved_query_with_a_malformed_query_is_refused(self):
        from pydantic import ValidationError

        from denoiser.query.models import QueryCreateSchema

        with pytest.raises(ValidationError, match="Unclosed quote"):
            QueryCreateSchema(name="q", query_text='"oops')


def test_explore_answers_400_not_503_for_a_malformed_histogram_query(monkeypatch):
    """The histogram parsed only on its fallback path, so a typo looked like an outage."""
    from fastapi.testclient import TestClient

    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app
    from denoiser.storage.db import User

    app.dependency_overrides[get_current_user] = lambda: User(id=1, email="t@x.io", role="VIEWER", tenant_id=1)
    try:
        res = TestClient(app).post("/query/histogram", json={"query": "(level:ERROR"})
        assert res.status_code == 400
        assert "never closed" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()
