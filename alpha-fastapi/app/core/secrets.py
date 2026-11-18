"""Encrypting people's Slack sign-ins and signing the sign-in link.

A person's Slack user token can read their private DMs, so it is never stored in clear:
it is encrypted with a key derived from TOKEN_KEY. The sign-in link carries a signed,
expiring state so nobody can forge a callback and attach their token to someone else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

STATE_SECONDS = 15 * 60


class TokenKeyMissing(RuntimeError):
    """TOKEN_KEY is not set, so sign-ins cannot be kept."""


class Secrets:
    def __init__(self, token_key: str) -> None:
        self._key = token_key.encode("utf-8") if token_key else b""

    @property
    def ready(self) -> bool:
        return bool(self._key)

    def _fernet(self) -> Fernet:
        if not self._key:
            raise TokenKeyMissing("TOKEN_KEY is not set")
        digest = hashlib.sha256(b"alpha-token:" + self._key).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, token: str) -> str:
        return self._fernet().encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, token_enc: str) -> str | None:
        try:
            return self._fernet().decrypt(token_enc.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None

    def _sign(self, body: str) -> str:
        if not self._key:
            raise TokenKeyMissing("TOKEN_KEY is not set")
        mac = hmac.new(self._key, b"alpha-state:" + body.encode("utf-8"), hashlib.sha256)
        return mac.hexdigest()[:32]

    def make_state(self, now: float | None = None) -> str:
        body = f"{secrets.token_urlsafe(12)}.{int(now or time.time())}"
        return f"{body}.{self._sign(body)}"

    def check_state(self, state: str, now: float | None = None) -> bool:
        parts = state.split(".")
        if len(parts) != 3:
            return False
        body = f"{parts[0]}.{parts[1]}"
        try:
            issued = int(parts[1])
        except ValueError:
            return False
        if not hmac.compare_digest(self._sign(body), parts[2]):
            return False
        return 0 <= (now or time.time()) - issued <= STATE_SECONDS
