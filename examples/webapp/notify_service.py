from templates import render_greeting


def greet(name):
    return render_greeting({"name": name})
