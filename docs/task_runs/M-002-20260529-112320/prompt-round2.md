# 修复任务: M-002

之前的实现测试未通过。请修复以下问题：

## 测试输出（最后 30 行）
```
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2619, in rollback
    self._do_rollback()
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2738, in _do_rollback
    self._close_impl(try_deactivate=True)
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2721, in _close_impl
    self._connection_rollback_impl()
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2713, in _connection_rollback_impl
    self.connection._rollback_impl()
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1130, in _rollback_impl
    self._handle_dbapi_exception(e, None, None, None, None)
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1128, in _rollback_impl
    self.engine.dialect.do_rollback(self.connection)
  File "/Users/maybee/TradingAgents-AShare/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 712, in do_rollback
    dbapi_connection.rollback()
sqlalchemy.exc.ProgrammingError: (sqlite3.ProgrammingError) SQLite objects created in a thread can only be used in that same thread. The object was created in thread id 8474025600 and this is thread id 6180990976.
(Background on this error at: https://sqlalche.me/e/20/f405)

ERROR    scheduler.main:main.py:254 [Scheduler] Could not record failure: (sqlite3.OperationalError) no such table: scheduled_analyses
[SQL: SELECT scheduled_analyses.id AS scheduled_analyses_id, scheduled_analyses.user_id AS scheduled_analyses_user_id, scheduled_analyses.symbol AS scheduled_analyses_symbol, scheduled_analyses.horizon AS scheduled_analyses_horizon, scheduled_analyses.trigger_time AS scheduled_analyses_trigger_time, scheduled_analyses.is_active AS scheduled_analyses_is_active, scheduled_analyses.last_run_date AS scheduled_analyses_last_run_date, scheduled_analyses.last_run_status AS scheduled_analyses_last_run_status, scheduled_analyses.last_report_id AS scheduled_analyses_last_report_id, scheduled_analyses.consecutive_failures AS scheduled_analyses_consecutive_failures, scheduled_analyses.created_at AS scheduled_analyses_created_at, scheduled_analyses.updated_at AS scheduled_analyses_updated_at 
FROM scheduled_analyses 
WHERE scheduled_analyses.id = ?
 LIMIT ? OFFSET ?]
[parameters: ('549bcfa9c2ce4b3aa7f0b156fb5aae8b', 1, 0)]
(Background on this error at: https://sqlalche.me/e/20/e3q8)
=========================== short test summary info ============================
FAILED tests/test_portfolio_import.py::TestPortfolioImportService::test_scheduled_job_uses_imported_position_context
FAILED tests/test_portfolio_import.py::TestPortfolioImportService::test_scheduled_job_marks_failed_when_underlying_job_fails
2 failed, 801 passed, 11 skipped in 91.17s (0:01:31)
```

## 约束
- 不改 tradingagents/prompts/
- 不写入生产 tradingagents.db
- 不 push
- **不要 git commit**
- 修复后更新 docs/DEVLOG.md
