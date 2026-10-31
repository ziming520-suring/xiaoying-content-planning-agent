# 第六阶段修复、复测与候选交付

2026-10-31。原始第五阶段结果保留；本版本修复无依据文案和无素材生成尝试，新增有界格式纠错。最终候选仍有一个真实模型失败场景，不能声称全通过。

## 最终结果

|检查|结果|说明|
|---|---:|---|
|相同18任务对比|17/18达到预期|两个7场景轮次+4扰动，与第五阶段输入逐项一致|
|预算、原始截止、保存状态|18/18通过|未完成的任务没有冒称保存成功|
|新协议脚本|12/12原句校验通过|包括一个未保存任务保留的成功脚本|
|无资料扰动P04|0次无效生成/估算|旧版3次；新版只补查或停止|
|其他失败动作|3次|两次生成未满足原句/覆盖约束，一次模型动作格式错误，均拒绝而未伪装成功|
|预设工具故障|4次|两轮各一次生成、一次保存|
|免费最终回归|92项测试、10个离线场景通过|有界格式纠错、标量参数、原句拒绝、旧响应恢复等|

原始记录见[live-reviewed/summary.json](../examples/phase6/live-reviewed/summary.json)，独立重算见[comparison.json](../examples/phase6/comparison.json)，测试见[offline-release/summary.json](../examples/phase6/offline-release/summary.json)。免费重算命令：`python -X utf8 examples/phase6/compare.py`。比较脚本正常退出表示证据完整且可重算，不代表18任务全通过；请查看tasks_passed。

## 保留的失败

最终第三轮L07先遇到一个格式无效动作和一次生成校验失败，随后成功生成脚本；保存发生预设故障后，模型选择finish，最终partial/unrecoverable_failure，没有再次保存。脚本及已花费用均保留。业务工具支持重试，但本样本模型没有选择重试，因此仍是未通过场景。[实际结果](../examples/phase6/live-reviewed/round3/L07-actual.json)与[Trace](../examples/phase6/live-reviewed/round3/L07-trace.jsonl)未改写。

不以另一次成功重跑覆盖这一失败；若继续优化保存后的恢复决策，应另记版本并重新评价。当前交付如实披露17/18，类似题面允许的未完成部分，不将安全拒绝当作完美决策。

## 内容变化与代价

第五阶段同样18个新任务中12份生成脚本，有7份被AI助手审阅发现具体无依据断言。本轮12份脚本均仅含给定facts原句和中性开场/收尾，经程序逐行核对并逐份阅读；原句中不含此前发现的上线、价格、数量、具体UI选项等扩写。

两轮不是等价的“自由写作质量”比较：新版显著限制表达，只让模型选择并排列原句，标题固定。它解决当前资料上的新增事实风险，牺牲创作自由度，不能声称通用事实核查、营销文案质量或真实产品功能验证。旧任务/旧缓存没有source_sentences_v2标记时仍按旧质量边界处理。

## 修复过程中保留的记录

|记录目录|实际发生|
|---|---|
|live-fixed|首批5个已完成记录中4失败、1符合预期；模型返回句子数组，原适配器要求字符串。主动停止后另存一个未完成任务快照，未知API调用保留预留|
|smoke-normalized|接纳句子数组并用换行归一化后，正常烟测通过；仍逐句校验，未删事实或扩大白名单|
|live-final|正常任务在第二个决策收到缺少结尾括号的工具参数，MODEL_PROTOCOL_ERROR停止，保留记录|
|live-reviewed|加入计入动作上限的格式纠错后，完整18任务执行，17通过、1失败|

脱敏协议例子见[protocol-failure-examples.json](../examples/phase6/protocol-failure-examples.json)。原始报告未覆盖；命名live-final不表示该次最终成功，以本报告和live-reviewed记录为准。

## 调用时版本与最终源码

真实18任务执行后，独立审阅发现模型参数为null/布尔/数字/字符串时会触发AttributeError。先复现并增加回归，再在JSON解析后补一行dict类型检查；这类输入现在也记录为拒绝观察并受max_actions限制。

该补充没有改变出站提示词、正常对象参数处理、生成协议或费用逻辑，未额外发起付费重跑。精确代码差异见[parser-hardening.patch](../examples/phase6/parser-hardening.patch)，最终指纹见[release-manifest.json](../examples/phase6/release-manifest.json)。调用时指纹仍留在live-reviewed，comparison明确报告source_unchanged_since_live=false；不声称最终源码与付费运行字节完全相同。

最终源码已通过92项免费测试和10个离线场景。独立审阅核对旧生成方法与第五阶段AST完全相同，保证旧在途回执不因提示词变化被重发。详见[审阅记录](phase6-review.md)。

## 累计费用

项目二账本338条调用身份，336条已结算，2条未知调用各保留0.10元。已结算保守金额0.723103元，含预留累计0.923103元，剩余授权9.076897元。按公开费率和已知usage估算0.568772元；均非供应商账单。该总额包含所有失败、烟测和复测，原账本未重置。

## 候选交付状态

本地代码、题面、演示、历史失败、评估与复现记录齐备，候选ZIP不含var、密钥或本机数据库。GitHub账号已核对为ziming520-suring，用户已授权新建公开仓库xiaoying-content-planning-agent。仓库已由用户创建并确认公开及写入权限，交付地址见[SUBMISSION](../SUBMISSION.md)。克隆后可用 `git rev-parse HEAD` 固定复现版本；早期候选ZIP保留原始校验值，发布入口文档随后更新。候选人仍需据实填写个人用时、修改与亲自复跑情况。
