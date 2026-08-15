-- Read-only: what the SQL panels of the dashboard see right now.
-- Queries 4, 5 and 1 of queries.sql, changing nothing.
SET NOCOUNT ON;

WITH last_insert AS (
    SELECT TOP 1 created_at FROM dbo.bag_tracking ORDER BY id DESC
),
active AS (
    SELECT COUNT(*) AS n FROM (
        SELECT status, ROW_NUMBER() OVER (PARTITION BY tag_id
                                          ORDER BY [timestamp] DESC, id DESC) AS rn
        FROM dbo.bag_tracking
        WHERE [timestamp] > DATEADD(HOUR, -6, GETUTCDATE())
    ) t WHERE rn = 1 AND status = 'IN_SYSTEM'
),
last_state AS (
    SELECT tag_id, [timestamp] AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id ORDER BY [timestamp] DESC, id DESC) AS rn
    FROM dbo.bag_tracking
)
SELECT
    DATEDIFF(MINUTE, li.created_at, GETDATE()) AS minutes_since_last,
    CASE WHEN DATEDIFF(MINUTE, li.created_at, GETDATE()) <= 5 THEN 'OK'
         WHEN a.n = 0 THEN 'IDLE' ELSE 'STALE' END AS freshness,
    (SELECT COUNT(*) FROM last_state
      WHERE rn = 1 AND status = 'IN_SYSTEM'
        AND last_seen > DATEADD(HOUR, -6, GETUTCDATE()))  AS bags_in_system_sql,
    (SELECT COUNT(*) FROM last_state
      WHERE rn = 1 AND status = 'IN_SYSTEM'
        AND last_seen <= DATEADD(HOUR, -6, GETUTCDATE())) AS probable_phantoms
FROM last_insert li CROSS JOIN active a;
