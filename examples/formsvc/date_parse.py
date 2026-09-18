def parse(s):
    # BUG: only handles YYYY-MM-DD; must also handle DD/MM/YYYY.
    y, m, d = s.split("-")
    return (int(y), int(m), int(d))
