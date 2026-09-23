"""Long-lived asynchronous Paratera API client."""

import json
import re
from pathlib import Path
from typing import Any

import httpx2

from .auth import load_credentials
from .signing import sign_headers


def _safe_business_code(code: Any) -> int | str | None:
    if type(code) is int:
        return code
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
        return code
    return None


class ParateraError(Exception):
    """A request failure with only safe, structured diagnostic fields."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        business_code: int | str | None = None,
        service: str | None = None,
        action: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.business_code = _safe_business_code(business_code)
        self.service = service
        self.action = action
        suffix = f" (code {self.business_code})" if self.business_code is not None else ""
        super().__init__(message + suffix)


class ParateraClient:
    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        *,
        credentials_file: str | Path | None = None,
        base_url: str = "https://ai.blsc.cn",
        timeout: float = 30,
        transport: httpx2.AsyncBaseTransport | None = None,
        header_prefix: str = "X-AIC",
    ) -> None:
        self._credentials = load_credentials(access_key, secret_key, credentials_file=credentials_file)
        self._base_url = base_url.rstrip("/")
        if header_prefix not in {"X-AIC", "X-TC"}:
            raise ValueError("Unsupported header prefix")
        self._header_prefix = header_prefix
        self._http = httpx2.AsyncClient(timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return "ParateraClient(<redacted>)"

    async def __aenter__(self) -> "ParateraClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        service: str,
        action: str,
        params: dict | None = None,
        *,
        path: str | None = None,
    ) -> Any:
        """POST signed JSON and return the business response's data field."""
        endpoint = path if path is not None else f"/v3/{service}/{action}"
        if not endpoint.startswith("/") or endpoint.startswith("//"):
            raise ValueError("Request path must be an absolute path")
        body = json.dumps(params if params is not None else {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = sign_headers(
            self._credentials, service, action, body, self._base_url,
            header_prefix=self._header_prefix,
        )
        try:
            response = await self._http.post(self._base_url + endpoint, content=body, headers=headers)
        except httpx2.HTTPError:
            raise ParateraError("Paratera request failed", service=service, action=action) from None
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        code = _safe_business_code(payload.get("code")) if isinstance(payload, dict) else None
        details = {
            "status_code": response.status_code,
            "business_code": code,
            "service": service,
            "action": action,
        }
        if not 200 <= response.status_code < 300:
            raise ParateraError("Paratera HTTP request failed", **details)
        if not isinstance(payload, dict):
            raise ParateraError("Invalid Paratera response", **details)
        if payload.get("code") != 200:
            raise ParateraError("Paratera API request failed", **details)
        return payload.get("data")
