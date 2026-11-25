"""Password hashing, HMAC tokens, CSRF, and SSRF URL validation."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import bcrypt
import jwt


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=10)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_email_address(email: str) -> str:
    raw = (email or "").strip().lower()
    if "@" not in raw:
        return raw
    local, domain = raw.split("@", 1)
    if len(local) <= 2:
        masked_local = f"{local[0]}***" if local else "***"
    elif len(local) <= 4:
        masked_local = f"{local[0]}***{local[-1]}"
    else:
        masked_local = f"{local[:2]}***{local[-2:]}"
    return f"{masked_local}@{domain}"


def derive_masked_relay_address(email: str) -> str:
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return "user@mask.subsphere.local"
    local = "".join(ch for ch in raw.split("@", 1)[0] if ch.isalnum())
    prefix = f"{local[:2]}{local[-2:]}" if len(local) >= 4 else (local or "user")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
    return f"u-{prefix}-{digest}@mask.subsphere.local"


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def create_jwt(payload: dict[str, Any], secret: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_jwt(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def sign_webhook_payload(secret: str, timestamp: str, payload_bytes: bytes) -> str:
    msg = f"{timestamp}.".encode("utf-8") + payload_bytes
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def is_safe_public_url(raw_url: str) -> tuple[bool, str]:
    """Validate URL is http(s) and does not target loopback, private, link-local, or metadata IPs."""
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return False, "Malformed URL"
    if parsed.scheme not in ("http", "https"):
        return False, f"Disallowed URL scheme: {parsed.scheme}"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "Missing hostname"
    if host in ("localhost", "localhost.localdomain", "metadata.google.internal"):
        return False, f"Disallowed internal hostname: {host}"
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"Disallowed private or reserved IP address: {host}"
    except ValueError:
        # Domain name: verify it does not resolve to private IP if DNS resolves
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                addr_str = info[4][0]
                ip = ipaddress.ip_address(addr_str)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    return False, f"Hostname resolves to disallowed IP: {addr_str}"
        except socket.gaierror:
            # Offline/unresolvable domain names allowed only if hostname itself isn't local
            if host.endswith(".local") or host.endswith(".internal"):
                return False, f"Disallowed internal domain suffix: {host}"
    return True, ""
