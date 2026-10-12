"""Whole-function replacement for JavaScript, TypeScript and Go (tree-sitter), plus the
patcher's dispatch and the Python path's qualified-name handling."""
from __future__ import annotations

import pytest

from app.patch import functions
from app.patch.patcher import FilePatch, Workspace, _is_test_path, apply_patch


def replace(text, lang, name, new):
    return functions.replace_function(text, lang, name, new)


def ok(text, lang, name, new) -> str:
    out, err = replace(text, lang, name, new)
    assert err is None, err
    return out


# --------------------------------------------------------------------------- #
# JavaScript
# --------------------------------------------------------------------------- #
JS = """\
// header comment
function plain(a) {
  return a;
}

export function exported(a) {
  return a * 2;
}

export default function defaulted() {
  return 1;
}

const arrow = (x) => x + 1;

export const earrow = async (x) => {
  return x;
};

class Box {
  get(x) {
    return x;
  }
  static make() {
    return new Box();
  }
}

class Other {
  get(x) {
    return x + 1;
  }
}

const api = {
  fetch() {
    return 1;
  },
  parse: (s) => JSON.parse(s),
};

exports.add = function (a, b) {
  return a - b;
};

module.exports.mul = (a, b) => a + b;
"""


def test_replaces_a_plain_function_and_nothing_else():
    out = ok(JS, "javascript", "plain", "function plain(a) {\n  return a + 100;\n}")
    assert "return a + 100;" in out
    assert out.count("function plain") == 1
    assert out.replace("a + 100", "a") == JS  # byte-for-byte identical elsewhere


def test_an_export_wrapper_is_part_of_the_span():
    out = ok(JS, "javascript", "exported", "export function exported(a) {\n  return a * 3;\n}")
    assert "export function exported(a) {\n  return a * 3;\n}" in out
    assert out.count("export function exported") == 1
    default = ok(JS, "javascript", "defaulted", "export default function defaulted() {\n  return 2;\n}")
    assert "return 2;" in default and default.count("export default function") == 1


def test_arrow_functions_bound_to_a_const():
    out = ok(JS, "javascript", "arrow", "const arrow = (x) => x + 2;")
    assert "const arrow = (x) => x + 2;" in out and "const arrow = (x) => x + 1;" not in out
    out = ok(JS, "javascript", "earrow", "export const earrow = async (x) => {\n  return x + 5;\n};")
    assert "return x + 5;" in out


def test_class_methods_by_qualified_name_and_ambiguous_bare_names():
    out = ok(JS, "javascript", "Box.get", "get(x) {\n  return x * 10;\n}")
    assert "return x * 10;" in out and "return x + 1;" in out  # Other.get untouched

    _, err = replace(JS, "javascript", "get", "get(x) {\n  return 0;\n}")
    assert err and "ambiguous" in err and "Box.get" in err and "Other.get" in err

    assert ok(JS, "javascript", "make", "static make() {\n  return null;\n}")  # unique bare name


def test_method_indentation_is_fitted_to_the_class():
    flush = ok(JS, "javascript", "Box.get", "get(x) {\n  return 7;\n}")
    assert "  get(x) {\n    return 7;\n  }" in flush
    absolute = ok(JS, "javascript", "Box.get", "  get(x) {\n    return 8;\n  }")
    assert "  get(x) {\n    return 8;\n  }" in absolute and "    get(x)" not in absolute


def test_object_literal_members_and_commonjs_exports():
    assert "return 2;" in ok(JS, "javascript", "api.fetch", "fetch() {\n  return 2;\n}")
    assert "JSON.parse(s).x" in ok(JS, "javascript", "api.parse", "parse: (s) => JSON.parse(s).x,")
    assert "return a + b;" in ok(JS, "javascript", "add", "exports.add = function (a, b) {\n  return a + b;\n};")
    assert "a * b" in ok(JS, "javascript", "mul", "module.exports.mul = (a, b) => a * b;")


def test_a_declarator_in_a_multi_declaration_is_replaced_alone():
    src = "const a = () => 1, b = () => 2;\n"
    out = ok(src, "javascript", "b", "b = () => 3")
    assert out == "const a = () => 1, b = () => 3;\n"


def test_broken_replacements_are_rejected_with_a_location():
    out, err = replace(JS, "javascript", "plain", "function plain(a) {\n  return a +;\n}")
    assert out is None and "invalid javascript" in err and "line" in err
    out, err = replace(JS, "javascript", "plain", "function plain(a) {\n  return a;\n")  # missing brace
    assert out is None and "invalid" in err


def test_unknown_names_list_what_exists():
    out, err = replace(JS, "javascript", "nope", "function nope() {}")
    assert out is None and "not found" in err and "plain" in err


def test_a_file_that_already_had_syntax_errors_can_still_be_edited():
    broken = "function good() { return 1; }\nfunction bad( {\n"
    out, err = replace(broken, "javascript", "good", "function good() { return 2; }")
    assert err is None and "return 2" in out


# --------------------------------------------------------------------------- #
# TypeScript / TSX
# --------------------------------------------------------------------------- #
TS = """\
export function typed(a: number, b: number): number {
  return a + b;
}

class Repo<T> {
  private items: T[] = [];
  find = (i: number): T | undefined => this.items[i];
  async load(): Promise<void> {
    return;
  }
}

function over(a: string): string;
function over(a: number): number;
function over(a: any): any {
  return a;
}
"""


def test_typescript_functions_methods_fields_and_overloads():
    out = ok(TS, "typescript", "typed", "export function typed(a: number, b: number): number {\n  return a * b;\n}")
    assert "return a * b;" in out
    out = ok(TS, "typescript", "Repo.load", "async load(): Promise<void> {\n  await Promise.resolve();\n}")
    assert "await Promise.resolve();" in out
    out = ok(TS, "typescript", "Repo.find", "find = (i: number): T | undefined => this.items[i + 1];")
    assert "this.items[i + 1]" in out
    out = ok(TS, "typescript", "over", "function over(a: any): any {\n  return String(a);\n}")
    assert "return String(a);" in out and out.count("function over(a: string): string;") == 1  # overloads kept


def test_tsx_components_with_jsx_parse():
    tsx = "export function Button({ label }: { label: string }) {\n  return <button>{label}</button>;\n}\n"
    out = ok(tsx, "tsx", "Button", "export function Button({ label }: { label: string }) {\n  return <button>{label}!</button>;\n}")
    assert "{label}!" in out


# --------------------------------------------------------------------------- #
# Go
# --------------------------------------------------------------------------- #
GO = """\
package calc

// Add adds two numbers.
func Add(a, b int) int {
\treturn a + b
}

type Stack struct{ items []int }

func (s *Stack) Push(x int) {
\ts.items = append(s.items, x)
}

func (s Stack) Len() int {
\treturn len(s.items)
}

type Queue struct{ items []int }

func (q Queue) Len() int {
\treturn len(q.items)
}
"""


def test_go_functions_and_methods_keep_their_doc_comments():
    out = ok(GO, "go", "Add", "func Add(a, b int) int {\n\treturn a - b\n}")
    assert "// Add adds two numbers.\nfunc Add(a, b int) int {\n\treturn a - b\n}" in out
    out = ok(GO, "go", "Stack.Push", "func (s *Stack) Push(x int) {\n\ts.items = append([]int{x}, s.items...)\n}")
    assert "append([]int{x}, s.items...)" in out


def test_go_ambiguous_and_qualified_method_names():
    _, err = replace(GO, "go", "Len", "func (s Stack) Len() int {\n\treturn 0\n}")
    assert err and "ambiguous" in err and "Queue.Len" in err and "Stack.Len" in err
    out = ok(GO, "go", "Queue.Len", "func (q Queue) Len() int {\n\treturn 42\n}")
    assert "return 42" in out and "return len(s.items)" in out
    assert ok(GO, "go", "Push", "func (s *Stack) Push(x int) {\n}")  # unique bare name works


def test_go_syntax_errors_are_rejected():
    out, err = replace(GO, "go", "Add", "func Add(a, b int) int {\n\treturn a +\n}")
    assert out is None and "invalid go" in err


# --------------------------------------------------------------------------- #
# The patcher
# --------------------------------------------------------------------------- #
def _ws(tmp_path, files: dict[str, str]) -> Workspace:
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return Workspace("w", str(tmp_path))


def _fn(file, name, src):
    return FilePatch(file=file, changes=[], function=name, new_source=src)


def test_apply_patch_dispatches_by_extension(tmp_path):
    ws = _ws(tmp_path, {"src/calc.js": "function add(a, b) {\n  return a - b;\n}\n",
                        "calc/calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a - b\n}\n",
                        "m.py": "def f():\n    return 1\n"})
    res = apply_patch(ws, [_fn("src/calc.js", "add", "function add(a, b) {\n  return a + b;\n}"),
                           _fn("calc/calc.go", "Add", "func Add(a, b int) int {\n\treturn a + b\n}"),
                           _fn("m.py", "f", "def f():\n    return 2")])
    assert res.applied, res.error
    assert "a + b" in (tmp_path / "src/calc.js").read_text()
    assert "return a + b" in (tmp_path / "calc/calc.go").read_text()
    assert "return 2" in (tmp_path / "m.py").read_text()


def test_apply_patch_reports_unsupported_files_and_bad_code(tmp_path):
    ws = _ws(tmp_path, {"a.rb": "def f; 1; end\n", "b.js": "function f() { return 1; }\n"})
    res = apply_patch(ws, [_fn("a.rb", "f", "def f; 2; end")])
    assert not res.applied and "aren't supported" in res.error
    res = apply_patch(ws, [_fn("b.js", "f", "function f() { return ; ;; }}")])
    assert not res.applied and "invalid javascript" in res.error
    assert (tmp_path / "b.js").read_text() == "function f() { return 1; }\n"  # untouched


def test_python_qualified_names_and_ambiguity(tmp_path):
    src = ("class A:\n    def run(self):\n        return 1\n\n"
           "class B:\n    def run(self):\n        return 2\n\n"
           "def run():\n    return 3\n")
    ws = _ws(tmp_path, {"m.py": src})
    res = apply_patch(ws, [_fn("m.py", "A.run", "    def run(self):\n        return 10")])
    assert res.applied, res.error
    text = (tmp_path / "m.py").read_text()
    assert "return 10" in text and "return 2" in text and "return 3" in text

    # "run" is exactly the top-level function: an exact qualified match wins, no ambiguity
    assert apply_patch(ws, [_fn("m.py", "run", "def run():\n    return 30")]).applied
    assert "return 30" in (tmp_path / "m.py").read_text()

    other = _ws(tmp_path / "two", {"m.py": "class A:\n    def go(self):\n        return 1\n\n"
                                          "class B:\n    def go(self):\n        return 2\n"})
    res = apply_patch(other, [_fn("m.py", "go", "def go(self):\n    return 0")])
    assert not res.applied and "ambiguous" in res.error and "A.go" in res.error


@pytest.mark.parametrize("path,expected", [
    ("calc/calc_test.go", True), ("src/button.test.tsx", True), ("src/button.spec.jsx", True),
    ("tests/test_a.py", True), ("calc/calc.go", False), ("src/button.tsx", False), ("src/latest.js", False),
])
def test_test_files_of_every_language_are_protected(path, expected):
    assert _is_test_path(path) is expected
