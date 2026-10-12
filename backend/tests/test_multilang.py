"""Language detection, result parsing and the repair loop for JavaScript/TypeScript/Go repos
and repos whose dependencies must be installed.

The runner is mock_runner.py in scripted mode: it replays canned test output (the formats
node:test, jest, vitest, mocha and `go test` really print), so what these tests exercise is
everything on the backend side: detection, parsing, prompts, patching with tree-sitter,
dependency-failure handling and the write-ups.
"""
from __future__ import annotations

import json
import os

import pytest

from app.agent import repair
from app.agent.repair import run_repair
from app.agent.report import repair_markdown
from app.config import get_settings
from app.sandbox import runner as sandbox
from app.sandbox.tests import (
    detect_language, detect_test_command, parse_results, run_tests_in_workspace,
)
from mock_runner import MockRunner

TOKEN = "t0ken-for-tests"


def make(tmp_path, files: dict[str, str]) -> str:
    root = tmp_path / "repo"
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return str(root)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
PKG_WITH_TEST = json.dumps({"name": "x", "scripts": {"test": "node --test"}})


@pytest.mark.parametrize("files,expected", [
    ({"tests/test_a.py": "", "package.json": PKG_WITH_TEST}, "python"),   # python tests win over a frontend manifest
    ({"calc/calc_test.go": "", "go.mod": "module x\n"}, "go"),
    ({"package.json": PKG_WITH_TEST, "src/a.js": ""}, "node"),
    ({"package.json": json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})}, "python"),
    ({"package.json": json.dumps({"name": "x"})}, "python"),
    ({"go.mod": "module x\n"}, "go"),
    ({"orders.py": ""}, "python"),
    ({"node_modules/dep/test_x.py": "", "package.json": PKG_WITH_TEST}, "node"),   # vendored files don't count
    ({"a_test.py": ""}, "python"),
])
def test_language_detection(tmp_path, files, expected):
    assert detect_language(make(tmp_path, files)) == expected


def test_a_broken_package_json_is_not_a_crash(tmp_path):
    assert detect_language(make(tmp_path, {"package.json": "{not json"})) == "python"


def test_test_commands_per_language(tmp_path):
    ws = make(tmp_path, {"a": ""})
    assert detect_test_command(ws, "tests/a.py", "python") == ["python", "-m", "pytest", "-q", "tests/a.py"]
    assert detect_test_command(ws, "", "node") == ["npm", "test", "--silent"]
    assert detect_test_command(ws, "test/a.test.js", "node")[-2:] == ["--", "test/a.test.js"]
    assert detect_test_command(ws, "", "go") == ["go", "test", "-v", "-count=1", "./..."]
    assert detect_test_command(ws, "./calc", "go")[-1] == "./calc"


# --------------------------------------------------------------------------- #
# Parsing the output each framework really prints
# --------------------------------------------------------------------------- #
NODE_TAP_FAIL = """TAP version 13
# Subtest: adds
not ok 1 - adds
# tests 3
# suites 0
# pass 2
# fail 1
# cancelled 0
"""
NODE_SPEC = "ℹ tests 4\nℹ pass 3\nℹ fail 1\nℹ duration_ms 55.2\n"
JEST = """FAIL src/cart.test.js
Test Suites: 1 failed, 1 total
Tests:       1 failed, 4 passed, 5 total
"""
JEST_ALL_GREEN = "Tests:       5 passed, 5 total\n"
VITEST = " Test Files  1 failed (1)\n      Tests  2 failed | 6 passed (8)\n"
MOCHA = "\n  6 passing (12ms)\n  2 failing\n"
GO_FAIL = """=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestPop
    stack_test.go:14: got 0, want false
--- FAIL: TestPop (0.00s)
=== RUN   TestTable
=== RUN   TestTable/neg
--- PASS: TestTable/neg (0.00s)
--- PASS: TestTable (0.00s)
FAIL
FAIL	example.com/t/stack	0.004s
"""
GO_OK = "ok  \texample.com/t/calc\t0.003s\n"
GO_BUILD = "# example.com/t/calc\ncalc/calc.go:5:9: undefined: x\nFAIL\texample.com/t/calc [build failed]\n"


@pytest.mark.parametrize("lang,output,expected", [
    ("python", "1 failed, 2 passed in 0.1s", (2, 1, 0)),
    ("node", NODE_TAP_FAIL, (2, 1, 0)),
    ("node", NODE_SPEC, (3, 1, 0)),
    ("node", JEST, (4, 1, 0)),
    ("node", JEST_ALL_GREEN, (5, 0, 0)),
    ("node", VITEST, (6, 2, 0)),
    ("node", MOCHA, (6, 2, 0)),
    ("node", "something unrelated", (0, 0, 0)),
    ("go", GO_FAIL, (3, 1, 0)),
    ("go", GO_OK, (1, 0, 0)),
    ("go", GO_BUILD, (0, 0, 1)),
    ("go", "", (0, 0, 0)),
])
def test_parsing_each_frameworks_output(lang, output, expected):
    assert parse_results(lang, output) == expected


# --------------------------------------------------------------------------- #
# The loop, through a scripted runner
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_runner(monkeypatch, tmp_path):
    runner = MockRunner(token=TOKEN, execute=False).start()
    monkeypatch.setenv("SANDBOX_BACKEND", "remote")
    monkeypatch.setenv("SANDBOX_URL", runner.url)
    monkeypatch.setenv("SANDBOX_TOKEN", TOKEN)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wk"))
    monkeypatch.setattr(sandbox, "_runner_awake_until", 0.0)
    monkeypatch.setattr(sandbox, "_POLL_EVERY", 0.01)
    get_settings.cache_clear()
    yield runner
    runner.stop()


def fake_model(monkeypatch, replies, candidates):
    prompts: list[dict] = []
    calls = {"n": 0}

    def generate(prompt, system=None, **kw):
        prompts.append({"prompt": prompt, "system": system})
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return reply

    monkeypatch.setattr(repair, "generate", generate)
    monkeypatch.setattr(repair, "_candidate_files", lambda *a, **k: candidates)
    return prompts


JS_CART = {
    "package.json": PKG_WITH_TEST,
    "src/cart.js": ("class Cart {\n  constructor() {\n    this.items = [];\n  }\n"
                    "  total() {\n    return this.items.reduce((sum, i) => sum + i.price, 0);\n  }\n}\n"
                    "module.exports = { Cart };\n"),
    "test/cart.test.js": "// ...\n",
}
JS_PATCH = {
    "root_cause": "Cart.total ignores each item's quantity.",
    "patches": [{"file": "src/cart.js", "function": "Cart.total",
                 "new_source": "total() {\n  return this.items.reduce((sum, i) => sum + i.price * i.qty, 0);\n}"}],
    "explanation": "Multiply by qty.",
}


def test_a_javascript_repair_goes_red_to_green_with_a_whole_method_patch(fake_runner, monkeypatch, tmp_path):
    fake_runner.results = [
        {"returncode": 1, "output": NODE_TAP_FAIL, "timed_out": False},
        {"returncode": 0, "output": "# tests 3\n# pass 3\n# fail 0\n", "timed_out": False},
    ]
    prompts = fake_model(monkeypatch, [json.dumps(JS_PATCH)], ["src/cart.js", "test/cart.test.js"])
    events: list[dict] = []
    result = run_repair("js", make(tmp_path, JS_CART), "cart total ignores quantity",
                        test_path="test/cart.test.js", on_progress=events.append)

    assert result.success and result.lang == "node" and result.files_changed == ["src/cart.js"]
    assert "2 passed, 1 failed" in result.tests_before and "3 passed, 0 failed" in result.tests_after
    assert "i.price * i.qty" in result.diff and "class Cart" not in result.diff.replace(" class Cart", "")
    # the language and the v5 prompt (JS/TS/Go function syntax) were used
    assert "LANGUAGE: JavaScript/TypeScript" in prompts[0]["prompt"]
    assert "Cart.total" in prompts[0]["system"] and "NAMING" in prompts[0]["system"]
    # the runner was told: node, the test target, install on
    first, second = fake_runner.uploads()
    assert first["query"]["lang"] == "node" and first["query"]["test_path"] == "test/cart.test.js"
    assert first["query"]["install"] == "1" and second["query"]["lang"] == "node"


def test_python_repos_keep_the_validated_v4_prompt(fake_runner, monkeypatch, tmp_path):
    fake_runner.execute = True
    prompts = fake_model(monkeypatch, [json.dumps({"root_cause": "r", "patches": [
        {"file": "a.py", "function": "f", "new_source": "def f():\n    return 2"}], "explanation": "e"})],
        ["a.py", "tests/test_a.py"])
    ws = make(tmp_path, {"a.py": "def f():\n    return 1\n",
                         "tests/test_a.py": "from a import f\n\n\ndef test_f():\n    assert f() == 2\n"})
    result = run_repair("py", ws, "f returns the wrong value", test_path="tests/test_a.py")
    assert result.success and result.lang == "python"
    assert "LANGUAGE:" not in prompts[0]["prompt"]
    assert "replacing whole functions" in prompts[0]["system"].lower() and "NAMING" not in prompts[0]["system"]


def test_an_ambiguous_method_name_is_sent_back_to_the_model_to_qualify(fake_runner, monkeypatch, tmp_path):
    files = dict(JS_CART)
    files["src/cart.js"] += "class Wishlist {\n  total() {\n    return 0;\n  }\n}\n"
    fake_runner.results = [
        {"returncode": 1, "output": NODE_TAP_FAIL, "timed_out": False},
        {"returncode": 0, "output": "# pass 3\n# fail 0\n", "timed_out": False},
    ]
    ambiguous = {"root_cause": "r", "explanation": "e", "patches": [
        {"file": "src/cart.js", "function": "total", "new_source": "total() {\n  return 1;\n}"}]}
    prompts = fake_model(monkeypatch, [json.dumps(ambiguous), json.dumps(JS_PATCH)], ["src/cart.js"])
    result = run_repair("js", make(tmp_path, files), "cart total ignores quantity", test_path="test/cart.test.js")
    assert result.success and len(prompts) == 2
    assert "ambiguous" in prompts[1]["prompt"] and "Cart.total" in prompts[1]["prompt"]
    assert result.iterations[0].test_summary.startswith("patch invalid")


def test_a_patch_that_does_not_parse_is_rejected_before_any_test_run(fake_runner, monkeypatch, tmp_path):
    fake_runner.results = [{"returncode": 1, "output": NODE_TAP_FAIL, "timed_out": False}]
    broken = {"root_cause": "r", "explanation": "e", "patches": [
        {"file": "src/cart.js", "function": "Cart.total", "new_source": "total() {\n  return (;\n}"}]}
    fake_model(monkeypatch, [json.dumps(broken)], ["src/cart.js"])
    result = run_repair("js", make(tmp_path, JS_CART), "bug", test_path="test/cart.test.js")
    assert not result.success and not result.patch_applied
    assert len(fake_runner.uploads()) == 1   # only the baseline ever ran
    assert "invalid javascript" in result.iterations[0].test_summary
    assert "repeated" in result.iterations[-1].test_summary   # the same broken patch again: stop, don't loop


def test_a_go_repair_end_to_end_on_the_backend_side(fake_runner, monkeypatch, tmp_path):
    files = {"go.mod": "module example.com/t\n\ngo 1.22\n",
             "stack/stack.go": ("package stack\n\ntype Stack struct{ items []int }\n\n"
                                "func (s *Stack) Pop() (int, bool) {\n\tv := s.items[len(s.items)-1]\n"
                                "\ts.items = s.items[:len(s.items)-1]\n\treturn v, true\n}\n"),
             "stack/stack_test.go": "package stack\n"}
    fake_runner.results = [
        {"returncode": 1, "output": GO_FAIL, "timed_out": False},
        {"returncode": 0, "output": "--- PASS: TestPop (0.00s)\nPASS\nok  \texample.com/t/stack\t0.002s\n",
         "timed_out": False},
    ]
    patch = {"root_cause": "Pop panics on an empty stack.", "explanation": "Guard it.", "patches": [
        {"file": "stack/stack.go", "function": "Stack.Pop",
         "new_source": "func (s *Stack) Pop() (int, bool) {\n\tif len(s.items) == 0 {\n\t\treturn 0, false\n\t}\n"
                       "\tv := s.items[len(s.items)-1]\n\ts.items = s.items[:len(s.items)-1]\n\treturn v, true\n}"}]}
    prompts = fake_model(monkeypatch, [json.dumps(patch)], ["stack/stack.go"])
    result = run_repair("go", make(tmp_path, files), "Pop panics when empty", test_path="./stack")
    assert result.success and result.lang == "go"
    assert "LANGUAGE: Go" in prompts[0]["prompt"]
    assert "len(s.items) == 0" in result.diff
    assert fake_runner.uploads()[0]["query"]["lang"] == "go"
    assert fake_runner.uploads()[0]["query"]["test_path"] == "./stack"


def test_a_failed_dependency_install_stops_the_repair_before_any_model_call(fake_runner, monkeypatch, tmp_path):
    fake_runner.results = [{"returncode": 1, "output": "Error: Cannot find module 'left-pad'", "timed_out": False}]
    fake_runner.install = {"ran": True, "ok": False, "summary": "dependency install failed: npm reported an error",
                           "failed": ["left-pad"], "log": "npm ERR! 404 Not Found - GET left-padd"}
    prompts = fake_model(monkeypatch, [json.dumps(JS_PATCH)], ["src/cart.js"])
    result = run_repair("js", make(tmp_path, JS_CART), "bug", test_path="test/cart.test.js")

    assert not result.success and result.note == "dependencies_unavailable" and not prompts
    md = repair_markdown(result)
    assert "dependencies couldn't be installed" in md and "left-pad" in md and "404" in md


def test_a_successful_install_is_reported_but_does_not_change_the_flow(fake_runner, monkeypatch, tmp_path):
    fake_runner.results = [
        {"returncode": 1, "output": NODE_TAP_FAIL, "timed_out": False},
        {"returncode": 0, "output": "# pass 3\n# fail 0\n", "timed_out": False},
    ]
    fake_runner.install = {"ran": True, "ok": True, "cached": True, "summary": "5 npm package(s) (cached)"}
    fake_model(monkeypatch, [json.dumps(JS_PATCH)], ["src/cart.js"])
    result = run_repair("js", make(tmp_path, JS_CART), "bug", test_path="test/cart.test.js")
    assert result.success and result.install_note == "5 npm package(s) (cached)"


def test_run_tests_reports_the_language_and_install_state(fake_runner, tmp_path):
    fake_runner.results = [{"returncode": 0, "output": "# pass 2\n# fail 0\n", "timed_out": False}]
    fake_runner.install = {"ran": True, "ok": True, "summary": "1 npm package(s) installed (scripts disabled)"}
    report = run_tests_in_workspace(make(tmp_path, JS_CART))
    assert report.ok and report.lang == "node" and report.passed == 2
    assert "scripts disabled" in report.install_note and not report.install_failed


def test_an_unparseable_failure_still_counts_as_an_error(fake_runner, tmp_path):
    fake_runner.results = [{"returncode": 2, "output": "sh: 1: mocha: not found", "timed_out": False}]
    report = run_tests_in_workspace(make(tmp_path, JS_CART))
    assert not report.ok and report.errors == 1 and "1 errors" in report.summary()


def test_the_local_backend_is_untouched_by_the_language_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_BACKEND", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    ws = make(tmp_path, {"tests/test_a.py": "def test_a():\n    assert True\n"})
    report = run_tests_in_workspace(ws)
    assert report.ok and report.lang == "python" and report.passed == 1
    assert os.path.isdir(ws)


# --------------------------------------------------------------------------- #
# The bundled samples and their benchmark
# --------------------------------------------------------------------------- #
EXAMPLES = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "examples"))
BENCH = os.path.join(os.path.dirname(__file__), "..", "eval", "benchmark_multilang.json")


@pytest.mark.parametrize("name,lang", [
    ("js_cart", "node"), ("js_text", "node"), ("go_stack", "go"), ("go_wallet", "go"), ("py_deps", "python"),
])
def test_bundled_samples_are_detected_as_their_language(name, lang):
    assert detect_language(os.path.join(EXAMPLES, name)) == lang


def test_the_multilang_benchmark_points_at_real_files():
    bench = json.load(open(BENCH, encoding="utf-8"))
    assert len(bench["cases"]) == 5
    for case in bench["cases"]:
        root = os.path.join(os.path.dirname(__file__), "..", "..", bench["repos"][case["repo"]])
        assert os.path.isdir(root), case["id"]
        for f in case["expected_files"]:
            assert os.path.isfile(os.path.join(root, f)), (case["id"], f)
        target = case["test_path"].removeprefix("./")
        assert os.path.exists(os.path.join(root, target)), (case["id"], target)


INTENDED_FIXES = [
    ("js_cart", "src/cart.js", "Cart.total",
     "total() {\n  return this.items.reduce((sum, item) => sum + item.price * item.qty, 0);\n}", "item.price * item.qty"),
    ("js_text", "src/text.js", "slugify",
     'export const slugify = (text) => {\n  return text.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");\n};',
     "replace(/^-+|-+$/g"),
    ("go_stack", "stack/stack.go", "Stack.Pop",
     "func (s *Stack) Pop() (int, bool) {\n\tif len(s.items) == 0 {\n\t\treturn 0, false\n\t}\n"
     "\tv := s.items[len(s.items)-1]\n\ts.items = s.items[:len(s.items)-1]\n\treturn v, true\n}", "len(s.items) == 0"),
    ("go_wallet", "wallet/wallet.go", "Wallet.Withdraw",
     "func (w *Wallet) Withdraw(amount int) error {\n\tif amount <= 0 {\n\t\treturn ErrInvalidAmount\n\t}\n"
     "\tif amount > w.balance {\n\t\treturn ErrInsufficientFunds\n\t}\n\tw.balance -= amount\n\treturn nil\n}", "ErrInsufficientFunds\n\t}"),
    ("py_deps", "settings.py", "get",
     'def get(cfg, path, default=None):\n    node = cfg\n    for part in path.split("."):\n'
     '        if not isinstance(node, dict) or part not in node:\n            return default\n'
     '        node = node[part]\n    return node', "isinstance(node, dict)"),
]


@pytest.mark.parametrize("name,file,function,source,expect", INTENDED_FIXES, ids=[c[0] for c in INTENDED_FIXES])
def test_the_intended_fix_for_each_sample_applies_cleanly(tmp_path, name, file, function, source, expect):
    import shutil
    from app.patch.patcher import FilePatch, Workspace, apply_patch

    dst = tmp_path / name
    shutil.copytree(os.path.join(EXAMPLES, name), dst)
    res = apply_patch(Workspace(name, str(dst)), [FilePatch(file=file, changes=[], function=function, new_source=source)])
    assert res.applied, res.error
    assert expect in (dst / file).read_text(encoding="utf-8")
