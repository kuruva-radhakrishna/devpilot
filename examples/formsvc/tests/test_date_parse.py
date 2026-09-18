from date_parse import parse


def test_iso():
    assert parse("2024-03-09") == (2024, 3, 9)


def test_slash():
    assert parse("09/03/2024") == (2024, 3, 9)
