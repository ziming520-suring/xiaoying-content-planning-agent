# 第二阶段工具细则

对第一阶段已确认契约的可执行细化，不更改题面要求。

## 数据范围

- 资料 schema_version 精确为整数 1；未知字段、重复 JSON 键、重复 ID、空事实、未知操作、负值及 bool 费用拒绝。
- task_id 最长 100，主题/检索文本最长 500，素材字符串最长 200、事实最长 1000；长度按去首尾空白后的字符数检查，保留原字符串用于身份。
- 工具费用和耗时为 0..10^9 整数；预算同范围。时间接受带时区 ISO 格式并转 UTC；日期溢出明确不可行，不返回未捕获异常。
- 成功/失败费用与耗时分别配置；本次最大值用于执行预检查，实际结果用于结算。

## 固定合成费用表

| 工具/模式 | 成功费用 | 失败费用 | 成功秒数 | 失败秒数 |
|---|---:|---:|---:|---:|
| 检索/default | 0 | 0 | 5 | 2 |
| 估算/default | 0 | 0 | 2 | 1 |
| 保存/default | 0 | 0 | 3 | 1 |
| 生成/economy | 8 | 3 | 120 | 30 |
| 生成/premium | 12 | 4 | 240 | 60 |
| 生成/quick | 9 | 3 | 60 | 20 |

费用版本 synthetic-v1；均为合成数据，无外部价格来源。

## 工作台接口

```python
catalog = Catalog.load(data_directory)
tools = ToolWorkbench(catalog, task, artifact_database_path)
found = tools.search_materials('字幕')
estimate = tools.estimate_plan(steps)
script = tools.generate_script(plan_id, plan_version, step_id)
saved = tools.save_plan(plan_id, plan_version)
```

Task 必须包含 task_id/topic/budget_limit/start_time/deadline。该构造接口只接受完整工具测试上下文；未来 task.create 的缺口处理并未被取消。

steps 每项精确包含 step_id/kind/mode_id/depends_on/input_refs。只允许引用当前工作台实际返回的素材检索结果，不允许模型提交正文、费用或通过标记。依赖无环、ID 唯一；空素材估算返回 DEPENDENCY_UNMET 缺口。

可行性由素材、累计预算和预计结束时间共同决定。日期加法超出可表示范围时 estimated_finish_time=null，并含 DEADLINE_EXCEEDED 缺口。

重复生成在同工作台内复用当前输入指纹匹配的成功结果，失败生成用新 attempt 重试并再次计费。重新估算不免费重放：它消耗 2 秒且产生新 estimate_id。完整请求键重放留到第三阶段。

新计划可复用匹配的成功脚本，但保存成果属于具体版本，必须重新计算保存消耗。返回内容和 snapshot 均为深拷贝。

工具的 snapshot() 返回尝试记录、可信结果、累计费用、预留和时间；observations() 返回工具调用记录。测试故障通过 inject_failure 控制，禁止作为模型工具注册。

工具表规定的正常费用和耗时用于计划估算；本次执行的最坏费用/耗时用于预检查。出现失败或额外检索后必须重新评估，不保证早期可行计划一定完成。
