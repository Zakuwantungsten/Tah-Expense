from tahmeed.models.user import User
from tahmeed.ui.admin.users_tab import _editable_roles


def test_admin_role_is_preserved_when_editing_admin() -> None:
    admin = User(
        username="admin",
        password_hash="",
        role="admin",
        full_name="Administrator",
    )

    assert "admin" in _editable_roles(admin)
    assert "admin" not in _editable_roles(None)
