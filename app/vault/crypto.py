import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config.settings import settings


class SecretDecryptionError(Exception):
    """Raised when secrets decryption fails (e.g. invalid key, tag validation fail, or corrupted payload)."""
    pass


class EncryptionService:
    """Service to handle AES-GCM encryption and decryption of secret payloads using Vault Master Key."""

    def _get_key(self) -> bytes:
        """Derives a deterministic 256-bit AES key from the master key settings."""
        master_key = settings.vault_master_key or ""
        # Generate 32 bytes using SHA-256
        return hashlib.sha256(master_key.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> str:
        """Encrypts the plaintext using AES-GCM with a random 12-byte IV and returns Base64 string."""
        if not plaintext:
            raise ValueError("Encryption error: Plaintext value cannot be empty")
        
        key = self._get_key()
        aesgcm = AESGCM(key)
        
        # Generate 12-byte IV
        iv = os.urandom(12)
        plaintext_bytes = plaintext.encode("utf-8")
        
        # Encrypt (the auth tag is automatically appended to ciphertext by cryptography package)
        ciphertext = aesgcm.encrypt(iv, plaintext_bytes, None)
        
        # Base64 encode the concatenated IV + ciphertext
        payload = iv + ciphertext
        return base64.b64encode(payload).decode("utf-8")

    def decrypt(self, encrypted_b64: str) -> str:
        """Decodes and decrypts the base64 AES-GCM payload. Raises SecretDecryptionError on failure."""
        if not encrypted_b64:
            raise ValueError("Decryption error: Encrypted payload cannot be empty")
            
        try:
            payload = base64.b64decode(encrypted_b64.encode("utf-8"))
            if len(payload) < 28: # 12 bytes IV + at least 16 bytes tag/ciphertext
                raise SecretDecryptionError("Invalid payload length: payload is too short")
                
            iv = payload[:12]
            ciphertext = payload[12:]
            
            key = self._get_key()
            aesgcm = AESGCM(key)
            
            decrypted_bytes = aesgcm.decrypt(iv, ciphertext, None)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            if isinstance(e, SecretDecryptionError):
                raise e
            raise SecretDecryptionError(f"Secret decryption failed: {str(e)}") from e
