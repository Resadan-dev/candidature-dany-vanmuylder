# Use Case 2: The Dashboard Request

## Scenario

The operations team at Nice Airport (NCE) has requested a real-time dashboard for the control room. They want to monitor baggage flow without switching between multiple applications.

**Requirements from the ops team:**

1. **Current bags in sortation loop**
   - Total count of bags currently in the system
   - Average dwell time (how long bags have been in the loop)

2. **Top 5 "stuck" bags**
   - Bags that have been in the system longer than 15 minutes
   - Show tag ID, entry time, last known location, associated flight

3. **Throughput graph**
   - Bags processed per hour over the last 24 hours
   - Simple bar or line chart

**Display:**
- Wall-mounted 55" TV in the control room
- Must auto-refresh (operators won't interact with it)
- Should be readable from 3 meters away

## Constraints

Read the `constraints.md` file for technical and budget limitations.

## Your Task

1. **Propose an architecture** — Create a diagram and 1-page description
2. **Data source selection** — Which source(s) would you use? Why?
3. **Build vs. reuse** — What would you build custom? What would you reuse?
4. **Effort estimate** — Rough estimate in days (not hours)
5. **Risks and questions** — What would you ask before starting?

## Deliverables

Create a folder with:
- `README.md` — Your design proposal (most important!)
- `architecture.png` or `architecture.md` — Visual diagram (can be ASCII art, draw.io, Mermaid, etc.)
- Any additional notes, config samples, or code snippets

## Evaluation Focus

We're not looking for production-ready code. We want to see:
- How you approach ambiguous requirements
- How you balance "build vs. buy" decisions
- Whether you respect constraints
- How you communicate technical decisions to stakeholders
