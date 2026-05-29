import re
from typing import List, Dict, Any

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

def evaluate_in_memory(node: QueryNode, log: Dict[str, Any]) -> bool:
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

def compile_to_sql(node: QueryNode) -> str:
    """Compile the AST to ClickHouse SQL WHERE clause"""
    if isinstance(node, TextMatch):
        if not node.text:
            return "1=1"
        # ClickHouse syntax for searching JSON string or message
        safe_text = node.text.replace("'", "''")
        return f"(message ILIKE '%{safe_text}%' OR raw_json ILIKE '%{safe_text}%')"
    elif isinstance(node, FieldMatch):
        safe_val = node.value.replace("'", "''")
        if node.field in ("source", "level"):
            return f"{node.field} = '{safe_val}'"
        else:
            # JSON extract for arbitrary fields
            return f"JSONExtractString(raw_json, '{node.field}') = '{safe_val}'"
    elif isinstance(node, AndNode):
        return f"({compile_to_sql(node.left)} AND {compile_to_sql(node.right)})"
    elif isinstance(node, OrNode):
        return f"({compile_to_sql(node.left)} OR {compile_to_sql(node.right)})"
    return "1=1"
