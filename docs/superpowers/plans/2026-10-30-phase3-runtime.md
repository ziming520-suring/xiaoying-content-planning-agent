# 项目 2 第三阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成任务持久化、CLI、离线动作循环和可验证的中断恢复。

**Architecture:** 每次本地工具动作的方案保存、完整工作台检查点、费用时间、动作记录同 SQLite 事务提交。决策器在事务外运行，单任务操作系统文件锁阻止双运行者；运行中断后明确停止，显式 resume 开新运行。现阶段工具均为本地离线函数，不把本地事务方案泛化为外部 API 原子性。

**Tech Stack:** Python 标准库、SQLite、Windows msvcrt / POSIX flock、unittest。

**Spec:** 需求与约定的项目2第一阶段规则与接口；docs/06-tools-contract.md。

## Global Constraints

- create/complete_input/run/resume 使用 schema_version/request_id/operation/payload。
- 缺主题、预算、截止时间进入 waiting_input；只补缺口，不覆盖硬要求。
- 请求同键同参数重放；不同参数冲突；task_id 业务唯一。
- 真实模型尚未接入，离线 Mock 明确标记；每次运行默认24、可设1–100动作，finish/非法动作也计数。
- 决策器在数据库事务外；本地动作统一提交，原子回滚不产生工具外部副作用。
- 不修改 project1；project2 当前无 Git 仓库，以阶段 ZIP 保留检查点。

## Review Focus

- 保存提交与状态提交之间崩溃，不能出现持久化半结果。
- 同 request_id 参数修改及缺口补齐后重放，不允许绕过身份绑定。
- 重启、损坏检查点、资料更新不能静默清空费用或改变依据。
- 同任务并发驱动或续跑不能重复执行，文件锁随进程释放。
- 循环触顶、模型异常、工具失败仍显示已完成部分；unknown 不盲目重试。

### Task 1: 请求、状态快照与事务内保存

Files: contracts.py, state.py, artifacts.py, tools.py, tests/test_runtime.py。
- [x] 写合法/非法请求、缺口、重放和快照测试，观察缺失实现失败。
- [x] 严格 JSON 解析、时间归一化、请求默认参数；工作台导出/恢复不使用 pickle。
- [x] SQLite 同连接方案保存与快照哈希校验；原第二阶段接口回归通过。

### Task 2: Runtime、互斥与离线循环

Files: runtime.py, locking.py, offline.py, tests/test_runtime.py, tests/runtime_worker.py。
Interfaces: Runtime.request(dict), status(task_id), drive(run_id, policy=None), trace(task_id)。
- [x] 写 task 状态、重复运行、预算替代、失败保留、执行上限、重启与真实子进程锁测试。
- [x] 实现任务/运行/请求重放/事件/提交恢复记录；资料快照随任务保存。
- [x] 单任务锁覆盖 drive 和启动/恢复；pending 可启动，running 中断后停止，resume 新 run。
- [x] 按动作原子提交，决策异常明确停止；所有回归通过。

### Task 3: CLI、证据和交付

Files: __main__.py, run_demo.py, tests/test_cli.py, tests/record_phase3.py, docs/07-runtime-contract.md。
- [x] CLI 子进程测试：execute、status、trace，非法输入无副作用。
- [x] 至少正常、缺信息、工具失败、超预算、重复/恢复场景留实际证据。
- [x] 独立审阅，必要修复并复测，生成检查点、干净解压复现。
