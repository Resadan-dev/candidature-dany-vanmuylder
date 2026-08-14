# System Architecture

## Overview

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌──────────────┐
│    PLC      │────▶│  Message Queue  │────▶│  Bag Tracker    │────▶│   SQLite DB  │
│  Scanners   │     │   (RabbitMQ)    │     │    Service      │     │              │
└─────────────┘     └─────────────────┘     └─────────────────┘     └──────────────┘
     │                                              │
     │                                              ▼
     │                                      ┌──────────────┐
     │                                      │   Log File   │
     │                                      └──────────────┘
     │
     ▼
┌─────────────┐
│  Scanner    │
│  Locations  │
│  SC-101     │
│  SC-102     │
│  SC-103     │
│  ...        │
└─────────────┘
```

## Components

### PLC Scanners
- Physical barcode/RFID scanners at sortation points
- Send events to message queue within 10-50ms of scan
- Event format: JSON with tag_id, location, timestamp, flight_id, destination

### Message Queue (RabbitMQ)
- Durable queue with at-least-once delivery
- Peak throughput: ~50 events/second
- Average throughput: ~4 events/second (15,000 bags / 3,600 seconds)

### Bag Tracker Service
- Python service consuming from the queue
- Batches writes for efficiency (batch size: 10 or 5-second timeout)
- Single instance (no horizontal scaling currently)

### SQLite Database
- Local file database
- Schema: `bag_tracking` table with tag_id, location, timestamp, flight_id, destination
- Indexed on tag_id for fast lookups

## Data Flow

1. Bag passes scanner SC-101
2. PLC generates scan event with 10-digit tag ID
3. Event published to RabbitMQ queue
4. Bag Tracker consumes event
5. Event buffered in memory (pending_bags dict)
6. On batch threshold or timeout, flush to SQLite
7. Downstream systems query SQLite for bag location

## Throughput Characteristics

- Normal: 1-5 events/second
- Peak (morning rush): 30-50 events/second
- Burst (multiple bags on conveyor): up to 100 events in 2 seconds

## Known Constraints

- Single-threaded message consumption
- Batching delays real-time visibility by up to 5 seconds
- No deduplication logic (same bag scanned twice = two records)
