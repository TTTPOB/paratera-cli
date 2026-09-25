"""Session-token transport for observed website endpoints, not the signed OpenAPI."""

import base64
import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx2

from .client import ParateraError

DEFAULT_SESSION_FILE = Path("~/.local/share/creds/paratera-session.json")
WEB_BASE_URL = "https://ai.paratera.com"
# Observed session exchange: the website reissues its one-hour JWT from these cookies,
# so a stored cookie jar keeps the CLI logged in without a password or a live browser.
REFRESH_PATH = "/platform/tsinghua/authAndGetCoupon"
REFRESH_ACCESS_KEY = "paraterA"
REFRESH_MARGIN_SECONDS = 300
EXPIRED_BUSINESS_CODES = frozenset({100005, 100006})


def write_session_file(path: str | Path, session: Mapping[str, Any]) -> None:
    """Write session JSON atomically with owner-only permissions."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".paratera-session-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(dict(session), file)
            file.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def token_expiry(token: str) -> int | None:
    """Return the unverified JWT exp claim, or None for an opaque token."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except (ValueError, TypeError):
        return None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return expiry if type(expiry) is int else None


class WebClient:
    """Use a logged-in website session and renew its short-lived token from cookies."""

    def __init__(
        self,
        *,
        session_file: str | Path = DEFAULT_SESSION_FILE,
        base_url: str = WEB_BASE_URL,
        timeout: float = 30,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._session_file = Path(session_file).expanduser()
        self._base_url = base_url.rstrip("/")
        self._http = httpx2.AsyncClient(timeout=timeout, transport=transport)
        env_token = os.environ.get("PARATERA_TOKEN")
        # An environment token is explicit input: never read or rewrite the stored session.
        self._session = {} if env_token else self._read_session()
        token = env_token or self._session.get("token")
        cookies = self._session.get("cookies")
        access_code = self._session.get("accessCode")
        self._token = (
            token.strip() if isinstance(token, str) and token.strip() else None
        )
        self._cookies = (
            cookies.strip() if isinstance(cookies, str) and cookies.strip() else None
        )
        self._access_code = (
            access_code.strip()
            if isinstance(access_code, str) and access_code.strip()
            else None
        )
        if self._token is None and not self._refreshable():
            raise ValueError("Missing web session; import a logged-in session again")

    def __repr__(self) -> str:
        return "WebClient(<redacted>)"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    def _read_session(self) -> dict[str, Any]:
        try:
            session = json.loads(self._session_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ValueError(
                "Cannot read web session JSON; import a logged-in session again"
            ) from None
        return session if isinstance(session, dict) else {}

    def _refreshable(self) -> bool:
        return bool(self._cookies and self._access_code)

    def _stale(self) -> bool:
        if self._token is None:
            return True
        expiry = token_expiry(self._token)
        return expiry is not None and expiry - time.time() <= REFRESH_MARGIN_SECONDS

    async def refresh(self) -> int | None:
        """Exchange stored website cookies for a fresh token; returns seconds left."""
        if not self._refreshable():
            raise ValueError(
                "No website cookie session stored; import a logged-in session again"
            )
        try:
            response = await self._http.get(
                self._base_url + REFRESH_PATH,
                params={
                    "accessKey": REFRESH_ACCESS_KEY,
                    "accessCode": self._access_code,
                },
                headers={"Cookie": self._cookies},
            )
        except httpx2.HTTPError:
            raise ParateraError("Paratera website request failed") from None
        token = response.headers.get("token")
        if response.status_code != 200 or not token or not token.strip():
            raise ParateraError(
                "Website session expired; log in again and re-import session",
                status_code=response.status_code,
            )
        self._token = token.strip()
        self._persist_token()
        expiry = token_expiry(self._token)
        return None if expiry is None else max(0, int(expiry - time.time()))

    def _persist_token(self) -> None:
        """Cache the renewed token; a failed write only costs one extra refresh."""
        try:
            write_session_file(
                self._session_file, {**self._session, "token": self._token}
            )
        except OSError:
            pass

    async def _send(self, method: str, path: str, body: Any) -> httpx2.Response:
        headers: dict[str, str] = {}
        if self._token is not None:
            headers = {
                "token": self._token,
                "Ai-Authorization": f"Bearer {self._token}",
            }
        if self._cookies is not None:
            headers["Cookie"] = self._cookies
        try:
            return await self._http.request(
                method,
                self._base_url + path,
                headers=headers,
                json=body if method == "POST" else None,
            )
        except httpx2.HTTPError:
            raise ParateraError("Paratera website request failed") from None

    @staticmethod
    def _decode(response: httpx2.Response) -> tuple[Any, Any]:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        code = payload.get("code") if isinstance(payload, dict) else None
        return payload, code

    async def request(
        self, path: str, body: Any = None, *, method: str = "POST"
    ) -> Any:
        if (
            not path.startswith("/platform/")
            or path.startswith("//")
            or (
                "?" in path
                and path != "/platform/zone/getServiceZoneList?resourceType=ACKCS"
            )
        ):
            raise ValueError("Unsupported website endpoint path")
        if method not in {"GET", "POST"}:
            raise ValueError("Unsupported website request method")
        if self._refreshable() and self._stale():
            await self.refresh()
        response = await self._send(method, path, body)
        payload, code = self._decode(response)
        expired = response.status_code == 401 or code in EXPIRED_BUSINESS_CODES
        if expired and self._refreshable():
            await self.refresh()
            response = await self._send(method, path, body)
            payload, code = self._decode(response)
        if response.status_code == 401:
            raise ParateraError(
                "Website session expired (401); log in again and re-import session",
                status_code=401,
            )
        if code in EXPIRED_BUSINESS_CODES:
            raise ParateraError(
                "Website session expired; log in again and re-import session",
                status_code=response.status_code,
                business_code=code,
            )
        if not 200 <= response.status_code < 300:
            raise ParateraError(
                "Paratera website HTTP request failed",
                status_code=response.status_code,
                business_code=code,
            )
        if not isinstance(payload, dict):
            raise ParateraError(
                "Invalid Paratera website response", status_code=response.status_code
            )
        if code != 200:
            raise ParateraError(
                "Paratera website API request failed",
                status_code=response.status_code,
                business_code=code,
            )
        return payload.get("data")


# These names preserve the CLI's service.Action contract, but are website mappings,
# not claims that any signed /v3 endpoint is accessible in production.
WEB_OPERATIONS = frozenset(
    {
        "region.DescribeZones",
        "ack_product.DescribeACKServiceTypes",
        "ack_product.DescribeACKAvailableResources",
        "ack_product.DescribeACKPublicImages",
        "ackcs.DescribeServices",
        "ackcs.InquiryPriceCreateServices",
        "ackcs.CreateServices",
        "ackcs.StartServices",
        "ackcs.RebootServices",
        "ackcs.StopServices",
        "ackcs.DeleteServices",
        "ackcs.DescribeServicesSSH",
        "ackcs.DescribeServicesJupyter",
        "ackcs.DescribeServicesTensorBoard",
        "ack_job.DescribeJobs",
    }
)


class WebParateraAPI:
    """Translate only observed website endpoints into the CLI's operation contract."""

    def __init__(self, client: WebClient) -> None:
        self.client = client
        self._zones: dict[str, dict[str, Any]] | None = None

    async def _zone_catalog(
        self, raw: list[dict[str, Any]] | None = None
    ) -> dict[str, dict[str, Any]]:
        if self._zones is None:
            if raw is None:
                raw = await self.client.request(
                    "/platform/resourceContainerService/getResourceContainerServiceList",
                    method="GET",
                )
            if not isinstance(raw, list):
                raise ParateraError("Unexpected website zone directory shape")
            catalog: dict[str, dict[str, Any]] = {}
            for item in raw:
                if not isinstance(item, dict):
                    continue
                code = item.get("zoneCode")
                if isinstance(code, str) and code:
                    location = {
                        key: item.get(key)
                        for key in ("zoneId", "regionId", "clusterId")
                    }
                    if all(value is not None for value in location.values()):
                        catalog[code] = {
                            "zoneCode": code,
                            "zoneName": item.get("zoneName"),
                            "regionCode": item.get("regionCode"),
                            **location,
                        }
            self._zones = catalog
        return self._zones

    async def _location(self, zone_code: Any) -> dict[str, Any]:
        if not isinstance(zone_code, str) or not zone_code:
            raise ValueError("A zoneCode is required")
        zone = (await self._zone_catalog()).get(zone_code)
        if zone is None:
            raise ValueError("Zone code not found in website directory")
        keys = ("zoneId", "regionId", "clusterId")
        if any(zone.get(key) is None for key in keys):
            raise ValueError("Website zone directory lacks zone/region/cluster IDs")
        return {key: zone[key] for key in keys}

    async def call(
        self, operation: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        if operation not in WEB_OPERATIONS:
            raise ValueError(f"Website backend does not support operation {operation}")
        p = dict(params or {})
        if operation == "region.DescribeZones":
            return list((await self._zone_catalog()).values())
        if operation == "ack_product.DescribeACKServiceTypes":
            data = await self.client.request(
                "/platform/resourceContainerService/getResourceContainerServiceList",
                method="GET",
            )
            if not isinstance(data, list):
                raise ParateraError("Unexpected website service types shape")
            zones = await self._zone_catalog(data)
            by_id = {z.get("zoneId"): z for z in zones.values()}
            return [
                {**row, "zone": {"zoneCode": by_id[row.get("zoneId")]["zoneCode"]}}
                if isinstance(row, dict) and row.get("zoneId") in by_id
                else row
                for row in data
            ]
        if operation == "ack_product.DescribeACKPublicImages":
            images: list[dict[str, Any]] = []
            for page_num in range(1, 101):
                data = await self.client.request(
                    "/platform/resourceContainerImage/getResourceCommonImage",
                    {"pageNum": page_num, "pageSize": 100},
                )
                if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
                    raise ParateraError("Unexpected website public images shape")
                rows = data["rows"]
                if not rows:
                    break
                if any(not isinstance(row, dict) for row in rows):
                    raise ParateraError("Unexpected website public image row")
                images.extend(rows)
                total = data.get("total")
                if isinstance(total, int) and len(images) >= total:
                    break
                if len(rows) < 100:
                    break
            else:
                raise ParateraError("Website public image listing exceeded 100 pages")
            zones = await self._zone_catalog()
            by_id = {zone["zoneId"]: zone["zoneCode"] for zone in zones.values()}
            normalized: list[dict[str, Any]] = []
            for row in images:
                creation_id = row.get("imageId") or row.get("providerImageUuid")
                if not isinstance(creation_id, str) or not creation_id:
                    raise ParateraError(
                        "Website public image has no usable creation ID"
                    )
                item = {
                    **row,
                    "sourceImageUuid": row.get("imageUuid"),
                    "imageUuid": creation_id,
                }
                if row.get("zoneId") in by_id:
                    item["zone"] = {"zoneCode": by_id[row["zoneId"]]}
                normalized.append(item)
            return normalized
        if operation == "ack_product.DescribeACKAvailableResources":
            models = p.get("serviceModels")
            if not isinstance(models, list):
                raise ValueError("serviceModels must be a list")
            groups: dict[str, list[str]] = {}
            for item in models:
                if not isinstance(item, dict) or not isinstance(
                    item.get("serviceModel"), str
                ):
                    raise TypeError(
                        "Each service model needs serviceModel and zoneCode"
                    )
                code = item.get("zoneCode")
                if not isinstance(code, str):
                    raise TypeError(
                        "Each service model needs serviceModel and zoneCode"
                    )
                groups.setdefault(code, []).append(item["serviceModel"])
            locations = [
                {**await self._location(code), "resourceModels": names}
                for code, names in groups.items()
            ]
            raw = await self.client.request(
                "/platform/resourceContainerService/describeServiceAvailableResource",
                locations,
            )
            if not isinstance(raw, list):
                raise ParateraError("Unexpected website availability shape")
            id_to_code = {
                (await self._location(code))["zoneId"]: code for code in groups
            }
            result: list[dict[str, Any]] = []
            for group in raw:
                if (
                    not isinstance(group, dict)
                    or group.get("zoneId") not in id_to_code
                    or not isinstance(group.get("resourceModels"), list)
                ):
                    raise ParateraError("Unexpected website availability group")
                for model in group["resourceModels"]:
                    if not isinstance(model, dict):
                        raise ParateraError("Unexpected website availability model")
                    result.append(
                        {
                            **model,
                            "zoneCode": id_to_code[group["zoneId"]],
                            "serviceModel": model.get("resourceModel"),
                        }
                    )
            return result
        if operation == "ackcs.DescribeServices":
            body = {
                "pageNum": p.pop("pageNum", 1),
                "pageSize": p.pop("pageSize", 12),
                "deleteStatus": "NotDeleted",
            }
            zone_code = p.pop("zoneCode", None)
            if zone_code is not None:
                body["zoneId"] = (await self._location(zone_code))["zoneId"]
            self._check_params(
                p,
                {
                    "serviceUuid",
                    "aliasName",
                    "serviceStatus",
                    "serviceType",
                    "deleteStatus",
                },
            )
            body.update(p)
            data = await self.client.request(
                "/platform/ack/service/getUserResourceContainerServiceDetailList", body
            )
            if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
                raise ParateraError("Unexpected website service list shape")
            zones = await self._zone_catalog()
            by_id = {z.get("zoneId"): z for z in zones.values()}
            rows = []
            for row in data["rows"]:
                if not isinstance(row, dict):
                    raise ParateraError("Unexpected website service row")
                zone = by_id.get(row.get("zoneId"))
                rows.append(
                    {
                        **row,
                        "zone": {"zoneCode": zone["zoneCode"]}
                        if zone
                        else row.get("zone", {}),
                    }
                )
            return {**data, "rows": rows}
        if operation == "ack_job.DescribeJobs":
            job = p.get("jobUuid")
            if not isinstance(job, str) or not job:
                raise ValueError("jobUuid is required")
            return await self.client.request(
                "/platform/jobs/ack", {"jobUuids": job.split(",")}
            )
        if operation in {"ackcs.CreateServices", "ackcs.InquiryPriceCreateServices"}:
            location = await self._location(p.pop("zoneCode", None))
            required = ("serviceModel", "billingType")
            if any(not p.get(key) for key in required):
                raise ValueError("Missing website service parameters")
            if operation == "ackcs.CreateServices":
                if any(not p.get(key) for key in ("aliasName", "imageUuid")):
                    raise ValueError("Missing website service creation parameters")
                allowed = {
                    *required,
                    "aliasName",
                    "imageUuid",
                    "count",
                    "payPeriod",
                    "autoContinue",
                    "password",
                    "volumes",
                }
                self._check_params(p, allowed)
                if p["billingType"] == "PostPaid":
                    p.pop("payPeriod", None)
                p.setdefault("count", 1)
                p.setdefault("autoContinue", False)
                return await self.client.request(
                    "/platform/ack/service/create", {**location, **p}
                )
            allowed = {
                *required,
                "aliasName",
                "imageUuid",
                "count",
                "payPeriod",
                "volumes",
            }
            self._check_params(p, allowed)
            body: dict[str, Any] = {
                **location,
                "billingType": p["billingType"],
                "containerService": [{"resourceModel": p["serviceModel"]}],
                "count": p.get("count", 1),
            }
            if p["billingType"] != "PostPaid" and "payPeriod" in p:
                body["payPeriod"] = p["payPeriod"]
            if "volumes" in p:
                body["volumes"] = p["volumes"]
            return await self.client.request(
                "/platform/resourcePrice/checkContainerPrice", body
            )
        action = operation.removeprefix("ackcs.")
        identifiers = p.pop("serviceUuids", None)
        if (
            not isinstance(identifiers, list)
            or not identifiers
            or not all(isinstance(x, str) and x for x in identifiers)
        ):
            raise ValueError("serviceUuids must be a nonempty list")
        zone_id = (await self._location(p.pop("zoneCode", None)))["zoneId"]
        body: dict[str, Any] = {"zoneId": zone_id, "serviceUuids": identifiers}
        if action == "StopServices":
            mode = p.pop("stoppedMode", "STOP_CHARGING")
            if mode not in {"STOP_CHARGING", "KEEP_CHARGING"}:
                raise ValueError("Unsupported stoppedMode")
            body.update(
                stopChargingEnabled=mode == "STOP_CHARGING",
                saveEnv=p.pop("saveEnv", True),
            )
        elif action == "DeleteServices":
            body["cascadeVolume"] = False
        self._check_params(p, set())
        routes = {
            "StartServices": "start",
            "RebootServices": "reboot",
            "StopServices": "stop",
            "DeleteServices": "delete",
            "DescribeServicesSSH": "describe/ssh",
            "DescribeServicesJupyter": "describe/jupyter",
            "DescribeServicesTensorBoard": "describe/tensorboard",
        }
        return await self.client.request(
            "/platform/ack/service/" + routes[action], body
        )

    @staticmethod
    def _check_params(params: Mapping[str, Any], allowed: set[str]) -> None:
        unknown = params.keys() - allowed
        if unknown:
            raise ValueError(
                "Unsupported website operation parameter: " + ", ".join(sorted(unknown))
            )
