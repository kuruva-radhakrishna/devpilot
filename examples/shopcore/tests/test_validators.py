from validators import sanitize_username


def test_sanitize():
    assert sanitize_username("  Alice ") == "alice"
