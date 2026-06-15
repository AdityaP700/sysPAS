import os
import tempfile
import pytest
from app.config.settings import settings
from app.storage.sqlite import SQLiteRepository
from app.auth.models import UserRole
from app.web.main import bootstrap_security


@pytest.fixture
def temp_db_file():
    """Create a temporary database file and clean it up after the test completes."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_bootstrap_security_missing_key_fails(temp_db_file, monkeypatch):
    """Verify that startup fails with RuntimeError if auth is active, no admins exist, and default key is empty."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "default_admin_api_key", None)
    monkeypatch.setattr(settings, "sqlite_db_path", temp_db_file)

    # Re-initialize repository for dependency injection lookup
    from app.web import dependencies
    dependencies._repo_instance = SQLiteRepository(temp_db_file)

    with pytest.raises(RuntimeError) as exc_info:
        bootstrap_security()
    
    assert "default_admin_api_key" in str(exc_info.value)


def test_bootstrap_security_saves_default_key(temp_db_file, monkeypatch):
    """Verify that the default_admin_api_key is hashed and inserted into an empty DB on startup."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "default_admin_api_key", "rm_key_test_bootstrap_admin_secret")
    monkeypatch.setattr(settings, "sqlite_db_path", temp_db_file)

    from app.web import dependencies
    repo = SQLiteRepository(temp_db_file)
    dependencies._repo_instance = repo

    # Run startup hook
    bootstrap_security()

    # Verify admin key was inserted
    keys = repo.list_api_keys()
    assert len(keys) == 1
    admin_key = keys[0]
    assert admin_key.key_id == "bootstrap_admin"
    assert admin_key.role == UserRole.ADMIN
    assert admin_key.name == "Default Bootstrap Administrator"

    # Verify plaintext key is NOT in logs, but matches computed hash
    import hashlib
    expected_hash = hashlib.sha256(b"rm_key_test_bootstrap_admin_secret").hexdigest()
    assert admin_key.key_hash == expected_hash
