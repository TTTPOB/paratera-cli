"""Credential loading without executing or logging credential contents."""

from dataclasses import dataclass
import os
from pathlib import Path

DEFAULT_CREDENTIALS_FILE = Path("~/.local/share/creds/paratera")


@dataclass(frozen=True, repr=False)
class Credentials:
    access_key: str
    secret_key: str

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


def _read_credentials_file(path: Path) -> dict[str, str]:
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError("Cannot read credentials file") from None

    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"PARATERA_ACCESS_KEY", "PARATERA_SECRET_KEY"}:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    return values


def load_credentials(
    access_key: str | None = None,
    secret_key: str | None = None,
    *,
    credentials_file: str | Path | None = None,
) -> Credentials:
    """Resolve each credential from explicit arguments, environment, then file."""
    env_access = os.environ.get("PARATERA_ACCESS_KEY")
    env_secret = os.environ.get("PARATERA_SECRET_KEY")
    file_values = (
        _read_credentials_file(Path(credentials_file or DEFAULT_CREDENTIALS_FILE))
        if (access_key is None and env_access is None) or (secret_key is None and env_secret is None)
        else {}
    )
    resolved_access = access_key if access_key is not None else env_access
    resolved_secret = secret_key if secret_key is not None else env_secret
    resolved_access = resolved_access if resolved_access is not None else file_values.get("PARATERA_ACCESS_KEY")
    resolved_secret = resolved_secret if resolved_secret is not None else file_values.get("PARATERA_SECRET_KEY")
    if not resolved_access or not resolved_secret:
        raise ValueError("Missing Paratera credentials")
    return Credentials(resolved_access, resolved_secret)
