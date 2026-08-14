-- ============================================================================
-- SQL control queries: fallback source and data-quality panels.
-- The REST API stays the primary source; these run in Grafana's MSSQL
-- datasource, with svc_readonly.
--
-- The reference queries in data_sources.md count events instead of bags and
-- compare UTC timestamps with a local GETDATE(). So: deduplicate by tag_id
-- (", id DESC" keeps the count stable despite the tracker's duplicate events)
-- and use GETUTCDATE() everywhere except query 4, where created_at is local by
-- construction. A bag still IN_SYSTEM but unscanned for over 6 h counts as a
-- probable phantom: excluded from queries 1-3, counted by query 5.
--
-- Index to validate with the DBA:
--   CREATE INDEX IX_bag_tracking_tag_ts ON bag_tracking (tag_id, timestamp DESC)
--       INCLUDE (status, location, flight_id);
-- ============================================================================


-- 1. Bags currently in the system + average dwell time.
--    Column names mirror the API payload, so both can be compared panel to panel.
WITH last_state AS (
    SELECT tag_id, timestamp AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id
                              ORDER BY timestamp DESC, id DESC) AS rn
    FROM bag_tracking
),
entry AS (
    SELECT tag_id, MIN(timestamp) AS entry_time FROM bag_tracking GROUP BY tag_id
)
SELECT COUNT(*) AS bags_in_system,
       AVG(CAST(DATEDIFF(MINUTE, e.entry_time, GETUTCDATE()) AS FLOAT)) AS avg_dwell_minutes
FROM last_state ls
JOIN entry e ON e.tag_id = ls.tag_id
WHERE ls.rn = 1
  AND ls.status = 'IN_SYSTEM'
  AND ls.last_seen > DATEADD(HOUR, -6, GETUTCDATE());   -- probable phantoms excluded


-- 2. Top 5 stuck bags (over 15 minutes in the system).
--    Dwell is measured from the first scan, like the API; oldest first.
WITH last_state AS (
    SELECT tag_id, location, flight_id, timestamp AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id
                              ORDER BY timestamp DESC, id DESC) AS rn
    FROM bag_tracking
),
entry AS (
    SELECT tag_id, MIN(timestamp) AS entry_time FROM bag_tracking GROUP BY tag_id
)
SELECT TOP 5
       ls.tag_id,
       e.entry_time,
       ls.location,
       ls.flight_id,
       DATEDIFF(MINUTE, e.entry_time, GETUTCDATE()) AS dwell_minutes
FROM last_state ls
JOIN entry e ON e.tag_id = ls.tag_id
WHERE ls.rn = 1
  AND ls.status = 'IN_SYSTEM'
  AND ls.last_seen  >  DATEADD(HOUR, -6, GETUTCDATE())     -- not a phantom
  AND e.entry_time  <= DATEADD(MINUTE, -15, GETUTCDATE())  -- stuck for over 15 min
ORDER BY e.entry_time ASC;


-- 3. Hourly throughput over the last 24 h, in UTC buckets.
--    Counts bags by ENTRY hour where the API counts bags processed: a gap with
--    the API is expected as long as the exit signal is unconfirmed.
SELECT DATEADD(HOUR, DATEDIFF(HOUR, 0, first_seen), 0) AS hour_bucket,
       COUNT(*)                                        AS bags_entered
FROM (
    SELECT tag_id, MIN(timestamp) AS first_seen
    FROM bag_tracking
    GROUP BY tag_id
    HAVING MIN(timestamp) > DATEADD(HOUR, -24, GETUTCDATE())
) firsts
GROUP BY DATEADD(HOUR, DATEDIFF(HOUR, 0, first_seen), 0)
ORDER BY hour_bucket;


-- 4. Freshness: drives the DATA STALE banner.
--    IDLE keeps the banner quiet at night, when having no insert is normal.
WITH last_insert AS (
    SELECT TOP 1 created_at          -- seek on the clustered PK, not a MAX() scan
    FROM bag_tracking
    ORDER BY id DESC
),
active AS (                          -- anything that should be moving right now?
    SELECT COUNT(*) AS n
    FROM (
        SELECT status,
               ROW_NUMBER() OVER (PARTITION BY tag_id
                                  ORDER BY timestamp DESC, id DESC) AS rn
        FROM bag_tracking
        WHERE timestamp > DATEADD(HOUR, -6, GETUTCDATE())
    ) t
    WHERE rn = 1 AND status = 'IN_SYSTEM'
)
SELECT li.created_at                                 AS last_insert,
       DATEDIFF(MINUTE, li.created_at, GETDATE())    AS minutes_since_last,
       CASE
           WHEN DATEDIFF(MINUTE, li.created_at, GETDATE()) <= 5 THEN 'OK'
           WHEN a.n = 0                                        THEN 'IDLE'
           ELSE 'STALE'
       END                                           AS freshness
FROM last_insert li
CROSS JOIN active a;


-- 5. Data health: probable phantoms (see use case 1).
--    Exact complement of query 1: a bag is in one counter or the other, never both.
WITH last_state AS (
    SELECT tag_id, timestamp AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id
                              ORDER BY timestamp DESC, id DESC) AS rn
    FROM bag_tracking
)
SELECT COUNT(*) AS probable_phantoms
FROM last_state
WHERE rn = 1
  AND status = 'IN_SYSTEM'
  AND last_seen <= DATEADD(HOUR, -6, GETUTCDATE());
