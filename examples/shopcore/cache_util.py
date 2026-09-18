_CACHE = {}


def get_or_compute(key, fn):
    # BUG: named like a cache but recomputes every time.
    return fn()
