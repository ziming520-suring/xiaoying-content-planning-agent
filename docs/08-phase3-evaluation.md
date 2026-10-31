# 第三阶段验证与已知边界

实测 **56 项自动测试通过，10/10 个 Runtime 场景符合预期**。来源：[最终汇总](../examples/phase3/final/summary.json)、[测试输出](../examples/phase3/final/test-results.txt)。真实模型调用 0。

| 场景 | 实际结论 | 输出 |
|---|---|---|
| R01 正常/预算替代 | completed，费用 8 | [记录](../examples/phase3/final/R01-actual.json) |
| R02 缺信息后补齐 | waiting_input→ready→completed，费用 8 | [记录](../examples/phase3/final/R02-actual.json) |
| R03 生成失败 | partial，失败费用 3 保留，素材不丢失 | [记录](../examples/phase3/final/R03-actual.json) |
| R04 无可行预算 | partial，费用 0，不伪报保存 | [记录](../examples/phase3/final/R04-actual.json) |
| R05 动作上限续跑 | 停止后新 run，完成且费用 8 | [记录](../examples/phase3/final/R05-actual.json) |
| R06 重复执行 | 同响应、同成果、同费用 | [记录](../examples/phase3/final/R06-actual.json) |
| R07 无素材 | incomplete，费用 0 | [记录](../examples/phase3/final/R07-actual.json) |
| R08 时间不足替代 | quick 模式完成，费用 9 | [记录](../examples/phase3/final/R08-actual.json) |
| R09 保存失败重试 | 脚本保留、费用仍 8 | [记录](../examples/phase3/final/R09-actual.json) |
| R10 保存事务中退出 | 子进程退出码24；未提交产物回滚，续跑完成且未重复生成 | [记录](../examples/phase3/final/R10-actual.json) |

每个场景保存输入、完整命令及响应、中间状态、最终状态和 JSONL Trace。离线策略根据实际观察选动作，但它是固定规则 Mock，不满足考核要求的真实模型动态决策证明。故障注入属于可信测试程序，不是业务输入。

自动测试还覆盖：真实双进程竞争锁、首次初始化失败后空库恢复、非法 Unicode、重复 JSON 键、请求归一化、数据损坏、旧版本产物丢失、事务内保存与检查点同时回滚、补齐后历史请求重放、非法动作计数、模型异常保留成果。

初次 52 项测试与10场景结果保存在 initial/。之后独立审阅补了4项输入/初始化回归，最终56项。前后源码与资料指纹均记录，各自对应当次版本；不把初次记录冒充最终验证。

第二阶段的27项工具测试仍在本次套件中通过。第二阶段历史 SHA-256 不再对应当前源码，恢复那个阶段请使用对应 ZIP 检查点；不覆盖旧报告。

## 限制

- 尚无真实模型供应商、API 费用授权或在线生成质量证据。
- SQLite 同事务原子性只适用于当前本地工具，不能回滚远程调用；第四阶段需先处理远程预留和未知结果。
- 任务级锁是同机文件锁，SQLite 写入串行；不提供分布式运行协调。
- 状态和费用按任务累积；每次 resume 有新的动作上限，但不会重置费用和时间。
- unknown 保守阻断，没有盲目清零恢复入口。commit 异常有收据核对代码，本阶段真实进程测试验证了事务前后中断，不声称模拟了全部磁盘/提交故障。
- 部分失败续跑已具备本地基础能力，第五阶段仍需围绕真实模型和更多业务变化验证。
