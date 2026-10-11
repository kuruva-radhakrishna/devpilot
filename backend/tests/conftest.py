import os
import sys

import pytest

# Make `import app...` work when pytest is run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Settings are lru_cached; tests change env vars, so reset around each one."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
