-- 003_budget_timestamps.sql
-- 002 forgot the created_at / updated_at columns on budgets.
-- The Budget dataclass has them, so the schema must too.

ALTER TABLE budgets ADD COLUMN created_at TEXT;
ALTER TABLE budgets ADD COLUMN updated_at TEXT;

-- Backfill any existing rows with current UTC time so NOT-NULL invariants
-- (which the dataclass relies on) hold.
UPDATE budgets SET created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE created_at IS NULL;
UPDATE budgets SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE updated_at IS NULL;
