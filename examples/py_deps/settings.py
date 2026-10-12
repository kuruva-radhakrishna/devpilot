"""Load a YAML config and read dotted keys from it ("db.port").

This sample has a real third-party dependency (PyYAML, pinned in requirements.txt), so its
tests can only run once the runner has installed it, which is the point of the sample.

Deliberate bug for the DevPilot benchmark: get() raises TypeError when an intermediate
value is a scalar (asking for "db.port.number" when port is 5432) instead of returning
the default.
"""
import yaml


def load(text):
    """Parse YAML text into a dict (an empty document is an empty dict)."""
    return yaml.safe_load(text) or {}


def get(cfg, path, default=None):
    """Read a dotted path such as "db.host" from a nested dict."""
    node = cfg
    for part in path.split("."):
        # BUG: `in` on a non-dict (an int or a string) raises or misbehaves.
        if part not in node:
            return default
        node = node[part]
    return node
