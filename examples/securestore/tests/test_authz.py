import pytest
from authz import delete_resource


def test_admin_can_delete():
    assert delete_resource({"role": "admin"}, 1) == "deleted 1"


def test_non_admin_denied():
    with pytest.raises(PermissionError):
        delete_resource({"role": "user"}, 1)
