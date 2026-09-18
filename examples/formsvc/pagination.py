def page_items(items, page, size):
    # BUG: off-by-one; page 1 skips the first page of items.
    start = page * size
    return items[start:start + size]
