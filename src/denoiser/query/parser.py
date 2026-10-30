"""LQL, the log query language behind Explore, `/v1/logs/query` and monitors.

Grammar (precedence lowest to highest; operators are upper-case only, so the
words "and", "or" and "not" in lower case are ordinary search terms)::

    query    := or_expr?
    or_expr  := and_expr ("OR" and_expr)*
    and_expr := unary (["AND"] unary)*          -- adjacent terms are ANDed
    unary    := ("NOT" | "-") unary | primary
    primary  := "(" or_expr ")" | term
    term     := FIELD ":" [CMP] value | value
    CMP      := ">" | ">=" | "<" | "<="
    value    := '"' chars '"' | bare-word       -- `*` is a wildcard in either
    FIELD    := name ("." name)*                -- a dot reaches into nested JSON

Examples::

    timeout                          free text, anywhere in the line
    "connection reset"               exact phrase
    level:ERROR service:payment      both (implicit AND)
    level:ERROR OR level:WARN
    status:>=500 status:<600         numeric range
    -level:DEBUG                     same as NOT level:DEBUG
    (db OR cache) AND timeout
    user.id:42                       nested field {"user": {"id": 42}}
    trace_id:*                       field is present
    path:/api/*                      wildcard

Field and text matches are case-insensitive. A malformed query is a
`QuerySyntaxError` naming the position, never a silent reinterpretation.

This replaced a regex tokenizer that had no NOT and no parentheses (both were
searched for as literal text), bound `a OR b AND c` as `(a OR b) AND c`, sent
`%` and `_` into LIKE unescaped, and compared JSON numbers with
`JSONExtractString`, which yields '' for a number, so `status:500` matched in
the file fallback and never in ClickHouse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: One segment of a field name. The name reaches SQL only as bound parameters,
#: but it is still restricted to what a log field is called.
_NAME = r"[A-Za-z0-9_@$-]+"
FIELD_NAME = re.compile(rf"^{_NAME}(?:\.{_NAME})*$")

#: Most terms a single query may contain. A clear 400 at parse time rather than
#: an arbitrarily large statement sent to ClickHouse.
MAX_QUERY_TERMS = 256

#: Deepest nesting of parentheses and NOT. The parser and compiler recurse on
#: these, so an unbounded depth would surface as a RecursionError (a 500).
MAX_QUERY_DEPTH = 32

#: Longest raw query string accepted.
MAX_QUERY_LENGTH = 8192

#: Columns of `semantic_logs` matched directly rather than inside raw_json.
_COLUMNS = ("source", "level", "message")

_COMPARATORS = (">=", "<=", ">", "<")


class QueryError(ValueError):
    """A query the caller must fix. Every subclass is a 400, never a 500."""


class QueryTooComplex(QueryError):
    """Raised when a query exceeds the parser's complexity bounds."""


class QuerySyntaxError(QueryError):
    """Raised when a query is malformed. The message names the position."""


# ── AST ─────────────────────────────────────────────────────────────────────

class QueryNode:
    pass


@dataclass
class TextMatch(QueryNode):
    """Free text anywhere in the record. Empty text matches everything."""
    text: str


@dataclass
class FieldMatch(QueryNode):
    """``field:value`` (op ``=``), ``field:>=n`` and friends, or ``field:*`` (op ``exists``)."""
    field: str
    value: str
    op: str = "="


@dataclass
class AndNode(QueryNode):
    children: list[QueryNode]


@dataclass
class OrNode(QueryNode):
    children: list[QueryNode]


@dataclass
class NotNode(QueryNode):
    child: QueryNode


# ── Tokenizer ───────────────────────────────────────────────────────────────

@dataclass
class _Token:
    kind: str   # LPAREN RPAREN AND OR NOT TERM
    pos: int
    field: str | None = None
    op: str = "="
    value: str = ""
    quoted: bool = False


def _read_value(text: str, i: int) -> tuple[str, bool, int]:
    """A quoted or bare value starting at ``i``. Returns (value, quoted, end)."""
    if i < len(text) and text[i] == '"':
        out = []
        j = i + 1
        while j < len(text):
            ch = text[j]
            if ch == "\\" and j + 1 < len(text) and text[j + 1] in '"\\':
                out.append(text[j + 1])
                j += 2
                continue
            if ch == '"':
                return "".join(out), True, j + 1
            out.append(ch)
            j += 1
        raise QuerySyntaxError(f'Unclosed quote starting at position {i + 1}')
    # Parentheses inside a word belong to it when they balance there, so
    # `getUser()` is a search term; `b)` in `(a OR b)` ends at the `)`.
    j = i
    depth = 0
    while j < len(text) and not text[j].isspace() and text[j] != '"':
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            if depth == 0:
                break
            depth -= 1
        j += 1
    return text[i:j], False, j


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token("LPAREN", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token("RPAREN", i))
            i += 1
            continue
        # `-term` is NOT, but only directly before a term: "a - b" keeps the
        # dash as text, and "-5" alone is a search for "-5".
        if ch == "-" and i + 1 < n and (text[i + 1] == "(" or text[i + 1] == '"' or text[i + 1].isalpha()):
            tokens.append(_Token("NOT", i))
            i += 1
            continue

        start = i
        if ch != '"':
            m = re.match(rf"({_NAME}(?:\.{_NAME})*):", text[i:])
            if m:
                field = m.group(1)
                j = i + m.end()
                op = "="
                for cmp in _COMPARATORS:
                    if text.startswith(cmp, j):
                        op, j = cmp, j + len(cmp)
                        break
                if j >= n or text[j].isspace() or text[j] in "()":
                    raise QuerySyntaxError(f"'{field}:' at position {start + 1} needs a value")
                value, quoted, j = _read_value(text, j)
                if op == "=" and value == "*" and not quoted:
                    op = "exists"
                tokens.append(_Token("TERM", start, field=field, op=op, value=value, quoted=quoted))
                i = j
                continue

        value, quoted, j = _read_value(text, i)
        if not quoted and value in ("AND", "OR", "NOT"):
            tokens.append(_Token(value, start))
        else:
            tokens.append(_Token("TERM", start, value=value, quoted=quoted))
        i = j

    terms = sum(1 for t in tokens if t.kind == "TERM")
    if terms > MAX_QUERY_TERMS:
        raise QueryTooComplex(f"Query has {terms} terms; the limit is {MAX_QUERY_TERMS}")
    return tokens


# ── Parser ──────────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> _Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _descend(self, tok: _Token) -> None:
        self.depth += 1
        if self.depth > MAX_QUERY_DEPTH:
            raise QueryTooComplex(f"Query nests deeper than {MAX_QUERY_DEPTH} levels")

    def or_expr(self) -> QueryNode:
        children = [self.and_expr()]
        while (tok := self.peek()) and tok.kind == "OR":
            self.take()
            if not self.peek() or self.peek().kind in ("OR", "AND", "RPAREN"):
                raise QuerySyntaxError(f"'OR' at position {tok.pos + 1} has nothing after it")
            children.append(self.and_expr())
        return children[0] if len(children) == 1 else OrNode(children)

    def and_expr(self) -> QueryNode:
        children = [self.unary()]
        while (tok := self.peek()) and tok.kind not in ("OR", "RPAREN"):
            if tok.kind == "AND":
                self.take()
                if not self.peek() or self.peek().kind in ("OR", "AND", "RPAREN"):
                    raise QuerySyntaxError(f"'AND' at position {tok.pos + 1} has nothing after it")
            children.append(self.unary())
        return children[0] if len(children) == 1 else AndNode(children)

    def unary(self) -> QueryNode:
        tok = self.peek()
        if tok is None:
            raise QuerySyntaxError("Query ends where a term was expected")
        if tok.kind == "NOT":
            self.take()
            self._descend(tok)
            if self.peek() is None:
                raise QuerySyntaxError(f"'NOT' at position {tok.pos + 1} has nothing after it")
            node = NotNode(self.unary())
            self.depth -= 1
            return node
        return self.primary()

    def primary(self) -> QueryNode:
        tok = self.take()
        if tok.kind == "LPAREN":
            self._descend(tok)
            if self.peek() and self.peek().kind == "RPAREN":
                raise QuerySyntaxError(f"Empty parentheses at position {tok.pos + 1}")
            node = self.or_expr()
            close = self.peek()
            if close is None or close.kind != "RPAREN":
                raise QuerySyntaxError(f"Parenthesis at position {tok.pos + 1} is never closed")
            self.take()
            self.depth -= 1
            return node
        if tok.kind == "TERM":
            if tok.field is None:
                return TextMatch(tok.value)
            if tok.op in _COMPARATORS:
                try:
                    float(tok.value)
                except ValueError:
                    raise QuerySyntaxError(
                        f"'{tok.field}:{tok.op}{tok.value}' at position {tok.pos + 1} compares with a non-number"
                    ) from None
            return FieldMatch(tok.field, tok.value, tok.op)
        if tok.kind == "RPAREN":
            raise QuerySyntaxError(f"Unmatched ')' at position {tok.pos + 1}")
        raise QuerySyntaxError(f"'{tok.kind}' at position {tok.pos + 1} has nothing before it")


def parse_query(query_str: str) -> QueryNode:
    """Parse LQL into an AST. Raises QueryError (a 400) for anything malformed."""
    if query_str and len(query_str) > MAX_QUERY_LENGTH:
        raise QueryTooComplex(
            f"Query is {len(query_str)} characters; the limit is {MAX_QUERY_LENGTH}"
        )
    tokens = _tokenize(query_str or "")
    if not tokens:
        return TextMatch("")

    parser = _Parser(tokens)
    node = parser.or_expr()
    leftover = parser.peek()
    if leftover is not None:
        raise QuerySyntaxError(f"Unmatched ')' at position {leftover.pos + 1}")
    return node


def validate_lql(value: str | None) -> str | None:
    """Pydantic validator: refuse a malformed query when it is saved.

    A monitor or saved query with a typo used to be accepted and then either
    matched something unintended or, once evaluated, sat in ERROR.
    """
    if value is not None:
        parse_query(value)
    return value


# ── Matching helpers shared by both back ends ───────────────────────────────

def _has_wildcard(value: str) -> bool:
    return "*" in value


def _like_pattern(value: str, *, contains: bool) -> str:
    """LIKE pattern for a value: `%`, `_` and `\\` escaped, `*` a wildcard."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%")
    return f"%{escaped}%" if contains else escaped


def _wildcard_regex(value: str, *, contains: bool) -> re.Pattern:
    body = ".*".join(re.escape(part) for part in value.split("*"))
    return re.compile(body if contains else f"^{body}$", re.IGNORECASE | re.DOTALL)


# ── In-memory evaluation (development file fallback) ────────────────────────

_MISSING = object()


def _lookup(log: dict[str, Any], field: str) -> Any:
    """A field's value: a literal dotted key first, then a nested path."""
    if field in log:
        return log[field]
    current: Any = log
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_in_memory(node: QueryNode, log: dict[str, Any]) -> bool:
    if isinstance(node, TextMatch):
        if not node.text:
            return True
        haystack = str(log)
        if _has_wildcard(node.text):
            return bool(_wildcard_regex(node.text, contains=True).search(haystack))
        return node.text.lower() in haystack.lower()
    if isinstance(node, FieldMatch):
        value = _lookup(log, node.field)
        if node.op == "exists":
            return value is not _MISSING and value not in (None, "")
        if value is _MISSING or value is None:
            return False
        if node.op in _COMPARATORS:
            have, want = _as_number(value), float(node.value)
            if have is None:
                return False
            return {">": have > want, ">=": have >= want, "<": have < want, "<=": have <= want}[node.op]
        text = str(value).lower()
        if _has_wildcard(node.value):
            return bool(_wildcard_regex(node.value, contains=False).match(text))
        if text == node.value.lower():
            return True
        # `status:500` matches 500, 500.0 and "500" alike, as it does in SQL.
        want, have = _as_number(node.value), _as_number(value)
        return want is not None and have is not None and want == have
    if isinstance(node, AndNode):
        return all(evaluate_in_memory(c, log) for c in node.children)
    if isinstance(node, OrNode):
        return any(evaluate_in_memory(c, log) for c in node.children)
    if isinstance(node, NotNode):
        return not evaluate_in_memory(node.child, log)
    return False


# ── ClickHouse compilation ──────────────────────────────────────────────────

def _bind(params: dict[str, Any], value: Any) -> str:
    """Bind ``value`` and return its placeholder name. Values are never interpolated."""
    name = f"p{len(params)}"
    params[name] = value
    return name


def _json_text(field: str, params: dict[str, Any]) -> str:
    """SQL for a raw_json field as text, whatever JSON type it holds.

    `JSONExtractString` returns '' for a number or a boolean, which is why
    `status:500` used to match nothing. Strings are extracted as strings (so
    escapes are decoded); everything else as its raw JSON text.
    """
    def path_args(keys: list[str]) -> str:
        return ", ".join(f"{{{_bind(params, k)}:String}}" for k in keys)

    def text_of(args: str) -> str:
        return (
            f"if(JSONType(raw_json, {args}) = 'String', "
            f"JSONExtractString(raw_json, {args}), JSONExtractRaw(raw_json, {args}))"
        )

    literal = path_args([field])
    if "." not in field:
        return text_of(literal)
    # A dotted name is a literal key if the record has one, else a nested path.
    nested = path_args(field.split("."))
    return f"if(JSONHas(raw_json, {literal}), {text_of(literal)}, {text_of(nested)})"


def _json_present(field: str, params: dict[str, Any]) -> str:
    literal = f"{{{_bind(params, field)}:String}}"
    if "." not in field:
        return f"JSONHas(raw_json, {literal})"
    nested = ", ".join(f"{{{_bind(params, k)}:String}}" for k in field.split("."))
    return f"(JSONHas(raw_json, {literal}) OR JSONHas(raw_json, {nested}))"


def compile_to_sql(node: QueryNode, params: dict[str, Any]) -> str:
    """Compile the AST to a ClickHouse WHERE clause. Every value is a bound parameter."""
    if isinstance(node, TextMatch):
        if not node.text:
            return "1=1"
        p = _bind(params, _like_pattern(node.text, contains=True))
        return f"(message ILIKE {{{p}:String}} OR raw_json ILIKE {{{p}:String}})"

    if isinstance(node, FieldMatch):
        if not FIELD_NAME.match(node.field):
            raise QuerySyntaxError(f"Invalid field name: {node.field!r}")
        column = node.field if node.field in _COLUMNS else None

        if node.op == "exists":
            return f"({column} != '')" if column else _json_present(node.field, params)

        expr = column or _json_text(node.field, params)
        if node.op in _COMPARATORS:
            p = _bind(params, float(node.value))
            return f"(toFloat64OrNull({expr}) {node.op} {{{p}:Float64}})"
        if _has_wildcard(node.value):
            p = _bind(params, _like_pattern(node.value, contains=False))
            return f"({expr} ILIKE {{{p}:String}})"
        p = _bind(params, node.value)
        text_eq = f"lower({expr}) = lower({{{p}:String}})"
        number = _as_number(node.value)
        if number is None:
            return f"({text_eq})"
        # Numerically too, so 500, 500.0 and "500" all satisfy `status:500`.
        n = _bind(params, number)
        return f"({text_eq} OR toFloat64OrNull({expr}) = {{{n}:Float64}})"

    if isinstance(node, AndNode):
        return "(" + " AND ".join(compile_to_sql(c, params) for c in node.children) + ")"
    if isinstance(node, OrNode):
        return "(" + " OR ".join(compile_to_sql(c, params) for c in node.children) + ")"
    if isinstance(node, NotNode):
        return f"(NOT {compile_to_sql(node.child, params)})"
    return "1=1"


def parse_plain_text_log(line: str) -> dict[str, Any] | None:
    """Attempt to parse common plain-text log formats into a structured dictionary."""
    line = line.strip()
    if not line:
        return None
        
    # Format: 2026-05-29T10:15:00Z INFO [service-name] message...
    # Or: 2026-05-17 17:15:00 [info]: message
    import re
    from datetime import UTC, datetime
    
    # Try ISO8601 with level and source
    # Regex for: 2026-05-08T21:23:59.516165Z INFO  [API Gateway] 200 OK GET...
    # Regex for: 2026-05-29T10:15:00Z INFO [acme-payment-gateway] Payment request received.
    iso_match = re.match(r'^([\d-]+\w[\d:.]+Z?)\s+(\w+)\s+\[(.*?)\]\s+(.*)', line)
    if iso_match:
        ts_str, level, source, msg = iso_match.groups()
        try:
            # Handle Python 3.11+ fromisoformat
            if ts_str.endswith('Z'):
                ts_str = ts_str[:-1] + '+00:00'
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            ts = dt.timestamp()
        except ValueError:
            ts = None
            
        return {
            "timestamp": ts,
            "level": level.upper(),
            "source": source,
            "message": msg
        }
        
    return {"message": line}
