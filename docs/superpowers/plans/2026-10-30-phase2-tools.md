# 项目 2 第二阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 交付合成资料及可独立验证的检索、生成、估算、保存工具。

**Architecture:** Python 标准库实现工具工作台，以内存上下文承载本阶段的账本、可信结果与计划版本；SQLite 仅用于方案保存。此为第三阶段持久化任务层的业务内核，不把内存测试工作台声称为生产 Runtime。

**Tech Stack:** Python 3.12、unittest、SQLite，无网络依赖。

**Spec:** 工作区需求与约定/项目2-第一阶段规则草案.md、项目2-第一阶段接口草案.md。

## Global Constraints

- 费用为非负整数，拒绝 bool；逻辑时间有时区，允许恰好截止完成。
- 失败按表计费，预检查拒绝不计费；费用和时间不倒退。
- 仅合成资料和明确标记的离线生成；不调用付费模型。
- 工具边界与模型入口不同；仅编排器持有工作台，不向未来模型开放内存对象。
- project2 是新的独立目录，无现有 Git 分支，不改 project1；本阶段以文件检查点代替 Git 提交。

## Review Focus

- 失败已花费 3、剩余生成 8、预算 10：必须判超预算。
- 成功结果被调用方修改：不能污染可信存储或伪造来源。
- 相同计划再次估算：版本不变、estimate_id 更新、时间推进。
- 保存时旧版本、脚本缺失或时间超限：不得写入产物。
- 文件写失败：保留先前成果并明确失败，不能假装保存成功。

### Task 1: 资料、检索与输入校验

Files: data/materials.json, data/tool_costs.json, src/content_planner/catalog.py, tests/test_catalog.py。
Interfaces: Catalog.load(path), Catalog.search(query), Catalog.rate(operation, mode_id)。
- [x] 先写并运行测试：命中、无结果、非法查询、重复 ID、非法费用、调用者修改返回值不影响源。
- [x] 实现严格资料加载、确定性检索与费用查询。
- [x] 跑通过后记录结果。

### Task 2: 估算、生成、保存工具

Files: src/content_planner/tools.py, src/content_planner/artifacts.py, tests/test_tools.py。
Interfaces: ToolWorkbench(catalog, task, artifact_db), search_materials(query), estimate_plan(steps), generate_script(plan_id, version, step_id), save_plan(plan_id, version)。
- [x] 先写并运行预算 12→8、沉没成本、时间边界、依赖、版本、离线生成、失败消耗与保存测试。
- [x] 实现可信结果、计划版本、内存尝试账本；模拟失败仅由测试配置控制。
- [x] SQLite 原子保存不可变快照，相同任务同版本内容冲突拒绝。
- [x] 跑完整工具测试并记录。

### Task 3: 场景证据与阶段文档

Files: data/scenarios.json, tests/record_phase2.py, examples/phase2/, README.md, docs/phase2-progress.md。
- [x] 固定资料和预期；执行场景记录实际输出及每次工具观察。
- [x] 比较预期和实际，保存源文件哈希及测试结果。
- [x] 独立审阅，必要修复后复测；记录本阶段限制。
- [x] 生成可恢复 ZIP 检查点并验证内容。
