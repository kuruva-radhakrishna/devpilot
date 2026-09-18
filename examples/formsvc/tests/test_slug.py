from slug import slugify


def test_slug():
    assert slugify("Hello   World") == "hello-world"
