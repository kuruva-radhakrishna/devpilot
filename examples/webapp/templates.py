GREETING = "Hello, {name}!"


def render_greeting(ctx):
    # BUG: uses the wrong context key.
    return GREETING.format(name=ctx.get("user", ""))
