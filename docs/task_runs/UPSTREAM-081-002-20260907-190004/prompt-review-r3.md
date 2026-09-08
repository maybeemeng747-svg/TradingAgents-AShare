你是独立代码审查者（round3，定向 delta 复审）。仓库：/Users/maybee/TradingAgents-AShare。只读审查。

## 范围（只审这一个文件）
`tests/test_job_store_redis_offline.py`（新增，未入库）。这是 r2 review P2-1 的定向修复：把「stub 驱动 RedisJobStore.subscribe() 生成器控制流」的离线测试入库，使 Redis 路径（ping×N → 终态补发 → done）不依赖活 Redis 即可测试。

r2 已核验通过的部分（job_store.py 纯函数、job_store_redis.py 终态补发、既有测试改写）**不在本轮范围**，勿重复审。

## 核验点（引用行号）
1. 离线真实性：全程不连活 Redis（monkeypatch redis.Redis.from_url → stub；stub get_job 以实例属性 shadow）；无新第三方依赖；monkeypatch 自动还原不泄漏。
2. 控制流断言质量：覆盖 r2 要求的两条路径（①错过事件/终态已落库→ping×N→补发 job.completed 或 job.failed→done；②监听即终态→直接重放）；tripwire（get_job/hgetall/publish 被调即 fail）是否真的能证明「零额外写」与「live 终态不触发 get_job」。
3. 断言与 r2 已核验的修复语义一致：终态 payload 与 terminal_replay_event 输出一致（job_id/decision/direction/result/risk_items/key_metrics/confidence/target_price/stop_loss_price；failed 为 job_id/error 兜底）。
4. 测试卫生：无 sleep 依赖的脆弱时序（监听线程 join 超时有界）；无对生产 DB/凭据引用；文件可独立运行。

## 允许操作
只读；可运行 `pytest tests/test_job_store_redis_offline.py -q`（沙箱临时目录受限用 -s 说明）。

## 输出（中文，stdout）
- 第一行：`结论: PASS` 或 `结论: FAIL`
- 4 个核验点逐条（引用行号）
- findings（P0/P1/P2；没有写"无"）
- 实际运行的命令与结果
