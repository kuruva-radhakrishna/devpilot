"""Token creation and validation for the sample auth service.

Deliberate bug for the DevPilot benchmark: is_valid() checks only that the token
has a user_id — it never checks expiry, so expired tokens are treated as valid.
The fix lives here, but the failing behavior surfaces through service.authorize.
"""
import time


def make_token(user_id, ttl=3600, now=None):
    now = now if now is not None else time.time()
    return {"user_id": user_id, "exp": now + ttl}


def is_valid(token, now=None):
    now = now if now is not None else time.time()
    # BUG: expiry is never checked — should also require now < token["exp"].
    return "user_id" in token
