"""Wait for asynchronous container jobs without changing API submission semantics."""

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any


class JobWaitError(Exception):
    """A job failed or its completion cannot safely be established."""

    def __init__(self, message: str, job_ids: Sequence[str]) -> None:
        self.job_ids = tuple(job_ids)
        super().__init__(f"{message}: {', '.join(self.job_ids)}")


class JobTimeoutError(JobWaitError):
    """The deadline elapsed; submitted jobs may still be running."""


async def wait_for_jobs(
    api: Any,
    jobs: Sequence[Mapping[str, Any] | str] | Mapping[str, Any] | str,
    *,
    timeout: float = 600,
    poll_interval: float = 5,
    batch: bool = False,
) -> list[dict[str, Any]]:
    """Return completed job records; batch only when the backend supports it.

    Accept an initial job response (list or single record), or job IDs. Already
    completed successful records are not queried again. Failure and timeout errors
    intentionally include only IDs, never server-supplied error text or bodies.
    Enable batch explicitly for the website backend; OpenAPI documents one ID per call.
    """
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError("timeout and poll_interval must be positive")
    entries = [jobs] if isinstance(jobs, (str, Mapping)) else list(jobs)
    if not entries:
        raise ValueError("At least one job is required")
    result: dict[str, dict[str, Any]] = {}
    pending: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            job_id = entry
            record: dict[str, Any] = {"jobUuid": job_id}
        elif isinstance(entry, Mapping):
            job_id = entry.get("jobUuid")
            record = dict(entry)
        else:
            raise TypeError("Jobs must contain job IDs or records")
        if not isinstance(job_id, str) or not job_id.strip() or "," in job_id:
            raise ValueError("Each job must have a valid jobUuid")
        if job_id in result:
            raise ValueError("Duplicate jobUuid")
        result[job_id] = record
        if record.get("done") is True:
            if record.get("success") is not True:
                raise JobWaitError(
                    "Job failed or completion status is unknown", [job_id]
                )
        else:
            pending.add(job_id)

    deadline = time.monotonic() + timeout
    while pending:
        if time.monotonic() >= deadline:
            raise JobTimeoutError(
                "Timed out waiting; jobs may still be running", sorted(pending)
            )
        remaining = deadline - time.monotonic()
        try:
            async with asyncio.timeout(remaining):
                if batch:
                    responses = [
                        await api.call(
                            "ack_job.DescribeJobs",
                            {"jobUuid": ",".join(sorted(pending))},
                        )
                    ]
                else:
                    responses = await asyncio.gather(
                        *(
                            api.call("ack_job.DescribeJobs", {"jobUuid": job_id})
                            for job_id in sorted(pending)
                        )
                    )
        except TimeoutError:
            raise JobTimeoutError(
                "Timed out waiting; jobs may still be running", sorted(pending)
            ) from None
        for response in responses:
            if not isinstance(response, list):
                raise JobWaitError("Unexpected job query response", sorted(pending))
            for record in response:
                if not isinstance(record, dict):
                    raise JobWaitError("Unexpected job query response", sorted(pending))
                job_id = record.get("jobUuid")
                if job_id not in pending:
                    continue
                result[job_id] = record
                if record.get("done") is True:
                    if record.get("success") is not True:
                        raise JobWaitError(
                            "Job failed or completion status is unknown", [job_id]
                        )
                    pending.remove(job_id)
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobTimeoutError(
                    "Timed out waiting; jobs may still be running", sorted(pending)
                )
            await asyncio.sleep(min(poll_interval, remaining))
    return list(result.values())
