# UI-014-R1 运行档案 — 研报证据中心真实契约与请求状态补修

- **任务卡**：docs/TASKS.md `### UI-014-R1: 研报证据中心真实契约与请求状态补修（P2）`
- **开始时间**：2026-09-11（本地，连续模式第 6 项）
- **基线 HEAD**：`740da13`（M-009-R1 实现提交）
- **批次说明**：按用户 2026-09-10 破例指示，本批次 review 统一安排。

## 任务卡要点

- **描述**：按 KB-020 真实 summary schema 展示；symbol 变化清空旧数据；失败后稳定展示错误而非无限重试。
- **允许修改**：`frontend/src/components/ResearchEvidenceCenter.tsx`、`frontend/src/utils/researchEvidenceCenter.test.ts`、相关 API client/types、离线 UI 测试与档案。
- **验收**：以 KB-020 实际响应 fixture 驱动组件测试，覆盖完整/空/部分失败、A→B 切股时 A 慢响应、重试上限和卸载取消；运行前端定向测试与 `npm run build`，提供桌面/手机 mock 截图。不得为迁就前端改写后端事实状态。

## 复现记录（修复前，基线 740da13）

对照后端真实产出（`api/services/research_evidence_service.py` 及上游
`matrix_to_ta_consumable_summary` / `audit_to_ta_consumable_summary` /
`timeline_to_ta_consumable_summary` / `snapshot_to_api_dict`）：

| 缺陷 | 修复前行为 |
|---|---|
| ① schema 不符 | 组件按臆造字段渲染：consensus 读 `reports[]/effective_report_count/consensus_direction`（真实为 `consensus_score/dimensions_brief/consensus_summary` 等）；audit 读 `items[]/total_claims`（真实为 `counts{}/total_claim_count/audit_summary[]`）；timeline 读 `timeline[]/drift_direction`（真实为 `theses_brief[]/版本计数`）；scores/theses_summary 读 summary 顶层（真实嵌套在 `snapshot` 内）；half_year 的 `latest_period/latest_disclosure_date` 在 bucket 层而组件从 summary 读。绝大多数 chip 永远渲染为空。 |
| ② symbol 变化不清数据 | 懒加载 effect 守卫 `if (!expanded \|\| data \|\| loading) return`——已加载后切换 symbol，effect 直接返回，旧 symbol 数据滞留展示在新 symbol 名下。 |
| ③ 失败无限重试 | 失败后 `data=null, loading=false`，effect 依赖（loading）翻转即重新执行，守卫全过 → 立即重发 → 再次失败 → 死循环，错误界面闪烁式无限重试。 |

## 设计与修改文件

- 新增 `frontend/src/utils/researchEvidenceCenter.ts`（纯函数层）：
  - **KB-020 真实 schema 视图模型**：`buildConsensusViewModel` /
    `buildCitationAuditViewModel` / `buildThesisTimelineViewModel` /
    `buildHalfYearFactsViewModel(bucket)`（latest_* 取 bucket 层）/
    `buildScoreSnapshotViewModel`（scores/theses 嵌套解包）；缺字段缺失
    展示，不编造数值；不含任何 decision/action 字段。
  - **请求状态机**：`reduceEvidenceCenter` 显式 phase（idle/loading/
    loaded/error）+ 数据归属 symbol；`symbol_changed` 清空旧数据；
    `load_succeeded/load_failed` 仅在"当前 symbol 且 loading 中"时采纳
    （慢响应/迟到失败丢弃）；失败稳定 error 不自动重试；`retry_clicked`
    受 `EVIDENCE_MAX_ATTEMPTS_PER_SYMBOL=3`（1 自动 + 2 手动）上限约束；
    `unmounted` 后事件全部丢弃。
- `frontend/src/components/ResearchEvidenceCenter.tsx`：detail 视图改渲染
  上述视图模型（维度表/审计行/时间线 theses/快照双卡评分）；状态改用
  useReducer 接状态机；error 态提供"重新加载"按钮（达上限后显示上限
  提示）；effect 只在 `shouldFetchEvidence` 为真时发请求。
- `frontend/src/types/index.ts`：五个 summary 接口重写为真实 schema
  （含 `ResearchEvidenceScoreSnapshotBody`），`latest_*` 移出
  HalfYearFactsSummary。
- mock 验收辅助：`frontend/evidence-mock.html` +
  `frontend/src/evidenceMockEntry.tsx`（KB-020 真实 schema fixture，
  页面内拦截 `api.getResearchEvidence`，零网络请求）。

## 测试结果

| 命令 | 结果 | 退出码 |
|---|---|---:|
| `npx vitest run src/utils/researchEvidenceCenter.test.ts` | 17 passed（完整/部分失败/空 fixture 的视图模型 + 状态机：A→B 慢响应丢弃、symbol 清空、稳定 error 不自动重试、重试上限、迟到失败丢弃、卸载丢弃） | 0 |
| `npx vitest run`（前端全量 12 文件） | 157 passed | 0 |
| `npx tsc --noEmit -p tsconfig.json` | 无错误 | 0 |
| `npm run build` | ✓ built in 1.50s | 0 |
| 离线 mock UI 截图（桌面 1280×900 + 手机 390×844，fixture 驱动零网络） | 存于本档案：mock-desktop-evidence.png / mock-mobile-evidence.png | — |

DOM 快照核验：共识 bucket 渲染共识分/分歧分/有效关注/主导立场、事实
核查警告、业绩预测/估值假设维度行；审计 bucket 渲染 total_claim_count
系 chips 与 audit_summary 行；时间线渲染版本统计与 theses_brief；半年报
渲染 bucket 层报告期/披露日；评分快照渲染双卡评分与缺失证据。后端
事实状态零改动（只读消费）。

## Codex review

按用户 2026-09-10 指示统一安排，本任务不单独 review；本档案即
统一 review 材料。
