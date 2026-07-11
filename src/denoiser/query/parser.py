import re
from typing import Any


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
    tokens = re.findall(r'([A-Za-z0-9_]+:[A-Za-z0-9_]+|"[^"]*"|\S+)', query_str)

    if not tokens:
        return TextMatch("")

    # very naive evaluator
    nodes = []
    for token in tokens:
        if token in ("AND", "OR"):
            nodes.append(token)
        elif ":" in token and not token.startswith('"'):
            k, v = token.split(":", 1)
            nodes.append(FieldMatch(k, v))
        elif token.startswith('"') and token.endswith('"'):
            nodes.append(TextMatch(token[1:-1]))
        else:
            nodes.append(TextMatch(token))

    if not nodes:
        return TextMatch("")

    # left associative building
    current = nodes[0]
    i = 1
    while i < len(nodes) - 1:
        op = nodes[i]
        next_node = nodes[i+1]
        if op == "AND":
            current = AndNode(current, next_node)
        elif op == "OR":
            current = OrNode(current, next_node)
        else:
            # implicit AND
            current = AndNode(current, op)
            i -= 1
        i += 2

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
    from datetime import datetime, UTC
    
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
