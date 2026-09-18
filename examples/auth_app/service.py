"""Authorization service — depends on token_util for validation."""
from token_util import is_valid


def authorize(token, now=None):
    """Return 200 if the token is valid, else 401."""
    if is_valid(token, now):
        return 200
    return 401
