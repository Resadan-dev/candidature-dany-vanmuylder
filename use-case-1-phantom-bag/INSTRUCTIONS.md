# Use Case 1: The Phantom Bag

## Scenario

A customer at Brussels Airport reports that bags are occasionally "disappearing" from the tracking system. The bag enters the sortation area (confirmed by PLC scan events), but never appears in the BHS software's tracking database.

**Frequency:** ~5 bags per day out of 15,000 processed
**Customer impact:** Manual search required, occasional missed flights
**Error logs:** No errors visible in application logs

## Your Task

1. **Identify the root cause** — Analyze the provided code and logs to find why bags disappear
2. **Propose a fix** — Provide corrected code or a detailed design change
3. **Verification plan** — Explain how you would test that your fix works
4. **Monitoring** — Suggest alerting/monitoring to detect this issue in production

## Provided Materials

| File | Description |
|------|-------------|
| `bag_tracker.py` | The bag tracking service (~150 lines) |
| `sample_logs.txt` | 1 hour of production logs (contains 2 phantom events) |
| `architecture.md` | System architecture overview |
| `sample_events.json` | Raw PLC events for reference |

## Deliverables

Create a folder with:
- `README.md` — Your analysis and explanation (most important!)
- `bag_tracker_fixed.py` — Your corrected code (if applicable)
- Any additional files (tests, diagrams, monitoring config)

## Hints

- The logs contain everything you need to identify the issue
- Think about what happens under high load
- Consider: what's the difference between the bags that work and those that don't?
