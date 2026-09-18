def find_user_query(username):
    # BUG: SQL injection via string interpolation.
    sql = "SELECT * FROM users WHERE name = '%s'" % username
    return (sql, ())
