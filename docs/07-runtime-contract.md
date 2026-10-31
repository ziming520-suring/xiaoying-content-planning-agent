# 第三阶段 Runtime 与 CLI 契约

本阶段为本地离线任务运行器，保留第二阶段工具接口。真实模型接入未开始。

## 请求与命令

统一 JSON 请求精确包含 schema_version=1、request_id、operation、payload。重复键、未知字段、无时区时间、布尔预算等被拒绝；格式校验在创建数据库之前完成。时间归一化为 UTC 后参与请求身份比较。

| operation | payload | 效果 |
|---|---|---|
| task.create | task_id、start_time 必填；topic、budget_limit、deadline 可缺/null | 缺信息 waiting_input，完整 ready |
| task.complete_input | task_id + 缺失字段的非空值 | 只能填缺口，不能覆盖已有硬要求 |
| task.run | task_id、可选 max_actions | 首次创建 pending run；已有运行返回原 run_id，不重做 |
| task.resume | task_id、可选 max_actions | 已停止 partial/incomplete 任务显式新建运行 |

max_actions 默认 24、范围 1–100。每个返回动作含 tool/arguments/reason；非法动作和 finish 也占动作数，工具内部最终估算不算另一个模型动作，但其费用/耗时/工具观察完整记录。决策器异常立即停止为 model_failed，不循环重试。

同 operation+request_id 同参数返回原响应，参数改变报 REQUEST_CONFLICT；相同 task_id 改输入报 TASK_CONFLICT。创建时的业务身份保留，不因补齐信息改变。响应 status 是请求提交状态；CLI 同时返回当前 task，最终业务结论看 task.task_status。

## 任务、运行和恢复

任务 waiting_input/ready/running/completed/partial/incomplete；运行 pending/running/stopped/completed，两者分离。pending 是已接受尚未执行的运行；已有 pending 时拒绝再次 resume，不能覆盖待执行 run。

- completed：当前计划实际保存成功，读取时检查保存快照仍与状态一致。
- partial：未保存完成，但保留有用素材；incomplete：没有可用业务成果。
- step_limit、model_failed、storage_failed、no_material、no_feasible_plan、interrupted 等停止原因与任务状态一起返回。
- 只读 status 不更新中断状态，也不创建数据库。因此进程刚退出时可能仍看到 running。
- drive/resume 先取得单任务 OS 文件锁。锁存在且被持有时返回 TASK_BUSY；文件存在本身不代表有人持锁，进程退出会释放内核锁。
- 已取得锁但发现旧 running，表示没有活跃执行者，先标记 interrupted，保留成功成果与账本；显式 resume 才新开运行。
- 原 run 请求重放不会自动创建新运行；驱动已终止运行只读取当前状态。
- unknown 尝试保留费用预留并阻止继续执行；当前本地工具同事务回滚能覆盖已测试中断，没有提供任意清除 unknown 的命令。

## 持久化与副作用

同一 SQLite 事务中写入本地动作结果、产物、费用/时间、工作台 JSON 检查点、动作计数和 Trace 事件。生成/保存失败已实际执行时按表结算；参数拒绝不新增尝试。数据库事务失败则整个本地动作回滚，不能把未提交的费用当作已发生外部收费。

第三阶段决策器调用在事务外且不发网络。第四阶段已加入独立发送前预留与缓存响应恢复，见[在线接入说明](09-live-integration.md)，不能把 SQLite 回滚当作远程调用撤销。

任务创建时固定合成资料与工具表快照，后续进程读当前文件变化不会改变旧任务依据。状态和资料使用哈希检测损坏，读状态还检查账本合计、时钟、依赖和产物。哈希是损坏检测，不是对拥有数据库写权限者的安全认证。

数据库有版本标记，仅接受本阶段完整结构。不把第二阶段仅含 artifacts 的演示数据库静默升级或清空，请使用新的 Runtime 数据库路径。

## Trace 与退出码

Trace 事件和动作结果同事务写入，含 run_id、序号、工具参数、简要依据、外层结果、内部工具观察、费用与时间。trace 命令只导出数据库中的事件，目标文件必须不存在。外部导出失败不会改变已完成业务状态。

CLI 退出码：0 成功；2 输入/存储/工具入口错误；3 waiting_input/partial/incomplete。第四阶段默认mock不收费，显式bailian入口会产生API费用；reconcile未完成返回3。

## 使用示例

在项目目录设置模块路径：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -X utf8 -m content_planner --db var/tasks.sqlite3 execute examples/requests/create.json
python -X utf8 -m content_planner --db var/tasks.sqlite3 execute examples/requests/run.json
python -X utf8 -m content_planner --db var/tasks.sqlite3 status EXAMPLE
python -X utf8 -m content_planner --db var/tasks.sqlite3 trace EXAMPLE --output var/example-trace.jsonl
```

一键演示：`python -X utf8 run_demo.py`。默认持久化到 var/demo.sqlite3，重复运行复用相同任务。不要通过清空数据库掩盖重复执行问题。
