# 项目 2：内容任务规划 Agent

当前交付：四类业务工具、持久化任务与费用、CLI、请求重放、动作上限、单任务锁，以及百炼北京 qwen-plus 真实工具决策与脚本生成。默认演示仍为免费离线模式；真实调用须显式选择 bailian。业务工具使用合成资料、成本和逻辑时间，API 人民币支出单独记录。

当前在线脚本采用受约束的资料原句编排：模型选择和排列事实，程序拒绝新增事实断言，不宣称自由创作。旧版本结果保留原标签，不会自动变成新校验结果。

当前验证：92项免费测试、10个离线场景通过；最新真实对比17/18达到预期。一个保存失败场景中模型提前停止，完整失败证据见第六阶段报告。

## 运行

Python 3.12 及以上，标准库即可，无需安装依赖或配置密钥。在本目录运行：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 run_demo.py
python -X utf8 run_tools_demo.py
python -X utf8 tests/record_phase3.py --output var/my-phase3-check
```

最后一个命令要求输出目录尚不存在，避免覆盖旧证据。run_demo.py 使用持久化 Runtime，数据库位于 var/demo.sqlite3，重复执行不重做已完成任务；run_tools_demo.py 保留第二阶段固定工具序列演示，使用临时数据库。两者均免费离线。

## 阅读入口

- [交付与演示入口](SUBMISSION.md)
- [当前生成约束与兼容说明](docs/13-grounded-generation.md)
- [第六阶段修复与复测](docs/14-phase6-evaluation.md)
- [第五阶段稳定性与内容评估](docs/11-phase5-evaluation.md)
- [题面验收映射](docs/12-acceptance.md)
- [第四阶段接入与恢复说明](docs/09-live-integration.md)
- [第四阶段评估与真实记录](docs/10-phase4-evaluation.md)
- [方案与阶段边界](docs/02-solution.md)
- [第三阶段评估与证据](docs/08-phase3-evaluation.md)
- [CLI 与恢复契约](docs/07-runtime-contract.md)
- [工具细则](docs/06-tools-contract.md)
- [第三阶段进度](docs/phase3-progress.md)
- [最终 Runtime 验证汇总](examples/phase3/final/summary.json)
- [第二阶段历史验证](docs/03-evaluation.md)

## 目录

```text
src/content_planner/  四类工具、Runtime、事务存储、状态、锁、CLI、离线决策器
data/                 4 项素材、6 项费用配置、10 个工具场景及10个Runtime场景
tests/                unittest 与实际证据生成脚本
examples/phase2/      第二阶段历史工具证据
examples/phase3/      第三阶段 Runtime 证据
docs/                 需求、方案、评估、AI 使用、复盘和进度
run_tools_demo.py     离线工具演示
run_demo.py           持久化离线任务演示
```

第三阶段历史验证：56 项测试、10 个离线 Runtime 场景通过。第四阶段新增 API 预算、原生工具调用、在线脚本、远程调用预留与缓存恢复；最终验证与限制见第四阶段评估。不得删除 var/bailian-budget.sqlite3 以重置累计授权。
