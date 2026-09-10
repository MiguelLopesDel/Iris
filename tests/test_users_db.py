from pathlib import Path

from core.auth import hash_password, verify_password
from core.users_db import create_user, get_user_by_id, get_user_by_username, has_users


def test_user_has_private_library_paths_and_password_hash(tmp_path: Path):
    users_db = tmp_path / "users.db"
    user = create_user(
        users_db, tmp_path, username="maria.silva", password_hash=hash_password("senha segura 123"),
        display_name="Maria Silva", is_admin=True,
    )

    assert has_users(users_db)
    assert user.db_path == tmp_path / "users" / str(user.id) / "iris.db"
    assert user.media_root == tmp_path / "users" / str(user.id) / "media"
    assert user.media_root.is_dir()
    assert user.is_admin
    assert verify_password("senha segura 123", user.password_hash)
    assert not verify_password("outra senha", user.password_hash)
    assert get_user_by_id(users_db, user.id) == user
    assert get_user_by_username(users_db, "MARIA.SILVA") == user
