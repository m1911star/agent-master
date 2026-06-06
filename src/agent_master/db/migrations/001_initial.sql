-- 001_initial.sql
-- Bootstrap migration. Real object schemas land in M1.2 (002+).
-- This file intentionally leaves the data tables empty; we just need the
-- schema_version table to exist so the runner has something to bump.

-- schema_version is also created by the runner (CREATE IF NOT EXISTS), so this
-- script is effectively a no-op placeholder. Keeping it on disk so the
-- migration list isn't empty and `migrate()` records v1 as applied.

SELECT 1;
