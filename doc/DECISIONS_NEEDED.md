# DECISIONS_NEEDED

Decisions deferred from automated work and explicitly waiting for user
input — or items I noticed but consciously chose not to fix in scope.

Format: `[milestone] - description`

---

## [M1.4] Claude Code adapter has 4 new record types not yet mapped

When testing M1.3 against the real `~/.claude/projects` data, the smoke
script found these record types that fall through to `kind="raw"`:

- `file-history-snapshot` (Claude's own file change tracking)
- `skill_listing` (Claude's superpowers skill index)
- `deferred_tools_delta` (mid-flight tool changes)
- record with `type="system"` and no role (system-injected context)

V0.1 adapter treats these as `raw`. They don't break anything but a
real-life user session will show ~50% raw events until we extend the
mapping. The fix is straightforward (add to `parse_session`'s switch),
but data shape varies and would benefit from a few more sample sessions
before locking the mapping. Punt to M1.4 (event stream) when we have a
better feel for what the UI actually wants to render.

## [M1.3 done — note for M1.4] Hermes session.workdir always empty

Hermes doesn't track workdir at session level. Our model has `workdir`
on Session but for hermes-sourced sessions it stays `""`. Either:
  (a) propagate from messages (some carry cwd in their content meta)
  (b) accept "" and rely on opencode/claude as the workdir-rich sources
  (c) make Session.workdir nullable

Going with (b) for now — won't bite until we wire the UI in M1.5.

## [M1.4] OpenCode session.model is JSON, not a string

Real OpenCode DB stores `session.model` as JSON like
`{"id":"claude-opus-4.7-fast","providerID":"github-copilot","variant":"default"}`
not as a flat string like our fixture assumed. Adapter still works
(stored as opaque text in meta), but if the UI wants "model name" it
needs to parse `meta.model` as JSON first. Document for M1.5 UI.

## [M1.5 / V0.4] Rule model missing `reason` field

doc/05-hitl.md spec'd Rule with a `reason: text?` field ("用户备注
\"为什么这样配置\"") but the dataclass + 002_core_schema.sql don't have
it. Mock data seeding had to drop the reason field to compile. When
V0.4 HITL lands, add:
  - models/rule.py: `reason: str | None = None`
  - migration 004: `ALTER TABLE rules ADD COLUMN reason TEXT;`
  - update Rule.from_row / to_row / to_dict to carry it
Until then the rules table works fine without it.
