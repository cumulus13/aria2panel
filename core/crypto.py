"""Symmetric encryption for secrets stored in the database (aria2 rpc secrets)."""
import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

log = logging.getLogger("core.crypto")


@lru_cache(maxsize=1)
def _fernet():
    key = os.environ.get("FIELD_ENCRYPTION_KEY")
    if key:
        return Fernet(key.encode())
    # derived from SECRET_KEY, set FIELD_ENCRYPTION_KEY to decouple them
    digest = hashlib.sha256(("aria2panel:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error(
            "cannot decrypt a stored secret: DJANGO_SECRET_KEY / FIELD_ENCRYPTION_KEY changed since it was saved, "
            "re-enter the RPC secret on the Servers page"
        )
        return ""
