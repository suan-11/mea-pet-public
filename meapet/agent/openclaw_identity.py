"""OpenClaw Gateway 设备身份与 v3 挑战签名。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_IDENTITY_VERSION = 1
_MAX_IDENTITY_BYTES = 4096


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: object) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("OpenClaw identity key is empty")
    try:
        return base64.b64decode(
            text + "=" * (-len(text) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("OpenClaw identity key is not base64url") from exc


def _normalize_metadata(value: object) -> str:
    return str(value or "").strip().lower()


def build_device_auth_payload_v3(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: Iterable[str],
    signed_at_ms: int,
    token: str,
    nonce: str,
    platform: str = "",
    device_family: str = "",
) -> str:
    """按 OpenClaw 官方字节契约构造 Ed25519 签名原文。"""
    return "|".join(
        (
            "v3",
            str(device_id),
            str(client_id),
            str(client_mode),
            str(role),
            ",".join(str(scope) for scope in scopes),
            str(int(signed_at_ms)),
            str(token or ""),
            str(nonce),
            _normalize_metadata(platform),
            _normalize_metadata(device_family),
        )
    )


@dataclass(frozen=True)
class OpenClawDeviceIdentity:
    """持久 Ed25519 私钥；设备 ID 是原始公钥的 SHA-256。"""

    private_key_bytes: bytes
    public_key_bytes: bytes

    @classmethod
    def from_private_bytes(cls, private_key_bytes: bytes) -> "OpenClawDeviceIdentity":
        raw = bytes(private_key_bytes)
        if len(raw) != 32:
            raise ValueError("OpenClaw Ed25519 private key must be 32 bytes")
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(raw, public_key)

    @classmethod
    def generate(cls) -> "OpenClawDeviceIdentity":
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cls.from_private_bytes(raw)

    @classmethod
    def load(cls, path: Path | str) -> "OpenClawDeviceIdentity":
        identity_path = Path(path).expanduser()
        try:
            size = identity_path.stat().st_size
            if size <= 0 or size > _MAX_IDENTITY_BYTES:
                raise ValueError("OpenClaw identity file has an invalid size")
            payload = json.loads(identity_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError("OpenClaw identity file cannot be read") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("OpenClaw identity file is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("version") != _IDENTITY_VERSION:
            raise ValueError("OpenClaw identity file version is unsupported")
        identity = cls.from_private_bytes(_b64url_decode(payload.get("privateKey")))
        recorded_id = str(payload.get("deviceId") or "").strip().lower()
        if recorded_id and recorded_id != identity.device_id:
            raise ValueError("OpenClaw identity device id does not match its key")
        return identity

    @classmethod
    def load_or_create(cls, path: Path | str) -> "OpenClawDeviceIdentity":
        identity_path = Path(path).expanduser()
        if identity_path.exists():
            return cls.load(identity_path)
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity = cls.generate()
        payload = json.dumps(
            {
                "version": _IDENTITY_VERSION,
                "deviceId": identity.device_id,
                "privateKey": _b64url_encode(identity.private_key_bytes),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(identity_path, flags, 0o600)
        except FileExistsError:
            return cls.load(identity_path)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(identity_path, 0o600)
        except BaseException:
            try:
                identity_path.unlink()
            except OSError:
                pass
            raise
        return identity

    @property
    def device_id(self) -> str:
        return hashlib.sha256(self.public_key_bytes).hexdigest()

    @property
    def public_key(self) -> str:
        return _b64url_encode(self.public_key_bytes)

    def sign(self, payload: str) -> str:
        private_key = Ed25519PrivateKey.from_private_bytes(self.private_key_bytes)
        return _b64url_encode(private_key.sign(str(payload).encode("utf-8")))
