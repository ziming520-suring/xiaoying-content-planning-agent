# 当前验收证据映射

本表连接实际证据，不把模型文本或工具拒绝等同于模型决策正确。原始预期留存在reference/acceptance-original.md，其阶段状态属于历史。

以下链接主要证明第四阶段基础能力。当前修复版的最新真实对比为17/18，第三轮保存故障后模型停止的未通过结果见[第六阶段报告](14-phase6-evaluation.md)，不能用旧成功记录覆盖它。

|题面要求|执行证据|说明|
|---|---|---|
|正常完成|[L01实际结果](../examples/phase4/live-final/L01-actual.json)|真实生成、保存，completed由持久化成果重建|
|缺预算/截止|[离线R02](../examples/phase4/offline-final/R02-actual.json)、tests/test_runtime.py|waiting_input与补齐由可信代码处理，不需要付费模型猜测|
|工具失败与成功保留|[L04 Trace](../examples/phase4/live-final/L04-trace.jsonl)、[L07 Trace](../examples/phase4/live-final/L07-trace.jsonl)|故障框架注入，后续动作由模型选择|
|预算超限改计划|[L02 Trace](../examples/phase4/live-final/L02-trace.jsonl)|12不可行后改8，再估算、生成、保存|
|预算无解|[L06实际结果](../examples/phase4/live-final/L06-actual.json)|partial而非虚报完成|
|重复执行|tests/test_runtime.py、tests/test_tools.py、干净解包重复demo|请求重放、任务唯一、生成复用、同版本保存唯一|
|续跑加分|tests/test_runtime.py、tests/test_live_runtime.py|免费测试证明保留成功结果和费用；不冒称所有远程故障可恢复|
|截止时间与依赖|[L03 Trace](../examples/phase4/live-final/L03-trace.jsonl)、tests/test_tools.py|时间替代、精确截止边界、依赖错误、旧版本保存拒绝|
|至少两条观察驱动路径|L02预算、L03时间、L04失败|均有实际工具结果及其后的模型动作|
|动作上限|tests/test_runtime.py:test_action_limit_and_explicit_resume|达到上限明确未完成，显式resume|
|信息不足|[L05实际结果](../examples/phase4/live-final/L05-actual.json)|无素材停止，不编造来源|
|来源与文案|[第五阶段评估](11-phase5-evaluation.md)|结构校验与语义审阅分别报告|

整体最终回归和重复/扰动结果见第五阶段报告。测试源码只说明断言定义，实际通过情况须结合运行日志。
