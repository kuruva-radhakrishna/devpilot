from summary import preview


def test_preview_len():
    assert preview("abcdefgh") == "abcde"
