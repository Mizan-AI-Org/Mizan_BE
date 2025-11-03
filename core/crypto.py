import base64
import json
import hashlib
from typing import Any, Dict

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    """Create a Fernet instance derived from Django SECRET_KEY.
    Uses SHA256 to derive a 32-byte key and urlsafe base64 encodes it.
    """
    secret = settings.SECRET_KEY.encode('utf-8')
    digest = hashlib.sha256(secret).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_json(payload: Dict[str, Any]) -> str:
    """Encrypt a JSON dict to a base64 token."""
    data = json.dumps(payload, separators=(",", ":")).encode('utf-8')
    return _fernet().encrypt(data).decode('utf-8')


def decrypt_json(token: str) -> Dict[str, Any]:
    """Decrypt a base64 token to a JSON dict."""
    data = _fernet().decrypt(token.encode('utf-8'))
    return json.loads(data.decode('utf-8'))