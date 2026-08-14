import json
import sqlite3
import threading
from pathlib import Path

import pytest

import bag_tracker_fixed
from bag_tracker_fixed import BagTracker


def raw_event(
    tag_id: str,
    *,
    location: str = "SC-101",
    timestamp: str = "2026-08-11T10:22:14.112",
) -> str:
    return json.dumps(
        {
            "tag_id": tag_id,
            "location": location,
            "timestamp": timestamp,
            "flight_id": "SN3012",
            "destination": "PIER-A-03",
        }
    )


def stored_scans(db_path) -> list[tuple[str, str]]:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT tag_id, location FROM bag_tracking ORDER BY id"
        ).fetchall()


def test_two_bags_with_the_same_prefix_are_both_persisted(tmp_path):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))

    tracker._on_message(raw_event("8594031301", location="SC-101"))
    tracker._on_message(raw_event("8594031302", location="SC-102"))
    tracker._flush_to_database()

    assert stored_scans(db_path) == [
        ("8594031301", "SC-101"),
        ("8594031302", "SC-102"),
    ]


def test_two_scans_of_the_same_bag_are_both_persisted(tmp_path):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))

    tracker._on_message(raw_event("8594031301", location="SC-101"))
    tracker._on_message(
        raw_event(
            "8594031301",
            location="SC-205",
            timestamp="2026-08-11T10:25:00.000",
        )
    )
    tracker._flush_to_database()

    assert stored_scans(db_path) == [
        ("8594031301", "SC-101"),
        ("8594031301", "SC-205"),
    ]


def test_batch_size_counts_events_instead_of_unique_prefixes(tmp_path):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))

    for suffix in range(10):
        tracker._on_message(raw_event(f"63728191{suffix:02d}"))

    assert len(stored_scans(db_path)) == 10


def test_all_provided_sample_events_are_persisted(tmp_path):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))
    sample_path = Path(__file__).parents[2] / "sample_events.json"
    events = json.loads(sample_path.read_text(encoding="utf-8"))

    for event in events:
        tracker._on_message(json.dumps(event))
    tracker._flush_to_database()

    assert [tag_id for tag_id, _ in stored_scans(db_path)] == [
        event["tag_id"] for event in events
    ]


def test_failed_flush_restores_events_to_buffer(tmp_path, monkeypatch):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))
    tracker._on_message(raw_event("8594031301"))
    real_connect = bag_tracker_fixed.sqlite3.connect

    def failing_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(bag_tracker_fixed.sqlite3, "connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="database unavailable"):
        tracker._flush_to_database()

    monkeypatch.setattr(bag_tracker_fixed.sqlite3, "connect", real_connect)
    tracker._flush_to_database()

    assert stored_scans(db_path) == [("8594031301", "SC-101")]


def test_event_arriving_during_flush_is_not_cleared(tmp_path, monkeypatch):
    db_path = tmp_path / "bags.db"
    tracker = BagTracker(str(db_path))
    tracker._on_message(raw_event("8594031301"))

    real_connect = bag_tracker_fixed.sqlite3.connect
    insert_started = threading.Event()
    allow_insert = threading.Event()
    block_once = threading.Event()

    class BlockingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def executemany(self, statement, parameters):
            if not block_once.is_set():
                block_once.set()
                insert_started.set()
                assert allow_insert.wait(timeout=2)
            return self._cursor.executemany(statement, parameters)

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    class BlockingConnection:
        def __init__(self, connection):
            self._connection = connection

        def cursor(self):
            return BlockingCursor(self._connection.cursor())

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    def blocking_connect(*args, **kwargs):
        return BlockingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(bag_tracker_fixed.sqlite3, "connect", blocking_connect)
    flush_thread = threading.Thread(target=tracker._flush_to_database)
    flush_thread.start()

    assert insert_started.wait(timeout=2)
    tracker._on_message(raw_event("9605142401", location="SC-103"))
    allow_insert.set()
    flush_thread.join(timeout=2)
    assert not flush_thread.is_alive()

    tracker._flush_to_database()

    assert stored_scans(db_path) == [
        ("8594031301", "SC-101"),
        ("9605142401", "SC-103"),
    ]
