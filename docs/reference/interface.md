# 项目 2：接口与数据结构草案

日期：2026-10-30。全部为设计示例，未实现、未运行；规则以[规则草案](confirmed-rules.md)为准。

## 请求和响应

写请求 envelope 精确字段：schema_version（整数 1）、request_id（非空字符串）、operation（下表枚举）、payload（对应对象）。拒绝未知字段、重复 JSON 键、非法枚举、非有限数值和类型错误；整数不接受布尔值。字符串去除首尾空白后不能为空；标识符保留原值用于身份比较，不静默改写。

| operation | payload 字段 | 用途 |
|---|---|---|
| task.create | task_id, topic, budget_limit, start_time, deadline | 创建任务；topic/budget_limit/deadline 可缺省或 null，形成 waiting_input |
| task.complete_input | task_id, topic, budget_limit, deadline | 仅补齐 waiting_input 的缺口，不覆盖已有值；非缺口字段不得提交 |
| task.run | task_id, max_actions | ready 任务首次执行；重复已结束运行返回状态，不自动续跑 |
| task.resume | task_id, max_actions | 对 partial/incomplete 任务显式继续；新的尝试使用新身份 |

task_id、start_time 为创建必填；空白 topic 当作缺失，非字符串拒绝。budget_limit 为非负整数或 null；deadline 为带时区的时间或 null。max_actions 为 1–100 的整数，省略默认 24。用户不直接提交执行步状态、实际费用或已保存标志。

外层 status 枚举 succeeded/failed/unknown，表示命令提交结果，与 data.task_status 区分；创建 waiting_input 任务本身可以 succeeded。请求启动后，通过 run_id 查询运行状态；重放启动请求返回原 run_id，不再次启动。只读 CLI status(task_id) 返回当前快照，不修改状态。

统一响应字段：schema_version、request_id、operation、status、data、error。成功时 error=null；失败/不确定时 error 包含 code、message、field（可为 null）、retryable。retryable 仅表示具备恢复可能，不授权自动换键执行。

```json
{
  "schema_version": 1,
  "request_id": "create-demo-001",
  "operation": "task.create",
  "payload": {
    "task_id": "T01",
    "topic": "介绍字幕编辑功能",
    "budget_limit": 10,
    "start_time": "2026-10-30T09:00:00+08:00",
    "deadline": "2026-10-30T10:00:00+08:00"
  }
}
```

上述只是合法输入示例，没有对应实际成功响应。

## 四类模型工具

工具以当前 task_id 的受限上下文运行，不允许跨任务读写。request_id、attempt_id 由编排器分配，不让模型伪造执行身份。

| 工具 | 模型参数 | 可信返回内容 |
|---|---|---|
| search_materials | query | result_id、素材 ID/版本/片段、命中与缺口 |
| estimate_plan | 候选 steps（每项 step_id、kind、depends_on、mode_id、input_refs） | plan_id/version、逐项费用与耗时、累计/剩余费用、预计完成时间、可行性及缺口 |
| generate_script | plan_id、plan_version、step_id | result_id、脚本内容、来源引用、attempt 状态、实际费用与耗时 |
| save_plan | plan_id、plan_version | artifact_id、保存状态、实际记录汇总或拒绝原因 |

estimate_plan 既估算又登记候选计划版本；同一候选规范化内容不重复产生版本。它允许返回不可行计划及缺口，供模型调整；不可行不等于工具故障。kind 限定 search_materials/generate_script/save_plan；estimate 本身为控制动作，不作为计划业务节点，耗时仍由账本记录。mode_id 必须来自工具表，depends_on 必须引用本计划节点、无环；input_refs 必须对应真实且适用的结果。替代计划可以引用旧成功结果，估算时只计算尚需执行的部分。

工具结果统一带 tool_status（succeeded/failed/unknown）、result、error；调用成功不等于方案可行。estimate 的 result.feasible=false 仍可 tool_status=succeeded。finish 为模型停止动作，带 reason，不属于业务工具；最终状态由程序汇总。

检索、估算、保存使用工具表唯一默认模式；生成模式由计划指定。每次真实执行估算也结算其表内耗时，输出基于本次估算完成后的逻辑时间与账本；重放原估算不再次耗时。最终保存前的剩余估算包含保存自身费用和耗时。保存复核是保存工具内部的确定性校验，不额外调用估算工具形成递归。

计划版本去重不等于估算结果重放：新估算调用生成新 estimate_id，读取最新账本、逻辑时间和可复用结果；只有原请求重放才返回旧估算。首次估算必须计入生成之后必需的最终估算耗时/费用，即使该控制动作不在业务步骤列表内；最终估算不再次预留另一次最终估算。额外补查和重规划的次数无法预知，发生时逐次记账并重新检查可行性，早期估算不构成完成保证。

## 核心对象和关系

| 对象 | 关键字段 |
|---|---|
| Task | task_id, topic, budget_limit, start_time, deadline, logical_time, task_status, missing_fields, stop_reason, active_plan_version |
| Plan | plan_id, task_id, version, superseded, steps, source_refs, estimate_id |
| Step | step_id, plan_version, kind, mode_id, depends_on, input_refs, status, result_id |
| Attempt | attempt_id, task_id, step_id（控制动作可为 null）, operation, input_fingerprint, status, cost_reserved, cost_actual, duration_reserved_seconds, duration_actual_seconds, result_id, error |
| Result | result_id, task_id, operation, content, source_versions, tool_table_version, input_fingerprint, created_by_attempt |
| SavedPlan | artifact_id, task_id, plan_version, snapshot, saved_at |
| Run | run_id, task_id, status, action_count, max_actions, trace_path |
| Replay | operation, request_id, normalized_request, response |

Task 1:N Plan；Plan 1:N Step；Step 1:N Attempt；成功 Attempt 关联 Result；可复用的 Result 可以被多个计划引用。SavedPlan 以 task_id + plan_version 唯一。费用按 Attempt 记账，不能按被引用次数重复累计。未知费用为 null 并保留预留，不能写 0 冒充已核实。

## 最终输出

必须输出 task_id、task_status、stop_reason、missing_fields、active_plan、steps、tool_results、budget、timing、saved_plan、unresolved_attempts。

- budget：limit、spent_cost、reserved_cost、remaining_available、remaining_cost_estimate（无可估计划时 null）、projected_total_cost（同前）。
- timing：start_time、current_time、deadline、estimated_finish_time（无法估算时 null）。
- saved_plan：成功后才包含 artifact_id 和内容/可读取位置，否则 null。
- steps：保留当前计划步骤和历史尝试的可追溯引用；失败明确显示 failed，不把其替代步骤成功反写为原步骤成功。

错误码建议：INVALID_REQUEST、REQUEST_CONFLICT、TASK_CONFLICT、TASK_NOT_FOUND、INVALID_STATE、DEPENDENCY_UNMET、STALE_PLAN、BUDGET_EXCEEDED、DEADLINE_EXCEEDED、TOOL_FAILED、STORAGE_FAILED、PENDING_RECONCILIATION。业务缺素材用工具正常返回空结果表达；输入缺口用 waiting_input 表达，不混为系统故障。

本草案定义第一阶段接口边界；字段长度上限、工具表具体数值、各错误的触发细节在第二阶段实现前补齐，不能据此宣称已有可直接调用 API。
