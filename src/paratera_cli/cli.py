"""Personal command-line interface for Paratera container services."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import shlex
import sys
from urllib.parse import urlsplit, urlunsplit

from .auth import DEFAULT_CREDENTIALS_FILE
from .client import ParateraError
from .web import DEFAULT_SESSION_FILE, WEB_BASE_URL, WEB_OPERATIONS, WebClient, WebParateraAPI


class CLIError(Exception):
    """A user-facing CLI error."""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paratera", description="Paratera container CLI; requests use the asynchronous API client")
    p.add_argument("--credentials-file", default=str(DEFAULT_CREDENTIALS_FILE), help="ENV=val credentials file (default: %(default)s); environment variables take precedence")
    p.add_argument("--base-url", help="override backend's base URL")
    p.add_argument("--backend", choices=("openapi", "web"), default="openapi", help="API backend (default: openapi)")
    p.add_argument("--session-file", default=str(DEFAULT_SESSION_FILE), help="web session JSON (default: %(default)s)")
    p.add_argument("--json", action="store_true", help="print redacted JSON")
    p.add_argument("--show-secrets", action="store_true", help="explicitly show SSH password and authenticated URLs")
    commands = p.add_subparsers(dest="command", required=True)

    def leaf(parent, name, **kwargs):
        child = parent.add_parser(name, **kwargs)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print redacted JSON")
        child.add_argument("--show-secrets", action="store_true", default=argparse.SUPPRESS, help="show SSH password and authenticated URLs")
        return child

    session = commands.add_parser("session", help="manage existing website login session")
    leaf(session.add_subparsers(dest="session_action", required=True), "import", help="read token or JSON from stdin and store mode 0600")
    leaf(commands, "zones", help="list available zones")
    for name, help_text in [("types", "list instance types"), ("availability", "check all instance types in one batch"), ("images", "list public images")]:
        c = leaf(commands, name, help=help_text)
        c.add_argument("--zone", help="zone code filter")
    c = leaf(commands, "get", help="list instances, or select one with --id")
    c.add_argument("--id", help="instance UUID")
    c.add_argument("--zone", help="zone code filter")
    c.add_argument("--page", type=positive_int, default=1, help="page number (default: 1)")
    c.add_argument("--page-size", type=positive_int, default=100, help="page size (default: 100)")
    for name in ("quote", "create"):
        c = leaf(commands, name, help="quote or create a container instance")
        c.add_argument("--zone", required=True, help="zoneCode")
        c.add_argument("--name", required=True, help="aliasName")
        c.add_argument("--model", required=True, help="serviceModel")
        c.add_argument("--image", required=True, help="imageUuid")
        c.add_argument("--billing-type", choices=("PostPaid", "PrePaid"), default="PostPaid")
        c.add_argument("--count", type=positive_int, default=1)
        c.add_argument("--pay-period", type=positive_int, help="months for PrePaid")
    for name in ("ssh", "endpoints", "delete"):
        c = leaf(commands, name, help={"ssh": "print a quoted ssh command (never execute it)", "endpoints": "show SSH, Jupyter and TensorBoard endpoints", "delete": "delete an instance (requires --yes)"}[name])
        target_flags(c)
        if name == "delete":
            c.add_argument("--yes", action="store_true", help="confirm irreversible deletion")
    power = commands.add_parser("power", help="manage instance power")
    power_commands = power.add_subparsers(dest="power_action", required=True)
    for name in ("on", "off", "reboot"):
        c = leaf(power_commands, name, help=f"power {name} one instance")
        target_flags(c)
        if name == "off":
            c.add_argument("--discard-env", action="store_true", help="do not preserve files and environment (destructive)")
            c.add_argument("--stopped-mode", choices=("STOP_CHARGING", "KEEP_CHARGING"), help="default: PostPaid STOP_CHARGING, PrePaid KEEP_CHARGING")
    jobs = commands.add_parser("jobs", help="inspect asynchronous tasks")
    c = leaf(jobs.add_subparsers(dest="jobs_action", required=True), "get", help="query a job once (no polling)")
    c.add_argument("job_uuid", help="job UUID")
    api = commands.add_parser("api", help="low-level access to registered operations")
    api_commands = api.add_subparsers(dest="api_action", required=True)
    leaf(api_commands, "list", help="list registered service.Action operations")
    c = leaf(api_commands, "call", help="call a registered operation with JSON parameters")
    c.add_argument("operation", help="service.Action from api list")
    c.add_argument("--params", default="{}", help="JSON object or @filename with JSON object")
    return p


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def target_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--id", help="instance UUID; omitted only if exactly one instance exists")
    p.add_argument("--zone", help="zoneCode; inferred from selected instance when omitted")


def scrub(value, *, show_secrets=False):
    """Redact credential keys, URL userinfo and query strings, recursively."""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if not show_secrets and any(word in key.lower() for word in ("password", "token", "secret", "credential", "accesskey", "access_key")) else scrub(item, show_secrets=show_secrets)) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, show_secrets=show_secrets) for item in value]
    if isinstance(value, str) and not show_secrets and "://" in value:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        user = f"{parts.username}@" if parts.username else ""
        return urlunsplit((parts.scheme, user + host, parts.path, "[REDACTED]" if parts.query else "", "[REDACTED]" if parts.fragment else ""))
    return value


def display(value, args, *, columns=None):
    safe = scrub(value, show_secrets=args.show_secrets)
    if args.json or not isinstance(safe, list) or not safe or not columns:
        print(json.dumps(safe, ensure_ascii=False, indent=2, default=str))
        return
    rows = [[str(cell_at(item, path)) for path in columns.values()] for item in safe]
    widths = [max(len(title), *(len(row[i]) for row in rows)) for i, title in enumerate(columns)]
    print("  ".join(title.ljust(widths[i]) for i, title in enumerate(columns)))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def cell_at(item, path):
    for key in path.split("."):
        item = item.get(key) if isinstance(item, dict) else None
    return "" if item is None else item


def create_params(args):
    result = {"zoneCode": args.zone, "aliasName": args.name, "serviceModel": args.model, "imageUuid": args.image, "billingType": args.billing_type, "count": args.count}
    if args.pay_period is not None:
        result["payPeriod"] = args.pay_period
    if args.billing_type == "PrePaid" and args.pay_period is None:
        raise CLIError("PrePaid requires --pay-period")
    if args.billing_type == "PostPaid" and args.pay_period is not None:
        raise CLIError("--pay-period applies only to PrePaid")
    return result


def import_session(path: str | Path) -> None:
    raw = sys.stdin.read().strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    token = parsed.get("token") if isinstance(parsed, dict) else parsed
    if not isinstance(token, str):
        raise CLIError("Session input must be a token or JSON with token")
    token = token.strip().removeprefix("Bearer ").strip()
    if not token or any(char.isspace() for char in token):
        raise CLIError("Invalid session token")
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".paratera-session-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"token": token}, file)
            file.write("\n")
        os.chmod(name, 0o600)
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_params(raw):
    if raw.startswith("@"):
        if len(raw) == 1:
            raise CLIError("--params @ requires a filename")
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CLIError(f"Invalid --params JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise CLIError("--params must contain a JSON object")
    return data


async def select_instance(api, args):
    params = {"pageNum": 1, "pageSize": 100}
    if args.id:
        params["serviceUuid"] = args.id
    if args.zone:
        params["zoneCode"] = args.zone
    rows = []
    seen_pages = set()
    while True:
        page = await api.call("ackcs.DescribeServices", params)
        if not isinstance(page, dict) or not isinstance(page.get("rows"), list):
            raise CLIError("Unexpected DescribeServices response (expected data.rows)")
        batch = page["rows"]
        fingerprint = tuple((item.get("serviceUuid"), cell_at(item, "zone.zoneCode")) for item in batch)
        if batch and fingerprint in seen_pages:
            raise CLIError("DescribeServices returned a repeated page; cannot select safely")
        seen_pages.add(fingerprint)
        rows.extend(item for item in batch if (not args.id or item.get("serviceUuid") == args.id) and (not args.zone or cell_at(item, "zone.zoneCode") == args.zone))
        if len(rows) > 1:
            raise CLIError("Multiple instances match; specify --id (and --zone if needed)")
        total = page.get("total")
        if len(batch) < params["pageSize"] and not (isinstance(total, int) and total > params["pageNum"] * params["pageSize"]):
            break
        params["pageNum"] += 1
    if not rows:
        raise CLIError("No matching instances")
    instance = rows[0]
    service_uuid = instance.get("serviceUuid") or args.id
    zone = args.zone or cell_at(instance, "zone.zoneCode")
    if not service_uuid or not zone:
        raise CLIError("Selected instance has no UUID or zone; specify --id and --zone")
    return instance, {"zoneCode": zone, "serviceUuids": [service_uuid]}


def ssh_command(entry):
    url = urlsplit(entry.get("url", ""))
    if url.scheme != "ssh" or not url.username or not url.hostname:
        raise CLIError("Invalid SSH endpoint URL")
    try:
        port = url.port or 22
    except ValueError:
        raise CLIError("Invalid SSH endpoint port") from None
    host = url.hostname
    if ":" in host:
        host = f"[{host}]"
    return " ".join(shlex.quote(part) for part in ("ssh", "-p", str(port), f"{url.username}@{host}"))


async def dispatch(args, api):
    cmd = args.command
    if cmd == "api":
        if args.api_action == "list":
            if args.backend == "web":
                display(sorted(WEB_OPERATIONS), args)
            else:
                from .api import OPERATIONS
                display(sorted(OPERATIONS), args)
        else:
            display(await api.call(args.operation, read_params(args.params)), args)
    elif cmd == "zones":
        display(await api.call("region.DescribeZones"), args, columns={"zoneCode": "zoneCode", "zoneName": "zoneName", "region": "regionCode"})
    elif cmd in ("types", "availability"):
        types = await api.call("ack_product.DescribeACKServiceTypes")
        if not isinstance(types, list):
            raise CLIError("Unexpected service types response")
        types = [item for item in types if not args.zone or cell_at(item, "zone.zoneCode") == args.zone]
        if cmd == "types":
            display(types, args, columns={"zone": "zone.zoneCode", "model": "serviceModel", "GPU": "serviceGpus", "CPU": "serviceCpus", "memory(bytes)": "serviceMemory"})
        else:
            models = [{"zoneCode": cell_at(item, "zone.zoneCode"), "serviceModel": item.get("serviceModel")} for item in types]
            if not models:
                display([], args)
            else:
                display(await api.call("ack_product.DescribeACKAvailableResources", {"serviceModels": models}), args, columns={"zone": "zoneCode", "model": "serviceModel", "soldOut": "soldOut"})
    elif cmd == "images":
        images = await api.call("ack_product.DescribeACKPublicImages")
        if args.zone:
            images = [item for item in images if cell_at(item, "zone.zoneCode") == args.zone]
        display(images, args, columns={"UUID": "imageUuid", "image": "imageName", "GPU type": "vhostType", "zone": "zone.zoneCode"})
    elif cmd == "get":
        params = {"pageNum": args.page, "pageSize": args.page_size}
        if args.id:
            params["serviceUuid"] = args.id
        if args.zone:
            params["zoneCode"] = args.zone
        data = await api.call("ackcs.DescribeServices", params)
        rows = data.get("rows", [])
        display(data if args.json else rows, args, columns={"UUID": "serviceUuid", "name": "aliasName", "status": "serviceStatus", "zone": "zone.zoneCode", "billing": "billingType"})
    elif cmd in ("quote", "create"):
        operation = "InquiryPriceCreateServices" if cmd == "quote" else "CreateServices"
        display(await api.call(f"ackcs.{operation}", create_params(args)), args)
    elif cmd == "jobs":
        display(await api.call("ack_job.DescribeJobs", {"jobUuid": args.job_uuid}), args)
    elif cmd in ("ssh", "endpoints", "power", "delete"):
        if cmd == "delete" and not args.yes:
            raise CLIError("Deletion blocked: add --yes to confirm")
        instance, target = await select_instance(api, args)
        if cmd == "ssh":
            data = await api.call("ackcs.DescribeServicesSSH", target)
            entries = data.get("sshes", [])
            if len(entries) != 1:
                raise CLIError("Expected exactly one SSH endpoint")
            command = ssh_command(entries[0])
            if args.json:
                display({"command": command, "endpoint": entries[0]}, args)
            else:
                print(command)
                if args.show_secrets and entries[0].get("password"):
                    print("password:", entries[0]["password"])
        elif cmd == "endpoints":
            names = ("SSH", "Jupyter", "TensorBoard")
            responses = await asyncio.gather(*(api.call(f"ackcs.DescribeServices{name}", target) for name in names))
            display(dict(zip((name.lower() for name in names), responses)), args)
        else:
            if cmd == "delete":
                operation = "DeleteServices"
            else:
                operation = {"on": "StartServices", "off": "StopServices", "reboot": "RebootServices"}[args.power_action]
                if args.power_action == "off":
                    billing = instance.get("billingType")
                    mode = args.stopped_mode or ("KEEP_CHARGING" if billing == "PrePaid" else "STOP_CHARGING")
                    if billing == "PrePaid" and mode != "KEEP_CHARGING":
                        raise CLIError("PrePaid requires KEEP_CHARGING")
                    if billing == "PostPaid" and mode != "STOP_CHARGING":
                        raise CLIError("PostPaid requires STOP_CHARGING")
                    target.update(stoppedMode=mode, saveEnv=not args.discard_env)
            display(await api.call(f"ackcs.{operation}", target), args)


async def run(args, api=None):
    if args.command == "session":
        if args.session_action == "import":
            import_session(args.session_file)
        return
    if api is not None or (args.command == "api" and args.api_action == "list"):
        await dispatch(args, api)
        return
    if args.backend == "web":
        async with WebClient(session_file=args.session_file, base_url=args.base_url or WEB_BASE_URL) as client:
            await dispatch(args, WebParateraAPI(client))
    else:
        from .api import ParateraAPI
        from .client import ParateraClient
        async with ParateraClient(credentials_file=args.credentials_file, base_url=args.base_url or "https://ai.blsc.cn") as client:
            await dispatch(args, ParateraAPI(client))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except (CLIError, ValueError, OSError) as exc:
        print(f"paratera: {exc}", file=sys.stderr)
        return 2
    except ParateraError as exc:
        print(f"paratera: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Unexpected errors must not expose response bodies or credentials.
        print(f"paratera: request failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
