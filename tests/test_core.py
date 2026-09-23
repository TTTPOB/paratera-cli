"""Offline tests using synthetic credentials and responses only."""

import asyncio
import hashlib
import hmac
import json

import httpx2
import pytest

from paratera_cli import (
    Credentials,
    ParateraClient,
    ParateraError,
    load_credentials,
    sign_headers,
)


def test_signing_exact_body_and_host():
    body = b'{"name":"example"}'
    headers = sign_headers(
        Credentials("fake-access", "fake-secret"),
        "region",
        "DescribeZones",
        body,
        "https://EXAMPLE.test:8443",
        timestamp=123,
    )
    canonical = (
        "POST\n/\n\ncontent-type:application/json\nhost:example.test"
        "\ncontent-type;host\n" + hashlib.sha256(body).hexdigest()
    )
    string_to_sign = (
        "HMAC-SHA256\nV3\nfake-access\nregion\nparatera/aicloud/region\n"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )
    expected = hmac.new(
        b"BC_SIGNATURE&fake-secret", string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    assert headers["X-AIC-Signature"] == expected
    assert headers["X-AIC-Timestamp"] == "123"
    assert headers["X-AIC-SignedHeaders"] == "content-type;host"
    assert "fake-secret" not in repr(Credentials("fake-access", "fake-secret"))
    assert "X-TC-Signature" in sign_headers(
        Credentials("fake-access", "fake-secret"),
        "region",
        "DescribeZones",
        body,
        "https://example.test",
        header_prefix="X-TC",
        timestamp=123,
    )


def test_credentials_precedence(tmp_path, monkeypatch):
    file = tmp_path / "credentials"
    file.write_text(
        "# ignored\nPARATERA_ACCESS_KEY='file-access'\nPARATERA_SECRET_KEY=file-secret\n"
    )
    monkeypatch.setenv("PARATERA_ACCESS_KEY", "env-access")
    monkeypatch.setenv("PARATERA_SECRET_KEY", "env-secret")
    assert load_credentials(credentials_file=file) == Credentials(
        "env-access", "env-secret"
    )
    assert load_credentials("explicit-access", credentials_file=file) == Credentials(
        "explicit-access", "env-secret"
    )
    monkeypatch.delenv("PARATERA_SECRET_KEY")
    assert load_credentials(credentials_file=file) == Credentials(
        "env-access", "file-secret"
    )
    monkeypatch.delenv("PARATERA_ACCESS_KEY")
    assert load_credentials(credentials_file=file) == Credentials(
        "file-access", "file-secret"
    )


def test_request_body_path_response_and_close():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json={"code": 200, "data": ["zone"]})

    transport = httpx2.MockTransport(handler)

    async def run():
        async with ParateraClient(
            "fake-access",
            "fake-secret",
            base_url="https://example.test:8443",
            transport=transport,
        ) as client:
            assert await client.request(
                "region", "DescribeZones", {"search": "é"}, path="/custom/zones"
            ) == ["zone"]
            assert not client._http.is_closed
        assert client._http.is_closed

    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].url.path == "/custom/zones"
    assert json.loads(seen[0].content) == {"search": "é"}
    assert seen[0].headers["X-AIC-Service"] == "region"
    assert seen[0].headers["Content-Type"] == "application/json"
    expected_headers = sign_headers(
        Credentials("fake-access", "fake-secret"),
        "region",
        "DescribeZones",
        seen[0].content,
        "https://example.test:8443",
        timestamp=int(seen[0].headers["X-AIC-Timestamp"]),
    )
    assert seen[0].headers["X-AIC-Signature"] == expected_headers["X-AIC-Signature"]


def test_business_and_http_errors_redacted():
    for status, payload, expected_code in [
        (200, {"code": 403, "message": "fake-secret"}, 403),
        (503, {"code": "ServerError", "data": "fake-secret"}, "ServerError"),
        (200, {"code": "fake-secret with spaces", "message": "fake-secret"}, None),
    ]:

        async def run(status=status, payload=payload, expected_code=expected_code):
            transport = httpx2.MockTransport(
                lambda request: httpx2.Response(status, json=payload)
            )
            async with ParateraClient(
                "fake-access", "fake-secret", transport=transport
            ) as client:
                with pytest.raises(ParateraError) as error:
                    await client.request("region", "DescribeZones")
                assert "fake-secret" not in str(error.value)
                assert "fake-access" not in str(error.value)
                assert error.value.status_code == status
                assert error.value.business_code == expected_code
                assert error.value.service == "region"
                assert error.value.action == "DescribeZones"

        asyncio.run(run())
