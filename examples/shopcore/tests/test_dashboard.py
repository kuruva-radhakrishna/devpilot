from dashboard import span_days


def test_span():
    assert span_days(10, 15) == 5
