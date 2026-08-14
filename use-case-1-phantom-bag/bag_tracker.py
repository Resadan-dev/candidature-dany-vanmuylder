"""
Bag Tracker Service
===================
Receives PLC scan events from a message queue and tracks bags in a SQLite database.

This service is the core of the baggage tracking system. When a bag passes a scanner,
the PLC sends an event to the message queue. This service consumes those events and
maintains the tracking database.

Author: EDAS Team
Version: 2.3.1
"""

import sqlite3
import json
import time
import threading
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from queue import Queue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class BagEvent:
    """Represents a bag scan event from the PLC."""
    tag_id: str           # 10-digit license plate (e.g., "0123456789")
    location: str         # Scanner location code (e.g., "SC-101")
    timestamp: datetime   # When the scan occurred
    flight_id: str        # Associated flight (e.g., "SN3012")
    destination: str      # Sort destination (e.g., "PIER-A-03")


class MessageQueueClient:
    """Simulates a message queue client (RabbitMQ/Kafka-like interface)."""
    
    def __init__(self):
        self._queue = Queue()
        self._running = False
    
    def connect(self, host: str, port: int) -> bool:
        logger.info(f"Connected to message queue at {host}:{port}")
        self._running = True
        return True
    
    def consume(self, callback):
        """Consume messages and call the callback for each one."""
        while self._running:
            try:
                message = self._queue.get(timeout=1.0)
                callback(message)
            except:
                continue
    
    def publish(self, message: str):
        """Publish a message (used for testing)."""
        self._queue.put(message)
    
    def close(self):
        self._running = False


class BagTracker:
    """
    Main tracking service. Receives PLC events and maintains the bag database.
    """
    
    def __init__(self, db_path: str = "bags.db"):
        self.db_path = db_path
        self.mq_client = MessageQueueClient()
        self._pending_bags = {}  # Buffer for batch processing
        self._batch_size = 10
        self._last_flush = time.time()
        self._flush_interval = 5.0  # seconds
        
        self._init_database()
    
    def _init_database(self):
        """Initialize the SQLite database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bag_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag_id TEXT NOT NULL,
                location TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                flight_id TEXT,
                destination TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tag_id ON bag_tracking(tag_id)")
        conn.commit()
        conn.close()
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
                destination=data.get("destination", "UNKNOWN")
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse message: {e}")
            return None
    
    def _generate_batch_key(self, event: BagEvent) -> str:
        """Generate a key for batching events. Uses tag prefix for grouping."""
        # Group by first 4 digits of tag for efficient batching
        return event.tag_id[:4]
    
    def _on_message(self, raw_message: str):
        """Handle an incoming message from the queue."""
        event = self._parse_event(raw_message)
        if event is None:
            return
        
        logger.info(f"Received scan: tag={event.tag_id} location={event.location}")
        
        # Add to pending batch
        batch_key = self._generate_batch_key(event)
        self._pending_bags[batch_key] = event
        
        # Check if we should flush
        if len(self._pending_bags) >= self._batch_size:
            self._flush_to_database()
        elif time.time() - self._last_flush > self._flush_interval:
            self._flush_to_database()
    
    def _flush_to_database(self):
        """Write pending bags to the database."""
        if not self._pending_bags:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        count = 0
        for batch_key, event in self._pending_bags.items():
            cursor.execute("""
                INSERT INTO bag_tracking (tag_id, location, timestamp, flight_id, destination)
                VALUES (?, ?, ?, ?, ?)
            """, (
                event.tag_id,
                event.location,
                event.timestamp.isoformat(),
                event.flight_id,
                event.destination
            ))
            count += 1
        
        conn.commit()
        conn.close()
        
        logger.info(f"Flushed {count} bags to database")
        self._pending_bags.clear()
        self._last_flush = time.time()
    
    def start(self, mq_host: str = "localhost", mq_port: int = 5672):
        """Start the tracking service."""
        logger.info("Starting Bag Tracker Service v2.3.1")
        
        if not self.mq_client.connect(mq_host, mq_port):
            logger.error("Failed to connect to message queue")
            return False
        
        # Start background flush timer
        def periodic_flush():
            while True:
                time.sleep(self._flush_interval)
                self._flush_to_database()
        
        flush_thread = threading.Thread(target=periodic_flush, daemon=True)
        flush_thread.start()
        
        # Start consuming messages
        logger.info("Listening for PLC events...")
        self.mq_client.consume(self._on_message)
        
        return True
    
    def stop(self):
        """Stop the service gracefully."""
        logger.info("Shutting down...")
        self._flush_to_database()
        self.mq_client.close()
        logger.info("Shutdown complete")


# For testing purposes
if __name__ == "__main__":
    tracker = BagTracker("test_bags.db")
    
    # Simulate some events
    test_events = [
        '{"tag_id": "0123456789", "location": "SC-101", "timestamp": "2026-08-11T10:30:01", "flight_id": "SN3012", "destination": "PIER-A-03"}',
        '{"tag_id": "0123456790", "location": "SC-101", "timestamp": "2026-08-11T10:30:02", "flight_id": "SN3012", "destination": "PIER-A-03"}',
        '{"tag_id": "0123456791", "location": "SC-102", "timestamp": "2026-08-11T10:30:03", "flight_id": "LH1234", "destination": "PIER-B-07"}',
    ]
    
    for event in test_events:
        tracker._on_message(event)
    
    tracker._flush_to_database()
    
    # Verify
    conn = sqlite3.connect("test_bags.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM bag_tracking")
    count = cursor.fetchone()[0]
    print(f"Total bags in database: {count}")
    conn.close()
