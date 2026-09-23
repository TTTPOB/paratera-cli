"""Offline contract tests: no credentials or remote requests."""

import asyncio
import inspect

import pytest

from paratera_cli.api import OPERATIONS, ParateraAPI


class SpyClient:
    def __init__(self, responses=()):
        self.calls = []
        self.responses = iter(responses)

    async def request(self, service, action, params=None, *, path=None):
        self.calls.append((service, action, params, path))
        return next(self.responses, {"ok": True})


def execute(awaitable):
    return asyncio.run(awaitable)


def test_registry_has_all_published_operations_and_discoverable_methods():
    assert len(OPERATIONS) == 46
    assert len({(v.service, v.action) for v in OPERATIONS.values()}) == 46
    assert all(key == f"{value.service}.{value.action}" for key, value in OPERATIONS.items())
    assert all(value.path.startswith("/v3/") and value.documentation.startswith("https://ai.paratera.com/document/openapi/") for value in OPERATIONS.values())
    named = {name for name, method in vars(ParateraAPI).items() if inspect.iscoroutinefunction(method) and name != "call"}
    assert len(named) == 46
    assert OPERATIONS["ackci.DescribeImages"].path == "/v3/ackci/DescribeImages"
    assert OPERATIONS["product.DescribeImages"].path == "/v3/product/DescribeImages"
    assert OPERATIONS["ack_job.DescribeJobs"].path == "/v3/ack_job/DescribeJobs"
    assert OPERATIONS["job.DescribeJobs"].path == "/v3/job/DescribeJobs"
    assert OPERATIONS["ecs.InquirePriceRunInstances"].documentation.endswith("/InquiryPriceRunInstances")
    assert OPERATIONS["region.DescribeZones"].documentation.endswith("/region/DescribeZones")
    assert OPERATIONS["ack_product.DescribeACKPublicImages"].path == "/v3/ack_product/DescribeACKPublicImages"


def test_stop_preserves_environment_but_respects_explicit_false():
    spy = SpyClient()
    api = ParateraAPI(spy)
    execute(api.stop_services("zone", "container"))
    execute(api.stop_services("zone", "container", save_env=False))
    execute(api.call("ackcs.StopServices", {"zoneCode": "zone", "serviceUuids": ["container"]}))
    execute(api.call("ackcs.StopServices", {"saveEnv": False, "stoppedMode": "KEEP_CHARGING"}))
    assert [c[2]["saveEnv"] for c in spy.calls] == [True, False, True, False]
    assert spy.calls[0] == ("ackcs", "StopServices", {"zoneCode": "zone", "serviceUuids": ["container"], "saveEnv": True, "stoppedMode": "STOP_CHARGING"}, "/v3/ackcs/StopServices")
    assert spy.calls[-1][2]["stoppedMode"] == "KEEP_CHARGING"


def test_container_payloads_and_raw_sensitive_data_are_unchanged():
    secret_data = {"sshes": [{"password": "not-a-real-secret"}]}
    spy = SpyClient([{"ok": True}, {"ok": True}, secret_data])
    api = ParateraAPI(spy)
    execute(api.describe_ack_available_resources([{"zoneCode": "z", "serviceModel": "gpu"}]))
    execute(api.create_services("z", "name", "gpu", "image", "ON_DEMAND", count=0, auto_continue=False))
    assert execute(api.describe_services_ssh("z", "container")) is secret_data
    assert spy.calls[0][2] == {"serviceModels": [{"zoneCode": "z", "serviceModel": "gpu"}]}
    assert spy.calls[1][2] == {"zoneCode": "z", "aliasName": "name", "serviceModel": "gpu", "imageUuid": "image", "billingType": "ON_DEMAND", "count": 0, "autoContinue": False}
    assert spy.calls[2][2] == {"zoneCode": "z", "serviceUuids": ["container"]}


def test_paginate_services_and_stop_on_last_or_repeated_page():
    spy = SpyClient([{"rows": [{"id": 1}, {"id": 2}]}, {"rows": [{"id": 3}]}, {"rows": [{"id": 9}]}, {"rows": [{"id": 9}]}])
    api = ParateraAPI(spy)

    async def collect():
        return [row async for row in api.iter_services(page_size=2, zone_code="z")]

    assert execute(collect()) == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [call[2]["pageNum"] for call in spy.calls] == [1, 2]
    assert spy.calls[0][2] == {"pageNum": 1, "pageSize": 2, "zoneCode": "z"}

    async def repeated():
        return [row async for row in api.iter_services(page_size=1, max_pages=50)]

    assert execute(repeated()) == [{"id": 9}]
    assert len(spy.calls) == 4
    with pytest.raises(ValueError):
        execute(api.iter_services(page_size=0).__anext__())


def test_compute_and_disk_payloads_and_low_frequency_call():
    spy = SpyClient()
    api = ParateraAPI(spy)
    instance = ("zone", "name", "gpu", "image", "SSD", 80, "ON_DEMAND", 5, "PUBLIC", "SSD", 100)
    execute(api.inquire_price_run_instances(*instance))
    execute(api.create_disks("zone", "disk", "SSD", 100, "ON_DEMAND"))
    execute(api.attach_disks("zone", "ecs", "ebs"))
    execute(api.call("ackcs.ChangeServicesModel", {"serviceUuids": ["one"]}))
    assert spy.calls[0][0:2] == ("ecs", "InquirePriceRunInstances")
    assert spy.calls[0][2] == {"zoneCode": "zone", "aliasName": "name", "ecsModel": "gpu", "imageUuid": "image", "rootDiskType": "SSD", "rootDiskSize": 80, "billingType": "ON_DEMAND", "networkSize": 5, "networkType": "PUBLIC", "diskType": "SSD", "diskSize": 100}
    assert spy.calls[1][2] == {"zoneCode": "zone", "aliasName": "disk", "diskType": "SSD", "diskSize": 100, "billingType": "ON_DEMAND"}
    assert spy.calls[2][2] == {"zoneCode": "zone", "ecsUuid": "ecs", "ebsUuids": ["ebs"]}
    assert spy.calls[3][3] == "/v3/ackcs/ChangeServicesModel"
    with pytest.raises(KeyError):
        execute(api.call("not.documented", {}))
