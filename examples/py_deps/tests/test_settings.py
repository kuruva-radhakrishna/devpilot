import settings

CONFIG = """
db:
  host: localhost
  port: 5432
name: demo
"""


def test_load_parses_yaml():
    assert settings.load(CONFIG)["db"]["port"] == 5432


def test_empty_document_is_an_empty_dict():
    assert settings.load("") == {}


def test_get_reads_nested_values():
    cfg = settings.load(CONFIG)
    assert settings.get(cfg, "db.host") == "localhost"
    assert settings.get(cfg, "name") == "demo"


def test_get_returns_the_default_for_missing_keys():
    cfg = settings.load(CONFIG)
    assert settings.get(cfg, "db.user", "admin") == "admin"
    assert settings.get(cfg, "cache.ttl", 60) == 60


def test_get_returns_the_default_when_a_parent_is_not_a_mapping():
    cfg = settings.load(CONFIG)
    assert settings.get(cfg, "db.port.number", 0) == 0
    assert settings.get(cfg, "name.first", "n/a") == "n/a"
