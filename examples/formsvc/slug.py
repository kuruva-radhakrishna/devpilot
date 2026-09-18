import re


def slugify(s):
    # BUG: collapses spaces to '-' but leaves doubled dashes.
    return re.sub(r"\s", "-", s.strip().lower())
