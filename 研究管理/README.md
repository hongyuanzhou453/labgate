# LabGate 科研管理系统操作手册

从[科研管理工作台](工作台.md)查看已登记课题、假设与运行统计；共用能力演进见[建设路线](建设路线.md)。本仓库自带 `demo-selectivity-benchmark` 全合成演示工作区，开箱即可验证全部工具、六套验收与离线看板。

| 入口 | 用途 |
| --- | --- |
| [离线只读科研看板](派生/看板/科研看板.html) · [看板生成器](看板/build_dashboard.py) | 离线单文件交互看板：汇总实时门禁、合成多路线图表、只读文献库与周报脉络 |
| [周报交付文件夹](../周报/) · [周报契约](规范/周报契约_v1.md) | 默认输出“文件夹 + MD + 相关材料 + 来源清单”，自动承接上周计划、对账完成度并校验下周预期 |
| [W10b 冻结与测试访问契约](规范/W10b_冻结与测试访问_v1.md) · [集中词表](规范/词表.json) | 按题冻结协议、跨版本测试暴露台账及运行包敏感凭据扫描 |
| [全文库联查与状态核对契约](规范/全文库联查与状态核对_v1.md) | 文献与课题双向联查、`S15` 九节状态一致性核对及统一只读总检入口 |
| [W7 候选划分验算](规范/W7_候选划分验算_v1.md) | 跨库系列隔离、供体覆盖与折内配对检查接口 |
| [来源映射](来源映射.json) · [来源登记](来源登记.json) | 声明式上游来源路径、字节数、SHA-256 与表头绑定 |
| [示例课题状态](问题/demo-selectivity-benchmark/状态.md) · [示例协议草稿](问题/demo-selectivity-benchmark/协议/H1-MINIMAL/draft-1/design.json) | 展示 `17` 项未定科研决定保持 `null` 时 `G3` 的默认闭门行为 |
| [阶段要求](规范/阶段要求.json) · [接入映射](规范/接入映射.json) | `G1`/`G2`/`G3` 检查列表及表格映射规则 |

## 日常只读查询与维护命令

在仓库根目录使用 `python3 -B` 运行（所有查询默认只读，`--dry-run` 不创建目录或锁）：

~~~bash
python3 -B 研究管理/工具/research.py overview
python3 -B 研究管理/工具/research.py show demo-selectivity-benchmark
python3 -B 研究管理/工具/research.py trace L101 --problem demo-selectivity-benchmark
python3 -B 研究管理/工具/research.py topic-literature demo-selectivity-benchmark
python3 -B 研究管理/工具/research.py literature-health
python3 -B 研究管理/工具/research.py report check
python3 -B 研究管理/看板/build_dashboard.py --check
python3 -B 研究管理/看板/build_dashboard.py --refresh
~~~

## 阶段门禁与全量验收

```bash
# 运行全部 6 套隔离验证（181 项）+ G2 + G3 fail-closed + 看板自检 + 隐私脱敏扫描
python3 -B scripts/run_all_suites.py

# 单独校验 G1 / G2 / G3 阶段门禁
python3 -B 研究管理/工具/check_all.py --stage G1 --dry-run
python3 -B 研究管理/工具/check_all.py --stage G2 --runtime-report 研究管理/_交付/demo-baseline/runtime-final/report.json --dry-run
python3 -B 研究管理/工具/check_all.py --stage G3 --phase prepare --problem demo-selectivity-benchmark --dry-run
```
