import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # the repository: .env, data/, vault/


def _load_env() -> None:
    """Read .env (or the file in ENV_FILE; ENV_FILE= with no value reads none, as the tests do)."""
    name = os.environ.get("ENV_FILE", str(ROOT / ".env"))
    path = Path(name) if name else None
    if path is None or not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
