"""Pure simulation model for the BHS control-room test bench.

No I/O and no global state: every function maps settings to derived values.
The whole model can therefore be unit-tested on its own (see tests/), and the
console server stays a thin layer that reads, validates and persists.

The phantom-bag model is not hand-waved. It reproduces the actual defect of
use case 1: bag_tracker.py buffers events in a dict keyed on tag_id[:4] and
flushes every 10 events or 5 seconds. Two scans sharing their first four
digits inside one window overwrite each other, and the loser never reaches the
database. That is a birthday problem over 10 000 prefixes, which means loss
grows quadratically with throughput -- the single most useful thing this bench
can show a reader.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

# --------------------------------------------------------------------------
# Constants taken from the assessment itself
# --------------------------------------------------------------------------

# bag_tracker.py:80-83 -- buffer size, flush interval and the 4-digit key.
BATCH_SIZE: Final[int] = 10
FLUSH_INTERVAL_S: Final[float] = 5.0
PREFIX_SPACE: Final[int] = 10_000

# Scans emitted per bag along its journey (check-in, sorter, hand-off).
# Calibrated so that a full day at realistic volumes loses a handful of bags,
# matching the "5 bags out of 15 000" of the incident report.
SCANS_PER_BAG: Final[float] = 2.0

# Dashboard thresholds (dashboard.json), mirrored here so the console can
# announce in advance what a scenario will turn orange or red.
BAGS_WARN: Final[int] = 300
BAGS_CRIT: Final[int] = 450
DWELL_WARN: Final[float] = 8.0
DWELL_CRIT: Final[float] = 12.0
HEALTH_WARN: Final[int] = 1
HEALTH_CRIT: Final[int] = 25
STUCK_RED_MIN: Final[int] = 30
STUCK_GHOST_MIN: Final[int] = 120

# The freshness query flips the banner after 5 minutes without an insert.
STALE_AFTER_MIN: Final[int] = 5

# Simulator cadence, kept in sync with docker-compose.yml.
TICK_SECONDS: Final[int] = 5

# Wall-clock time is compressed, otherwise the phantom counter would take a
# working day to move: at realistic volumes the defect strands well under one
# bag per hour. At 720x one tick is one simulated hour, which is also exactly
# what a 2-minute day cycle runs at -- manual and automatic modes therefore
# age the system at the same speed.
DEMO_TIME_SCALE: Final[float] = 720.0

# Baseline gap between the API counter and the deduplicated SQL counter.
#
# Zero, and deliberately so. The SQL counter de-duplicates by tag_id and drops
# stranded bags, so in a healthy system it must equal what the API reports.
# Any other value would leave the dashboard's "data health" panel permanently
# amber -- its warning step is 1 -- and a panel that is always amber is a panel
# operators stop reading, which is exactly what §6 argues against.
BASELINE_API_SQL_GAP: Final[int] = 0

ENUMS: Final[dict[str, tuple[str, ...]]] = {
    "tracker_feed": ("on", "off"),
    "api_status": ("up", "down"),
    "phantom_bug": ("fixed", "active"),
    "day_cycle": ("off", "on"),
}

RANGES: Final[dict[str, tuple[float, float]]] = {
    "arrival_rate": (0, 4000),
    "dwell_minutes": (1.0, 25.0),
    "stuck_bags": (0, 20),
    "day_speed_min": (1, 30),
    "sim_hour": (0.0, 24.0),
}


@dataclass(frozen=True)
class Settings:
    """What the operator controls from the console."""

    scenario: str = "normal"
    arrival_rate: int = 1960          # bags per hour
    dwell_minutes: float = 4.2        # average time in system
    stuck_bags: int = 5               # bags over the 15-minute threshold
    tracker_feed: str = "on"          # "off" freezes inserts -> DATA STALE
    api_status: str = "up"            # "down" makes the API return 503
    phantom_bug: str = "fixed"        # "active" replays the use case 1 defect
    day_cycle: str = "off"            # server-side 24 h auto-play
    day_speed_min: int = 2            # wall-clock minutes for 24 simulated hours
    sim_hour: float = 8.0             # simulated hour of day, 0..24


@dataclass(frozen=True)
class Derived:
    """What the rest of the bench needs in order to act.

    The console computes all of it, so the shell simulator only ever applies
    integers and never carries business logic.
    """

    bags_in_system: int               # what the API reports
    avg_dwell_minutes: float
    target_active_sql: int            # what SQL should hold, deduplicated
    api_sql_gap: int
    lost_scans_per_hour: float        # use case 1, prefix collisions
    phantoms_per_hour: float
    phantom_rate_per_tick: float
    time_scale: float                 # simulated seconds per real second
    freeze_inserts: bool
    expected_banner: str              # OK | IDLE | STALE


# --------------------------------------------------------------------------
# Use case 1: prefix-collision loss
# --------------------------------------------------------------------------

def flush_batch_size(arrival_rate: int, scans_per_bag: float = SCANS_PER_BAG) -> float:
    """Number of events sitting in the buffer when it is flushed.

    The buffer goes out on whichever comes first: 10 events, or 5 seconds.
    """
    events_per_second = arrival_rate * scans_per_bag / 3600.0
    return min(float(BATCH_SIZE), events_per_second * FLUSH_INTERVAL_S)


def lost_scans_per_hour(arrival_rate: int, scans_per_bag: float = SCANS_PER_BAG) -> float:
    """Scans silently dropped by the dict buffer, per hour.

    With k events per flush, the expected number of colliding pairs is
    C(k, 2) / 10000. Multiplied by the number of flushes per hour this
    simplifies to events_per_hour * (k - 1) / (2 * 10000).
    """
    batch = flush_batch_size(arrival_rate, scans_per_bag)
    if batch <= 1.0:
        return 0.0
    events_per_hour = arrival_rate * scans_per_bag
    return events_per_hour * (batch - 1.0) / (2.0 * PREFIX_SPACE)


def phantoms_per_hour(arrival_rate: int, phantom_bug: str) -> float:
    """Bags left stranded IN_SYSTEM forever, per hour.

    Only a lost *final* scan strands a bag: an earlier lost scan merely
    widens the API/SQL gap. One scan in scans_per_bag is a final one.
    """
    if phantom_bug != "active":
        return 0.0
    return lost_scans_per_hour(arrival_rate) / SCANS_PER_BAG


# --------------------------------------------------------------------------
# Traffic model
# --------------------------------------------------------------------------

# Share of the peak hourly rate, per hour of the day. Shaped like an airport
# feed: a dead night, a sharp 07:00 departure bank, a softer evening one.
DAY_CURVE: Final[tuple[float, ...]] = (
    0.03, 0.02, 0.01, 0.01, 0.08, 0.30, 0.75, 1.00,   # 00:00 - 07:00
    0.95, 0.85, 0.75, 0.70, 0.72, 0.75, 0.78, 0.76,   # 08:00 - 15:00
    0.80, 0.88, 0.90, 0.72, 0.55, 0.35, 0.18, 0.08,   # 16:00 - 23:00
)


# Below this the belts are considered stopped for the night rather than
# trickling. Without it the curve never quite reaches zero, no hour is ever
# truly quiet, and the blue IDLE banner -- the state that says "nothing is
# late, nothing is broken" -- would never be reached during a day cycle.
NIGHT_FLOOR: Final[int] = 80


def day_factor(sim_hour: float) -> float:
    """Traffic multiplier at a given hour, interpolated between hours."""
    hour = sim_hour % 24.0
    low = int(hour)
    high = (low + 1) % 24
    weight = hour - low
    return DAY_CURVE[low] * (1.0 - weight) + DAY_CURVE[high] * weight


def congestion_dwell(factor: float, base: float = 3.5, span: float = 6.5) -> float:
    """Dwell time under load.

    Quadratic rather than linear: a belt near capacity queues up much faster
    than a half-empty one, which is what makes the 8-minute threshold trip
    before the 300-bag one during a rush.
    """
    return round(base + span * factor * factor, 1)


def bags_in_system(arrival_rate: int, dwell_minutes: float, stuck_bags: int) -> int:
    """Little's law: bags in system = arrival rate x time in system."""
    return int(round(arrival_rate / 60.0 * dwell_minutes)) + stuck_bags


def hourly_throughput(peak_rate: int, sim_hour: float) -> list[int]:
    """The 24 hourly buckets served by /api/v1/throughput, oldest first."""
    return [
        int(round(peak_rate * day_factor(sim_hour - offset)))
        for offset in range(23, -1, -1)
    ]


def stuck_bag_dwells(count: int, phantom_bug: str) -> list[int]:
    """Dwell times of the stuck-bag table, longest first.

    A stranded bag shows up well past the 120-minute mark, which is exactly
    what the grey cell in the dashboard is meant to flag.
    """
    if count <= 0:
        return []
    head = [142] if phantom_bug == "active" or count >= 5 else []
    tail = [38, 30, 24, 20, 18, 17, 16, 16, 15][: max(0, count - len(head))]
    while len(head) + len(tail) < count:
        tail.append(15)
    return (head + tail)[:count]


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------

def expected_banner(settings: Settings, target_active: int) -> str:
    """What the SQL freshness query (queries.sql, query 4) will return.

    OK while inserts keep coming. Once they stop, the distinction that matters
    is whether anything was still moving: nothing moving is a quiet night
    (IDLE), bags moving is a broken feed (STALE).
    """
    if settings.tracker_feed == "on" and settings.arrival_rate > 0:
        return "OK"
    return "IDLE" if target_active == 0 else "STALE"


def time_scale(settings: Settings) -> float:
    """How much faster than real time the bench ages.

    Tied to the day-cycle speed when it runs, so that switching auto-play on
    does not suddenly change how fast phantoms accumulate.
    """
    if settings.day_cycle == "on":
        return 24.0 * 60.0 / settings.day_speed_min
    return DEMO_TIME_SCALE


def derive(settings: Settings) -> Derived:
    """Everything the bench needs, computed from the operator's settings."""
    bags = bags_in_system(settings.arrival_rate, settings.dwell_minutes, settings.stuck_bags)

    # The gap tracks divergence between the two sources -- replication lag, a
    # wrong SQL query -- and nothing else. The prefix-collision bug does not
    # show up here: a stranded bag leaves the active SQL count (last scan over
    # 6 h old) and lands in the phantom counter instead, which is the panel's
    # other number.
    gap = BASELINE_API_SQL_GAP if bags > 0 else 0
    target_active = max(0, bags - gap)

    lost = lost_scans_per_hour(settings.arrival_rate) if settings.phantom_bug == "active" else 0.0
    phantoms = phantoms_per_hour(settings.arrival_rate, settings.phantom_bug)

    # Inserts stop when the feed is cut, and also when no bag is arriving:
    # both are needed for the banner to leave the OK state.
    freeze = settings.tracker_feed == "off" or settings.arrival_rate == 0

    scale = time_scale(settings)
    simulated_hours_per_tick = TICK_SECONDS * scale / 3600.0

    return Derived(
        bags_in_system=bags,
        avg_dwell_minutes=round(settings.dwell_minutes, 1),
        target_active_sql=target_active,
        api_sql_gap=gap,
        lost_scans_per_hour=round(lost, 2),
        phantoms_per_hour=round(phantoms, 2),
        phantom_rate_per_tick=round(phantoms * simulated_hours_per_tick, 4),
        time_scale=scale,
        freeze_inserts=freeze,
        expected_banner=expected_banner(settings, target_active),
    )


def advance_day(settings: Settings, elapsed_seconds: float) -> Settings:
    """Move the simulated clock forward and re-derive traffic from the curve.

    Arrival rate and dwell both follow the curve, so a full cycle walks the
    dashboard through idle nights, a rush that trips the orange thresholds,
    and back down -- without anyone touching a slider.
    """
    if settings.day_cycle != "on":
        return settings

    hours_per_second = 24.0 / (settings.day_speed_min * 60.0)
    hour = (settings.sim_hour + elapsed_seconds * hours_per_second) % 24.0
    factor = day_factor(hour)
    peak = SCENARIOS["morning_rush"].arrival_rate

    rate = int(round(peak * factor))
    if rate < NIGHT_FLOOR:
        rate = 0

    return replace(
        settings,
        sim_hour=round(hour, 3),
        arrival_rate=rate,
        dwell_minutes=congestion_dwell(factor),
        stuck_bags=int(round(12 * factor * factor)),
    )


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

SCENARIOS: Final[dict[str, Settings]] = {
    "normal": Settings(
        scenario="normal", arrival_rate=1960, dwell_minutes=4.2, stuck_bags=5,
    ),
    "morning_rush": Settings(
        scenario="morning_rush", arrival_rate=2600, dwell_minutes=8.5, stuck_bags=9,
    ),
    "system_jam": Settings(
        scenario="system_jam", arrival_rate=2600, dwell_minutes=13.0, stuck_bags=14,
    ),
    "night_idle": Settings(
        scenario="night_idle", arrival_rate=0, dwell_minutes=2.0, stuck_bags=0,
    ),
    "tracker_outage": Settings(
        scenario="tracker_outage", arrival_rate=1960, dwell_minutes=4.2, stuck_bags=5,
        tracker_feed="off",
    ),
    "api_outage": Settings(
        scenario="api_outage", arrival_rate=1960, dwell_minutes=4.2, stuck_bags=5,
        api_status="down",
    ),
    "phantom_storm": Settings(
        scenario="phantom_storm", arrival_rate=2600, dwell_minutes=8.5, stuck_bags=9,
        phantom_bug="active",
    ),
}


def clamp(name: str, value: float) -> float:
    low, high = RANGES[name]
    return max(low, min(high, value))


def sanitize(raw: dict[str, object], current: Settings) -> Settings:
    """Build settings from untrusted input.

    The console is a write endpoint whose values reach both a shell loop and
    sqlcmd. Every field is therefore either clamped to a range or matched
    against a closed set; anything unknown is dropped rather than passed
    through.
    """
    values: dict[str, object] = {}

    for name in ("arrival_rate", "stuck_bags", "day_speed_min"):
        if name in raw:
            try:
                values[name] = int(clamp(name, float(raw[name])))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass

    for name in ("dwell_minutes", "sim_hour"):
        if name in raw:
            try:
                values[name] = round(float(clamp(name, float(raw[name]))), 2)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass

    for name, allowed in ENUMS.items():
        if name in raw and raw[name] in allowed:
            values[name] = raw[name]

    scenario = raw.get("scenario")
    if isinstance(scenario, str) and (scenario in SCENARIOS or scenario == "custom"):
        values["scenario"] = scenario

    return replace(current, **values)  # type: ignore[arg-type]
