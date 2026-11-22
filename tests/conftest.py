"""Every test gets an empty data folder, no settings from .env or the shell, and its patches undone after."""
import os
import random
import sys

os.environ["ENV_FILE"] = ""            # set before untisian is imported: never read the real .env
SETTINGS = ("ALLOWED_USERS", "ALERTS", "ALERT_TELEGRAM_ID", "DB_PATH", "GEMINI_API_KEY", "GEMINI_MODEL",
            "OCR_PROVIDER", "TELEGRAM_TOKEN", "TELEGRAM_ENABLED", "UNTIS_PROVIDER", "UNTIS_SERVER",
            "UNTIS_SCHOOL", "UNTIS_USER", "UNTIS_PASSWORD", "MAX_UPLOADS_PER_USER")
for _k in SETTINGS:
    os.environ.pop(_k, None)

import pytest  # noqa: E402


def _snapshot():
    """Module globals of untisian (and random, which a test seeds), so monkeypatching in a test is undone."""
    mods = [m for n, m in list(sys.modules.items()) if n == "untisian" or n.startswith("untisian.")] + [random]
    return [(m, {k: (v, v.copy() if isinstance(v, (list, dict, set)) else None) for k, v in vars(m).items()})
            for m in mods]


def _restore(snap):
    for mod, saved in snap:
        d = vars(mod)
        for k in [k for k in d if k not in saved]:
            del d[k]
        for k, (v, contents) in saved.items():
            d[k] = v
            if contents is not None:            # containers are restored in place: other modules hold them too
                if isinstance(v, list):
                    v[:] = contents
                else:
                    v.clear()
                    v.update(contents)


class Checks:
    """Soft asserts: a story test runs to the end and then reports every check that failed, by name."""
    def __init__(self):
        self.failed = []

    def __call__(self, name, cond):
        if not cond:
            self.failed.append(name)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach Google, WebUntis or Telegram: any real HTTP request fails the test loudly."""
    import requests
    def refuse(*a, **k):
        raise RuntimeError(f"a test tried to use the network: {a[1:3]}")
    monkeypatch.setattr(requests.Session, "request", refuse)


@pytest.fixture(autouse=True)
def isolated(tmp_path):
    env = dict(os.environ)
    os.environ.update(DATA_DIR=str(tmp_path / "data"), VAULT_PATH=str(tmp_path / "vault"))
    snap = _snapshot()
    yield tmp_path
    _restore(snap)
    os.environ.clear()
    os.environ.update(env)


@pytest.fixture
def check():
    c = Checks()
    yield c
    assert not c.failed, "failed checks:\n  " + "\n  ".join(c.failed)
