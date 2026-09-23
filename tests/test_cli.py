"""Offline command-line contract tests; no Paratera credentials or network."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from paratera_cli.cli import (
    CLIError,
    create_params,
    dispatch,
    main,
    parser,
    read_params,
    run,
    scrub,
    select_instance,
    ssh_command,
)
from paratera_cli.client import ParateraError


class FakeAPI:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, operation, params=None):
        self.calls.append((operation, params))
        response = self.responses[operation]
        return response(params) if callable(response) else response


def execute(args, responses):
    api = FakeAPI(responses)
    asyncio.run(dispatch(parser().parse_args(args), api))
    return api


def instance(uuid="instance-1", billing="PostPaid"):
    return {"serviceUuid": uuid, "zone": {"zoneCode": "zone-1"}, "billingType": billing}


def test_help_and_payload():
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["--help"])
    assert exc.value.code == 0
    args = parser().parse_args(
        ["create", "--zone", "z", "--name", "n", "--model", "m", "--image", "i"]
    )
    assert create_params(args) == {
        "zoneCode": "z",
        "aliasName": "n",
        "serviceModel": "m",
        "imageUuid": "i",
        "billingType": "PostPaid",
        "count": 1,
    }
    assert read_params('{"x":1}') == {"x": 1}
    with pytest.raises(CLIError):
        read_params("[]")


def test_availability_batches_all_zone_models(capsys):
    types = [
        {"zone": {"zoneCode": "z"}, "serviceModel": "m1"},
        {"zone": {"zoneCode": "z"}, "serviceModel": "m2"},
        {"zone": {"zoneCode": "other"}, "serviceModel": "m3"},
    ]
    api = execute(
        ["availability", "--zone", "z", "--json"],
        {
            "ack_product.DescribeACKServiceTypes": types,
            "ack_product.DescribeACKAvailableResources": [
                {"serviceModel": "m1", "soldOut": False}
            ],
        },
    )
    assert api.calls == [
        ("ack_product.DescribeACKServiceTypes", None),
        (
            "ack_product.DescribeACKAvailableResources",
            {
                "serviceModels": [
                    {"zoneCode": "z", "serviceModel": "m1"},
                    {"zoneCode": "z", "serviceModel": "m2"},
                ]
            },
        ),
    ]
    assert json.loads(capsys.readouterr().out)[0]["soldOut"] is False


def test_select_instance_requires_exactly_one_and_paginates():
    args = parser().parse_args(["ssh"])

    async def choose(rows):
        return await select_instance(
            FakeAPI({"ackcs.DescribeServices": {"rows": rows}}), args
        )

    assert asyncio.run(choose([instance()]))[1] == {
        "zoneCode": "zone-1",
        "serviceUuids": ["instance-1"],
    }
    for rows in ([], [instance("a"), instance("b")]):
        with pytest.raises(CLIError):
            asyncio.run(choose(rows))
    args = parser().parse_args(["ssh", "--id", "instance-1"])
    api = FakeAPI({"ackcs.DescribeServices": {"rows": [instance()]}})
    asyncio.run(select_instance(api, args))
    assert api.calls[0][1]["serviceUuid"] == "instance-1"
    wrong = [instance(f"other-{i}") for i in range(100)]
    pages = FakeAPI(
        {
            "ackcs.DescribeServices": lambda params: {
                "rows": wrong if params["pageNum"] == 1 else [instance()]
            }
        }
    )
    assert asyncio.run(select_instance(pages, args))[1]["serviceUuids"] == [
        "instance-1"
    ]
    assert len(pages.calls) == 2
    repeated = FakeAPI({"ackcs.DescribeServices": {"rows": wrong}})
    with pytest.raises(CLIError, match="repeated page"):
        asyncio.run(select_instance(repeated, args))
    assert len(repeated.calls) == 2


def test_ssh_command_and_secret_redaction(capsys):
    entry = {"url": "ssh://pod@host.example:3456", "password": "private"}
    assert ssh_command(entry) == "ssh -p 3456 pod@host.example"
    api = execute(
        ["ssh", "--json"],
        {
            "ackcs.DescribeServices": {"rows": [instance()]},
            "ackcs.DescribeServicesSSH": {"sshes": [entry]},
        },
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown == {
        "command": "ssh -p 3456 pod@host.example",
        "endpoint": {"url": "ssh://pod@host.example:3456", "password": "[REDACTED]"},
    }
    assert api.calls[-1][1] == {"zoneCode": "zone-1", "serviceUuids": ["instance-1"]}
    data = {
        "password": "private",
        "items": [
            {
                "secretKey": "private",
                "urls": ["https://u:p@host/lab?token=private#fragment"],
            }
        ],
    }
    safe = json.dumps(scrub(data))
    assert "private" not in safe and "u:p" not in safe and "token=" not in safe
    assert scrub(data, show_secrets=True) == data


def test_endpoints_redact_jupyter_token(capsys):
    execute(
        ["endpoints", "--json"],
        {
            "ackcs.DescribeServices": {"rows": [instance()]},
            "ackcs.DescribeServicesSSH": {
                "sshes": [{"url": "ssh://pod@host:123", "password": "private"}]
            },
            "ackcs.DescribeServicesJupyter": {
                "jupyters": [{"urls": ["https://host/lab?token=private"]}]
            },
            "ackcs.DescribeServicesTensorBoard": {
                "tensorboards": [{"urls": ["https://host/tensorboard/"]}]
            },
        },
    )
    printed = capsys.readouterr().out
    assert "private" not in printed and "token=" not in printed
    assert "https://host/tensorboard/" in printed


def test_off_selects_prepaid_mode_and_preserves_environment(capsys):
    api = execute(
        ["power", "off", "--json", "--no-wait"],
        {
            "ackcs.DescribeServices": {"rows": [instance(billing="PrePaid")]},
            "ackcs.StopServices": [{"jobUuid": "j", "done": False, "success": False}],
        },
    )
    assert api.calls[-1] == (
        "ackcs.StopServices",
        {
            "zoneCode": "zone-1",
            "serviceUuids": ["instance-1"],
            "stoppedMode": "KEEP_CHARGING",
            "saveEnv": True,
        },
    )
    assert json.loads(capsys.readouterr().out)[0]["done"] is False


def test_lifecycle_default_wait_and_no_wait(capsys):
    create = [
        "create",
        "--zone",
        "z",
        "--name",
        "n",
        "--model",
        "m",
        "--image",
        "i",
        "--json",
    ]
    submitted = [{"jobUuid": "j", "done": False, "success": False}]
    finished = [{"jobUuid": "j", "done": True, "success": True}]
    for flags, expected_calls, expected_output in (
        ([], 2, finished),
        (["--no-wait"], 1, submitted),
        (["--wait"], 2, finished),
    ):
        api = execute(
            create + flags,
            {
                "ackcs.CreateServices": submitted,
                "ack_job.DescribeJobs": finished,
            },
        )
        output = capsys.readouterr()
        assert json.loads(output.out) == expected_output
        assert len(api.calls) == expected_calls
        if expected_calls == 2:
            assert api.calls[-1] == ("ack_job.DescribeJobs", {"jobUuid": "j"})
            assert "j" in output.err
        else:
            assert output.err == ""


def test_power_and_delete_default_wait(capsys):
    for command, operation in (
        (["power", "on"], "StartServices"),
        (["delete", "--yes"], "DeleteServices"),
    ):
        api = execute(
            command + ["--json"],
            {
                "ackcs.DescribeServices": {"rows": [instance()]},
                f"ackcs.{operation}": [{"jobUuid": "j", "done": True, "success": True}],
            },
        )
        assert len(api.calls) == 2
        assert json.loads(capsys.readouterr().out)[0]["success"] is True


def test_jobs_wait_queries_completion(capsys):
    api = execute(
        ["jobs", "wait", "j", "--json"],
        {"ack_job.DescribeJobs": [{"jobUuid": "j", "done": True, "success": True}]},
    )
    assert api.calls == [("ack_job.DescribeJobs", {"jobUuid": "j"})]
    assert json.loads(capsys.readouterr().out)[0]["done"] is True


def test_wait_failure_returns_nonzero_without_response_leak(monkeypatch, capsys):
    from paratera_cli.tasks import JobWaitError

    async def fail(args):
        raise JobWaitError("Job failed", ["j"])

    monkeypatch.setattr("paratera_cli.cli.run", fail)
    assert main(["jobs", "wait", "j", "--json"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "paratera jobs get JOB_ID" in output.err
    assert main(["--backend", "web", "jobs", "wait", "j"]) == 1
    assert "paratera --backend web jobs get JOB_ID" in capsys.readouterr().err


def test_wait_arguments_reject_nonpositive():
    for flag, value in (
        ("--timeout", "0"),
        ("--poll-interval", "-1"),
        ("--timeout", "nan"),
    ):
        with pytest.raises(SystemExit):
            parser().parse_args(["jobs", "wait", "j", flag, value])


def test_delete_blocked_without_yes():
    with pytest.raises(CLIError, match="--yes"):
        execute(["delete", "--id", "instance-1", "--zone", "zone-1"], {})


def test_api_list_requires_no_credentials(monkeypatch, capsys):
    monkeypatch.setitem(
        sys.modules,
        "paratera_cli.api",
        SimpleNamespace(OPERATIONS={"region.DescribeZones": object()}),
    )
    assert asyncio.run(run(parser().parse_args(["api", "list"]))) is None
    assert "region.DescribeZones" in capsys.readouterr().out


def test_client_error_message_is_preserved(monkeypatch, capsys):
    async def fail(args):
        raise ParateraError("Paratera HTTP request failed (status 429)")

    monkeypatch.setattr("paratera_cli.cli.run", fail)
    assert main(["zones"]) == 1
    assert "status 429" in capsys.readouterr().err


def test_api_call_json_file(tmp_path, capsys):
    source = tmp_path / "payload.json"
    source.write_text('{"jobUuid":"job-1"}', encoding="utf-8")
    api = execute(
        ["api", "call", "ack_job.DescribeJobs", "--params", f"@{source}", "--json"],
        {"ack_job.DescribeJobs": [{"jobUuid": "job-1", "password": "sensitive"}]},
    )
    assert api.calls == [("ack_job.DescribeJobs", {"jobUuid": "job-1"})]
    assert "sensitive" not in capsys.readouterr().out
