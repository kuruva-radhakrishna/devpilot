from cart_service import cart_value


def test_cart_value_dollars():
    assert cart_value(["book", "pen"]) == 13.5
