-- ============================================================================
-- Clear the stranded bags -- what the use case 1 fix achieves in production.
--
-- The corrected tracker keeps a list instead of a prefix-keyed dict, so no
-- scan is overwritten and no bag is left without its closing event. Here we
-- close the ones already stranded: they get the LOADED row the lost scan
-- should have written.
--
-- Reconciling rather than deleting matters: the rows are real scans, and an
-- audit trail is not something you erase to make a counter look better.
-- ============================================================================

SET NOCOUNT ON;

WITH last_state AS (
    SELECT tag_id, location, flight_id, [timestamp] AS last_seen, status,
           ROW_NUMBER() OVER (PARTITION BY tag_id
                              ORDER BY [timestamp] DESC, id DESC) AS rn
    FROM dbo.bag_tracking
),
phantoms AS (
    SELECT tag_id, location, flight_id, last_seen
    FROM last_state
    WHERE rn = 1
      AND status = 'IN_SYSTEM'
      AND last_seen <= DATEADD(HOUR, -6, GETUTCDATE())
)
INSERT INTO dbo.bag_tracking (tag_id, location, [timestamp], flight_id, status, created_at)
SELECT tag_id, location, DATEADD(MINUTE, 4, last_seen), flight_id, 'LOADED', GETDATE()
FROM phantoms;

SELECT @@ROWCOUNT AS reconciled_bags;
