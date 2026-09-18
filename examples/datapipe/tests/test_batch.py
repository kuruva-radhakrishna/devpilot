from batch import fetch_prices


class FakeDB:
    def __init__(self):
        self.get_calls = 0
        self.many_calls = 0
        self.data = {1: 10, 2: 20, 3: 30}
    def get(self, i):
        self.get_calls += 1
        return self.data[i]
    def get_many(self, ids):
        self.many_calls += 1
        return {i: self.data[i] for i in ids}


def test_batched():
    db = FakeDB()
    result = fetch_prices([1, 2, 3], db)
    assert result == {1: 10, 2: 20, 3: 30}
    assert db.get_calls == 0
    assert db.many_calls == 1
