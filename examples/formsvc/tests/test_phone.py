from phone import format_phone


def test_format():
    assert format_phone("1234567890") == "(123) 456-7890"
