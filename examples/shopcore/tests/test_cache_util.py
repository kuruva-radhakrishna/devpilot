from cache_util import get_or_compute


def test_caches():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        return 42
    assert get_or_compute("k", fn) == 42
    assert get_or_compute("k", fn) == 42
    assert calls["n"] == 1
