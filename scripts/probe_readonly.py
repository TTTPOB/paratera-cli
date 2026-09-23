"""Manually run a read-only DescribeZones header-prefix probe."""

import argparse
import asyncio
import json

from paratera_cli import ParateraClient


async def probe(header_prefix: str) -> None:
    try:
        async with ParateraClient(header_prefix=header_prefix) as client:
            data = await client.request("region", "DescribeZones", path="/v3/region/DescribeZones")
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__}))
        return
    print(json.dumps({"success": True, "data_type": type(data).__name__, "count": len(data) if isinstance(data, (list, dict)) else 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only Paratera region probe")
    parser.add_argument("--header-prefix", choices=("X-AIC", "X-TC"), required=True)
    arguments = parser.parse_args()
    asyncio.run(probe(arguments.header_prefix))
