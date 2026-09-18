def validate(pw):
    # BUG: only checks length; must also require a digit and an uppercase letter.
    if len(pw) < 8:
        raise ValueError("too short")
    return True
