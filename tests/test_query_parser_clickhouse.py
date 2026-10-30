"""LQL's compiled SQL, executed by a real ClickHouse, must agree with the
in-memory evaluator on every case in tests/test_query_parser.py.

The previous compiler was only ever checked as a string: `status:500` compiled
to a `JSONExtractString` comparison that is '' for a JSON number, so it matched
nothing in ClickHouse while the file fallback matched it, and no test noticed.

Skipped when no ClickHouse is reachable, like tests/test_clickhouse_integration.py:

    docker compose -f docker-compose-infra.yml up -d
    CLICKHOUSE_HOST=localhost uv run pytest tests/test_query_parser_clickhouse.py
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.integration

clickhouse_connect = pytest.importorskip("clickhouse_connect")


@pytest.fixture(scope="module")
def client():
    try:
        c = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            username=os.getenv("CLICKHOUSE_USER", "default"),
            password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            database=os.getenv("CLICKHOUSE_DB", "default"),
        )
        c.command("SELECT 1")
    except Exception:
        pytest.skip("no ClickHouse reachable — start docker-compose-infra.yml to run these")
    return c


@pytest.mark.parametrize("query", sorted(__import__("test_query_parser").PARITY))
def test_clickhouse_agrees_with_in_memory(client, query):
    from test_query_parser import PARITY, RECORDS

    from denoiser.query.parser import compile_to_sql, parse_query

    # The records as `semantic_logs` holds them: three columns lifted out,
    # everything in raw_json. Bound like the rows themselves, so the test
    # touches no table.
    rows_params = {}
    rows = []
    for i, r in enumerate(RECORDS):
        rows_params.update({
            f"s{i}": r.get("source", ""), f"l{i}": r.get("level", ""),
            f"m{i}": r.get("message", ""), f"j{i}": json.dumps(r),
        })
        rows.append(f"({i}, {{s{i}:String}}, {{l{i}:String}}, {{m{i}:String}}, {{j{i}:String}})")
    table = (
        "values('id UInt32, source String, level String, message String, raw_json String', "
        + ", ".join(rows) + ")"
    )

    params: dict = {}
    where = compile_to_sql(parse_query(query), params)
    result = client.query(f"SELECT id FROM {table} WHERE {where} ORDER BY id", parameters={**params, **rows_params})

    assert [row[0] for row in result.result_rows] == PARITY[query]
