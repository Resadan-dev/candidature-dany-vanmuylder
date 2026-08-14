# Data Sources Summary

This document summarizes the available data sources for the dashboard project.

## Quick Comparison

| Source | Real-time? | Polling OK? | Effort to Use | Data Available |
|--------|------------|-------------|---------------|----------------|
| SQL Server | No (30s delay) | Yes | Low | Historical, full detail |
| RabbitMQ | Yes (real-time) | No (consume = delete) | Medium | Live events only |
| REST API | Near real-time | Yes | Very Low | Aggregated metrics |

## Recommendation Matrix

| Requirement | Best Source | Reason |
|-------------|-------------|--------|
| Bags in system (count) | REST API | Already aggregated |
| Average dwell time | REST API | Already calculated |
| Top 5 stuck bags | REST API | Dedicated endpoint |
| Throughput graph | REST API or SQL | API has hourly aggregates; SQL for custom windows |

## API Examples

### Current Status
```bash
curl http://api.nce-bhs.local:8080/api/v1/status
```

```json
{
  "bags_in_system": 142,
  "avg_dwell_minutes": 4.2,
  "last_update": "2026-08-11T10:35:00Z"
}
```

### Stuck Bags
```bash
curl "http://api.nce-bhs.local:8080/api/v1/bags/stuck?threshold_minutes=15"
```

```json
[
  {
    "tag_id": "1234567890",
    "entry_time": "2026-08-11T10:15:00Z",
    "location": "SC-103",
    "flight_id": "SN3012",
    "dwell_minutes": 22
  },
  {
    "tag_id": "0987654321",
    "entry_time": "2026-08-11T10:18:00Z",
    "location": "SC-101",
    "flight_id": "LH1234",
    "dwell_minutes": 19
  }
]
```

### Throughput History
```bash
curl "http://api.nce-bhs.local:8080/api/v1/throughput?hours=24"
```

```json
[
  {"hour": "2026-08-10T11:00:00Z", "count": 1102},
  {"hour": "2026-08-10T12:00:00Z", "count": 1356},
  {"hour": "2026-08-10T13:00:00Z", "count": 1489},
  {"hour": "2026-08-10T14:00:00Z", "count": 1623},
  {"hour": "2026-08-10T15:00:00Z", "count": 1534},
  {"hour": "2026-08-10T16:00:00Z", "count": 1445},
  {"hour": "2026-08-10T17:00:00Z", "count": 1267},
  {"hour": "2026-08-10T18:00:00Z", "count": 1123},
  {"hour": "2026-08-10T19:00:00Z", "count": 987},
  {"hour": "2026-08-10T20:00:00Z", "count": 756},
  {"hour": "2026-08-10T21:00:00Z", "count": 543},
  {"hour": "2026-08-10T22:00:00Z", "count": 321},
  {"hour": "2026-08-10T23:00:00Z", "count": 198},
  {"hour": "2026-08-11T00:00:00Z", "count": 87},
  {"hour": "2026-08-11T01:00:00Z", "count": 45},
  {"hour": "2026-08-11T02:00:00Z", "count": 23},
  {"hour": "2026-08-11T03:00:00Z", "count": 12},
  {"hour": "2026-08-11T04:00:00Z", "count": 34},
  {"hour": "2026-08-11T05:00:00Z", "count": 156},
  {"hour": "2026-08-11T06:00:00Z", "count": 489},
  {"hour": "2026-08-11T07:00:00Z", "count": 876},
  {"hour": "2026-08-11T08:00:00Z", "count": 1234},
  {"hour": "2026-08-11T09:00:00Z", "count": 1456},
  {"hour": "2026-08-11T10:00:00Z", "count": 1532}
]
```

## SQL Server Schema

```sql
-- Main tracking table
CREATE TABLE bag_tracking (
    id INT IDENTITY PRIMARY KEY,
    tag_id VARCHAR(10) NOT NULL,
    location VARCHAR(20) NOT NULL,
    timestamp DATETIME2 NOT NULL,
    flight_id VARCHAR(10),
    status VARCHAR(20) DEFAULT 'IN_SYSTEM',
    created_at DATETIME2 DEFAULT GETDATE()
);

-- Useful queries (for reference)

-- Current bags in system
SELECT COUNT(*) as bag_count, AVG(DATEDIFF(MINUTE, timestamp, GETDATE())) as avg_dwell
FROM bag_tracking
WHERE status = 'IN_SYSTEM';

-- Stuck bags (>15 min)
SELECT tag_id, timestamp as entry_time, location, flight_id,
       DATEDIFF(MINUTE, timestamp, GETDATE()) as dwell_minutes
FROM bag_tracking
WHERE status = 'IN_SYSTEM'
  AND DATEDIFF(MINUTE, timestamp, GETDATE()) > 15
ORDER BY timestamp ASC;

-- Hourly throughput
SELECT DATEADD(HOUR, DATEDIFF(HOUR, 0, timestamp), 0) as hour,
       COUNT(*) as count
FROM bag_tracking
WHERE timestamp > DATEADD(HOUR, -24, GETDATE())
GROUP BY DATEADD(HOUR, DATEDIFF(HOUR, 0, timestamp), 0)
ORDER BY hour;
```
