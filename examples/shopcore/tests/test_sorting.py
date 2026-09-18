from sorting import sort_by_price


def test_sort_by_price():
    items = [{"name": "z", "price": 1}, {"name": "a", "price": 9}]
    assert [it["price"] for it in sort_by_price(items)] == [1, 9]
