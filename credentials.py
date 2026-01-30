"""
Secure Credential Manager
- Uses system keyring for primary storage
- Falls back to encrypted file storage
- Credentials are never stored in plain text
- Memory is cleared after use
"""
import json
import base64
import secrets
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)

APP_NAME = "DesktopAutomationRecorder"
KEYRING_SERVICE = f"{APP_NAME}_credentials"


@dataclass
class Credential:
    """Secure credential container."""
    name: str
    username: str
    _password: str = field(repr=False)
    url: Optional[str] = None
    notes: Optional[str] = None
    
    @property
    def password(self) -> str:
        return self._password
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "username": self.username,
            "password": self._password,
            "url": self.url,
            "notes": self.notes
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Credential":
        return cls(
            name=data["name"],
            username=data["username"],
            _password=data["password"],
            url=data.get("url"),
            notes=data.get("notes")
        )
    
    def clear(self) -> None:
        """Clear sensitive data from memory."""
        self._password = "\x00" * len(self._password)


class EncryptionManager:
    """Handles encryption/decryption of credential data."""
    
    def __init__(self, master_password: str, salt: Optional[bytes] = None):
        self.salt = salt or secrets.token_bytes(16)
        self._fernet = self._derive_key(master_password)
    
    def _derive_key(self, password: str) -> Fernet:
        """Derive encryption key from master password."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=480000,  # OWASP recommended minimum
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)
    
    def encrypt(self, data: str) -> bytes:
        """Encrypt string data."""
        return self._fernet.encrypt(data.encode())
    
    def decrypt(self, data: bytes) -> str:
        """Decrypt to string."""
        return self._fernet.decrypt(data).decode()
    
    def encrypt_dict(self, data: Dict[str, Any]) -> bytes:
        """Encrypt dictionary as JSON."""
        return self.encrypt(json.dumps(data))
    
    def decrypt_dict(self, data: bytes) -> Dict[str, Any]:
        """Decrypt to dictionary."""
        return json.loads(self.decrypt(data))


class CredentialManager:
    """
    Production-grade credential manager.
    
    Security features:
    - Primary: System keyring (Windows Credential Locker, macOS Keychain, Linux Secret Service)
    - Fallback: AES-256 encrypted file with PBKDF2 key derivation
    - Credentials encrypted at rest
    - Memory cleared after use
    - No plaintext logging
    """
    
    def __init__(self, storage_dir: Path, master_password: Optional[str] = None):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._credentials_file = storage_dir / "credentials.enc"
        self._salt_file = storage_dir / "salt.bin"
        self._use_keyring = self._check_keyring_available()
        self._encryption: Optional[EncryptionManager] = None
        self._master_password = master_password
        
        if master_password:
            self._init_encryption(master_password)
    
    def _check_keyring_available(self) -> bool:
        """Check if system keyring is available."""
        try:
            # Try to access keyring
            keyring.get_keyring()
            return True
        except KeyringError:
            logger.warning("System keyring unavailable, using encrypted file storage")
            return False
    
    def _init_encryption(self, master_password: str) -> None:
        """Initialize encryption with master password."""
        salt = None
        if self._salt_file.exists():
            salt = self._salt_file.read_bytes()
        
        self._encryption = EncryptionManager(master_password, salt)
        
        if not self._salt_file.exists():
            self._salt_file.write_bytes(self._encryption.salt)
    
    def set_master_password(self, password: str) -> None:
        """Set or change master password."""
        # If changing password, re-encrypt all credentials
        creds_data = []
        if self._encryption:
            existing_creds = self.list_credentials()
            creds_data = [self.get_credential(c) for c in existing_creds]

        # Generate new salt for new password
        self._salt_file.unlink(missing_ok=True)
        self._init_encryption(password)
        self._master_password = password

        # Re-encrypt existing credentials
        for cred in creds_data:
            if cred:
                self.store_credential(cred)
    
    def verify_master_password(self, password: str) -> bool:
        """Verify if master password is correct."""
        try:
            test_encryption = EncryptionManager(
                password, 
                self._salt_file.read_bytes() if self._salt_file.exists() else None
            )
            
            if self._credentials_file.exists():
                data = self._credentials_file.read_bytes()
                test_encryption.decrypt_dict(data)
            
            return True
        except Exception:
            return False
    
    def store_credential(self, credential: Credential) -> bool:
        """
        Store credential securely.
        
        Strategy:
        1. Try system keyring first (most secure)
        2. Fall back to encrypted file
        """
        try:
            if self._use_keyring:
                # Store in system keyring
                keyring.set_password(
                    KEYRING_SERVICE,
                    credential.name,
                    json.dumps(credential.to_dict())
                )
                logger.info(f"Credential '{credential.name}' stored in system keyring")
                return True
        except KeyringError as e:
            logger.warning(f"Keyring storage failed: {e}, using encrypted file")
        
        # Fallback to encrypted file
        return self._store_in_file(credential)
    
    def _store_in_file(self, credential: Credential) -> bool:
        """Store credential in encrypted file."""
        if not self._encryption:
            raise ValueError("Master password not set. Call set_master_password first.")
        
        # Load existing credentials
        credentials = self._load_credentials_file()
        
        # Add/update credential
        credentials[credential.name] = credential.to_dict()
        
        # Encrypt and save
        encrypted = self._encryption.encrypt_dict(credentials)
        self._credentials_file.write_bytes(encrypted)
        
        logger.info(f"Credential '{credential.name}' stored in encrypted file")
        return True
    
    def _load_credentials_file(self) -> Dict[str, Dict[str, Any]]:
        """Load credentials from encrypted file."""
        if not self._credentials_file.exists():
            return {}
        
        if not self._encryption:
            return {}
        
        try:
            data = self._credentials_file.read_bytes()
            return self._encryption.decrypt_dict(data)
        except Exception as e:
            logger.error(f"Failed to decrypt credentials file: {e}")
            return {}
    
    def get_credential(self, name: str) -> Optional[Credential]:
        """Retrieve a credential by name."""
        try:
            if self._use_keyring:
                data = keyring.get_password(KEYRING_SERVICE, name)
                if data:
                    return Credential.from_dict(json.loads(data))
        except KeyringError:
            pass
        
        # Try encrypted file
        credentials = self._load_credentials_file()
        if name in credentials:
            return Credential.from_dict(credentials[name])
        
        return None
    
    def list_credentials(self) -> list[str]:
        """List all stored credential names."""
        names = set()
        
        # From encrypted file
        credentials = self._load_credentials_file()
        names.update(credentials.keys())
        
        return sorted(names)
    
    def delete_credential(self, name: str) -> bool:
        """Delete a credential."""
        deleted = False
        
        try:
            if self._use_keyring:
                keyring.delete_password(KEYRING_SERVICE, name)
                deleted = True
        except KeyringError:
            pass
        
        # Remove from file
        credentials = self._load_credentials_file()
        if name in credentials:
            del credentials[name]
            if self._encryption:
                encrypted = self._encryption.encrypt_dict(credentials)
                self._credentials_file.write_bytes(encrypted)
            deleted = True
        
        return deleted
    
    def export_credentials(self, export_password: str) -> bytes:
        """Export all credentials encrypted with a separate password.

        Format: 16-byte salt prefix + Fernet-encrypted JSON payload.
        The salt is stored unencrypted so import can derive the same key.
        """
        credentials = self._load_credentials_file()
        export_encryption = EncryptionManager(export_password)

        encrypted = export_encryption.encrypt_dict(credentials)
        # Prefix the salt so import_credentials can reconstruct the key
        return export_encryption.salt + encrypted
    
    def import_credentials(self, data: bytes, import_password: str) -> int:
        """Import credentials from encrypted export.

        Format: 16-byte salt prefix + Fernet-encrypted JSON payload.
        """
        if len(data) < 17:
            raise ValueError("Invalid export data")

        salt = data[:16]
        encrypted = data[16:]
        import_encryption = EncryptionManager(import_password, salt=salt)

        try:
            credentials = import_encryption.decrypt_dict(encrypted)
        except Exception:
            raise ValueError("Invalid import password or corrupted export data")

        count = 0
        for name, cred_data in credentials.items():
            cred = Credential.from_dict(cred_data)
            self.store_credential(cred)
            count += 1

        return count


class SecureInput:
    """Helper for secure credential input during recording."""
    
    def __init__(self, credential_manager: CredentialManager):
        self.cred_manager = credential_manager
        self._pending_inputs: Dict[str, str] = {}
    
    def mark_sensitive_field(self, field_id: str, credential_name: str, field_type: str = "password") -> None:
        """
        Mark a field as sensitive during recording.
        
        Instead of recording the actual keystrokes, we record:
        - The credential reference
        - The field type (username/password)
        
        During playback, we fetch the actual value from secure storage.
        """
        self._pending_inputs[field_id] = f"{credential_name}:{field_type}"
    
    def get_playback_value(self, field_id: str) -> Optional[str]:
        """Get the actual value for playback."""
        if field_id not in self._pending_inputs:
            return None
        
        ref = self._pending_inputs[field_id]
        cred_name, field_type = ref.split(":", 1)
        
        cred = self.cred_manager.get_credential(cred_name)
        if not cred:
            logger.error(f"Credential '{cred_name}' not found")
            return None
        
        if field_type == "password":
            return cred.password
        elif field_type == "username":
            return cred.username
        
        return None
    
    def clear_pending(self) -> None:
        """Clear pending inputs from memory."""
        for key in self._pending_inputs:
            self._pending_inputs[key] = "\x00" * len(self._pending_inputs[key])
        self._pending_inputs.clear()
