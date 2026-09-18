from userdb import find_user_query


def test_parameterized():
    sql, params = find_user_query("alice")
    assert params == ("alice",)
    assert "alice" not in sql
