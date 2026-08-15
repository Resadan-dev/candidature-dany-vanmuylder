"""Mocked BHS API: the three endpoints of data_sources.md, no dependencies.

Payload shapes come from the assessment; the numbers come from state/sim.env,
which the console writes. Timestamps are recomputed on every call so the
dashboard shows live durations rather than a frozen fixture.

Two settings change the shape of the answer:

    TRACKER_FEED=off   last_update stops moving -- the API keeps answering
                       while nothing reaches the database any more
    API_STATUS=down    every endpoint returns 503, which is what proves the
                       DATA STALE banner does not depend on this service
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8080"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/state/sim.env"))

# Fallbacks, used only until the console has written its first state file.
DEFAULTS: dict[str, str] = {
    "BAGS_IN_SYSTEM": "142",
    "AVG_DWELL_MINUTES": "4.2",
    "ARRIVAL_RATE": "1960",
    "STUCK_BAGS": "5",
    "PHANTOM_BUG": "fixed",
    "TRACKER_FEED": "on",
    "API_STATUS": "up",
    "SIM_HOUR": "8.0",
}

# Fixed identities for the stuck-bag table, so the panel stays readable from
# one refresh to the next instead of reshuffling tag numbers.
STUCK_IDENTITIES: list[tuple[str, str, str]] = [
    ("0847291063", "SC-091", "AF7702"),
    ("2290184417", "ML-004", "EJU4517"),
    ("1234567890", "SC-103", "SN3012"),
    ("0987654321", "SC-101", "LH1234"),
    ("5561203984", "SC-117", "BA341"),
    ("7741029385", "ML-002", "TP662"),
    ("3390127744", "SC-088", "IB3201"),
    ("6612840390", "SC-112", "KL1728"),
    ("9081774213", "SC-004", "AF1234"),
]

# Share of the peak hourly rate per hour of day; mirrors console/simulation.py.
DAY_CURVE: tuple[float, ...] = (
    0.03, 0.02, 0.01, 0.01, 0.08, 0.30, 0.75, 1.00,
    0.95, 0.85, 0.75, 0.70, 0.72, 0.75, 0.78, 0.76,
    0.80, 0.88, 0.90, 0.72, 0.55, 0.35, 0.18, 0.08,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    """ISO 8601 UTC with a Z suffix, as in the assessment."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state() -> dict[str, str]:
    """Read state/sim.env, a plain KEY=VALUE file.

    Parsed rather than sourced: it is written by a web console, and executing
    it would turn a slider into a shell.
    """
    values = dict(DEFAULTS)
    try:
        content = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return values

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def as_float(values: dict[str, str], key: str) -> float:
    try:
        return float(values.get(key, DEFAULTS.get(key, "0")))
    except ValueError:
        return float(DEFAULTS.get(key, "0"))


def as_int(values: dict[str, str], key: str) -> int:
    return int(round(as_float(values, key)))


def day_factor(sim_hour: float) -> float:
    hour = sim_hour % 24.0
    low = int(hour)
    high = (low + 1) % 24
    weight = hour - low
    return DAY_CURVE[low] * (1.0 - weight) + DAY_CURVE[high] * weight


# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------

def payload_status(state: dict[str, str]) -> dict[str, Any]:
    frozen = state.get("TRACKER_FEED", "on") == "off"
    lag = timedelta(minutes=7) if frozen else timedelta(minutes=1)
    return {
        "bags_in_system": as_int(state, "BAGS_IN_SYSTEM"),
        "avg_dwell_minutes": round(as_float(state, "AVG_DWELL_MINUTES"), 1),
        "last_update": iso(now_utc() - lag),
    }


def stuck_dwells(count: int, phantom_bug: str) -> list[int]:
    """Dwell times, longest first.

    One bag sits past 120 minutes whenever there are enough of them, or
    whenever the phantom bug is on: that is the grey cell the dashboard uses
    to flag a bag nobody is going to find on a belt.
    """
    if count <= 0:
        return []
    head = [142] if phantom_bug == "active" or count >= 5 else []
    tail = [38, 30, 24, 20, 18, 17, 16, 16][: max(0, count - len(head))]
    while len(head) + len(tail) < count:
        tail.append(15)
    return (head + tail)[:count]


def payload_stuck(state: dict[str, str], threshold_minutes: int) -> list[dict[str, Any]]:
    now = now_utc()
    dwells = stuck_dwells(as_int(state, "STUCK_BAGS"), state.get("PHANTOM_BUG", "fixed"))
    rows = []
    for index, dwell in enumerate(dwells):
        if dwell <= threshold_minutes:
            continue
        tag, location, flight = STUCK_IDENTITIES[index % len(STUCK_IDENTITIES)]
        rows.append(
            {
                "tag_id": tag,
                "entry_time": iso(now - timedelta(minutes=dwell)),
                "location": location,
                "flight_id": flight,
                "dwell_minutes": dwell,
            }
        )
    return rows


def payload_throughput(state: dict[str, str], hours: int) -> list[dict[str, Any]]:
    """Hourly buckets, oldest first, shaped by the daily traffic curve."""
    peak = as_int(state, "ARRIVAL_RATE")
    sim_hour = as_float(state, "SIM_HOUR")
    span = max(1, min(hours, 48))
    top_of_hour = now_utc().replace(minute=0, second=0)
    return [
        {
            "hour": iso(top_of_hour - timedelta(hours=span - 1 - offset)),
            "count": int(round(peak * day_factor(sim_hour - (span - 1 - offset)))),
        }
        for offset in range(span)
    ]


def int_param(query: str, name: str, default: int) -> int:
    raw = parse_qs(query).get(name, [""])[0]
    try:
        return int(raw)
    except ValueError:
        return default


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BHSMockAPI/1.0"

    def do_GET(self) -> None:
        url = urlparse(self.path)
        state = read_state()

        if url.path in ("/", "/health"):
            self.respond(200, {"status": "up", "api_status": state.get("API_STATUS", "up")})
            return

        # Simulated outage: the API is unreachable, the SQL banner is not.
        if state.get("API_STATUS", "up") == "down":
            self.respond(503, {"error": "BHS API unavailable"})
            return

        if url.path == "/api/v1/status":
            self.respond(200, payload_status(state))
        elif url.path == "/api/v1/bags/stuck":
            threshold = int_param(url.query, "threshold_minutes", 15)
            self.respond(200, payload_stuck(state, threshold))
        elif url.path == "/api/v1/throughput":
            self.respond(200, payload_throughput(state, int_param(url.query, "hours", 24)))
        else:
            self.respond(404, {"error": "not found", "path": url.path})

    def respond(self, code: int, body: Any) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[mock-api] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"[mock-api] listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
