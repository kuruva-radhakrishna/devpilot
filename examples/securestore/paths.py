import os


def safe_read(base, name):
    # BUG: path traversal; name like '../secret' escapes base.
    path = os.path.join(base, name)
    return os.path.normpath(path)
