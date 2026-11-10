"""Encrypted, per-user OAuth token store (E4: tokens encrypted at rest, never logged).

Tokens are Fernet-encrypted with TOKEN_ENCRYPTION_KEY before they are written. Every row is keyed by
(user_id, provider), so one user can never read another user's tokens. `UserTokenStorage` adapts the
store to the MCP SDK's `TokenStorage` protocol, which `OAuthClientProvider` uses during the Upwork
OAuth 2.1 flow.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "processed" / "tokens.sqlite3"


class EncryptedTokenStore:
    def __init__(self, path: str | Path | None = None, key: str | None = None):
        key = key or os.getenv("TOKEN_ENCRYPTION_KEY")
        if not key:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set (generate one with "
                               "`python -c 'from cryptography.fernet import Fernet; "
                               "print(Fernet.generate_key().decode())'`)")
        self._fernet = Fernet(key.encode())
        self._path = Path(path or os.getenv("TOKEN_STORE_PATH", _DEFAULT_PATH))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as con:
            con.execute("create table if not exists oauth_secrets(user_id text, provider text, kind text,"
                        " ciphertext blob, primary key(user_id, provider, kind))")

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def put(self, user_id: str, provider: str, kind: str, plaintext: str) -> None:
        blob = self._fernet.encrypt(plaintext.encode())
        with self._lock, self._con() as con:
            con.execute("insert or replace into oauth_secrets values(?,?,?,?)", (user_id, provider, kind, blob))

    def get(self, user_id: str, provider: str, kind: str) -> str | None:
        with self._con() as con:
            r = con.execute("select ciphertext from oauth_secrets where user_id=? and provider=? and kind=?",
                            (user_id, provider, kind)).fetchone()
        return self._fernet.decrypt(r[0]).decode() if r else None

    def delete(self, user_id: str, provider: str) -> None:
        with self._lock, self._con() as con:
            con.execute("delete from oauth_secrets where user_id=? and provider=?", (user_id, provider))

    def is_connected(self, user_id: str, provider: str) -> bool:
        return self.get(user_id, provider, "tokens") is not None


class UserTokenStorage:
    """MCP SDK TokenStorage for one (user, provider)."""

    def __init__(self, store: EncryptedTokenStore, user_id: str, provider: str = "upwork"):
        self.store, self.user_id, self.provider = store, user_id, provider

    async def get_tokens(self) -> OAuthToken | None:
        raw = self.store.get(self.user_id, self.provider, "tokens")
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.store.put(self.user_id, self.provider, "tokens", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self.store.get(self.user_id, self.provider, "client_info")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.store.put(self.user_id, self.provider, "client_info", client_info.model_dump_json())
