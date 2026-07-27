# Fix task: B-001-R1

Codex review found correctness issues, please fix every finding:

## Final Codex Review
```
STATUS=FINDINGS
--- FINAL CODEX REVIEW ---
The patch fixes the main MiMo runtime key path, but the legacy fallback is incomplete: migrated keys can remain active after clearing and are not recognized correctly by the settings UI. These are functional regressions for existing MiMo users with old `openai:`-scoped keys.

Full review comments:

- [P1] Clear legacy MiMo keys when using fallback — /Users/maybee/TradingAgents-AShare/api/services/auth_service.py:287-290
  For users with an existing MiMo key stored as `openai:<url>`, this fallback makes that legacy row an active credential for `provider=mimo`, but `clear_user_provider_api_key()` still deletes only the current `mimo:<url>` scope. In that migration scenario, a `clear_api_key` request can report/update config while leaving the fallback key in place, so later config reads or analyses continue using a key the user tried to remove.

- [P2] Treat legacy MiMo scopes as stored in settings — /Users/maybee/TradingAgents-AShare/frontend/src/pages/Settings.tsx:27-28
  When a user already has a MiMo key saved under the old `openai:<url>` scope, switching these presets to `mimo` makes `currentApiKeyScope` become `mimo:<url>`, while `/v1/config` still returns only the raw stored `openai:<url>` in `api_key_scopes`. The settings page then overwrites `hasStoredApiKey` with `apiKeyScopes.includes(currentApiKeyScope)` and shows no stored key even though the backend fallback will use one, which blocks normal key management for migrated users.
The patch fixes the main MiMo runtime key path, but the legacy fallback is incomplete: migrated keys can remain active after clearing and are not recognized correctly by the settings UI. These are functional regressions for existing MiMo users with old `openai:`-scoped keys.

Full review comments:

- [P1] Clear legacy MiMo keys when using fallback — /Users/maybee/TradingAgents-AShare/api/services/auth_service.py:287-290
  For users with an existing MiMo key stored as `openai:<url>`, this fallback makes that legacy row an active credential for `provider=mimo`, but `clear_user_provider_api_key()` still deletes only the current `mimo:<url>` scope. In that migration scenario, a `clear_api_key` request can report/update config while leaving the fallback key in place, so later config reads or analyses continue using a key the user tried to remove.

- [P2] Treat legacy MiMo scopes as stored in settings — /Users/maybee/TradingAgents-AShare/frontend/src/pages/Settings.tsx:27-28
  When a user already has a MiMo key saved under the old `openai:<url>` scope, switching these presets to `mimo` makes `currentApiKeyScope` become `mimo:<url>`, while `/v1/config` still returns only the raw stored `openai:<url>` in `api_key_scopes`. The settings page then overwrites `hasStoredApiKey` with `apiKeyScopes.includes(currentApiKeyScope)` and shows no stored key even though the backend fallback will use one, which blocks normal key management for migrated users.
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
