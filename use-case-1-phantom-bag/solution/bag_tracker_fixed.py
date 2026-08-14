"""
Bag Tracker Service - corrected implementation.

Every valid PLC scan is buffered as a distinct event. Database flushes are
serialized and operate on an atomic snapshot so that concurrent arrivals are
neither overwritten nor cleared before being persisted.
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Queue
from typing import Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class BagEvent:
    """A bag scan event produced by a PLC."""

    tag_id: str
    location: str
    timestamp: datetime
    flight_id: str
    destination: str


class MessageQueueClient:
    """Small in-memory message queue adapter used by this exercise."""

    def __init__(self):
        self._queue = Queue()
        self._running = False

    def connect(self, host: str, port: int) -> bool:
        logger.info("Connected to message queue at %s:%s", host, port)
        self._running = True
        return True

    def consume(self, callback):
        """Consume messages until the client is closed."""
        while self._running:
            try:
                message = self._queue.get(timeout=1.0)
                callback(message)
            except Empty:
                continue
            except Exception:
                logger.exception("Failed to process message")

    def publish(self, message: str):
        """Publish a message (used for testing)."""
        self._queue.put(message)

    def close(self):
        self._running = False


class BagTracker:
    """Consume PLC scan events and persist their complete history."""

    def __init__(self, db_path: str = "bags.db"):
        self.db_path = db_path
        self.mq_client = MessageQueueClient()
        self._pending_events: list[BagEvent] = []
        self._pending_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._batch_size = 10
        self._last_flush = time.monotonic()
        self._flush_interval = 5.0

        self._init_database()

    def _init_database(self):
        """Initialize the SQLite database schema."""
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bag_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tag_id TEXT NOT NULL,
                    location TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    flight_id TEXT,
                    destination TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tag_id ON bag_tracking(tag_id)"
            )
        logger.info("Database initialized")

    def _parse_event(self, raw_message: str) -> Optional[BagEvent]:
        """Parse a raw JSON message into a BagEvent."""
        try:
            data = json.loads(raw_message)
            return BagEvent(
                tag_id=data["tag_id"],
                location=data["location"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                flight_id=data.get("flight_id", "UNKNOWN"),
                destination=data.get("destination", "UNKNOWN"),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            logger.error("Failed to parse message: %s", error)
            return None

    def _on_message(self, raw_message: str):
        """Buffer one incoming scan and flush when size or time requires it."""
        event = self._parse_event(raw_message)
        if event is None:
            return

        logger.info(
            "Received scan: tag=%s location=%s", event.tag_id, event.location
        )

        with self._pending_lock:
            self._pending_events.append(event)
            batch_is_full = len(self._pending_events) >= self._batch_size

        flush_is_due = time.monotonic() - self._last_flush >= self._flush_interval
        if batch_is_full or flush_is_due:
            self._flush_to_database()

    def _flush_to_database(self) -> int:
        """Atomically detach and persist all pending events.

        New scans can enter a fresh buffer while SQLite is writing the detached
        snapshot. If persistence fails, the snapshot is restored ahead of those
        newer scans so that no accepted event is silently lost.
        """
        with self._flush_lock:
            with self._pending_lock:
                if not self._pending_events:
                    self._last_flush = time.monotonic()
                    return 0
                events_to_flush = self._pending_events
                self._pending_events = []

            try:
                parameters = []
                for event in events_to_flush:
                    row = (
                        event.tag_id,
                        event.location,
                        event.timestamp.isoformat(),
                        event.flight_id,
                        event.destination,
                    )
                    parameters.append(row)

                with sqlite3.connect(self.db_path) as connection:
                    cursor = connection.cursor()
                    cursor.executemany(
                        """
                        INSERT INTO bag_tracking
                            (tag_id, location, timestamp, flight_id, destination)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        parameters,
                    )
            except Exception:
                with self._pending_lock:
                    self._pending_events = events_to_flush + self._pending_events
                logger.exception(
                    "Failed to flush %s events; restored to the in-memory buffer",
                    len(events_to_flush),
                )
                raise

            self._last_flush = time.monotonic()
            logger.info("Flushed %s events to database", len(events_to_flush))
            return len(events_to_flush)

    def _periodic_flush(self):
        """Flush periodically without letting a transient failure kill the timer."""
        while not self._stop_event.wait(self._flush_interval):
            try:
                self._flush_to_database()
            except Exception:
                # _flush_to_database logs the error and restores the snapshot.
                continue

    def start(self, mq_host: str = "localhost", mq_port: int = 5672) -> bool:
        """Start the tracking service."""
        logger.info("Starting Bag Tracker Service v2.3.2")

        if not self.mq_client.connect(mq_host, mq_port):
            logger.error("Failed to connect to message queue")
            return False

        self._stop_event.clear()
        flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        flush_thread.start()

        logger.info("Listening for PLC events...")
        self.mq_client.consume(self._on_message)
        return True

    def stop(self):
        """Stop consumption and persist the final pending events."""
        logger.info("Shutting down...")
        self.mq_client.close()
        self._stop_event.set()
        self._flush_to_database()
        logger.info("Shutdown complete")
