# sample_app — DevPilot repair demo fixture

A minimal, dependency-free Python app with **one deliberate bug** and a test
that catches it. Use it to see DevPilot's repair loop go red → patch → green.

- `orders.py` — `get_order_city` dereferences `order["address"]` without checking
  it exists, so an order with no address raises `KeyError`.
- `tests/test_orders.py` — `test_missing_address_defaults_to_unknown` expects the
  app to ship to `"unknown"` instead of crashing. It currently **fails**.

## Run the demo

```bash
cd backend
# 1. Index the sample repo
python cli.py ingest ../examples/sample_app
# 2. Copy the repo_id it prints, then run the repair loop:
python cli.py debug sample_app-XXXXXXXX "Orders without an address crash with a KeyError. They should ship to 'unknown' instead."
```

Expected: DevPilot runs the tests (1 fails), generates a structured patch to
`orders.py`, applies it, re-runs the tests in the sandbox (both pass), and prints
the root cause, the diff, and the validated result.

The intended fix is roughly:

```python
def get_order_city(order):
    address = order.get("address")
    if not address:
        return "unknown"
    return address.get("city", "unknown")
```
