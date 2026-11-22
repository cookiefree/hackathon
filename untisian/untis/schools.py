"""Find a school on WebUntis, so the login works for any school and not just the one in .env.

Uses WebUntis's own public school search. A server name only ever comes from that answer (never from
what a user types), and it must be `<name>.webuntis.com`, so a made-up address cannot make this
server call somewhere else.
"""
import re
from dataclasses import dataclass

import requests

from untisian import config

SEARCH_URL = "https://mobile.webuntis.com/ms/schoolquery2"
_HOST = re.compile(r"^[a-z0-9][a-z0-9-]*\.webuntis\.com$")


class SchoolSearchError(Exception):
    pass


class TooManyResults(SchoolSearchError):
    """The search was too broad (WebUntis error -6003)."""


@dataclass(frozen=True)
class School:
    server: str          # e.g. hpg.webuntis.com
    login_name: str      # the school= value, e.g. hpg
    name: str            # display name
    address: str = ""


def valid_server(host: str) -> bool:
    return bool(_HOST.match(host or ""))


def search(query: str, limit: int = 8) -> tuple[list[School], bool]:
    """(schools, more). `more` is True when the search found more than `limit`."""
    query = query.strip()
    if config.get("UNTIS_PROVIDER") == "mock":              # demo mode: one fake school, no network
        hit = [School("demo.webuntis.com", "demo", "Demo School", "12345 Demo City, Demo Street 1")]
        return (hit if "demo" in query.lower() else []), False
    try:
        r = requests.post(SEARCH_URL, timeout=10, json={
            "id": "1", "method": "searchSchool", "params": [{"search": query}], "jsonrpc": "2.0"})
        data = r.json()
        if isinstance(data, dict) and data.get("error", {}).get("code") == -6003:
            raise TooManyResults()
        raw = data["result"]["schools"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise SchoolSearchError(type(e).__name__) from e
    found = [School(s["server"], s["loginName"], s.get("displayName") or s["loginName"], s.get("address", ""))
             for s in raw if isinstance(s, dict) and valid_server(s.get("server", "")) and s.get("loginName")]
    return found[:limit], len(found) > limit
