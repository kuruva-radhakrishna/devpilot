"""Pure-logic tests for the runner (no Linux, root or Docker needed)."""
from __future__ import annotations

import io
import os
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import deps, sandbox  # noqa: E402
from app.sandbox import RunnerError, clamp_timeout, safe_extract, validate_test_path  # noqa: E402


def make_tar(entries: list[tuple[str, bytes | None, str]]) -> bytes:
    """entries: (name, content, kind) where kind is file|dir|symlink|hardlink|dev."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(content or b"")
                tf.addfile(info, io.BytesIO(content or b""))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = "other"
                tf.addfile(info)
            elif kind == "dev":
                info.type = tarfile.CHRTYPE
                tf.addfile(info)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
def test_safe_extract_unpacks_plain_files(tmp_path):
    data = make_tar([("pkg", None, "dir"), ("pkg/a.py", b"x = 1\n", "file"),
                     ("tests/test_a.py", b"def test_x(): pass\n", "file")])
    assert safe_extract(data, str(tmp_path)) == 2
    assert (tmp_path / "pkg" / "a.py").read_text() == "x = 1\n"
    assert (tmp_path / "tests" / "test_a.py").exists()


@pytest.mark.parametrize("entry", [
    ("link", None, "symlink"),
    ("link", None, "hardlink"),
    ("null", None, "dev"),
    ("../escape.py", b"x", "file"),
    ("a/../../escape.py", b"x", "file"),
    ("/etc/cron.d/evil", b"x", "file"),
    ("a\\..\\..\\evil.py", b"x", "file"),
])
def test_safe_extract_rejects_dangerous_entries(tmp_path, entry):
    with pytest.raises(RunnerError) as ei:
        safe_extract(make_tar([entry]), str(tmp_path))
    assert ei.value.status == 400
    assert not (tmp_path.parent / "escape.py").exists()


def test_safe_extract_rejects_non_archives(tmp_path):
    for junk in (b"", b"not a tarball", os.urandom(200)):
        with pytest.raises(RunnerError):
            safe_extract(junk, str(tmp_path))


def test_safe_extract_enforces_size_and_count_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_UNPACKED_BYTES", 100)
    with pytest.raises(RunnerError) as ei:
        safe_extract(make_tar([("big.txt", b"x" * 500, "file")]), str(tmp_path))
    assert ei.value.status == 413

    monkeypatch.setattr(sandbox, "MAX_UNPACKED_BYTES", 10**9)
    monkeypatch.setattr(sandbox, "MAX_FILES", 3)
    many = make_tar([(f"f{i}.txt", b"x", "file") for i in range(4)])
    with pytest.raises(RunnerError) as ei:
        safe_extract(many, str(tmp_path / "many"))
    assert ei.value.status == 413

    monkeypatch.setattr(sandbox, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(RunnerError) as ei:
        safe_extract(make_tar([("a.txt", b"hello", "file")]), str(tmp_path / "up"))
    assert ei.value.status == 413


@pytest.mark.parametrize("path", [
    "tests/test_orders.py", "tests", "test_a.py::test_x", "tests/test_a.py::TestK::test_y[param-1]",
    "pkg/sub-dir/test_b.py",
])
def test_valid_python_test_paths(path):
    assert validate_test_path("python", path) == path


@pytest.mark.parametrize("lang", ["python", "node", "go"])
@pytest.mark.parametrize("path", [
    "--rootdir=/", "-p evil", "-x", "/etc/passwd", "../outside", "a/../../b", "tests/../..",
    "a b", "tests;rm -rf /", "$(id)", "a`id`", "x" * 400, "tests/\n--collect-only", "a\\b",
])
def test_invalid_test_paths_are_refused(lang, path):
    with pytest.raises(RunnerError):
        validate_test_path(lang, path)


@pytest.mark.parametrize("lang", ["python", "node", "go"])
def test_empty_test_path_means_run_everything(lang):
    assert validate_test_path(lang, "") == ""
    assert validate_test_path(lang, "   ") == ""


def test_node_test_paths():
    assert validate_test_path("node", "test/cart.test.js") == "test/cart.test.js"
    assert validate_test_path("node", "src/__tests__/a.spec.ts") == "src/__tests__/a.spec.ts"


def test_go_test_paths_are_package_patterns():
    assert validate_test_path("go", "./calc") == "./calc"
    assert validate_test_path("go", "calc") == "./calc"          # bare directory is normalised
    assert validate_test_path("go", "./...") == "./..."
    assert validate_test_path("go", "./pkg/sub/...") == "./pkg/sub/..."
    for bad in (".", "./a;b", "./a b", "-run=x", "./a$(id)"):
        with pytest.raises(RunnerError):
            validate_test_path("go", bad)


def test_language_is_validated():
    assert sandbox.validate_lang("") == "python"
    assert sandbox.validate_lang(" Node ") == "node"
    for bad in ("ruby", "rm -rf", "python; id"):
        with pytest.raises(RunnerError):
            sandbox.validate_lang(bad)


def test_exactly_one_fixed_command_per_language():
    assert sandbox.test_command("node", "")[:3] == ["npm", "test", "--silent"]
    assert sandbox.test_command("node", "t/a.test.js")[-2:] == ["--", "t/a.test.js"]
    assert sandbox.test_command("go", "") == ["go", "test", "-v", "-count=1", "./..."]
    assert sandbox.test_command("go", "./calc")[-1] == "./calc"
    py = sandbox.test_command("python", "tests/test_a.py")
    assert py[1:3] == ["-m", "pytest"] and py[-2:] == ["--", "tests/test_a.py"]


def test_node_and_go_are_limited_by_data_segment_not_address_space():
    # they reserve huge virtual ranges at startup, so RLIMIT_AS would kill them instantly
    assert any(a.startswith("--as=") for a in sandbox.limit_args("python", "test", 30))
    for lang in ("node", "go"):
        args = sandbox.limit_args(lang, "test", 30)
        assert not any(a.startswith("--as=") for a in args)
        assert any(a.startswith("--data=") for a in args)


def test_go_tests_run_offline_with_no_cgo():
    env = sandbox.test_env("go", "/ws", deps.DepsResult())
    assert env["GOPROXY"] == "off" and env["CGO_ENABLED"] == "0" and env["GOTOOLCHAIN"] == "local"
    assert "RUNNER_TOKEN" not in env


# ---- dependency planning ---------------------------------------------------- #
def test_requirements_accept_plain_lines_and_skip_everything_dangerous():
    ok, skipped = deps.parse_requirements("""
# comment
requests>=2.31,<3
pyyaml==6.0.1  # trailing comment
numpy[extras]~=1.26 ; python_version >= "3.9"
--index-url https://evil.example/simple
-r other.txt
-e .
git+https://github.com/x/y.git
pkg @ https://evil.example/pkg.whl
./local/path
https://evil.example/x.tar.gz
bad line with spaces
""")
    assert [deps._req_name(r) for r in ok] == ["requests", "pyyaml", "numpy"]
    assert len(skipped) == 8


def test_plan_python_reads_requirements_files_and_pyproject_and_drops_pytest(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.32.3\npytest==8.0\n-e .\n")
    (tmp_path / "requirements-dev.txt").write_text("freezegun>=1.2\nrequests==2.0\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["click>=8"]\n'
        '[project.optional-dependencies]\ntest=["hypothesis>=6"]\ndocs=["sphinx"]\n')
    reqs, skipped = deps.plan_python(str(tmp_path))
    names = sorted(deps._req_name(r) for r in reqs)
    assert names == ["click", "freezegun", "hypothesis", "requests"]   # no pytest, no docs extra
    assert "requests==2.32.3" in reqs                                   # first occurrence wins
    assert skipped == ["-e ."]


def test_timeout_is_clamped():
    assert clamp_timeout(1) == 5
    assert clamp_timeout(60) == 60
    assert clamp_timeout(10_000) == sandbox.MAX_TIMEOUT
    assert clamp_timeout("30") == 30


def test_slots_are_finite_and_released():
    taken = [sandbox.acquire_slot(wait=0.05) for _ in range(sandbox.SLOTS)]
    with pytest.raises(RunnerError) as ei:
        sandbox.acquire_slot(wait=0.05)
    assert ei.value.status == 503
    for s in taken:
        sandbox.release_slot(s)
    sandbox.release_slot(sandbox.acquire_slot(wait=0.05))


def test_read_tail_truncates_from_the_front(tmp_path):
    f = tmp_path / "o.txt"
    f.write_bytes(b"A" * 1000 + b"END")
    text = sandbox._read_tail(str(f), 50)
    assert text.endswith("END") and "truncated" in text and len(text) < 120


def test_dir_size_stops_early_once_over_the_cap(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}").write_bytes(b"x" * 1000)
    assert sandbox._dir_size(str(tmp_path), 10**9) == 10_000
    assert sandbox._dir_size(str(tmp_path), 2500) > 2500


def test_node_modules_becomes_a_writable_directory_of_symlinks(tmp_path):
    cache = tmp_path / "cache" / "node_modules"
    (cache / "left-pad").mkdir(parents=True)
    (cache / "@scope" / "pkg").mkdir(parents=True)
    (cache / ".bin").mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    try:
        sandbox._link_dependencies(str(cache), str(ws / "node_modules"))
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    link = ws / "node_modules"
    assert link.is_dir() and not link.is_symlink()            # real directory: caches can be written in it
    assert sorted(p.name for p in link.iterdir()) == [".bin", "@scope", "left-pad"]
    assert (link / "left-pad").is_symlink() and (link / "left-pad").resolve() == (cache / "left-pad").resolve()
    (link / ".vite").mkdir()                                  # what vitest does
    sandbox._link_dependencies(str(cache), str(link))         # idempotent
    other = tmp_path / "gomod"
    other.mkdir()
    sandbox._link_dependencies(str(other), str(ws / "vendor_cache"))
    assert (ws / "vendor_cache").is_symlink()                 # anything else is linked whole
