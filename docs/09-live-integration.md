# 第四阶段：真实模型接入与恢复

当前第六阶段生成改为source_sentences_v2，新增原句校验、空素材工具白名单和有界格式纠错；本页保留接入与账本基础说明，生成行为更新见[修复说明](13-grounded-generation.md)。

用户于2026-10-30批准沿用百炼北京 qwen-plus，项目二累计上限10元，独立于项目一。密钥从进程环境 DASHSCOPE_API_KEY 读取，不写入代码、Trace或示例；.env.example仅说明配置，不自动加载.env。

## 执行入口

在项目目录，先设置好进程密钥，再运行：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -X utf8 -m content_planner --db var/online.sqlite3 execute examples/requests/create.json
python -X utf8 -m content_planner --db var/online.sqlite3 execute examples/requests/run.json --model bailian
python -X utf8 -m content_planner --db var/online.sqlite3 status EXAMPLE
```

execute/drive 默认 mock；只有 --model bailian 才启用在线策略。默认地址沿用项目一的北京工作空间端点。仅接受北京百炼HTTPS地址，拒绝重定向；模型固定qwen-plus非思考、单次输出最多2048 tokens、请求最多60000 UTF-8字节。报错不输出密钥或供应商响应正文。

在线策略使用原生function calling，根据成功/失败/不可行观察选择下一步，输出简短行动依据，不保存内部思维过程。生成器单独调用模型返回title/script/source_ids，并校验结构与来源ID。来源ID校验不等于语义事实验证，文本仍需抽查。

## 两套费用和时间口径

业务budget_limit、spent_cost、reserved_cost是合成工具表中的整数单位，不能称为API人民币费用。economy/premium/quick只是合成费用与逻辑耗时策略，均调用相同模型，不证明创意质量差异。截止时间基于逻辑时钟，不包含现实网络等待。

API账本固定在var/bailian-budget.sqlite3，发送前持久化预留0.10元，收到有效usage后按每输入token 1微元、输出token 3微元保守结算。北京官方非思考<=128k输入价格为输入0.8/输出2元每百万tokens，2026-10-30核对[官方说明](https://help.aliyun.com/zh/model-studio/qwen-plus)。estimated_yuan按该公开价格估算，accounted_yuan含保守结算及未知预留，两者均非供应商账单。

同call_key同参数只能返回已缓存响应；参数不同拒绝；结果未知拒绝重发。账本累计达到上限前会拒绝新调用。不可删除、更换路径或复制项目后另开账本来绕过项目二累计10元授权。

## 远程生成事务边界

1. 本地事务保存业务预留、原状态、调用身份及输入。
2. 事务外执行API请求；独立API账本保存usage与响应。
3. 本地保存远程回执，再原子结算业务费用、结果、动作计数和Trace。

网络失败无法证明供应商没收到请求，API预留保留；生成结果未知时业务预留也保留，并阻断resume。已收到但未完成本地结算的响应可恢复：

```powershell
python -X utf8 -m content_planner --db var/online.sqlite3 reconcile EXAMPLE
```

reconcile只使用现有缓存，不主动重发。发送前中断且API账本无该调用，释放对应业务预留；已缓存响应则结算一次；已标记unknown且没有可核对回执的调用继续阻断。当前没有供应商侧查询或任意清除unknown命令。恢复结算不代表整项任务完成；需显式task.resume继续后续动作。未完成时CLI退出码3。

数据库新建为v2；旧第三阶段v1必须显式升级，不清空旧状态：

```powershell
python -X utf8 -m content_planner --db var/tasks.sqlite3 upgrade
```

升级只支持完整第三阶段Runtime数据库，不支持第二阶段单独产物库。保留原有离线测试和入口。task.execution_mode记录最后运行的策略；每份脚本generator记录实际来源，saved_plan.snapshot.execution_mode仅在全部脚本为在线时标为bailian_live。跨模式续跑可能保留旧脚本，应查看脚本来源，不能仅凭最后策略断言所有内容在线生成。

## 显式付费验证

tests/record_live.py不是自动测试入口。指定新的输出目录运行七个受控场景，保存实际输入、结果、Trace、代码指纹和累计API记账。失败目录保留，不覆盖。unittest和record_phase3.py始终免费，不发真实API请求。
