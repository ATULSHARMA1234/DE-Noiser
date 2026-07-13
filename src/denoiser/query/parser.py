import re
from typing import Any

FIELD_NAME = re.compile(r"^[A-Za-z0-9_]+$")


class QueryNode:
    pass

class FieldMatch(QueryNode):
    def __init__(self, field: str, value: str):
        self.field = field
        self.value = value

class TextMatch(QueryNode):
    def __init__(self, text: str):
        self.text = text

class AndNode(QueryNode):
    def __init__(self, left: QueryNode, right: QueryNode):
        self.left = left
        self.right = right

class OrNode(QueryNode):
    def __init__(self, left: QueryNode, right: QueryNode):
        self.left = left
        self.right = right

def parse_query(query_str: str) -> QueryNode:
    """
    Extremely simple recursive descent parser for:
    field:value
    "exact text"
    AND / OR
    """
    # For a real implementation, we'd use pyparsing or similar.
    # This is a naive regex-based tokenization for the demo.
    # field:"quoted value" | field:bare-value (hyphens/dots/slashes ok) | "phrase" | word
    tokens = re.findall(r'([A-Za-z0-9_]+:"[^"]*"|[A-Za-z0-9_]+:[^\s"]+|"[^"]*"|\S+)', query_str)

    if not tokens:
        return TextMatch("")

    nodes = []
    for token in tokens:
        if token in ("AND", "OR"):
            nodes.append(token)
        elif ":" in token and not token.startswith('"'):
            k, v = token.split(":", 1)
            # The field name is interpolated into SQL (JSONExtractString), so it
            # must be a plain identifier. Anything else is treated as free text
            # rather than trusted as a column/JSON key.
            if not FIELD_NAME.match(k):
                nodes.append(TextMatch(token))
                continue
            if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
                v = v[1:-1]  # strip quotes from field:"quoted value"
            nodes.append(FieldMatch(k, v))
        elif token.startswith('"') and token.endswith('"'):
            nodes.append(TextMatch(token[1:-1]))
        else:
            nodes.append(TextMatch(token))

    if not nodes:
        return TextMatch("")

    # Left-associative build. Terms sitting next to each other with no explicit
    # operator are an implicit AND. The previous loop stopped at len(nodes)-1 and
    # so silently dropped every term after the first in `level:ERROR timeout`,
    # quietly widening the result set instead of erroring.
    current = nodes[0] if not isinstance(nodes[0], str) else TextMatch("")
    i = 1 if not isinstance(nodes[0], str) else 0
    while i < len(nodes):
        token = nodes[i]
        if isinstance(token, str):  # AND / OR
            if i + 1 >= len(nodes):
                break  # dangling operator -- ignore
            nxt = nodes[i + 1]
            if isinstance(nxt, str):  # two operators in a row -- skip the first
                i += 1
                continue
            current = AndNode(current, nxt) if token == "AND" else OrNode(current, nxt)
            i += 2
        else:
            current = AndNode(current, token)  # implicit AND
            i += 1

    return current

def evaluate_in_memory(node: QueryNode, log: dict[str, Any]) -> bool:
    if isinstance(node, TextMatch):
        if not node.text:
            return True
        return node.text.lower() in str(log).lower()
    elif isinstance(node, FieldMatch):
        val = str(log.get(node.field, "")).lower()
        return val == node.value.lower()
    elif isinstance(node, AndNode):
        return evaluate_in_memory(node.left, log) and evaluate_in_memory(node.right, log)
    elif isinstance(node, OrNode):
        return evaluate_in_memory(node.left, log) or evaluate_in_memory(node.right, log)
    return False

def compile_to_sql(node: QueryNode, params: dict[str, Any]) -> str:
    """Compile the AST to ClickHouse SQL WHERE clause using parameterized parameters"""
    param_id = f"p{len(params)}"

    if isinstance(node, TextMatch):
        if not node.text:
            return "1=1"
        params[param_id] = f"%{node.text}%"
        return f"(message ILIKE {{{param_id}:String}} OR raw_json ILIKE {{{param_id}:String}})"

    elif isinstance(node, FieldMatch):
        params[param_id] = node.value
        if node.field in ("source", "level"):
            return f"{node.field} = {{{param_id}:String}}"
        else:
            if not FIELD_NAME.match(node.field):
                raise ValueError(f"Invalid field name: {node.field!r}")
            return f"JSONExtractString(raw_json, '{node.field}') = {{{param_id}:String}}"

    elif isinstance(node, AndNode):
        left_sql = compile_to_sql(node.left, params)
        right_sql = compile_to_sql(node.right, params)
        return f"({left_sql} AND {right_sql})"

    elif isinstance(node, OrNode):
        left_sql = compile_to_sql(node.left, params)
        right_sql = compile_to_sql(node.right, params)
        return f"({left_sql} OR {right_sql})"

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
