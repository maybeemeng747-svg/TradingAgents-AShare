# Z Code 项目交接

## 打开项目

当前机器没有可用的 `zcode` shell 命令，因此使用 Z Code 桌面客户端：

1. 在 Z Code 选择“打开文件夹/项目”。
2. 选择 `/Users/maybee/TradingAgents-AShare`，不要只选择 `docs/`、`frontend/` 或单个文件。
3. 为该目录新建会话。
4. 输入 `/tasks`。项目级命令会先检查 Git 状态，再读取项目规则和任务池。

## 第一次交接提示词

可直接发送：

```text
你现在接管 TradingAgents-AShare 的代码实现工作。当前工作区必须是
/Users/maybee/TradingAgents-AShare。先执行 /tasks，只读核对 git 状态、项目规则和
docs/TASKS.md；没有 ready 任务就停止，不要自行创建或释放任务。你负责实现与测试，
Codex 负责独立 review。任何测试失败、P0/P1/P2 finding、review UNKNOWN/超时、脏树
来源不明都要停止为 NEEDS_HUMAN。不得自动 push、不得读取或输出密钥、不得写生产
数据库、不得修改 prompts。用户明确说“开始连续开发”后再执行 /tasks run。
```

## 连续开发

任务池已经由 Codex/人工释放出 `ready` 后，发送：

```text
/tasks run
```

Z Code 应按以下闭环逐项执行：

```text
clean tree
  -> 读取一个 ready 任务
  -> in_progress + 运行档案
  -> 实现
  -> 定向测试和必要回归
  -> codex review --uncommitted
  -> PASS 后选择性提交并标 done
  -> 释放满足依赖的下一项
  -> 重新检查 clean tree
```

任何一项失败都停止整批，不带病进入下一项。

## 当前注意事项

- 截至 2026-09-10，任务解析器识别到 0 个可领取任务；在 Codex/人工释放任务前，`/tasks run` 应正常退出。
- 本地分支相对远端 ahead 35。连续开发会继续增加本地提交，push 仍由用户决定。
- `docs/TASKS.md` 存在历史状态漂移和旧任务元数据缺失，不能仅按“未完成数量”直接领取。
- 知识库研究评分任务属于 `/Users/maybee/Documents/knowledge`，不要在本项目里改写知识库评分公式或正式快照。
