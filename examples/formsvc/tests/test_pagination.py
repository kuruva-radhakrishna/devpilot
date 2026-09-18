from pagination import page_items


def test_first_page():
    assert page_items([1, 2, 3, 4, 5], 1, 2) == [1, 2]


def test_second_page():
    assert page_items([1, 2, 3, 4, 5], 2, 2) == [3, 4]
