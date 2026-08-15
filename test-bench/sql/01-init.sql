-- ============================================================================
-- Test bench: the bag_tracking schema of data_sources.md, plus fake data.
--
-- Volumes are chosen to reproduce the figures of the screenshots:
--   137 bags in transit + 5 stuck = 142 active
--                        -> query 1 of queries.sql (de-duplicated count)
--     7 phantoms         -> query 5 (last scan over 6 h old)
--   142 on the API side  -> API/SQL gap = 0: in a healthy system both
--                           sources agree, and the data-health panel stays
--                           green as long as nothing diverges.
--
-- Every business timestamp is UTC (like the real tracker); created_at is in
-- server local time by construction, which is what query 4 (the DATA STALE
-- banner) relies on.
-- ============================================================================

SET NOCOUNT ON;
GO

IF DB_ID('bhs') IS NULL
    CREATE DATABASE bhs;
GO

USE bhs;
GO

DROP TABLE IF EXISTS dbo.bag_tracking;
GO

CREATE TABLE dbo.bag_tracking (
    id          INT IDENTITY PRIMARY KEY,
    tag_id      VARCHAR(10)  NOT NULL,
    location    VARCHAR(20)  NOT NULL,
    [timestamp] DATETIME2    NOT NULL,
    flight_id   VARCHAR(10),
    status      VARCHAR(20)  DEFAULT 'IN_SYSTEM',
    created_at  DATETIME2    DEFAULT GETDATE()
);
GO

-- The index suggested in the header of queries.sql, so the dashboard queries
-- run here under the same conditions as in production.
CREATE INDEX IX_bag_tracking_tag_ts
    ON dbo.bag_tracking (tag_id, [timestamp] DESC)
    INCLUDE (status, location, flight_id);
GO

-- Bag numbers for the simulator: a sequence rather than a random draw, so
-- that a tag_id is never accidentally reused.
DROP SEQUENCE IF EXISTS dbo.bag_seq;
CREATE SEQUENCE dbo.bag_seq AS BIGINT START WITH 7000000000 INCREMENT BY 1;
GO

-- ----------------------------------------------------------------------------
-- Numbers table, to generate the test data without a loop.
-- ----------------------------------------------------------------------------
DECLARE @now DATETIME2 = GETUTCDATE();

;WITH n AS (
    SELECT TOP (20000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i
    FROM sys.all_objects a CROSS JOIN sys.all_objects b
)
SELECT i INTO #n FROM n;

-- ----------------------------------------------------------------------------
-- 1. The last 24 h: bags that entered, then were loaded.
--    Hourly volumes are those of /api/v1/throughput in data_sources.md,
--    divided by 8 to keep the bench light while preserving the shape of the
--    curve (morning peak, night trough).
-- ----------------------------------------------------------------------------
DECLARE @hourly TABLE (h INT, cnt INT);
INSERT INTO @hourly (h, cnt) VALUES
    (-23, 138), (-22, 170), (-21, 186), (-20, 203), (-19, 192), (-18, 181),
    (-17, 158), (-16, 140), (-15, 123), (-14,  95), (-13,  68), (-12,  40),
    (-11,  25), (-10,  11), ( -9,   6), ( -8,   3), ( -7,   2), ( -6,   4),
    ( -5,  20), ( -4,  61), ( -3, 110), ( -2, 154), ( -1, 182);

-- One entry row plus one loading row per historical bag. The loading is dated
-- four minutes after the entry: without that gap both rows would share the
-- same timestamp, and "the last state of the bag" would depend on insertion
-- order.
INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
SELECT
    RIGHT('000000000' + CAST(1000000 + h.h * 1000 + n.i AS VARCHAR(10)), 10),
    'SC-' + RIGHT('000' + CAST((n.i % 120) + 1 AS VARCHAR(3)), 3),
    DATEADD(MINUTE, s.decalage_min, DATEADD(SECOND, (n.i * 37) % 3600, DATEADD(HOUR, h.h, @now))),
    'SN' + CAST(3000 + (n.i % 90) AS VARCHAR(5)),
    s.status,
    DATEADD(MINUTE, s.decalage_min, DATEADD(SECOND, (n.i * 37) % 3600, DATEADD(HOUR, h.h, @now)))
FROM @hourly h
JOIN #n n ON n.i <= h.cnt
CROSS JOIN (VALUES ('IN_SYSTEM', 0), ('LOADED', 4)) AS s(status, decalage_min);

-- ----------------------------------------------------------------------------
-- 2. The 137 bags in normal transit.
--    Three scans each, the last one under 6 h old: they therefore count in
--    query 1 and not in query 5. Together with the 5 stuck bags of the next
--    block, the de-duplicated SQL counter lands on 142.
-- ----------------------------------------------------------------------------
INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
SELECT
    RIGHT('000000000' + CAST(8400000000 + n.i AS VARCHAR(10)), 10),
    'SC-' + RIGHT('000' + CAST((n.i % 120) + 1 AS VARCHAR(3)), 3),
    DATEADD(MINUTE, -((n.i * 7) % 55) - scan.offset_min, @now),
    'SN' + CAST(3000 + (n.i % 90) AS VARCHAR(5)),
    'IN_SYSTEM',
    DATEADD(MINUTE, -((n.i * 7) % 55) - scan.offset_min, @now)
FROM #n n
CROSS JOIN (VALUES (0), (3), (9)) AS scan(offset_min)
WHERE n.i <= 137;

-- ----------------------------------------------------------------------------
-- 3. The 5 stuck bags shown by the "Top 5" panel.
--    Dwell times of 142, 38, 20, 17 and 16 minutes, the same as in the
--    screenshots. The 142-minute one is a probable phantom in the sense of
--    use case 1, but its last scan is under 6 h old: it therefore stays in
--    the active counter, and it is the 120-minute display threshold that
--    flags it.
-- ----------------------------------------------------------------------------
DECLARE @stuck TABLE (tag_id VARCHAR(10), location VARCHAR(20), flight_id VARCHAR(10), dwell INT);
INSERT INTO @stuck (tag_id, location, flight_id, dwell) VALUES
    ('0847291063', 'SC-091', 'AF7702',  142),
    ('2290184417', 'ML-004', 'EJU4517',  38),
    ('1234567890', 'SC-103', 'SN3012',   20),
    ('0987654321', 'SC-101', 'LH1234',   17),
    ('5561203984', 'SC-117', 'BA341',    16);

INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
SELECT s.tag_id, s.location, DATEADD(MINUTE, -s.dwell, @now), s.flight_id, 'IN_SYSTEM',
       DATEADD(MINUTE, -s.dwell, @now)
FROM @stuck s;

-- ----------------------------------------------------------------------------
-- 4. The 7 phantom bags: still IN_SYSTEM, unscanned for 7 to 31 hours.
--    This is exactly the population counted by query 5.
-- ----------------------------------------------------------------------------
INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
SELECT
    RIGHT('000000000' + CAST(9900000000 + n.i AS VARCHAR(10)), 10),
    'SC-' + RIGHT('000' + CAST((n.i * 13 % 120) + 1 AS VARCHAR(3)), 3),
    DATEADD(HOUR, -6 - (n.i * 4), @now),
    'SN' + CAST(3100 + n.i AS VARCHAR(5)),
    'IN_SYSTEM',
    DATEADD(HOUR, -6 - (n.i * 4), @now)
FROM #n n
WHERE n.i <= 7;

DROP TABLE #n;
GO

-- ----------------------------------------------------------------------------
-- Read-only service account, the one the dashboard uses.
-- ----------------------------------------------------------------------------
USE master;
GO
IF SUSER_ID('svc_readonly') IS NULL
    EXEC('CREATE LOGIN svc_readonly WITH PASSWORD = ''$(readonly_password)'', CHECK_POLICY = OFF');
GO

USE bhs;
GO
IF USER_ID('svc_readonly') IS NULL
    CREATE USER svc_readonly FOR LOGIN svc_readonly;
GO
ALTER ROLE db_datareader ADD MEMBER svc_readonly;
GO

-- ----------------------------------------------------------------------------
-- Check: the three figures the dashboard expects.
-- ----------------------------------------------------------------------------
WITH last_state AS (
    SELECT tag_id, [timestamp] AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id ORDER BY [timestamp] DESC, id DESC) AS rn
    FROM dbo.bag_tracking
)
SELECT
    (SELECT COUNT(*) FROM last_state
      WHERE rn = 1 AND status = 'IN_SYSTEM'
        AND last_seen > DATEADD(HOUR, -6, GETUTCDATE()))  AS bags_in_system_sql,
    (SELECT COUNT(*) FROM last_state
      WHERE rn = 1 AND status = 'IN_SYSTEM'
        AND last_seen <= DATEADD(HOUR, -6, GETUTCDATE())) AS probable_phantoms,
    (SELECT COUNT(*) FROM dbo.bag_tracking)               AS scans_total;
GO
