"""Whole-function replacement for JavaScript, TypeScript and Go, using tree-sitter.

The Python repair path swaps a function located with `ast`; this is the same idea for
other languages. The model returns the COMPLETE new declaration and we locate the old
one by name and swap it, so there are no line numbers to get wrong. A replacement that
would leave the file unparseable is rejected, never written.

What counts as "a function" (and therefore what a patch can target):
  JS / TS:  function declarations, class methods (`Class.method`), arrow / function
            expressions bound to a const/let/var or a class field, `exports.x = ...` /
            `module.exports = ...` assignments, and methods or function-valued
            properties of an object literal bound to a variable (`api.get`).
            An `export` / `export default` wrapper is part of the span: return it too.
  Go:       function declarations and methods (`Type.Method`).

Names may be bare (`withdraw`) or qualified (`Account.withdraw`). A bare name that
matches several functions is refused with the candidates listed, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

LANG_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
}

_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


@lru_cache(maxsize=None)
def _parser(lang: str):
    from tree_sitter import Language, Parser

    if lang == "javascript":
        import tree_sitter_javascript as grammar
        ptr = grammar.language()
    elif lang == "typescript":
        import tree_sitter_typescript as grammar
        ptr = grammar.language_typescript()
    elif lang == "tsx":
        import tree_sitter_typescript as grammar
        ptr = grammar.language_tsx()
    elif lang == "go":
        import tree_sitter_go as grammar
        ptr = grammar.language()
    else:
        raise ValueError(f"unsupported language: {lang}")
    return Parser(Language(ptr))


def language_for(path: str) -> str | None:
    import os
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower())


@dataclass
class Fn:
    name: str
    qualified: str
    start: int   # byte offsets of the whole replaceable statement
    end: int


def _name(node, field: str = "name") -> str:
    child = node.child_by_field_name(field)
    if child is None:
        return ""
    text = child.text.decode("utf-8", errors="replace")
    return text.strip("'\"`")


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------------- #
def _js_functions(root) -> list[Fn]:
    out: list[Fn] = []

    def add(name: str, qualified: str, span) -> None:
        if name:
            out.append(Fn(name, qualified, span.start_byte, span.end_byte))

    def object_members(obj, owner: str) -> None:
        for member in obj.named_children:
            if member.type == "method_definition":
                key = _name(member)
                add(key, f"{owner}.{key}" if owner else key, member)
            elif member.type == "pair":
                value = member.child_by_field_name("value")
                if value is not None and value.type in _JS_FUNCTION_VALUES:
                    key = _name(member, "key")
                    add(key, f"{owner}.{key}" if owner else key, member)

    def class_members(cls) -> None:
        cname = _name(cls)
        body = cls.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "method_definition":
                key = _name(member)
                add(key, f"{cname}.{key}" if cname else key, member)
            elif member.type in ("public_field_definition", "field_definition"):
                value = member.child_by_field_name("value")
                if value is not None and value.type in _JS_FUNCTION_VALUES:
                    key = _name(member, "name") or _name(member, "property")
                    add(key, f"{cname}.{key}" if cname else key, member)

    def declaration(node, outer) -> None:
        span = outer or node
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            name = _name(node)
            add(name, name, span)
        elif t in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in node.named_children if c.type == "variable_declarator"]
            for d in declarators:
                value = d.child_by_field_name("value")
                name = _name(d)
                if value is None or not name:
                    continue
                if value.type in _JS_FUNCTION_VALUES:
                    add(name, name, span if len(declarators) == 1 else d)
                elif value.type == "object":
                    object_members(value, name)
        elif t in ("class_declaration", "class"):
            class_members(node)
        elif t == "expression_statement":
            for expr in node.named_children:
                if expr.type != "assignment_expression":
                    continue
                left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
                if right is None or left is None:
                    continue
                target = left.text.decode("utf-8", errors="replace")
                short = target.rsplit(".", 1)[-1]
                if right.type in _JS_FUNCTION_VALUES:
                    # `module.exports = function named() {}` is called by its own name too
                    inner = _name(right)
                    add(inner or short, target, span)
                elif right.type == "object" and target in ("module.exports", "exports"):
                    object_members(right, "")

    for child in root.named_children:
        if child.type == "export_statement":
            for inner in child.named_children:
                declaration(inner, child)
        else:
            declaration(child, None)
    return out


# --------------------------------------------------------------------------- #
# Go
# --------------------------------------------------------------------------- #
def _go_receiver_type(method) -> str:
    receiver = method.child_by_field_name("receiver")
    if receiver is None:
        return ""
    stack = [receiver]
    while stack:
        node = stack.pop()
        if node.type == "type_identifier":
            return node.text.decode("utf-8", errors="replace")
        stack.extend(reversed(node.named_children))
    return ""


def _go_functions(root) -> list[Fn]:
    out: list[Fn] = []
    for node in root.named_children:
        if node.type == "function_declaration":
            name = _name(node)
            out.append(Fn(name, name, node.start_byte, node.end_byte))
        elif node.type == "method_declaration":
            name = _name(node)
            recv = _go_receiver_type(node)
            out.append(Fn(name, f"{recv}.{name}" if recv else name, node.start_byte, node.end_byte))
    return [f for f in out if f.name]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def list_functions(source: bytes, lang: str) -> list[Fn]:
    root = _parser(lang).parse(source).root_node
    return _go_functions(root) if lang == "go" else _js_functions(root)


def _first_error_line(root) -> int | None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            return node.start_point[0] + 1
        stack.extend(reversed(node.children))
    return None


def _fit_indentation(data: bytes, start: int, new_source: str) -> str:
    """Make the replacement sit correctly at the original's indentation. The bytes before
    `start` already hold the original's leading whitespace, so the first line must not
    repeat it; continuation lines are indented only if the model returned them flush-left
    (detected by the closing line starting at column 0)."""
    line_start = data.rfind(b"\n", 0, start) + 1
    prefix = data[line_start:start]
    indent = prefix.decode("utf-8", errors="replace") if prefix.strip() == b"" else ""

    lines = new_source.strip("\n").rstrip().split("\n")
    lines[0] = lines[0].lstrip(" \t")
    if indent and len(lines) > 1:
        last = next((ln for ln in reversed(lines[1:]) if ln.strip()), "")
        flush_left = last and not last.startswith((" ", "\t"))
        if flush_left:
            lines = [lines[0]] + [(indent + ln) if ln.strip() else ln for ln in lines[1:]]
    return "\n".join(lines)


def replace_function(text: str, lang: str, name: str, new_source: str) -> tuple[str | None, str | None]:
    """Swap function `name` in `text` for `new_source`. Returns (new_text, None) or
    (None, error message) — the message is written to be fed back to the model."""
    data = text.encode("utf-8")
    try:
        parser = _parser(lang)
        functions = list_functions(data, lang)
    except (ImportError, ValueError) as exc:
        return None, f"can't edit {lang} files here: {exc}"

    exact = [f for f in functions if f.qualified == name]
    matches = exact or [f for f in functions if f.name == name]
    if not matches:
        known = ", ".join(sorted({f.qualified for f in functions})[:12]) or "none found"
        return None, f"function '{name}' not found in this file (functions here: {known})"
    if len(matches) > 1:
        options = ", ".join(sorted(f.qualified for f in matches))
        return None, (f"'{name}' is ambiguous: it matches {options}. "
                      "Use the qualified name, e.g. Class.method.")

    target = matches[0]
    replacement = _fit_indentation(data, target.start, new_source)
    result = data[:target.start] + replacement.encode("utf-8") + data[target.end:]

    before_has_error = parser.parse(data).root_node.has_error
    after_root = parser.parse(result).root_node
    if after_root.has_error and not before_has_error:
        line = _first_error_line(after_root)
        where = f" near line {line}" if line else ""
        return None, f"replacement would produce invalid {lang} code{where}"
    return result.decode("utf-8", errors="replace"), None
