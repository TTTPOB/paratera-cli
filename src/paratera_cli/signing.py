"""Paratera V3 request signing."""

import hashlib
import hmac
import time
from urllib.parse import urlsplit

from .auth import Credentials


def sign_headers(
    credentials: Credentials,
    service: str,
    action: str,
    body: bytes,
    base_url: str,
    *,
    header_prefix: str = "X-AIC",
    timestamp: int | None = None,
) -> dict[str, str]:
    """Sign the exact JSON bytes that will be sent as the request content."""
    if header_prefix not in {"X-AIC", "X-TC"}:
        raise ValueError("Unsupported header prefix")
    hostname = urlsplit(base_url).hostname
    if not hostname:
        raise ValueError("Invalid base URL")
    canonical_headers = f"content-type:application/json\nhost:{hostname.lower()}"
    body_hash = hashlib.sha256(body).hexdigest()
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\ncontent-type;host\n{body_hash}"
    )
    request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = (
        f"HMAC-SHA256\nV3\n{credentials.access_key}\n{service}"
        f"\nparatera/aicloud/{service}\n{request_hash}"
    )
    signature = hmac.new(
        f"BC_SIGNATURE&{credentials.secret_key}".encode(),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        f"{header_prefix}-Version": "V3",
        f"{header_prefix}-Action": action,
        f"{header_prefix}-Timestamp": str(
            int(time.time()) if timestamp is None else timestamp
        ),
        f"{header_prefix}-AccessKey": credentials.access_key,
        f"{header_prefix}-SignedHeaders": "content-type;host",
        f"{header_prefix}-Signature": signature,
        f"{header_prefix}-Service": service,
    }
