# Fix task: UPSTREAM-081-003

Previous OpenCode run failed (exit code 241). Please fix:

## OpenCode log (last 30 lines)
```
[0m← [0mEdit tests/test_upstream081_akshare_gap.py
Index: /Users/maybee/TradingAgents-AShare/tests/test_upstream081_akshare_gap.py
===================================================================
--- /Users/maybee/TradingAgents-AShare/tests/test_upstream081_akshare_gap.py
+++ /Users/maybee/TradingAgents-AShare/tests/test_upstream081_akshare_gap.py
@@ -339,15 +339,13 @@
     assert "ConnectionError" in result
 
 
 def test_lhb_missing_interface_is_classified_not_failed_silently():
-    class _NoLhbFake(_FakeAkshare):
+    class _NoLhbAkshare:
         def __getattr__(self, name):
-            if name == "stock_lhb_detail_em":
-                raise AttributeError(name)
             raise AttributeError(name)
 
-    provider = _provider_with(_NoLhbFake())
+    provider = _provider_with(_NoLhbAkshare())
 
     result = provider.get_lhb_detail("603629.SH", "2026-09-04", force=True)
 
     assert "LHB_FAILED" in result

[0m
[0m$ [0msource .venv/bin/activate && python -m pytest tests/test_upstream081_akshare_gap.py -q --tb=short 2>&1 | tail -8
......................                                                   [100%]
22 passed in 0.40s
[0m
All 22 pass. Now run the related existing suites to check for regressions:
```

## Constraints
- No changes to tradingagents/prompts/
- No writes to prod tradingagents.db
- No push
- **Do NOT git commit**
- Update docs/DEVLOG.md after fix
