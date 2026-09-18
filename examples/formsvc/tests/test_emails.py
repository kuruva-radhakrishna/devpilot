from emails import normalize_email


def test_normalize():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
