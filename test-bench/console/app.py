"""Control console for the BHS test bench.

Serves the operator UI and owns state/sim.env, the single file the rest of the
bench reads: the mocked API answers from it, and the simulator drives SQL
Server from it.

Standard library only, like the rest of the bench: no build step, no wheel to
download, nothing to keep up to date.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import simulation as sim

PORT = int(os.environ.get("PORT", "8090"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/state/sim.env"))
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))
GRAFANA_PORT = os.environ.get("GRAFANA_PORT", "3000")
GRAFANA_PUBLIC_URL = os.environ.get("GRAFANA_PUBLIC_URL", "")
DASHBOARD_PATH = "/d/bhs-controlroom/salle-de-controle?kiosk&refresh=5s"

# Optional shared secret. Empty (the default) leaves the console open, which
# is what you want on a laptop bound to 127.0.0.1. Set it before exposing the
# bench on a public host.
TOKEN = os.environ.get("CONSOLE_TOKEN", "")

AUTOPLAY_TICK_S = 2.0

_lock = threading.Lock()
_settings = sim.Settings()
_purge_counter = 0

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


# --------------------------------------------------------------------------
# state/sim.env
# --------------------------------------------------------------------------

HEADER = """# Written by the console. Read by mock-api (python) and by the simulator
# (bash, via grep). Never `source` this file: it is written from a web UI.
"""


def write_state(settings: sim.Settings, purge_counter: int) -> None:
    """Persist settings and everything derived from them, atomically.

    The simulator reads this file every 5 seconds; a half-written file would
    make it act on a truncated value, so the write goes through a temporary
    file on the same volume and an atomic replace.
    """
    derived = sim.derive(settings)
    lines = [HEADER]

    for key, value in asdict(settings).items():
        lines.append(f"{key.upper()}={value}")

    lines.append("")
    lines.append("# derived by the console -- the simulator only applies these")
    for key, value in asdict(derived).items():
        if isinstance(value, bool):
            value = "on" if value else "off"
        lines.append(f"{key.upper()}={value}")

    lines.append(f"PURGE_REQUEST={purge_counter}")
    lines.append("")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(".sim.env.tmp")
    # Bytes, not write_text: on Windows the text layer would turn every \n
    # into \r\n, and the shell simulator would then hand sqlcmd values with a
    # trailing carriage return.
    tmp.write_bytes("\n".join(lines).encode("utf-8"))
    os.replace(tmp, STATE_FILE)


def load_state() -> tuple[sim.Settings, int]:
    """Re-read the file at boot so a restart does not reset the demo."""
    if not STATE_FILE.exists():
        return sim.Settings(), 0

    raw: dict[str, Any] = {}
    purge = 0
    for line in STATE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "PURGE_REQUEST":
            purge = int(value) if value.isdigit() else 0
        raw[key.lower()] = value

    return sim.sanitize(raw, sim.Settings()), purge


# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------

# Notes kept to one short line: the console shows them in a narrow column and
# truncates anything longer, and the chips already carry the figures.
SCENARIO_LABELS: dict[str, dict[str, str]] = {
    "normal": {
        "title": "Normal operations",
        "note": "The nominal screenshots. Everything green.",
    },
    "morning_rush": {
        "title": "Morning rush",
        "note": "Departure bank. Dwell trips before volume.",
    },
    "system_jam": {
        "title": "System jam",
        "note": "Blocked sorter. Both counters red.",
    },
    "night_idle": {
        "title": "Night — idle",
        "note": "Nothing moving. Blue, not red: nothing is late.",
    },
    "tracker_outage": {
        "title": "Tracker outage",
        "note": "Inserts stopped, bags still in flight.",
    },
    "api_outage": {
        "title": "BHS API outage",
        "note": "API down, the SQL banner holds.",
    },
    "phantom_storm": {
        "title": "Phantom storm (UC1)",
        "note": "Prefix-collision bug on. Phantoms climb.",
    },
}


def scenario_payload() -> list[dict[str, Any]]:
    items = []
    for name, settings in sim.SCENARIOS.items():
        derived = sim.derive(settings)
        items.append(
            {
                "name": name,
                "title": SCENARIO_LABELS[name]["title"],
                "note": SCENARIO_LABELS[name]["note"],
                "bags": derived.bags_in_system,
                "dwell": derived.avg_dwell_minutes,
                "banner": derived.expected_banner,
                "phantoms_per_hour": derived.phantoms_per_hour,
            }
        )
    return items


def state_payload(settings: sim.Settings) -> dict[str, Any]:
    derived = sim.derive(settings)
    return {
        "settings": asdict(settings),
        "derived": asdict(derived),
        "throughput": sim.hourly_throughput(settings.arrival_rate, settings.sim_hour),
        "stuck_dwells": sim.stuck_bag_dwells(settings.stuck_bags, settings.phantom_bug),
        "batch_size": round(sim.flush_batch_size(settings.arrival_rate), 2),
        "thresholds": {
            "bags": [sim.BAGS_WARN, sim.BAGS_CRIT],
            "dwell": [sim.DWELL_WARN, sim.DWELL_CRIT],
            "health": [sim.HEALTH_WARN, sim.HEALTH_CRIT],
            "stuck": [sim.STUCK_RED_MIN, sim.STUCK_GHOST_MIN],
        },
        "scenarios": scenario_payload(),
        "grafana": {
            "port": GRAFANA_PORT,
            "public_url": GRAFANA_PUBLIC_URL,
            "path": DASHBOARD_PATH,
        },
    }


# --------------------------------------------------------------------------
# Auto-play
# --------------------------------------------------------------------------

def autoplay_loop() -> None:
    """Advance the simulated clock server-side.

    Deliberately not in the browser: the demo has to keep running with the tab
    closed, so that someone opening only the dashboard still sees a day go by.
    """
    global _settings
    last = time.monotonic()
    while True:
        time.sleep(AUTOPLAY_TICK_S)
        now = time.monotonic()
        elapsed, last = now - last, now

        with _lock:
            if _settings.day_cycle != "on":
                continue
            _settings = sim.advance_day(_settings, elapsed)
            write_state(_settings, _purge_counter)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BHSConsole/1.0"

    # -- helpers ----------------------------------------------------------

    def send_json(self, code: int, body: Any) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 64_000:
            return {}
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def authorised(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-Console-Token", "") == TOKEN

    def serve_static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / name).resolve()

        # Path traversal guard: refuse anything that escapes the static dir.
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_json(404, {"error": "not found"})
            return

        raw = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/state":
            with _lock:
                self.send_json(200, state_payload(_settings))
        elif route == "/health":
            self.send_json(200, {"status": "up"})
        else:
            self.serve_static(route)

    def do_POST(self) -> None:
        global _settings, _purge_counter
        route = urlparse(self.path).path

        if not self.authorised():
            self.send_json(403, {"error": "invalid console token"})
            return

        body = self.read_json()

        with _lock:
            if route == "/api/state":
                # Any manual change means the preset no longer describes what
                # is on screen, so the scenario becomes "custom".
                merged = sim.sanitize(body, _settings)
                if merged != _settings and "scenario" not in body:
                    merged = replace(merged, scenario="custom")
                _settings = merged

            elif route == "/api/scenario":
                name = body.get("name")
                if name not in sim.SCENARIOS:
                    self.send_json(400, {"error": "unknown scenario"})
                    return
                # A preset is a full reset, but the day cycle is a viewing
                # mode rather than a traffic setting: keep it running.
                _settings = replace(
                    sim.SCENARIOS[name],
                    day_cycle=_settings.day_cycle,
                    day_speed_min=_settings.day_speed_min,
                    sim_hour=_settings.sim_hour,
                )

            elif route == "/api/purge-phantoms":
                # Bumping the counter is what the simulator watches; doing it
                # this way makes the request idempotent and crash-safe.
                _purge_counter += 1

            else:
                self.send_json(404, {"error": "not found"})
                return

            write_state(_settings, _purge_counter)
            self.send_json(200, state_payload(_settings))

    def log_message(self, fmt: str, *args: Any) -> None:
        if "/api/state" in (args[0] if args else ""):
            return  # the UI polls; do not drown the log
        print(f"[console] {fmt % args}", flush=True)


def main() -> None:
    global _settings, _purge_counter
    _settings, _purge_counter = load_state()
    write_state(_settings, _purge_counter)

    threading.Thread(target=autoplay_loop, daemon=True).start()

    guard = "token required" if TOKEN else "open (bind to localhost only)"
    print(f"[console] listening on 0.0.0.0:{PORT}, {guard}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
