import os
import tempfile
import pytest
from app.storage.sqlite import SQLiteRepository
from app.auth.models import UserRole
from app.auth.api_keys import APIKeyManager


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


def test_api_key_manager_generation_and_hashing(temp_db_file):
    """Verify raw token creation, prefix extraction, SHA-256 hashing, and validation."""
    repo = SQLiteRepository(temp_db_file)
    manager = APIKeyManager(repo)

    # 1. Generate key
    raw_token, record = manager.create_api_key("Operator Key", UserRole.OPERATOR)
    assert raw_token.startswith("rm_key_")
    assert record.name == "Operator Key"
    assert record.role == UserRole.OPERATOR
    assert record.enabled is True
    assert record.key_prefix == raw_token[:11]

    # 2. Check hashing and retrieval
    computed_hash = manager.compute_hash(raw_token)
    assert record.key_hash == computed_hash

    # Validate valid token
    validated = manager.validate_api_key(raw_token)
    assert validated is not None
    assert validated.key_id == record.key_id

    # Validate invalid token
    assert manager.validate_api_key("rm_key_invalid_token") is None


def test_api_key_revocation_and_listing(temp_db_file):
    """Test listing metadata, disabling, and checking validation fails after revocation."""
    repo = SQLiteRepository(temp_db_file)
    manager = APIKeyManager(repo)

    raw_token_1, record_1 = manager.create_api_key("Key 1", UserRole.VIEWER)
    raw_token_2, record_2 = manager.create_api_key("Key 2", UserRole.ADMIN)

    # 1. List keys
    keys = repo.list_api_keys()
    assert len(keys) == 2
    assert keys[0].name == "Key 2"  # ordered DESC by created_at

    # 2. Revoke key 1
    revoked = repo.revoke_api_key(record_1.key_id)
    assert revoked is True

    # Check validation is blocked
    assert manager.validate_api_key(raw_token_1) is None
    # Key 2 remains valid
    assert manager.validate_api_key(raw_token_2) is not None

    # Revoke non-existent
    assert repo.revoke_api_key("non-existent-id") is False
