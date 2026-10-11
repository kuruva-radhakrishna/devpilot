"""Pure-logic tests for the runner (no Linux, root or Docker needed)."""
from __future__ import annotations

import io
import os
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import sandbox  # noqa: E402
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
def test_valid_test_paths(path):
    assert validate_test_path(path) == path


@pytest.mark.parametrize("path", [
    "--rootdir=/", "-p evil", "-x", "/etc/passwd", "../outside", "a/../../b", "tests/../..",
    "a b", "tests;rm -rf /", "$(id)", "a`id`", "x" * 400, "tests/\n--collect-only",
])
def test_invalid_test_paths_are_refused(path):
    with pytest.raises(RunnerError):
        validate_test_path(path)


def test_empty_test_path_means_run_everything():
    assert validate_test_path("") == ""
    assert validate_test_path("   ") == ""


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
