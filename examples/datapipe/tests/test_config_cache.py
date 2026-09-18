from config_cache import get_setting


def test_loader_called_once():
    calls = {"n": 0}
    def loader(key):
        calls["n"] += 1
        return "value-" + key
    assert get_setting("a", loader) == "value-a"
    assert get_setting("a", loader) == "value-a"
    assert calls["n"] == 1
