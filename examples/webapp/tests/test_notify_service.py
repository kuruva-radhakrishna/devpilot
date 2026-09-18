from notify_service import greet


def test_greet():
    assert greet("Sam") == "Hello, Sam!"
