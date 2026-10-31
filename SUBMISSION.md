# 题号2：内容任务规划 Agent

公开交付仓库：[ziming520-suring/xiaoying-content-planning-agent](https://github.com/ziming520-suring/xiaoying-content-planning-agent)。完整代码、题面、演示与复测证据随 main 分支交付；克隆后运行 `git rev-parse HEAD` 获取所检出的版本 SHA。

## 五分钟阅读与演示

1. 阅读[需求](docs/01-requirements.md)和[真实模型接入](docs/09-live-integration.md)，区分模拟业务单位、逻辑时间与真实API费用。
2. 运行 `python -X utf8 -m unittest discover -s tests -v`，验证约束、幂等、恢复及预算；免费且无需密钥。
3. 运行 `python -X utf8 run_demo.py`，观察计划、步骤、保存结果；再次运行应返回相同成果。该演示明确标记offline_mock。
4. 对照[第六阶段修复复测](docs/14-phase6-evaluation.md)、[历史第五阶段评估](docs/11-phase5-evaluation.md)与[验收映射](docs/12-acceptance.md)，阅读真实输入、输出和Trace。
5. 重点查看[预算重规划](examples/phase4/live-final/L02-trace.jsonl)、[时间重规划](examples/phase4/live-final/L03-trace.jsonl)和[生成失败后调整](examples/phase4/live-final/L04-trace.jsonl)。这些是实际模型调用记录，不是离线预设分支。

## 解释重点

- 模型选择下一步，可信工具控制费用、依赖、输入引用和保存；模型自报完成不等于完成。
- 计划预计成本包含已发生失败消耗，重规划不能清零沉没成本。
- 同请求重放不等于新尝试；同任务单运行者；远程调用未知时保留预留并禁止盲目重发。
- 保存只在依赖、费用、时间满足时完成。逻辑截止不表示现实网络延迟达标。
- 文案事实忠实度单独审阅；source_ids正确不能证明每句话都有依据。
- 新版在线生成只编排资料原句，逐行核查，不以此宣称自由创作；旧任务恢复的旧脚本仍需按旧版限制审阅。

## 运行与资料

Python3.12及以上，标准库，无第三方运行依赖。完整命令见[README](README.md)。题面与已确认规则已纳入[reference](docs/reference/exam.md)，原始“草案”标题保留，确认口径见当前需求说明。

API预算账本和本机状态仅保存在var，不包含在归档中。解包免费演示不需要这些文件；继续本项目付费调用必须使用原有累计账本，不得另建预算绕过授权。

## AI使用与个人贡献

AI助手参与需求整理、实现、测试、评估、独立代理审阅和文档。用户确定方向、确认规则、授权阶段推进及API额度。没有完整人工计时记录，不声称三小时完成；候选人亲自复跑、修改和解释的内容须本人据实补充。见[AI说明](docs/04-ai-usage.md)。
