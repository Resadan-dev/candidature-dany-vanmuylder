-- Machine-readable probe: a single "freshness;active;phantoms" line, so the
-- verification scripts do not have to slice a tabular output.
-- Same queries as inspect.sql, which stays the human-readable version.
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
    CASE WHEN DATEDIFF(MINUTE, li.created_at, GETDATE()) <= 5 THEN 'OK'
         WHEN a.n = 0 THEN 'IDLE' ELSE 'STALE' END
    + ';' +
    CAST((SELECT COUNT(*) FROM last_state
           WHERE rn = 1 AND status = 'IN_SYSTEM'
             AND last_seen > DATEADD(HOUR, -6, GETUTCDATE())) AS VARCHAR(10))
    + ';' +
    CAST((SELECT COUNT(*) FROM last_state
           WHERE rn = 1 AND status = 'IN_SYSTEM'
             AND last_seen <= DATEADD(HOUR, -6, GETUTCDATE())) AS VARCHAR(10))
FROM last_insert li CROSS JOIN active a;
