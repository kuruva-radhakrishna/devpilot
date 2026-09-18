from file_scan import total_size


class FakeFS:
    def __init__(self):
        self.reads = 0
        self.sizes = {"a": 1, "b": 2}
    def read(self, p):
        self.reads += 1
        return self.sizes[p]


def test_reads_unique_only():
    fs = FakeFS()
    # 'a' appears twice; a correct impl reads each unique path once.
    total = total_size(["a", "a", "b"], fs)
    assert total == 3
    assert fs.reads == 2
