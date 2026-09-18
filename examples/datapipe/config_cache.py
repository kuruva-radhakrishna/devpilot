def get_setting(key, loader):
    # BUG: calls the expensive loader every time; no caching.
    return loader(key)
