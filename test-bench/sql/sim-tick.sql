-- ============================================================================
-- One simulation tick. Run every 5 s by the `simulator` service.
--
-- All the thinking happens in the console (console/simulation.py); this script
-- only steers the table towards the targets it is handed:
--
--   $(target_active)   bags that should currently be IN_SYSTEM, deduplicated
--   $(phantom_rate)    probability of stranding one more bag this tick
--   $(freeze)          1 = the tracker feed is cut, no fresh scan may land
--   $(created_offset)  0 normally, -7 when frozen, so created_at stays old
--
-- The created_at offset is what lets a frozen feed still settle the table
-- without refreshing the freshness clock: the banner query looks at
-- created_at, so back-dating writes keeps it reading STALE or IDLE.
-- ============================================================================

SET NOCOUNT ON;

DECLARE @now      DATETIME2 = GETUTCDATE();
DECLARE @created  DATETIME2 = DATEADD(MINUTE, $(created_offset), GETDATE());
DECLARE @target   INT       = $(target_active);
DECLARE @freeze   INT       = $(freeze);
DECLARE @phantom  FLOAT     = $(phantom_rate);
DECLARE @active   INT;
DECLARE @delta    INT;

-- Current picture: last known state per bag, phantoms excluded.
WITH last_state AS (
    SELECT tag_id, [timestamp] AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id
                              ORDER BY [timestamp] DESC, id DESC) AS rn
    FROM dbo.bag_tracking
)
SELECT @active = COUNT(*)
FROM last_state
WHERE rn = 1
  AND status = 'IN_SYSTEM'
  AND last_seen > DATEADD(HOUR, -6, GETUTCDATE());

SET @delta = @target - @active;

-- --------------------------------------------------------------------------
-- Too few bags: new arrivals, spread over the last few minutes so the table
-- holds a believable spread of dwell times rather than one synchronised batch.
-- --------------------------------------------------------------------------
IF @delta > 0
BEGIN
    -- A range of numbers reserved in one go: NEXT VALUE FOR is not allowed in
    -- a query carrying a TOP, and sp_sequence_get_range exists precisely for
    -- batch insertion.
    DECLARE @first SQL_VARIANT;
    EXEC sys.sp_sequence_get_range
        @sequence_name     = N'dbo.bag_seq',
        @range_size        = @delta,
        @range_first_value = @first OUTPUT;
    DECLARE @base BIGINT = CAST(@first AS BIGINT);

    INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
    SELECT TOP (@delta)
        RIGHT('0000000000' + CAST(@base + ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1
                                  AS VARCHAR(20)), 10),
        'SC-' + RIGHT('000' + CAST((ABS(CHECKSUM(NEWID())) % 120) + 1 AS VARCHAR(3)), 3),
        DATEADD(SECOND, -(ABS(CHECKSUM(NEWID())) % 600), @now),
        'SN' + CAST(3000 + (ABS(CHECKSUM(NEWID())) % 90) AS VARCHAR(5)),
        'IN_SYSTEM',
        @created
    FROM sys.all_objects a CROSS JOIN sys.all_objects b;
END

-- --------------------------------------------------------------------------
-- Too many bags: load the oldest ones onto their flight. A LOADED row after
-- the last IN_SYSTEM row is exactly how a bag leaves the counter.
-- --------------------------------------------------------------------------
IF @delta < 0
BEGIN
    WITH last_state AS (
        SELECT tag_id, location, flight_id, [timestamp] AS last_seen, status,
               ROW_NUMBER() OVER (PARTITION BY tag_id
                                  ORDER BY [timestamp] DESC, id DESC) AS rn
        FROM dbo.bag_tracking
    ),
    leaving AS (
        SELECT TOP (-@delta) tag_id, location, flight_id
        FROM last_state
        WHERE rn = 1
          AND status = 'IN_SYSTEM'
          AND last_seen > DATEADD(HOUR, -6, GETUTCDATE())
        ORDER BY last_seen ASC
    )
    INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
    SELECT tag_id, location, @now, flight_id, 'LOADED', @created
    FROM leaving;
END

-- --------------------------------------------------------------------------
-- Heartbeat: one ordinary scan, so created_at keeps moving and the banner
-- stays green. Skipped entirely when the feed is cut -- that is the outage.
-- --------------------------------------------------------------------------
IF @freeze = 0
BEGIN
    WITH last_state AS (
        SELECT tag_id, flight_id, [timestamp], status,
               ROW_NUMBER() OVER (PARTITION BY tag_id
                                  ORDER BY [timestamp] DESC, id DESC) AS rn
        FROM dbo.bag_tracking
    )
    INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
    SELECT TOP 1
        tag_id,
        'SC-' + RIGHT('000' + CAST((ABS(CHECKSUM(NEWID())) % 120) + 1 AS VARCHAR(3)), 3),
        @now, flight_id, 'IN_SYSTEM', @created
    FROM last_state
    WHERE rn = 1 AND status = 'IN_SYSTEM'
      AND [timestamp] > DATEADD(HOUR, -6, GETUTCDATE())
    ORDER BY NEWID();
END

-- --------------------------------------------------------------------------
-- Use case 1: a bag whose final scan was overwritten in the buffer. It never
-- gets its LOADED row, so it sits IN_SYSTEM for ever. Back-dated past the
-- 6-hour mark, which is what query 5 counts as a probable phantom.
--
-- Simulating the outcome rather than waiting six hours for it is deliberate:
-- the loss rate is what the model computes, the ageing is not the point.
-- --------------------------------------------------------------------------
-- The rate can exceed one per tick once time is compressed, so take the whole
-- part and roll the dice on the remainder.
IF @phantom > 0
BEGIN
    DECLARE @strand INT = FLOOR(@phantom);
    IF RAND(CHECKSUM(NEWID())) < (@phantom - @strand) SET @strand = @strand + 1;

    IF @strand > 0
    BEGIN
        DECLARE @ghost_first SQL_VARIANT;
        EXEC sys.sp_sequence_get_range
            @sequence_name     = N'dbo.bag_seq',
            @range_size        = @strand,
            @range_first_value = @ghost_first OUTPUT;
        DECLARE @ghost_base BIGINT = CAST(@ghost_first AS BIGINT);

        INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
        SELECT TOP (@strand)
            RIGHT('0000000000' + CAST(@ghost_base + ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1
                                      AS VARCHAR(20)), 10),
            'SC-' + RIGHT('000' + CAST((ABS(CHECKSUM(NEWID())) % 120) + 1 AS VARCHAR(3)), 3),
            DATEADD(HOUR, -7 - (ABS(CHECKSUM(NEWID())) % 24), @now),
            'SN' + CAST(3000 + (ABS(CHECKSUM(NEWID())) % 90) AS VARCHAR(5)),
            'IN_SYSTEM',
            @created
        FROM sys.all_objects a CROSS JOIN sys.all_objects b;
    END
END

-- --------------------------------------------------------------------------
-- A frozen feed must read as frozen straight away, not five minutes later.
-- --------------------------------------------------------------------------
IF @freeze = 1
BEGIN
    UPDATE dbo.bag_tracking
    SET created_at = @created
    WHERE id > (SELECT MAX(id) - 50 FROM dbo.bag_tracking);
END
