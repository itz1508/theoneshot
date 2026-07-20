"""Simple file-backed authentication service with PBKDF2 + random tokens."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Mapping

from audisor.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse


class AuthError(RuntimeError):
    pass


class AuthService:
    """Minimal file-backed user store. Not for production scale."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is None:
            data_dir = Path(os.environ.get("AUDISOR_DATA_DIR", Path.home() / ".audisor"))
        self._root = Path(data_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._users_path = self._root / "users.json"
        self._tokens_path = self._root / "tokens.json"
        self._users: dict[str, str] = {}
        self._tokens: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._users_path.exists():
            try:
                self._users = json.loads(self._users_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                self._users = {}
        if self._tokens_path.exists():
            try:
                self._tokens = json.loads(self._tokens_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                self._tokens = {}

    def _save_users(self) -> None:
        tmp = self._users_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._users, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._users_path)

    def _save_tokens(self) -> None:
        tmp = self._tokens_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._tokens_path)

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        if salt is None:
            salt = secrets.token_hex(16).encode()
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return salt.decode() + ":" + key.hex()

    def _verify_password(self, password: str, stored: str) -> bool:
        try:
            salt_hex, _ = stored.split(":", 1)
        except ValueError:
            return False
        return secrets.compare_digest(stored, self._hash_password(password, salt_hex.encode()))

    def register(self, request: RegisterRequest) -> UserResponse:
        if request.username in self._users:
            raise AuthError("Username already exists")
        self._users[request.username] = self._hash_password(request.password)
        self._save_users()
        return UserResponse(username=request.username)

    def login(self, request: LoginRequest) -> TokenResponse:
        stored = self._users.get(request.username)
        if stored is None or not self._verify_password(request.password, stored):
            raise AuthError("Invalid username or password")
        token = secrets.token_urlsafe(32)
        self._tokens[token] = request.username
        self._save_tokens()
        return TokenResponse(access_token=token)

    def logout(self, token: str) -> None:
        self._tokens.pop(token, None)
        self._save_tokens()

    def user_from_token(self, token: str) -> UserResponse | None:
        username = self._tokens.get(token)
        if username is None:
            return None
        return UserResponse(username=username)

    def list_users(self) -> Mapping[str, str]:
        return dict(self._users)