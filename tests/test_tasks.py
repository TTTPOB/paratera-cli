"""Offline tests for batched, low-frequency asynchronous job waiting."""

import asyncio

import pytest

from paratera_cli.tasks import JobTimeoutError, JobWaitError, wait_for_jobs


class FakeAPI:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def call(self, operation, params):
        self.calls.append((operation, params))
        return next(self.responses)


def test_pending_then_success_batches_all_jobs(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("paratera_cli.tasks.asyncio.sleep", sleep)
    api = FakeAPI(
        [
            [{"jobUuid": "a", "done": False, "success": False}],
            [
                {"jobUuid": "a", "done": True, "success": True},
                {"jobUuid": "b", "done": True, "success": True},
            ],
        ]
    )
    result = asyncio.run(wait_for_jobs(api, ["a", "b"], poll_interval=5, batch=True))
    assert [row["done"] for row in result] == [True, True]
    assert api.calls == [
        ("ack_job.DescribeJobs", {"jobUuid": "a,b"}),
        ("ack_job.DescribeJobs", {"jobUuid": "a,b"}),
    ]
    assert sleeps == [5]


def test_default_queries_one_job_id_per_openapi_call():
    api = FakeAPI(
        [
            [{"jobUuid": "a", "done": True, "success": True}],
            [{"jobUuid": "b", "done": True, "success": True}],
        ]
    )
    result = asyncio.run(wait_for_jobs(api, ["a", "b"]))
    assert [record["jobUuid"] for record in result] == ["a", "b"]
    assert api.calls == [
        ("ack_job.DescribeJobs", {"jobUuid": "a"}),
        ("ack_job.DescribeJobs", {"jobUuid": "b"}),
    ]


def test_network_query_is_bounded_by_deadline():
    class SlowAPI:
        async def call(self, operation, params):
            await asyncio.sleep(0.05)
            return [{"jobUuid": "a", "done": True, "success": True}]

    with pytest.raises(JobTimeoutError, match="a"):
        asyncio.run(wait_for_jobs(SlowAPI(), "a", timeout=0.001))


def test_completed_initial_records_skip_queries():
    api = FakeAPI([])
    records = [{"jobUuid": "a", "done": True, "success": True}]
    assert asyncio.run(wait_for_jobs(api, records)) == records
    assert api.calls == []


def test_failure_does_not_leak_server_error():
    api = FakeAPI(
        [[{"jobUuid": "a", "done": True, "success": False, "errorMessage": "SECRET"}]]
    )
    with pytest.raises(JobWaitError, match="a") as error:
        asyncio.run(wait_for_jobs(api, "a"))
    assert "SECRET" not in str(error.value)
    assert error.value.job_ids == ("a",)


def test_missing_job_times_out_without_false_success(monkeypatch):
    now = [0.0]

    async def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr("paratera_cli.tasks.time.monotonic", lambda: now[0])
    monkeypatch.setattr("paratera_cli.tasks.asyncio.sleep", sleep)
    api = FakeAPI([[{"jobUuid": "a", "done": True, "success": True}]])
    with pytest.raises(JobTimeoutError, match="b") as error:
        asyncio.run(
            wait_for_jobs(api, ["a", "b"], timeout=2, poll_interval=5, batch=True)
        )
    assert error.value.job_ids == ("b",)
    assert api.calls == [("ack_job.DescribeJobs", {"jobUuid": "a,b"})]


def test_validation_and_unknown_terminal_status():
    api = FakeAPI([])
    for kwargs in ({"timeout": 0}, {"poll_interval": -1}):
        with pytest.raises(ValueError):
            asyncio.run(wait_for_jobs(api, "a", **kwargs))
    with pytest.raises(ValueError):
        asyncio.run(wait_for_jobs(api, [{"done": True}]))
    with pytest.raises(JobWaitError):
        asyncio.run(wait_for_jobs(api, [{"jobUuid": "a", "done": True}]))
