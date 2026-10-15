"""Encryption helpers for SMTP relay credentials stored in the database."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

PREFIX = "fernet:v1:"


def _fernet(key: str) -> Fernet:
    if not key:
        raise RuntimeError("SMTP_CONFIG_ENCRYPTION_KEY is required to encrypt SMTP credentials")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("SMTP_CONFIG_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt_secret(secret: str, purpose: str = "application secret") -> str:
    """Encrypt a managed application secret with the configured Fernet key."""
    if not secret:
        return ""
    if not settings.SMTP_CONFIG_ENCRYPTION_KEY:
        raise RuntimeError(f"SMTP_CONFIG_ENCRYPTION_KEY is required to encrypt {purpose}")
    token = _fernet(settings.SMTP_CONFIG_ENCRYPTION_KEY).encrypt(secret.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_secret(stored: str | None, purpose: str = "application secret") -> str:
    """Decrypt a managed secret, allowing the configured rotation key."""
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        raise RuntimeError(f"Stored {purpose} is not encrypted")
    try:
        token = stored[len(PREFIX):].encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(f"Stored {purpose} ciphertext is malformed") from exc
    keys = [settings.SMTP_CONFIG_ENCRYPTION_KEY, settings.SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS]
    if not any(keys):
        raise RuntimeError(f"SMTP_CONFIG_ENCRYPTION_KEY is required to access encrypted {purpose}")
    for key in dict.fromkeys(keys):
        if not key:
            continue
        try:
            return _fernet(key).decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"Stored {purpose} is not valid UTF-8") from exc
    raise RuntimeError(f"Stored {purpose} cannot be decrypted; verify the encryption key and its backup")


def encrypt_password(password: str) -> str:
    if not password:
        return ""
    token = _fernet(settings.SMTP_CONFIG_ENCRYPTION_KEY).encrypt(password.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_password(stored: str | None) -> str:
    if not stored:
        return ""
    if not stored.startswith(PREFIX):
        # Existing installations may have plaintext credentials; startup upgrades
        # these when an encryption key is configured.
        return stored
    try:
        token = stored[len(PREFIX):].encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError("Stored SMTP credential ciphertext is malformed") from exc
    keys = [settings.SMTP_CONFIG_ENCRYPTION_KEY, settings.SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS]
    if not any(keys):
        raise RuntimeError("SMTP_CONFIG_ENCRYPTION_KEY is required to access encrypted SMTP credentials")
    for key in dict.fromkeys(keys):
        if not key:
            continue
        try:
            return _fernet(key).decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
        except UnicodeDecodeError as exc:
            raise RuntimeError("Stored SMTP credential is not valid UTF-8") from exc
    raise RuntimeError("Stored SMTP credential cannot be decrypted; verify the encryption key and its backup")


def decrypt_with_current_key(stored: str) -> str | None:
    """Return plaintext only when the current key (not the rotation key) decrypts it."""
    if not settings.SMTP_CONFIG_ENCRYPTION_KEY:
        return None
    try:
        return _fernet(settings.SMTP_CONFIG_ENCRYPTION_KEY).decrypt(stored[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
    except (UnicodeEncodeError, UnicodeDecodeError, RuntimeError) as exc:
        raise RuntimeError("Stored SMTP credential cannot be decrypted; verify the encryption key and its backup") from exc
