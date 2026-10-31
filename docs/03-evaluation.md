# 当前评估入口

当前修复版见[第六阶段评估](14-phase6-evaluation.md)：真实17/18，内容改为资料原句编排；以下第五、二阶段均为历史。

最新重复验证、提示扰动、内容审阅和已知问题见[第五阶段报告](11-phase5-evaluation.md)。下文保留第二阶段实际结果，不代表当前完整能力。

# 第二阶段验证

历史阶段报告：当前源码已进入第三阶段，最新结果见 [第三阶段验证](08-phase3-evaluation.md)。本文的“最终”指第二阶段冻结版本。

最终实测：**27 项自动测试通过，10/10 个工具场景符合预期**。来源：[final/summary.json](../examples/phase2/final/summary.json)、[测试输出](../examples/phase2/final/test-results.txt)。执行模式为 scripted_tool_validation_not_agent，真实模型调用为 0。

| 场景 | 检查结果 | 证据 |
|---|---|---|
| T01 正常 | 保存成功，累计 8 | [实际](../examples/phase2/final/T01-actual.json) |
| T02 无素材 | 估算不可行、生成拒绝 | [实际](../examples/phase2/final/T02-actual.json) |
| T03 生成失败再试 | 保留素材，累计 3+8=11，预算 20 内保存 | [实际](../examples/phase2/final/T03-actual.json) |
| T04 超预算替代 | 12 不可行，8 可行后保存 | [实际](../examples/phase2/final/T04-actual.json) |
| T05 时间替代 | economy 超时，quick 满足后保存 | [实际](../examples/phase2/final/T05-actual.json) |
| T06 无可行预算 | 预算 7 拒绝生成，费用 0 | [实际](../examples/phase2/final/T06-actual.json) |
| T07 保存失败 | 脚本保留，再保存成功，费用仍为 8 | [实际](../examples/phase2/final/T07-actual.json) |
| T08 沉没成本 | 已耗 3，再估算总额 11，拒绝重试 | [实际](../examples/phase2/final/T08-actual.json) |
| T09 截止边界 | 132 秒完成，等于截止时间 | [实际](../examples/phase2/final/T09-actual.json) |
| T10 同工作台重复 | 生成/保存复用，费用和时间不重复 | [实际](../examples/phase2/final/T10-actual.json) |

每个场景均有 input.json、actual.json、trace.jsonl；工具内部最终估算也记录在 Trace。故障由测试程序注入，替代方案由脚本选择，不是模型选择。

初次 20 项测试和 10 场景证据保存在 initial/，不覆盖。最终版本新增了回归并修复问题，尤其初版空素材估算曾错误返回 feasible=true；最终证据才对应当前实现。summary 保留 src/data/tests SHA-256，运行前后核对一致。计划与文档不属于代码指纹。

额外测试覆盖布尔/负数预算、空查询、重复素材 ID、未知模式、错误依赖、旧版本保存、返回值篡改、真实存储路径错误、跨版本保存、极端日期等。详见测试输出与 [审阅记录](phase2-review.md)。

限制：本阶段不证明 task.create 缺口状态、跨进程重复执行、并发运行者锁、持久化费用、完整 unknown 恢复、真实模型重规划或生成质量。相应题面最终验收仍需第三至五阶段完成，不能将工具场景通过率称为 Agent 成功率。
