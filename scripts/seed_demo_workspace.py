#!/usr/bin/env python3
"""Deterministically seed a 100% synthetic, privacy-safe demonstration workspace for LabGate.

Creates synthetic literature ledger/DB, synthetic candidate problem dossier,
eligibility view, weekly carryover bundles, portable baseline snapshot, and
verified runtime report so all six validation suites and G1/G2 checks pass
immediately without containing any private or unpublished thesis data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "研究管理"
TOOLS = HOME / "工具"
DEMO = ROOT / "demo_data"
INDEX_DIR = ROOT / "论文内容索引"
PAPERS_DIR = INDEX_DIR / "papers"
TOPIC_ID = "demo-selectivity-benchmark"
TOPIC_DIR = HOME / "问题" / TOPIC_ID


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, obj: object) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def run_tool(argv: list[str], *, check: bool = True) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", *argv],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(argv)}\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def seed_demo_data() -> list[dict]:
    DEMO.mkdir(parents=True, exist_ok=True)

    ledger_rows = [
        {
            "id": "L101",
            "year": "2024",
            "authors": "Chen A, Patel R, Novak K",
            "title": "Series-Aware Evaluation Protocol for Multi-Assay Kinase Selectivity Models",
            "venue_or_status": "J Chem Inf Model (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2024.101",
            "topic": "benchmark-split",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic benchmark entry demonstrating congeneric series holdout variance.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L101.txt",
            "priority": "P0",
            "establishes": "Split leakage across close analogs inflates ΔpKd R^2 by +0.28 in synthetic benchmarks.",
            "does_not_establish": "Does not establish optimal donor weighting weights for external assays.",
        },
        {
            "id": "L102",
            "year": "2023",
            "authors": "Sato T, Muller H",
            "title": "Handling Inequality-Censored Ki Measurements in Selectivity Transfer",
            "venue_or_status": "Eur J Med Chem (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2023.102",
            "topic": "curation-censoring",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic curation benchmark contrasting exact point values with right-censored >10 uM records.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L102.txt",
            "priority": "P0",
            "establishes": "Imputing censored Ki inequalities as point targets introduces systematic ΔpKi bias.",
            "does_not_establish": "Does not prove Tobit loss improves prospective OOD ranking across scaffolds.",
        },
        {
            "id": "L103",
            "year": "2025",
            "authors": "Alvarez M, Kim J",
            "title": "When Retrieval-Guided Source Selection Fails Under Narrow Panel Coverage",
            "venue_or_status": "Nat Mach Intell (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2025.103",
            "topic": "source-borrowing",
            "added_in_round": "demo-v1",
            "evidence_mode": "negative_control",
            "evidence_note": "Synthetic negative-transfer reference showing matched-budget conditional baselines tying retrieval.",
            "supports_conclusion": "conditional",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L103.txt",
            "priority": "P0",
            "establishes": "Explicit source selection can underperform matched conditional baselines on sparse series.",
            "does_not_establish": "Does not rule out positive borrowing when donor overlap exceeds 300 ligands.",
        },
        {
            "id": "L104",
            "year": "2022",
            "authors": "Park S, Lindholm O",
            "title": "Author-Cluster Correlation in Public Kinase ChEMBL Extractions",
            "venue_or_status": "Bioinformatics (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2022.104",
            "topic": "curation-clustering",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic provenance audit demonstrating author-connected paper clusters.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L104.txt",
            "priority": "P1",
            "establishes": "Connected co-author clusters account for >75% of public bilateral series in synthetic pools.",
            "does_not_establish": "Does not establish construct equivalence across laboratories.",
        },
        {
            "id": "L105",
            "year": "2024",
            "authors": "Bhatia N, Rossi F",
            "title": "Matched-Budget Controls for Asymmetric Target/Off-Target Delta Regression",
            "venue_or_status": "J Med Chem (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2024.105",
            "topic": "delta-modeling",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic baseline design benchmark for single-point vs direct delta models.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L105.txt",
            "priority": "P1",
            "establishes": "Single-point target/off-target subtractors match pairwise delta regressors at equal budgets.",
            "does_not_establish": "Does not test pre-assay conformational pocket features.",
        },
        {
            "id": "L106",
            "year": "2021",
            "authors": "Gomez L, Wei Y",
            "title": "BAO Format Mismatches in Automated Single-Protein Assay Queries",
            "venue_or_status": "SLAS Discov (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2021.106",
            "topic": "assay-eligibility",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic assay-metadata quality check demonstrating cell-based leaks.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L106.txt",
            "priority": "P1",
            "establishes": "Automated Ki extraction requires explicit BAO single-protein filtering.",
            "does_not_establish": "Does not characterize ATP-competitive vs allosteric kinetics.",
        },
        {
            "id": "L107",
            "year": "2025",
            "authors": "Okafor C, Berg M",
            "title": "Holistic vs Catalytic-Domain Construct Drift in Competitive Displacement Panels",
            "venue_or_status": "ACS Omega (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2025.107",
            "topic": "construct-bias",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic construct comparison between full-length and truncated kinase domains.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L107.txt",
            "priority": "P1",
            "establishes": "Truncated catalytic domains diverge by up to 0.9 log units for type-II ligands.",
            "does_not_establish": "Does not measure cyclin partner effects in holoenzyme complexes.",
        },
        {
            "id": "L108",
            "year": "2023",
            "authors": "Tanaka K, Dupont E",
            "title": "Bootstrap Power Diagnostics for Small Congeneric Series Counts",
            "venue_or_status": "Patterns (Synthetic Demo Ref)",
            "doi_or_url": "10.0000/labgate.demo.2023.108",
            "topic": "statistical-power",
            "added_in_round": "demo-v1",
            "evidence_mode": "quantitative_audit",
            "evidence_note": "Synthetic power analysis motivating underpowered_series_count warnings.",
            "supports_conclusion": "yes",
            "peer_reviewed": "true",
            "local_pdf": "demo_data/papers/L108.txt",
            "priority": "P0",
            "establishes": "Fewer than 6 held-out series cannot reliably separate +0.15 MAE noise from signal.",
            "does_not_establish": "Does not define universal harm thresholds without dev variance calibration.",
        },
        {
            "id": "L109",
            "year": "2024",
            "authors": "Chen A, Patel R, Novak K",
            "title": "Preprint: Series-Aware Evaluation Protocol for Multi-Assay Kinase Selectivity",
            "venue_or_status": "arXiv (Synthetic Preprint Alias)",
            "doi_or_url": "10.0000/labgate.demo.preprint.109",
            "topic": "benchmark-split",
            "added_in_round": "demo-v1",
            "evidence_mode": "version_alias",
            "evidence_note": "Preprint version grouped with L101 via SRC-ALIASES to test related_version_only guard.",
            "supports_conclusion": "yes",
            "peer_reviewed": "false",
            "local_pdf": "demo_data/papers/L109.txt",
            "priority": "P2",
            "establishes": "Same work group as L101; never double-counted as independent confirmation.",
            "does_not_establish": "Not an independent peer-reviewed source.",
        },
        {
            "id": "L110",
            "year": "2026",
            "authors": "Fischer D, Holm T",
            "title": "Pre-Assay Structural Conditioning in Kinase Families (Unindexed Placeholder)",
            "venue_or_status": "Manuscript in preparation (Synthetic Unindexed)",
            "doi_or_url": "10.0000/labgate.demo.2026.110",
            "topic": "3d-conditioning",
            "added_in_round": "demo-v1",
            "evidence_mode": "unindexed_ref",
            "evidence_note": "Demonstrates not_indexed boundary in trace queries.",
            "supports_conclusion": "no",
            "peer_reviewed": "false",
            "local_pdf": "demo_data/papers/L110.txt",
            "priority": "P2",
            "establishes": "Placeholder reference absent from formal verified card index.",
            "does_not_establish": "Zero verified claims until full text is indexed.",
        },
    ]

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(ledger_rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(ledger_rows)
    write_text(DEMO / "demo_ledger.csv", buf.getvalue())

    write_json(
        DEMO / "demo_aliases.json",
        {
            "schema_version": "source-aliases.v1",
            "groups": [
                {
                    "work_id": "W101",
                    "canonical_id": "L101",
                    "related_ids": ["L109"],
                    "note": "Journal version (L101) and earlier preprint (L109) share canonical identity.",
                }
            ],
        },
    )

    compare_cols = [
        "row_id",
        "paper_id",
        "任务",
        "使用时已知信息",
        "方法改变了什么",
        "数据与标签预算",
        "划分与评价",
        "排除的替代解释",
        "相对本问题",
        "核验",
    ]
    compare_rows = [
        {
            "row_id": "C01",
            "paper_id": "L101",
            "任务": "多测定激酶 ΔpKd 选择性预测",
            "使用时已知信息": "2D 图特征 + 目标/反靶序列锚点",
            "方法改变了什么": "引入系列感知留出（Series Holdout）协议",
            "数据与标签预算": "48 个跨源双侧系列 / 受控标签预算",
            "划分与评价": "Murcko >=3 系列分组留一 + 95% 自助区间",
            "排除的替代解释": "近邻结构泄漏放大虚高增益",
            "相对本问题": "定义本合成示例题的标准 series-level 评测基底",
            "核验": "已核示例",
        },
        {
            "row_id": "C02",
            "paper_id": "L102",
            "任务": "含不等式删失的 Ki 数据建模",
            "使用时已知信息": "分子指纹 + 明确等号/删失边界标记",
            "方法改变了什么": "隔离 > / < 删失记录与精确点值",
            "数据与标签预算": "合成 Ki 测量池（含 16.5% 删失样本）",
            "划分与评价": "等预算删失隔离 vs 直接插补对照组",
            "排除的替代解释": "边界插补导致的伪选择性差值",
            "相对本问题": "约束准入视图必须在 point_training 排除非精确边界",
            "核验": "已核示例",
        },
        {
            "row_id": "C03",
            "paper_id": "L103",
            "任务": "检索引导历史源选源迁移",
            "使用时已知信息": "历史供体池描述 + 目标少量支撑点",
            "方法改变了什么": "显式供体加权选源对比同信息条件模型",
            "数据与标签预算": "固定供体池规模与匹配调参次数",
            "划分与评价": "未见系列 ΔpKd MAE 与置换零对照",
            "排除的替代解释": "靠增加调参步数或隐式标签泄漏获胜",
            "相对本问题": "提供关键负结果护栏：同预算条件下不可预设显式选源必胜",
            "核验": "已核示例",
        },
        {
            "row_id": "C04",
            "paper_id": "L108",
            "任务": "小系列数统计检验力诊断",
            "使用时已知信息": "系列级误差向量（无标签前提）",
            "方法改变了什么": "低于 6 个独立系列触发 underpowered 警告",
            "数据与标签预算": "3–12 个系列分层合成采样",
            "划分与评价": "系列均值增益 + 系列损害率 (harmed fraction)",
            "排除的替代解释": "样本微弱噪声被误判为跨系列真实增益",
            "相对本问题": "决定候选面板筛选须优先满足多配体同系物系列数",
            "核验": "已核示例",
        },
        {
            "row_id": "C05",
            "paper_id": "项目参照",
            "任务": "三层边界判别（H1 -> H2.1 -> H2.2）",
            "使用时已知信息": "同预算前提下分层解锁部署前生化条件",
            "方法改变了什么": "未确认决定严格 hold-off；正负结论双闭环",
            "数据与标签预算": "待定（决策前保持 null，不设伪预算）",
            "划分与评价": "先系列隔离后配对 + 测试暴露跨版本追查",
            "排除的替代解释": "混淆单点拟合精度、可解释性与因果增益",
            "相对本问题": "LabGate 示例课题主参照行",
            "核验": "草稿对照",
        },
    ]
    tbuf = io.StringIO(newline="")
    tw = csv.DictWriter(tbuf, fieldnames=compare_cols, delimiter="\t", lineterminator="\n")
    tw.writeheader()
    tw.writerows(compare_rows)
    write_text(DEMO / "demo_compare.tsv", tbuf.getvalue())

    edge_cols = ["from_id", "to_id", "relation", "compare_rows", "locator", "status", "basis"]
    edge_rows = [
        {
            "from_id": "L101",
            "to_id": "项目参照",
            "relation": "supports_protocol",
            "compare_rows": "C01;C05",
            "locator": "Synthetic Spec Sec 2.1",
            "status": "checked_demo",
            "basis": "Provides series-aware holdout and bootstrap evaluation unit.",
        },
        {
            "from_id": "L103",
            "to_id": "项目参照",
            "relation": "constrains_claim",
            "compare_rows": "C03;C05",
            "locator": "Synthetic Negative Control Fig 1",
            "status": "checked_demo",
            "basis": "Prevents assuming explicit source selection outperforms matched conditional baselines.",
        },
        {
            "from_id": "L108",
            "to_id": "L101",
            "relation": "calibrates_power",
            "compare_rows": "C01;C04",
            "locator": "Synthetic Power Table 2",
            "status": "checked_demo",
            "basis": "Defines minimum independent series count before running confirmatory tests.",
        },
    ]
    ebuf = io.StringIO(newline="")
    ew = csv.DictWriter(ebuf, fieldnames=edge_cols, lineterminator="\n")
    ew.writeheader()
    ew.writerows(edge_rows)
    write_text(DEMO / "demo_edges.csv", ebuf.getvalue())

    write_text(
        DEMO / "demo_audit_review.md",
        "# Synthetic Demo Eligibility Audit Review\n\n"
        "- Scope: Self-contained synthetic multi-panel measurements for demonstrating LabGate fail-closed eligibility gates.\n"
        "- All numbers in `demo_data/` are purely synthetic fixtures with zero real unpublished screening values.\n",
    )

    # Synthetic measurements (48 rows: 30 strict Kd points, 10 censored >, 8 Ki/high-tail)
    mcols = [
        "measurement_id", "construct_id", "compound_id", "value_status", "measurement_type",
        "is_verified_kd_point", "is_strict_clean_kd_point", "pkd_point", "pkd_lower", "pkd_upper",
        "lower_strict", "upper_strict", "source_locator", "lineage_id", "layer_id",
    ]
    mbuf = io.StringIO(newline="")
    mw = csv.DictWriter(mbuf, fieldnames=mcols, lineterminator="\n")
    mw.writeheader()
    for idx in range(1, 49):
        mid = f"DM-{idx:04d}"
        cid = f"CMPD-{(idx - 1) // 2 + 1:03d}"
        targ = "SYN_T_WT" if idx % 2 == 1 else "SYN_O_WT"
        if idx <= 30:
            val = f"{6.5 + (idx % 7) * 0.25:.2f}"
            mw.writerow({
                "measurement_id": mid, "construct_id": targ, "compound_id": cid,
                "value_status": "point_estimate", "measurement_type": "Kd",
                "is_verified_kd_point": "True", "is_strict_clean_kd_point": "True",
                "pkd_point": val, "pkd_lower": val, "pkd_upper": val,
                "lower_strict": "False", "upper_strict": "False",
                "source_locator": f"demo_table_r{idx}", "lineage_id": "SYN-LIN-A", "layer_id": "L1",
            })
        elif idx <= 40:
            mw.writerow({
                "measurement_id": mid, "construct_id": targ, "compound_id": cid,
                "value_status": "right_censored", "measurement_type": "Kd",
                "is_verified_kd_point": "False", "is_strict_clean_kd_point": "False",
                "pkd_point": "", "pkd_lower": "", "pkd_upper": "5.00",
                "lower_strict": "False", "upper_strict": "True",
                "source_locator": f"demo_table_r{idx}", "lineage_id": "SYN-LIN-A", "layer_id": "L1",
            })
        else:
            val = f"{7.1 + (idx % 5) * 0.2:.2f}"
            mw.writerow({
                "measurement_id": mid, "construct_id": targ, "compound_id": cid,
                "value_status": "point_estimate_high_kd_tail", "measurement_type": "Kd",
                "is_verified_kd_point": "True", "is_strict_clean_kd_point": "False",
                "pkd_point": val, "pkd_lower": val, "pkd_upper": val,
                "lower_strict": "False", "upper_strict": "False",
                "source_locator": f"demo_table_r{idx}", "lineage_id": "SYN-LIN-B", "layer_id": "L2",
            })
    write_text(DEMO / "demo_measurements.csv", mbuf.getvalue())

    # Synthetic route summary & synthetic eval report for Dashboard Exhibits
    write_json(
        DEMO / "demo_panel_audit.json",
        {
            "schema_version": "demo-panel-audit.v1",
            "note": "Purely synthetic demonstration numbers for LabGate offline dashboard.",
            "panels": [
                {"label": "SYN-KinA / KinB Ki", "bilateral": 420, "docs": 18, "murcko_total": 185, "murcko_ge2": 64, "murcko_ge3": 36, "donor_non_overlap": 240, "donor_docs": 12},
                {"label": "SYN-RecC / RecD Ki", "bilateral": 140, "docs": 7, "murcko_total": 52, "murcko_ge2": 21, "murcko_ge3": 12, "donor_non_overlap": 115, "donor_docs": 6},
                {"label": "SYN-CpxE / CpxF Ki", "bilateral": 76, "docs": 4, "murcko_total": 28, "murcko_ge2": 14, "murcko_ge3": 9, "donor_non_overlap": 62, "donor_docs": 4},
                {"label": "SYN-BroadG / H Kd", "bilateral": 84, "docs": 1, "murcko_total": 72, "murcko_ge2": 3, "murcko_ge3": 1, "donor_non_overlap": None, "donor_docs": None},
                {"label": "SYN-BroadJ / K Kd", "bilateral": 68, "docs": 1, "murcko_total": 61, "murcko_ge2": 2, "murcko_ge3": 0, "donor_non_overlap": None, "donor_docs": None},
            ],
            "waterfall": {
                "raw_measurements": 1200,
                "censored_gt_lt": -198,
                "exact_measurements": 1002,
                "multi_assay_dedup": -242,
                "unilateral_excluded": -340,
                "bilateral_ligands": 420,
                "patent_only_excluded": -280,
                "journal_bilateral": 140,
            },
        },
    )

    write_json(
        DEMO / "demo_synthetic_eval.json",
        {
            "schema_version": "synthetic-eval-proto.v1",
            "controls": [
                {
                    "name": "positive_12_series (Heterogeneous Signal)",
                    "groups": 12,
                    "compounds": 96,
                    "delta_gain": "+0.3959",
                    "ci95": "[+0.3254, +0.4806]",
                    "harmed": "0/12 (Uni 6/12)",
                    "kind": "pass",
                    "warnings": "—",
                },
                {
                    "name": "negative_12_series_perm (Permutation Null)",
                    "groups": 12,
                    "compounds": 96,
                    "delta_gain": "+0.0335",
                    "ci95": "[-0.1686, +0.2533]",
                    "harmed": "6/12 (50% harmed)",
                    "kind": "risk",
                    "warnings": "CI crosses zero; negative transfer detected",
                },
                {
                    "name": "small_3_series_underpowered (Power Guard)",
                    "groups": 3,
                    "compounds": 30,
                    "delta_gain": "+0.3241",
                    "ci95": "[+0.3006, +0.3631]",
                    "harmed": "0/3",
                    "kind": "accent",
                    "warnings": "underpowered_series_count",
                },
            ],
        },
    )

    write_json(
        DEMO / "demo_glossary.json",
        {
            "congeneric_series": ["Congeneric Series / 同系物系列", "Analogous ligands sharing a core scaffold in a SAR series.", "共享核心骨架的一组药化修饰配体，是防泄漏评估的独立结构单位。"],
            "fail_closed": ["Fail-Closed Gate / 默认闭门护栏", "Missing prerequisites or unconfirmed research choices hard-block downstream runs.", "缺失前置确认或校验证据时显式拒绝执行，绝不静默跳过或代填决策。"],
            "censored_ki": ["Inequality-Censored Value (>/<)", "Upper/lower assay bounds rather than exact singleton intervals.", "带不等号界限的测量记录，与精确点值严格区分，禁止插补为精确靶标差。"],
            "delta_p": ["Target vs. Off-Target Selectivity (Δ_P)", "Affinity difference pKd/pKi(Target) - pKd/pKi(Off-target) on a fixed panel.", "同一配体在固定目标靶点与反靶点之间的选择性差值。"],
            "test_exposure": ["Cross-Version Test Exposure Journal", "Immutable append-only log tracking every access to final_test splits across protocol freezes.", "跨协议版本追查 final_test 预登记与实际读取时间的只增不改审计日志。"],
            "carryover_reconciliation": ["Weekly Carryover Reconciliation", "Every unfinished item from week N-1 must map to a task in week N or a reasoned backlog entry.", "周报承接对账机制，严禁静默丢失未完成任务。"],
        },
    )

    return ledger_rows


def seed_literature_db(ledger_rows: list[dict]) -> None:
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    db_path = INDEX_DIR / "literature.db"
    ledger_sha = sha256_bytes((DEMO / "demo_ledger.csv").read_bytes())

    concepts = [
        ("C01", "历史供体选源与源任务检索迁移", "Historical Source Selection & Transfer"),
        ("C02", "目标—反靶选择性与固定蛋白面板", "Target vs Off-Target Selectivity"),
        ("C03", "防泄漏划分协议与 OOD 泛化", "Leakage-Controlled Splitting & OOD"),
        ("C04", "公共库策展与删失/边界可比性", "Database Curation & Censoring Bounds"),
        ("C05", "统计检验力与正负合成对照诊断", "Power Diagnostics & Synthetic Controls"),
    ]

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE corpus_inventory (
                paper_id TEXT PRIMARY KEY,
                work_id TEXT,
                title TEXT,
                doi_or_url TEXT,
                status TEXT
            );
            CREATE TABLE papers (
                paper_id TEXT PRIMARY KEY,
                work_id TEXT,
                title TEXT,
                short_title TEXT,
                year INTEGER,
                authors TEXT,
                venue_or_status TEXT,
                doi TEXT,
                priority TEXT,
                ledger_topic TEXT,
                theme_name TEXT,
                reading_type TEXT,
                review_status TEXT,
                quantitative_check_status TEXT,
                result_count INTEGER,
                visual_verified_count INTEGER,
                text_verified_count INTEGER,
                pending_row_count INTEGER,
                card_path TEXT,
                card_sha256 TEXT,
                card_bytes INTEGER,
                source_path TEXT,
                source_pages INTEGER,
                retrieval_abstract TEXT,
                negative_results_summary TEXT,
                unsupported_claims_summary TEXT,
                project_use_summary TEXT
            );
            CREATE TABLE results (
                id TEXT PRIMARY KEY,
                paper_id TEXT,
                work_id TEXT,
                result_id TEXT,
                task TEXT,
                sample TEXT,
                split TEXT,
                condition TEXT,
                metric TEXT,
                value TEXT,
                uncertainty TEXT,
                comparator TEXT,
                locator TEXT,
                check_status TEXT,
                card_sha256 TEXT
            );
            CREATE TABLE concepts (
                concept_id TEXT PRIMARY KEY,
                name TEXT,
                en_name TEXT
            );
            CREATE TABLE paper_concepts (
                paper_id TEXT,
                concept_id TEXT,
                PRIMARY KEY (paper_id, concept_id)
            );
            """
        )

        conn.execute("INSERT INTO meta VALUES (?, ?)", ("schema_version", "2.0.0"))
        conn.execute(
            "INSERT INTO meta VALUES (?, ?)",
            ("inputs_detail_json", json.dumps({"ledger": ledger_sha}, sort_keys=True)),
        )
        for cid, cname, cen in concepts:
            conn.execute("INSERT INTO concepts VALUES (?, ?, ?)", (cid, cname, cen))

        work_map = {"L109": "L101"}
        concept_map = {
            "L101": ["C02", "C03"],
            "L102": ["C04"],
            "L103": ["C01", "C02"],
            "L104": ["C03", "C04"],
            "L105": ["C01", "C02"],
            "L106": ["C04"],
            "L107": ["C02", "C04"],
            "L108": ["C03", "C05"],
        }

        for row in ledger_rows:
            pid = row["id"]
            wid = work_map.get(pid, pid)
            inv_status = "formal_verified" if pid in {f"L{n}" for n in range(101, 109)} else "unindexed"
            conn.execute(
                "INSERT INTO corpus_inventory VALUES (?, ?, ?, ?, ?)",
                (pid, wid, row["title"], row["doi_or_url"], inv_status),
            )
            if inv_status != "formal_verified":
                continue

            card_rel = f"论文内容索引/papers/{pid}.md"
            front = {
                "paper_id": pid,
                "work_id": wid,
                "title": row["title"],
                "review_status": "full_text_reviewed",
                "quantitative_check_status": "selected_results_verified",
            }
            card_md = (
                "---\n"
                + json.dumps(front, ensure_ascii=False, indent=2)
                + "\n---\n\n"
                + f"# {pid} · {row['title']}\n\n"
                + f"- Scope note: Synthetic open-source demonstration card for LabGate.\n"
                + f"- Establishes: {row['establishes']}\n"
                + f"- Does not establish: {row['does_not_establish']}\n"
            )
            card_bytes = card_md.encode("utf-8")
            write_bytes(ROOT / card_rel, card_md.encode("utf-8"))
            csha = sha256_bytes(card_bytes)

            res_items = [
                (
                    f"{pid}:R01",
                    pid,
                    wid,
                    "R01",
                    f"{row['topic']} primary eval",
                    "Synthetic 12-series benchmark",
                    "Series Holdout",
                    "Matched budget",
                    "ΔpKd MAE",
                    "0.312",
                    "±0.024",
                    "Naive pooling 0.441",
                    "Synthetic Table 1, p.4",
                    "visual_verified",
                    csha,
                ),
                (
                    f"{pid}:R02",
                    pid,
                    wid,
                    "R02",
                    f"{row['topic']} null control",
                    "Synthetic permuted labels",
                    "Series Holdout",
                    "Label permutation",
                    "Series Gain",
                    "+0.021",
                    "95% CI [-0.14, +0.18]",
                    "Baseline 0.000",
                    "Synthetic Fig 2, p.7",
                    "text_verified",
                    csha,
                ),
            ]
            for rit in res_items:
                conn.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rit)

            conn.execute(
                "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    wid,
                    row["title"],
                    f"{pid} · {row['title'][:36]}",
                    int(row["year"]),
                    row["authors"],
                    row["venue_or_status"],
                    row["doi_or_url"],
                    row["priority"],
                    row["topic"],
                    row["topic"],
                    "Full-Text Verified (Synthetic Demo)",
                    "full_text_reviewed",
                    "selected_results_verified",
                    len(res_items),
                    1,
                    1,
                    0,
                    card_rel,
                    csha,
                    len(card_bytes),
                    row["local_pdf"],
                    10,
                    row["evidence_note"],
                    row["does_not_establish"],
                    "Cannot generalize claims outside matched assay and split definitions.",
                    row["establishes"],
                ),
            )
            for cid in concept_map.get(pid, ["C01"]):
                conn.execute("INSERT INTO paper_concepts VALUES (?, ?)", (pid, cid))

        conn.commit()
    finally:
        conn.close()


def seed_topic_and_eligibility() -> None:
    if TOPIC_DIR.exists():
        shutil.rmtree(TOPIC_DIR)

    run_tool([str(TOOLS / "research.py"), "new", TOPIC_ID, "--title", "Synthetic Selectivity Borrowing Demo"])

    # Replace 状态.md with complete 9-section scientific state + card refs
    state_md = (
        "# 固定面板历史借用（合成演示课题）：当前状态\n\n"
        f"2026-11-24。problem_id=`{TOPIC_ID}`。本文件为公开开源演示用的 100% 合成课题卷宗，用于展示 S15 九节核查与 G3 fail-closed 机制。\n\n"
        "## 当前判断\n\n"
        "全部 8 篇合成核心文献已进入只读全文核验库（16 条定量结果，0 pending）。工程门禁 G1 与 G2 已通过；因 17 项核心科研决定尚待人工外部确认，G3 准入保持关闭，`formal_use_allowed` 为 `false`。\n\n"
        "## 支持证据\n\n"
        "1. 合成基线表明，在受控预算下以同系物系列为单位留出可消除近邻结构泄漏放大效应。\n"
        "2. 合成正负对照验证了当持有足够多独立系列时，跨系列平均增益与置信区间可有效分离。\n\n"
        "- [主卡 L101] 已核验\n"
        "- [主卡 L102] 已核验\n\n"
        "## 反对证据\n\n"
        "1. 当广谱筛查面板仅含 <=3 个多配体系列时，统计检验力不足（触发 underpowered 告警）。\n"
        "2. 显式检索选源策略在稀疏系列下可能出现负迁移，必须与同信息条件基线和标签置换对照并行。\n\n"
        "## 资源缺口\n\n"
        "外部确认原件、正式阈值登记、云端 GPU 训练仓库与持久存储路径尚未指定。\n\n"
        "## 实验草案\n\n"
        "[H1设计草稿](协议/H1-MINIMAL/draft-1/design.json) 中 17 项待确认科研字段全部保持 `null`，仅记录缺失原因 `not_checked`。\n\n"
        "## 决策记录\n\n"
        "[科研决定登记](决策.json) 当前为空（`[]`），尚无已确认正式决定。\n\n"
        "## 需领域确认\n\n"
        "1. 在多系列药化测定数据与单库广谱筛选面板之间明确首轮主路线及测量端点（Kd 或 Ki）。\n"
        "2. 明确代理 Murcko 骨架是否可近似作为独立 SAR 系列单位。\n\n"
        "## 待决定\n\n"
        "1. 由研究者与指导者共同确认 `target_offtarget`、`endpoint` 与 `source_selection` 后再生成候选划分。\n"
        "2. 在开发集观测基线波动前，暂不盲目预设 `effect_threshold` 与 `harm_threshold`。\n\n"
        "## 下一步\n\n"
        "1. 完成多路线覆盖对比后确认课题范围与主端点。\n"
        "2. 随后履行 `决策.json` 外置确认登记、`W7` 划分检查与 `W10b` 协议冻结。\n"
    )
    write_text(TOPIC_DIR / "状态.md", state_md)

    hyp_csv = (
        "problem_id,hypothesis_id,hypothesis_version,statement,claim_scope,protocol_id,protocol_version,supersedes,decision_ref,note\n"
        f"{TOPIC_ID},H1,draft-1,固定预算下来源加权收益能否跨独立合成系列重复,固定面板同信息同预算,H1-MINIMAL,draft-1,,,提案未确认\n"
        f"{TOPIC_ID},H2.1,draft-1,部署前可得结构背景相对相似性对照组是否有净增益,固定面板同信息同预算,H1-MINIMAL,draft-1,,,H1后再决定\n"
        f"{TOPIC_ID},H2.2,draft-1,相同输入条件模型之后显式选源是否仍有额外价值,固定面板同信息同预算,H1-MINIMAL,draft-1,,,H1后再决定\n"
    )
    write_text(TOPIC_DIR / "假设.csv", hyp_csv)

    bud_csv = (
        "problem_id,budget_id,kind,unit,planned_limit,actual_value,missing_reason,count_definition,stop_action\n"
        f"{TOPIC_ID},BUD-T,target_labels,unique_measurements,,,not_checked,按目标侧测量去重,未确认不运行\n"
        f"{TOPIC_ID},BUD-O,offtarget_labels,unique_measurements,,,not_checked,按反靶侧测量去重,未确认不运行\n"
        f"{TOPIC_ID},BUD-GPU,compute,gpu_hours,,,not_checked,多GPU累计时长上限,达到确认上限即停止\n"
    )
    write_text(TOPIC_DIR / "预算.csv", bud_csv)

    req_keys = [
        "target_offtarget", "endpoint", "source_selection", "construct_comparability",
        "series_rule", "information_availability", "effect_threshold", "harm_threshold",
        "precision_target", "seed_plan", "resampling_plan", "multiplicity_policy",
        "label_budget", "compute_budget", "training_repository", "persistent_storage",
        "prior_test_exposure",
    ]
    design_path = TOPIC_DIR / "协议/H1-MINIMAL/draft-1/design.json"
    design = {
        "schema_version": "design-draft.v0.2",
        "problem_id": TOPIC_ID,
        "protocol_id": "H1-MINIMAL",
        "version": "draft-1",
        "status": "draft",
        "formal_use_allowed": False,
        "study_kind": "retrospective_exploratory_proposal",
        "hypotheses": [
            {"hypothesis_id": "H1", "hypothesis_version": "draft-1", "statement": "Fixed-budget source weighting yields repeatable series-level selectivity gains on unseen chemical series.", "status": "proposed"},
            {"hypothesis_id": "H2.1", "hypothesis_version": "draft-1", "statement": "Pre-assay pocket/conformational context adds value beyond similarity and label-permutation controls.", "status": "deferred_until_H1"},
            {"hypothesis_id": "H2.2", "hypothesis_version": "draft-1", "statement": "Explicit source selection outperforms an equally informed conditional baseline under matched budgets.", "status": "deferred_until_H1"},
        ],
        "decisions": {k: None for k in req_keys},
        "missing_reasons": {f"decisions.{k}": "not_checked" for k in req_keys},
        "freeze_record_ref": None,
        "decision_refs": [],
        "notes": "Synthetic draft for open-source LabGate demonstration; 17 required decisions remain null.",
    }
    write_json(design_path, design)

    # Register sources first so SRC-LEDGER / SRC-COMPARE / SRC-EDGES exist
    reg_path = HOME / "来源登记.json"
    if reg_path.exists():
        reg_path.unlink()
    run_tool([str(TOOLS / "register_sources.py")])

    # Build diagnostic eligibility preview view
    elig_dir = HOME / "数据/准入/E-DEMO-20261124"
    if elig_dir.exists():
        shutil.rmtree(elig_dir)
    elig_dir.mkdir(parents=True, exist_ok=True)
    meas_path = DEMO / "demo_measurements.csv"
    audit_path = DEMO / "demo_audit_review.md"
    rules = {
        "schema_version": "eligibility-preview.v1",
        "problem_id": TOPIC_ID,
        "eligibility_id": "E-DEMO-20261124",
        "mode": "diagnostic_only",
        "formal_use_allowed": False,
        "intended_uses": ["diagnostic", "point_training"],
        "design_ref": {
            "path": design_path.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(design_path.read_bytes()),
            "bytes": design_path.stat().st_size,
        },
        "source_snapshot_refs": [
            {
                "path": audit_path.relative_to(ROOT).as_posix(),
                "sha256": sha256_bytes(audit_path.read_bytes()),
                "bytes": audit_path.stat().st_size,
            }
        ],
        "measurements_ref": {
            "path": meas_path.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(meas_path.read_bytes()),
            "bytes": meas_path.stat().st_size,
        },
    }
    rules_path = elig_dir / "eligibility_rules.json"
    write_json(rules_path, rules)
    sys.path.insert(0, str(TOOLS))
    from build_eligibility import generate  # noqa: E402
    generate(ROOT, rules_path, elig_dir, dry_run=False)

    # Bind literature and G3 preview cleanly via 接入.json
    elig_mf = elig_dir / "manifest.json"
    write_json(
        TOPIC_DIR / "接入.json",
        {
            "schema_version": "topic-bindings.v1",
            "problem_id": TOPIC_ID,
            "literature": {"comparison": "SRC-COMPARE", "relations": "SRC-EDGES"},
            "g3": {
                "profile": "fixed-panel-preview.v1",
                "design_ref": {
                    "path": design_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256_bytes(design_path.read_bytes()),
                    "bytes": design_path.stat().st_size,
                },
                "eligibility_ref": {
                    "path": elig_mf.relative_to(ROOT).as_posix(),
                    "sha256": sha256_bytes(elig_mf.read_bytes()),
                    "bytes": elig_mf.stat().st_size,
                },
            },
        },
    )


def seed_weekly_reports() -> None:
    for d in [HOME / "周报记录", HOME / "派生/周报", ROOT / "周报"]:
        if d.exists():
            shutil.rmtree(d)
    (HOME / "周报记录").mkdir(parents=True, exist_ok=True)
    (HOME / "派生/周报").mkdir(parents=True, exist_ok=True)
    (ROOT / "周报").mkdir(parents=True, exist_ok=True)

    # Legacy W46 anchor (clean synthetic sample)
    legacy_w46 = (
        "# 周报 · W46（11.09–11.15）· Researcher\n\n"
        "> 合成示例周报（用于演示 LabGate 上周计划自动化承接与哈希锁定）。\n\n"
        "## 本周一句话\n\n"
        "完成合成选择性预测课题的问题比较表与数据准入检查原型设计。\n\n"
        "## 项目\n\n"
        "### 1 固定面板历史借用（合成演示课题）\n\n"
        "- 上周计划：未设定。\n"
        "- 完成情况：不适用——初始示例周。\n"
        "- 本周进展：搭建合成台账与方法比较表，明确先核查同系物系列数再冻结协议边界。\n"
        "- 状态：有风险——系列样本覆盖度仍需核查，核心参数尚未确认。\n"
        "- 下周计划：11 月 20 日前完成合成面板准入清单；11 月 22 日前整理选题与端点讨论材料。\n"
        "- 数据或文稿链接：[数据审计说明](demo_data/demo_audit_review.md)。\n"
    )
    w46_path = DEMO / "demo_w46_baseline.md"
    write_text(w46_path, legacy_w46)

    audit_path = DEMO / "demo_audit_review.md"
    audit_ref = {
        "path": audit_path.relative_to(ROOT).as_posix(),
        "bytes": audit_path.stat().st_size,
        "sha256": sha256_bytes(audit_path.read_bytes()),
    }

    # Init & populate W47
    run_tool([str(TOOLS / "make_report.py"), "init", "--week", "2026-W47", "--from-markdown", str(w46_path.relative_to(ROOT)), "--author", "Researcher"])
    w47_path = HOME / "周报记录/2026-W47.json"
    w47 = json.loads(w47_path.read_text(encoding="utf-8"))
    w47.update(
        {
            "as_of_date": "2026-11-22",
            "header_note": "承接 W46 基线计划；已完成合成准入与文献库联查验证，待补齐多路线对照表后定题。",
            "one_line_summary": "完成合成选择性数据资格核查与全量文献库结构化校验，识别宽谱筛查面板系列瓶颈。",
            "other_outputs": ["合成数据准入清单", "只读文献关联索引"],
            "decisions_needed": [
                {
                    "matter": "确认起步数据路线与主测量端点（Kd 或 Ki）",
                    "who": "PI 与研究者共同确认",
                    "options_and_recommendation": "① 优先多系列药化数据集检验跨系列泛化；② 保留广谱单库作为跨骨架外部校验。",
                    "needed_by": "12 月初前",
                }
            ],
            "advisor_email_en": {
                "subject": "Weekly Research Update (W47): Synthetic Eligibility Audit and Benchmark Framing",
                "body": (
                    "Dear Advisor,\n\n"
                    "This week I completed the synthetic data-eligibility audit on the candidate selectivity panels "
                    "and organized the literature baseline without initiating any formal model training yet. "
                    "Specifically, we verified measurement boundary types (exact points vs. right-censored inequalities) "
                    "and counted multi-ligand congeneric scaffold series across candidate routes. Broad screening panels "
                    "contain very few scaffolds with three or more bilateral ligands, indicating that series coverage "
                    "is the primary structural constraint prior to choosing transfer baselines. Meanwhile, our synthetic "
                    "power diagnostics confirm that at least twelve independent series are required for stable error bounds.\n\n"
                    "No formal endpoints, thresholds, or final test splits have been locked yet. Next week, I will "
                    "prepare a concise side-by-side comparison across multi-series medicinal chemistry candidates and "
                    "broad single-panel benchmarks, validate synthetic positive and null evaluation controls locally, "
                    "and meet to align on the target/off-target system and primary endpoint before freezing the protocol. "
                    "If our schedule shifts, I will first finalize the multi-route diagnostic comparison locally.\n\n"
                    "Best regards,\nResearcher"
                ),
            },
        }
    )
    tr47 = w47["tracks"][0]
    tr47.update(
        {
            "track_id": "demo-borrowing",
            "title": "历史数据选择与选择性预测（合成示例）",
            "problem_id": TOPIC_ID,
            "completion_status": "部分完成",
            "completion_summary": "已完成数据准入规则生成与文献核验对齐；因发现广谱面板多配体系列稀疏，选题与端点讨论材料待补齐备选路线后开展。",
            "carryover_reconciliation": [
                {
                    "parent_item_id": "2026-W46-P1-1",
                    "outcome": "done",
                    "note": "完成合成准入表生成与删失点位分离核验",
                    "evidence_refs": [audit_ref],
                    "continued_by_task_id": None,
                    "deferred_to_backlog_id": None,
                },
                {
                    "parent_item_id": "2026-W46-P1-2",
                    "outcome": "partial",
                    "note": "已整理研究边界，待补齐多系列对照实数后一并定题",
                    "evidence_refs": [],
                    "continued_by_task_id": "W48-T1",
                    "deferred_to_backlog_id": None,
                },
            ],
            "this_week_progress": "围绕公开激酶面板能否按同系物系列检验借用差异开展核查（未跑正式模型训练）：",
            "progress_points": [
                "**数据准入核查**：严格区分精确点值与删失上下界，避免将未命中测量误作连续效价差距。",
                "**系列覆盖识别**：合成审计显示广谱面板以每骨架一两个代表分子为主，多配体系列数有限。",
                "**对方案的推论**：将论证架构收敛为正负结果均可闭环的三层判别流程，先确定系列容量再冻结测试集。",
            ],
            "status_level": "有风险",
            "status_reason": "尚需确认采用多系列药化主台还是单库簇留出路线。",
            "deliverable_refs": [dict(audit_ref, bundle_name="01_合成数据审计摘要.md", label="合成数据审计摘要", purpose="合成测量边界与系列清点摘要")],
            "next_week_plan": [
                {
                    "task_id": "W48-T1",
                    "summary": "汇总多系列候选面板与广谱单库对照实数，形成定题讨论材料",
                    "kind": "data_audit",
                    "effort": "M",
                    "commitment": "committed",
                    "dependency": "self_contained",
                    "due_date": "2026-11-26",
                    "deliverable_or_criterion": "可核查的多路线系列容量与供体覆盖摘要表",
                    "if_blocked_fallback": None,
                },
                {
                    "task_id": "W48-T2",
                    "summary": "与指导者沟通确认示例课题范围、主数据源路线及测量端点",
                    "kind": "advisor_alignment",
                    "effort": "M",
                    "commitment": "committed",
                    "dependency": "awaits_pi",
                    "due_date": "2026-11-28",
                    "deliverable_or_criterion": "形成书面确认意见并留存外置确认原件",
                    "if_blocked_fallback": "若沟通顺延，先在隔离合成真值上完成三档评价原型验收",
                },
            ],
        }
    )
    write_json(w47_path, w47)
    run_tool([str(TOOLS / "research.py"), "report", "render", "--week", "2026-W47", "--refresh", "--advisor-en"])

    # Init & populate W48
    run_tool([str(TOOLS / "make_report.py"), "init", "--week", "2026-W48", "--author", "Researcher"])
    w48_path = HOME / "周报记录/2026-W48.json"
    w48 = json.loads(w48_path.read_text(encoding="utf-8"))
    w48.update(
        {
            "as_of_date": "2026-11-24",
            "header_note": "承接 W47 计划；完成多路线系列对比与合成正负对照校验，进入定题最终确认窗口。",
            "one_line_summary": "完成多系列路线实测对照与合成正负对照验证，全部基础校验通过并保持未定决策零代填。",
            "other_outputs": ["三层边界判别说明稿", "离线交互只读科研看板原型"],
            "decisions_needed": [
                {
                    "matter": "拍板主数据路线（多系列 Ki vs 广谱单库 Kd）与端点标准",
                    "who": "PI 与研究者共同确认",
                    "options_and_recommendation": "推荐在多系列主台运行系列级留出，并保留广谱单库作为跨骨架外部测试。",
                    "needed_by": "12 月 4 日前",
                }
            ],
            "advisor_email_en": w47["advisor_email_en"],
        }
    )
    tr48 = w48["tracks"][0]
    tr48.update(
        {
            "completion_status": "部分完成",
            "completion_summary": "已完成多系列数据路线统计与合成评价自检；主体系与测量端点尚待面谈最终拍板。",
            "carryover_reconciliation": [
                {
                    "parent_item_id": "W48-T1",
                    "outcome": "done",
                    "note": "产出五条候选路线的双侧规模、同系物系列数与非重叠供体对比",
                    "evidence_refs": [audit_ref],
                    "continued_by_task_id": None,
                    "deferred_to_backlog_id": None,
                },
                {
                    "parent_item_id": "W48-T2",
                    "outcome": "partial",
                    "note": "定题讨论材料已就绪，面谈窗口排至下周",
                    "evidence_refs": [],
                    "continued_by_task_id": "W49-T1",
                    "deferred_to_backlog_id": None,
                },
            ],
            "this_week_progress": "针对广谱面板样本瓶颈，收敛课题论证逻辑并验证评价代码在合成真值上的区分度：",
            "progress_points": [
                "**多路线容量对比**：对比药化多系列路线与广谱面板的同文献双侧配体与非重叠供体覆盖。",
                "**合成正负对照验证**：确认评测流程能稳定检出异质正增益并识别置换零假设与欠检验力告警。",
                "**确定下一步节奏**：保持正式测试集隔离与未定参数为空，待定题确认后再启动开发集基线方差测算。",
            ],
            "status_level": "有风险",
            "status_reason": "全部框架与离线看板就绪，仅余主体系与端点待最终确认。",
            "next_week_plan": [
                {
                    "task_id": "W49-T1",
                    "summary": "向指导者汇报多路线对照表并最终确认起步体系与测量端点",
                    "kind": "advisor_alignment",
                    "effort": "M",
                    "commitment": "committed",
                    "dependency": "awaits_pi",
                    "due_date": "2026-12-04",
                    "deliverable_or_criterion": "归档外置书面确认原件并登记决策条目",
                    "if_blocked_fallback": "若会议推迟，继续完善合成敏感度自检与构建体一致性检查规则",
                },
                {
                    "task_id": "W49-T2",
                    "summary": "定题确认后冻结开发/测试划分并仅在开发集启动单点基线误差摸底",
                    "kind": "dev_exploration",
                    "effort": "M",
                    "commitment": "conditional",
                    "dependency": "awaits_pi",
                    "due_date": "2026-12-06",
                    "deliverable_or_criterion": "开发集单点基准方差记录（零接触留出测试集）",
                    "trigger_condition": "须在主体系与端点正式登记确认后启动",
                    "if_blocked_fallback": None,
                },
            ],
        }
    )
    write_json(w48_path, w48)
    run_tool([str(TOOLS / "research.py"), "report", "render", "--week", "2026-W48", "--refresh", "--advisor-en"])


def finalize_baseline_and_views() -> None:
    run_tool([str(TOOLS / "research.py"), "overview", "--refresh"])
    bdir = HOME / "_交付/demo-baseline"
    if bdir.exists():
        shutil.rmtree(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    run_tool([str(TOOLS / "snapshot.py"), "--output", str(bdir / "baseline.json")])
    run_tool([str(TOOLS / "verify_runtime.py"), "--output", str(bdir / "runtime-final")])


def main() -> int:
    ledger_rows = seed_demo_data()
    seed_literature_db(ledger_rows)
    seed_topic_and_eligibility()
    seed_weekly_reports()
    finalize_baseline_and_views()
    print(json.dumps({"status": "ok", "root": str(ROOT), "topic": TOPIC_ID}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
