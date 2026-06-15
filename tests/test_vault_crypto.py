import pytest
import base64
from app.vault.crypto import EncryptionService, SecretDecryptionError
from app.config.settings import settings


def test_encryption_decryption_consistency():
    # Set a mock master key
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        service = EncryptionService()
        plaintext = "SuperSecretPassword123"
        
        # Encrypt
        encrypted = service.encrypt(plaintext)
        assert encrypted != plaintext
        
        # Verify it is valid base64
        decoded = base64.b64decode(encrypted.encode("utf-8"))
        assert len(decoded) > 12 # 12 bytes IV + ciphertext/tag
        
        # Decrypt
        decrypted = service.decrypt(encrypted)
        assert decrypted == plaintext
    finally:
        settings.vault_master_key = old_key


def test_decryption_errors_invalid_key():
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        service = EncryptionService()
        plaintext = "AnotherSecret"
        encrypted = service.encrypt(plaintext)
        
        # Change master key and try to decrypt
        settings.vault_master_key = "b" * 32
        with pytest.raises(SecretDecryptionError):
            service.decrypt(encrypted)
    finally:
        settings.vault_master_key = old_key


def test_decryption_errors_corrupted_payload():
    old_key = settings.vault_master_key
    settings.vault_master_key = "a" * 32
    
    try:
        service = EncryptionService()
        plaintext = "TestMessage"
        encrypted = service.encrypt(plaintext)
        
        # Corrupt the payload (decode, alter bytes, encode)
        payload = bytearray(base64.b64decode(encrypted.encode("utf-8")))
        payload[-1] ^= 0xFF # Flip bits in auth tag
        corrupted = base64.b64encode(payload).decode("utf-8")
        
        with pytest.raises(SecretDecryptionError):
            service.decrypt(corrupted)
            
        # Too short payload
        with pytest.raises(SecretDecryptionError):
            service.decrypt(base64.b64encode(b"short").decode("utf-8"))
    finally:
        settings.vault_master_key = old_key
