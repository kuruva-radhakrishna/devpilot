def total_size(paths, fs):
    # BUG: re-reads duplicate paths instead of reading each unique path once.
    return sum(fs.read(p) for p in paths)
