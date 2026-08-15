"""Tests for the bench simulation model.

Run from test-bench/:  python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "console"))

import simulation as sim  # noqa: E402


# --------------------------------------------------------------------------
# Use case 1: prefix-collision model
# --------------------------------------------------------------------------

def test_buffer_never_exceeds_the_batch_size() -> None:
    """The dict is flushed at 10 events, whatever the load."""
    assert sim.flush_batch_size(100_000) == sim.BATCH_SIZE


def test_quiet_traffic_loses_nothing() -> None:
    """Fewer than two events per window means nothing can collide."""
    assert sim.lost_scans_per_hour(100) == 0.0


def test_loss_grows_faster_than_traffic() -> None:
    """Doubling the rate more than doubles the loss.

    This is the point of the whole exercise: the defect is invisible at night
    and bites during the morning bank.
    """
    single = sim.lost_scans_per_hour(1000)
    double = sim.lost_scans_per_hour(2000)
    assert double > 2 * single > 0


def test_loss_matches_the_incident_report_order_of_magnitude() -> None:
    """A realistic day should strand a handful of bags, not none and not 500.

    The incident describes roughly 5 bags out of 15 000. Nothing here is
    tuned to hit that number directly: it falls out of the birthday model
    applied to the daily traffic curve, which is what makes it credible.
    """
    peak = sim.SCENARIOS["morning_rush"].arrival_rate
    daily_phantoms = sum(
        sim.phantoms_per_hour(int(round(peak * sim.day_factor(hour))), "active")
        for hour in range(24)
    )
    assert 2 <= daily_phantoms <= 20


def test_fixed_bug_strands_no_bag() -> None:
    assert sim.phantoms_per_hour(3000, "fixed") == 0.0
    assert sim.derive(sim.SCENARIOS["morning_rush"]).phantoms_per_hour == 0.0


def test_active_bug_strands_bags_at_the_same_traffic() -> None:
    storm = sim.derive(sim.SCENARIOS["phantom_storm"])
    calm = sim.derive(sim.SCENARIOS["morning_rush"])
    assert storm.phantoms_per_hour > 0
    assert calm.phantoms_per_hour == 0
    # Same traffic, only the switch differs.
    assert storm.bags_in_system == calm.bags_in_system


# --------------------------------------------------------------------------
# Traffic model
# --------------------------------------------------------------------------

def test_bags_in_system_follows_littles_law() -> None:
    # 1200 bags/h for 5 min in system = 100 bags, plus the stuck ones.
    assert sim.bags_in_system(1200, 5.0, 7) == 107


def test_normal_scenario_reproduces_the_documented_figures() -> None:
    """The nominal screenshots show 142 bags and 4.2 minutes."""
    derived = sim.derive(sim.SCENARIOS["normal"])
    assert derived.bags_in_system == 142
    assert derived.avg_dwell_minutes == 4.2


def test_healthy_system_has_the_two_sources_agreeing() -> None:
    """Below the panel's amber step, otherwise it warns during normal work.

    The SQL counter de-duplicates and excludes stranded bags, so there is no
    reason for it to differ from the API when nothing is wrong.
    """
    derived = sim.derive(sim.SCENARIOS["normal"])
    assert derived.api_sql_gap < sim.HEALTH_WARN
    assert derived.target_active_sql == derived.bags_in_system


def test_the_bug_shows_up_as_phantoms_not_as_a_gap() -> None:
    """A stranded bag leaves the active count; it does not widen the gap."""
    storm = sim.derive(sim.SCENARIOS["phantom_storm"])
    assert storm.phantoms_per_hour > 0
    assert storm.api_sql_gap < sim.HEALTH_WARN


def test_day_curve_is_continuous_across_midnight() -> None:
    assert sim.day_factor(23.99) == pytest.approx(sim.day_factor(0.0), abs=0.05)


def test_night_is_quiet_and_morning_peaks() -> None:
    assert sim.day_factor(3.0) < 0.05
    assert sim.day_factor(7.0) == 1.0


def test_congestion_raises_dwell_faster_than_load() -> None:
    """Half load must not mean half the queueing."""
    assert sim.congestion_dwell(1.0) > 2 * (sim.congestion_dwell(0.5) - 3.5) + 3.5


# --------------------------------------------------------------------------
# Dashboard thresholds: each scenario must land where it claims
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, floor, ceiling",
    [
        ("normal", 0, sim.BAGS_WARN - 1),               # green
        ("morning_rush", sim.BAGS_WARN, sim.BAGS_CRIT), # orange
        ("system_jam", sim.BAGS_CRIT, 10_000),          # red
    ],
)
def test_scenarios_cross_the_intended_bag_thresholds(name: str, floor: int, ceiling: int) -> None:
    bags = sim.derive(sim.SCENARIOS[name]).bags_in_system
    assert floor <= bags <= ceiling


def test_rush_trips_the_dwell_threshold_before_the_volume_one() -> None:
    """Dwell is the earlier warning: bags queueing before the belt fills up."""
    rush = sim.SCENARIOS["morning_rush"]
    assert rush.dwell_minutes >= sim.DWELL_WARN


def test_jam_is_critical_on_both_counters() -> None:
    jam = sim.SCENARIOS["system_jam"]
    assert jam.dwell_minutes >= sim.DWELL_CRIT
    assert sim.derive(jam).bags_in_system >= sim.BAGS_CRIT


# --------------------------------------------------------------------------
# The banner: the distinction that is easiest to get wrong
# --------------------------------------------------------------------------

def test_banner_is_ok_while_the_feed_runs() -> None:
    assert sim.derive(sim.SCENARIOS["normal"]).expected_banner == "OK"


def test_quiet_night_is_idle_not_stale() -> None:
    """No insert and no bag moving is a quiet night, not a broken feed."""
    derived = sim.derive(sim.SCENARIOS["night_idle"])
    assert derived.target_active_sql == 0
    assert derived.expected_banner == "IDLE"


def test_cut_feed_with_bags_moving_is_stale() -> None:
    derived = sim.derive(sim.SCENARIOS["tracker_outage"])
    assert derived.target_active_sql > 0
    assert derived.expected_banner == "STALE"
    assert derived.freeze_inserts is True


def test_api_outage_leaves_the_sql_banner_untouched() -> None:
    """The whole point of backing the banner with SQL rather than the API."""
    assert sim.derive(sim.SCENARIOS["api_outage"]).expected_banner == "OK"


# --------------------------------------------------------------------------
# Time compression
# --------------------------------------------------------------------------

def test_one_tick_is_one_simulated_hour_by_default() -> None:
    """Without it a phantom storm would take a working day to show up."""
    settings = sim.SCENARIOS["phantom_storm"]
    derived = sim.derive(settings)
    assert derived.phantom_rate_per_tick == pytest.approx(derived.phantoms_per_hour, rel=0.01)


def test_day_cycle_speed_drives_the_same_clock() -> None:
    """Turning auto-play on must not change how fast the system ages."""
    manual = sim.derive(sim.Settings(day_cycle="off"))
    auto = sim.derive(sim.Settings(day_cycle="on", day_speed_min=2))
    assert manual.time_scale == auto.time_scale


def test_a_slower_cycle_ages_more_slowly() -> None:
    fast = sim.derive(sim.Settings(day_cycle="on", day_speed_min=2)).time_scale
    slow = sim.derive(sim.Settings(day_cycle="on", day_speed_min=10)).time_scale
    assert slow == pytest.approx(fast / 5)


def test_storm_crosses_the_red_health_threshold_in_a_demo_minute() -> None:
    """25 phantoms is the red step; it has to be reachable while watching."""
    rate = sim.derive(sim.SCENARIOS["phantom_storm"]).phantom_rate_per_tick
    ticks_per_minute = 60 / sim.TICK_SECONDS
    assert rate * ticks_per_minute * 5 >= sim.HEALTH_CRIT


# --------------------------------------------------------------------------
# Auto-play
# --------------------------------------------------------------------------

def test_day_cycle_off_changes_nothing() -> None:
    settings = sim.SCENARIOS["normal"]
    assert sim.advance_day(settings, 60.0) == settings


def test_day_cycle_walks_the_clock_and_the_traffic() -> None:
    start = sim.Settings(day_cycle="on", day_speed_min=2, sim_hour=3.0)
    # A quarter of the 2-minute cycle is 6 simulated hours: 03:00 -> 09:00.
    later = sim.advance_day(start, 30.0)
    assert later.sim_hour == pytest.approx(9.0, abs=0.1)
    assert later.arrival_rate > start.arrival_rate


def test_day_cycle_reaches_a_genuinely_idle_night() -> None:
    """The blue IDLE banner has to be reachable without touching a slider.

    The raw curve never hits zero, so a night would read as light traffic and
    the banner would stay green all cycle long.
    """
    night = sim.advance_day(sim.Settings(day_cycle="on", sim_hour=3.0), 0.0)
    derived = sim.derive(night)
    assert night.arrival_rate == 0
    assert derived.target_active_sql == 0
    assert derived.expected_banner == "IDLE"


def test_day_cycle_peak_is_busy() -> None:
    peak = sim.advance_day(sim.Settings(day_cycle="on", sim_hour=7.0), 0.0)
    assert peak.arrival_rate >= sim.NIGHT_FLOOR * 20
    assert sim.derive(peak).expected_banner == "OK"


def test_day_cycle_wraps_past_midnight() -> None:
    start = sim.Settings(day_cycle="on", day_speed_min=2, sim_hour=23.0)
    assert sim.advance_day(start, 10.0).sim_hour < 23.0


# --------------------------------------------------------------------------
# Input handling: the console writes values that reach a shell and sqlcmd
# --------------------------------------------------------------------------

def test_out_of_range_values_are_clamped() -> None:
    result = sim.sanitize({"arrival_rate": 99_999, "dwell_minutes": -5}, sim.Settings())
    assert result.arrival_rate == sim.RANGES["arrival_rate"][1]
    assert result.dwell_minutes == sim.RANGES["dwell_minutes"][0]


def test_unknown_enum_values_are_ignored() -> None:
    result = sim.sanitize({"tracker_feed": "; rm -rf /"}, sim.Settings(tracker_feed="on"))
    assert result.tracker_feed == "on"


def test_unknown_keys_never_reach_the_settings() -> None:
    result = sim.sanitize({"__class__": "boom", "cmd": "whoami"}, sim.Settings())
    assert result == sim.Settings()


def test_non_numeric_input_is_dropped_not_crashing() -> None:
    result = sim.sanitize({"arrival_rate": "abc", "stuck_bags": None}, sim.Settings())
    assert result == sim.Settings()


def test_known_scenario_names_pass_through() -> None:
    assert sim.sanitize({"scenario": "phantom_storm"}, sim.Settings()).scenario == "phantom_storm"
    assert sim.sanitize({"scenario": "nope"}, sim.Settings()).scenario == "normal"
