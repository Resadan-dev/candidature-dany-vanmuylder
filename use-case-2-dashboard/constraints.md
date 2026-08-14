# Technical Constraints

## Budget

- **No new infrastructure purchases** — Use existing tools and servers only
- **No SaaS subscriptions** — Cannot add new cloud services
- **Developer time:** Up to 2 weeks of effort is acceptable

## Network

- Control room TV is on the **office VLAN** (192.168.10.x)
- Production servers are on the **BHS VLAN** (10.0.50.x)
- Firewall allows:
  - Office → BHS: HTTP/HTTPS to specific endpoints (ports 80, 443, 8080)
  - Office → BHS: SQL Server (port 1433) — read-only access via service account
  - BHS → Office: Not allowed (no push from production to office)

## TV / Display

- Model: Samsung Business Display (LH55)
- OS: Tizen-based, runs Chromium in kiosk mode
- Resolution: 1920x1080
- Input: HDMI (connected to a thin client) OR built-in browser
- Thin client available: Dell Wyse 5070 (Windows 10 IoT, 4GB RAM)
- **No keyboard/mouse in daily operation** — must auto-start and auto-refresh

## Existing Tools (Already Deployed)

| Tool | Version | Location | Notes |
|------|---------|----------|-------|
| Grafana | 9.5.x | `http://monitor.nce-bhs.local:3000` | Already used for PLC metrics |
| SQL Server | 2019 | `sql.nce-bhs.local` | Main BHS database |
| RabbitMQ | 3.12 | `mq.nce-bhs.local:5672` | PLC event queue |
| REST API | Custom | `http://api.nce-bhs.local:8080` | BHS status API (read-only) |

## Development Environment

Available languages/frameworks:
- Python 3.11 (on all servers)
- .NET 6 (on Windows servers)
- Node.js 18 (on dev machines)
- Docker (on Linux servers only)

## Data Sources

### 1. SQL Server Database (`sql.nce-bhs.local`)

```
Database: BHS_Tracking
Tables:
  - bag_tracking (tag_id, location, timestamp, flight_id, status)
  - flight_schedule (flight_id, departure_time, gate, destination)
  - scanner_events (event_id, tag_id, scanner_id, timestamp)

Update frequency: Every 30 seconds (batch insert from tracker service)
Retention: 7 days rolling
```

Service account: `svc_readonly` (SELECT only)

### 2. RabbitMQ Queue (`mq.nce-bhs.local`)

```
Queue: plc.scan.events
Format: JSON
{
  "tag_id": "1234567890",
  "location": "SC-101",
  "timestamp": "2026-08-11T10:30:01.123Z",
  "event_type": "SCAN"
}

Throughput: ~5 msg/sec average, ~50 msg/sec peak
```

Note: Consuming from this queue removes messages (competing consumer pattern). A dedicated consumer would need to be added.

### 3. REST API (`http://api.nce-bhs.local:8080`)

```
GET /api/v1/status
{
  "bags_in_system": 142,
  "avg_dwell_minutes": 4.2,
  "last_update": "2026-08-11T10:35:00Z"
}

GET /api/v1/bags/stuck?threshold_minutes=15
[
  {"tag_id": "1234567890", "entry_time": "2026-08-11T10:15:00Z", "location": "SC-103", "flight_id": "SN3012"},
  ...
]

GET /api/v1/throughput?hours=24
[
  {"hour": "2026-08-11T09:00:00Z", "count": 1245},
  {"hour": "2026-08-11T10:00:00Z", "count": 1532},
  ...
]

Rate limit: 60 requests/minute
```

## Security Requirements

- No credentials hardcoded in client-side code
- Dashboard should not expose sensitive passenger data (tag IDs are OK, names are NOT)
- Audit logging not required for read-only dashboard

## Operational Requirements

- Dashboard must recover automatically after network glitches
- Should display a clear "DATA STALE" warning if data is >5 minutes old
- Operators should not need to restart or refresh manually
