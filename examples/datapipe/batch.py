def fetch_prices(ids, db):
    # BUG: N+1 — one db.get per id instead of a single batch call.
    return {i: db.get(i) for i in ids}
