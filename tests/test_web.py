"""Offline website-backend contract tests; no live credentials or requests."""

import asyncio
import base64
import json
import time

import httpx2
import pytest

from paratera_cli.cli import main, parser
from paratera_cli.client import ParateraError
from paratera_cli.web import WEB_BASE_URL, WEB_OPERATIONS, WebClient, WebParateraAPI

CATALOG = [
    {
        "zoneCode": "cn-test-a",
        "zoneId": 50,
        "regionId": 38,
        "clusterId": 39,
        "serviceModel": "gpu.small",
        "serviceGpus": 1,
    }
]


def test_session_import_and_backend_defaults(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Bearer safe-token\n"))
    path = tmp_path / "session.json"
    assert main(["--session-file", str(path), "session", "import"]) == 0
    assert json.loads(path.read_text()) == {"token": "safe-token"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert "safe-token" not in capsys.readouterr().out
    assert parser().parse_args(["zones"]).backend == "web"
    assert parser().parse_args(["--backend", "openapi", "zones"]).base_url is None
    monkeypatch.setenv("PARATERA_TOKEN", "from-env")

    async def verify():
        async with WebClient(
            session_file=tmp_path / "does-not-exist",
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json={"code": 200, "data": []})
            ),
        ) as client:
            assert await client.request("/platform/jobs/ack", {}) == []
            assert "from-env" not in repr(client)

    asyncio.run(verify())


def test_web_mapper_zone_conversion_and_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    path = tmp_path / "session.json"
    path.write_text('{"token":"secret"}', encoding="utf-8")
    requests = []

    def handler(request):
        requests.append(
            (
                request.url,
                request.headers,
                json.loads(request.content) if request.content else None,
            )
        )
        if request.url.path.endswith("getResourceContainerServiceList"):
            data = CATALOG
        elif request.url.path.endswith("getUserResourceContainerServiceDetailList"):
            data = {"rows": [{"serviceUuid": "one", "zoneId": 50}], "total": 1}
        elif request.url.path.endswith("describeServiceAvailableResource"):
            data = [
                {
                    "zoneId": 50,
                    "resourceModels": [
                        {"resourceModel": "gpu.small", "soldOut": False}
                    ],
                }
            ]
        else:
            data = [{"jobUuid": "job-1"}]
        return httpx2.Response(200, json={"code": 200, "data": data})

    async def verify():
        async with WebClient(
            session_file=path, transport=httpx2.MockTransport(handler)
        ) as client:
            api = WebParateraAPI(client)
            rows = await api.call("ackcs.DescribeServices", {"zoneCode": "cn-test-a"})
            assert rows["rows"][0]["zone"]["zoneCode"] == "cn-test-a"
            assert requests[-1][2]["zoneId"] == 50
            assert requests[-1][2]["deleteStatus"] == "NotDeleted"
            stock = await api.call(
                "ack_product.DescribeACKAvailableResources",
                {
                    "serviceModels": [
                        {"zoneCode": "cn-test-a", "serviceModel": "gpu.small"}
                    ]
                },
            )
            assert stock[0]["serviceModel"] == "gpu.small"
            assert requests[-1][2] == [
                {
                    "zoneId": 50,
                    "regionId": 38,
                    "clusterId": 39,
                    "resourceModels": ["gpu.small"],
                }
            ]
            await api.call(
                "ackcs.StopServices",
                {
                    "zoneCode": "cn-test-a",
                    "serviceUuids": ["one"],
                    "stoppedMode": "KEEP_CHARGING",
                    "saveEnv": True,
                },
            )
            assert requests[-1][2] == {
                "zoneId": 50,
                "serviceUuids": ["one"],
                "stopChargingEnabled": False,
                "saveEnv": True,
            }
            await api.call(
                "ackcs.InquiryPriceCreateServices",
                {
                    "zoneCode": "cn-test-a",
                    "serviceModel": "gpu.small",
                    "billingType": "PostPaid",
                    "aliasName": "n",
                    "imageUuid": "image",
                    "count": 1,
                },
            )
            assert requests[-1][2] == {
                "zoneId": 50,
                "regionId": 38,
                "clusterId": 39,
                "containerService": [{"resourceModel": "gpu.small"}],
                "billingType": "PostPaid",
                "count": 1,
            }
            await api.call(
                "ackcs.CreateServices",
                {
                    "zoneCode": "cn-test-a",
                    "serviceModel": "gpu.small",
                    "billingType": "PostPaid",
                    "aliasName": "n",
                    "imageUuid": "sha256:public",
                    "volumes": [{"volumeType": "LOCAL", "volumeSize": 100}],
                },
            )
            assert requests[-1][2] == {
                "zoneId": 50,
                "regionId": 38,
                "clusterId": 39,
                "serviceModel": "gpu.small",
                "billingType": "PostPaid",
                "aliasName": "n",
                "imageUuid": "sha256:public",
                "volumes": [{"volumeType": "LOCAL", "volumeSize": 100}],
                "count": 1,
                "autoContinue": False,
            }
            assert all(r[0].host == "ai.paratera.com" for r in requests)
            assert all(
                r[1]["token"] == "secret"
                and r[1]["Ai-Authorization"] == "Bearer secret"
                for r in requests
            )
            assert "ackcs.RebootServices" in WEB_OPERATIONS
            await api.call(
                "ackcs.RebootServices",
                {"zoneCode": "cn-test-a", "serviceUuids": ["one"]},
            )
            assert requests[-1][0].path == "/platform/ack/service/reboot"
            assert requests[-1][2] == {"zoneId": 50, "serviceUuids": ["one"]}
            with pytest.raises(ValueError, match="Zone code not found"):
                await api.call(
                    "ackcs.StartServices",
                    {"zoneCode": "missing", "serviceUuids": ["one"]},
                )

    asyncio.run(verify())


def test_web_images_use_frontend_creation_id(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text('{"token":"test"}', encoding="utf-8")
    images = [
        {"zoneId": 50, "imageId": "sha256:public", "imageUuid": "ackci-original"},
        {
            "zoneId": 50,
            "providerImageUuid": "provider-private",
            "imageUuid": "ackci-other",
        },
    ]

    def handler(request):
        if request.url.path.endswith("getResourceContainerServiceList"):
            data = CATALOG
        else:
            assert json.loads(request.content) == {"pageNum": 1, "pageSize": 100}
            data = {"pageNum": 1, "total": 2, "rows": images}
        return httpx2.Response(200, json={"code": 200, "data": data})

    async def verify():
        async with WebClient(
            session_file=session, transport=httpx2.MockTransport(handler)
        ) as client:
            result = await WebParateraAPI(client).call(
                "ack_product.DescribeACKPublicImages"
            )
            assert [row["imageUuid"] for row in result] == [
                "sha256:public",
                "provider-private",
            ]
            assert [row["sourceImageUuid"] for row in result] == [
                "ackci-original",
                "ackci-other",
            ]
            assert result[0]["zone"]["zoneCode"] == "cn-test-a"

    asyncio.run(verify())


def test_expired_session_has_no_response_body(tmp_path):
    path = tmp_path / "session.json"
    path.write_text('{"token":"private"}', encoding="utf-8")

    async def verify():
        async with WebClient(
            session_file=path,
            base_url=WEB_BASE_URL,
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(401, text="private response details")
            ),
        ) as client:
            with pytest.raises(ParateraError, match="log in again") as exc:
                await client.request("/platform/jobs/ack", {})
            assert "private" not in str(exc.value)

    asyncio.run(verify())


def make_token(expiry: int, subject: str = "61300") -> str:
    """Build an unsigned JWT-shaped token so tests never need real credentials."""

    def segment(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return raw.rstrip("=")

    claims = {"sub": subject, "exp": expiry}
    return f"{segment({'typ': 'JWT', 'alg': 'HS512'})}.{segment(claims)}.signature"


def test_session_import_stores_cookies(tmp_path, monkeypatch, capsys):
    import io

    payload = json.dumps(
        {"cookies": "para_sess=abc;  has_auth=Y", "accessCode": "jWOG61300"}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    path = tmp_path / "session.json"
    assert main(["--session-file", str(path), "session", "import"]) == 0
    assert json.loads(path.read_text()) == {
        "cookies": "para_sess=abc; has_auth=Y",
        "accessCode": "jWOG61300",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert "para_sess" not in capsys.readouterr().out


def test_session_import_warns_without_access_code(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cookies": "a=b"})))
    path = tmp_path / "session.json"
    assert main(["--session-file", str(path), "session", "import"]) == 0
    assert "accessCode" in capsys.readouterr().err


def test_web_client_renews_token_from_stored_cookies(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps(
            {
                "token": "stale",
                "cookies": "para_sess=abc; has_auth=Y",
                "accessCode": "jWOG61300",
            }
        ),
        encoding="utf-8",
    )
    fresh = make_token(int(time.time()) + 3600)
    exchanges = []

    def handler(request):
        if request.url.path.endswith("authAndGetCoupon"):
            exchanges.append(request)
            return httpx2.Response(
                200, headers={"token": fresh}, json={"code": 200, "data": {}}
            )
        if request.headers.get("token") == "stale":
            return httpx2.Response(401, json={"code": 100005, "message": "token失效"})
        return httpx2.Response(200, json={"code": 200, "data": [{"jobUuid": "job-1"}]})

    async def verify():
        async with WebClient(
            session_file=session, transport=httpx2.MockTransport(handler)
        ) as client:
            assert await client.request("/platform/jobs/ack", {"jobUuids": ["j1"]}) == [
                {"jobUuid": "job-1"}
            ]

    asyncio.run(verify())
    assert len(exchanges) == 1
    refresh = exchanges[0]
    assert refresh.url.params["accessCode"] == "jWOG61300"
    assert refresh.headers.get("cookie") == "para_sess=abc; has_auth=Y"
    assert refresh.headers.get("token") is None
    stored = json.loads(session.read_text())
    assert stored == {
        "token": fresh,
        "cookies": "para_sess=abc; has_auth=Y",
        "accessCode": "jWOG61300",
    }
    assert session.stat().st_mode & 0o777 == 0o600


def test_web_client_refreshes_before_request_when_token_expired(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps(
            {
                "token": make_token(int(time.time()) - 10),
                "cookies": "para_sess=abc",
                "accessCode": "code-1",
            }
        ),
        encoding="utf-8",
    )
    fresh = make_token(int(time.time()) + 3600)
    api_tokens = []

    def handler(request):
        if request.url.path.endswith("authAndGetCoupon"):
            return httpx2.Response(
                200, headers={"token": fresh}, json={"code": 200, "data": {}}
            )
        api_tokens.append(request.headers.get("token"))
        return httpx2.Response(200, json={"code": 200, "data": []})

    async def verify():
        async with WebClient(
            session_file=session, transport=httpx2.MockTransport(handler)
        ) as client:
            await client.request("/platform/jobs/ack", {})

    asyncio.run(verify())
    assert api_tokens == [fresh]


def test_web_client_starts_from_cookies_only(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps({"cookies": "para_sess=abc", "accessCode": "code-1"}),
        encoding="utf-8",
    )
    fresh = make_token(int(time.time()) + 3600)

    def handler(request):
        if request.url.path.endswith("authAndGetCoupon"):
            return httpx2.Response(
                200, headers={"token": fresh}, json={"code": 200, "data": {}}
            )
        assert request.headers.get("token") == fresh
        return httpx2.Response(200, json={"code": 200, "data": [1]})

    async def verify():
        async with WebClient(
            session_file=session, transport=httpx2.MockTransport(handler)
        ) as client:
            assert await client.request("/platform/jobs/ack", {}) == [1]

    asyncio.run(verify())
    assert json.loads(session.read_text())["token"] == fresh


def test_web_client_reports_unrefreshable_expiry(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": "para_sess=abc"}), encoding="utf-8")

    async def verify():
        with pytest.raises(ValueError, match="logged-in session"):
            WebClient(
                session_file=session, transport=httpx2.MockTransport(lambda r: None)
            )

    asyncio.run(verify())


def test_web_client_rejects_dead_cookie_session(tmp_path, monkeypatch):
    monkeypatch.delenv("PARATERA_TOKEN", raising=False)
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps({"cookies": "para_sess=dead", "accessCode": "code-1"}),
        encoding="utf-8",
    )

    def handler(request):
        if request.url.path.endswith("authAndGetCoupon"):
            return httpx2.Response(200, json={"code": 200, "data": {}})
        return httpx2.Response(200, json={"code": 200, "data": []})

    async def verify():
        async with WebClient(
            session_file=session, transport=httpx2.MockTransport(handler)
        ) as client:
            with pytest.raises(ParateraError, match="log in again") as exc:
                await client.request("/platform/jobs/ack", {})
            assert "para_sess" not in str(exc.value)

    asyncio.run(verify())
