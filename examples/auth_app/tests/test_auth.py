from service import authorize
from token_util import make_token


def test_valid_token_authorized():
    token = make_token("u1", ttl=100, now=0)
    assert authorize(token, now=10) == 200


def test_expired_token_unauthorized():
    # A token created at now=0 with ttl=100 is expired at now=200 -> 401.
    # Currently FAILS: is_valid ignores expiry, so authorize returns 200.
    token = make_token("u1", ttl=100, now=0)
    assert authorize(token, now=200) == 401
