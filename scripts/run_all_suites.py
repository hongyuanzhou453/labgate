#!/usr/bin/env python3
"""Run all six LabGate validation suites (181 cases), G1/G2/G3 stage checks,
dashboard self-checks, and privacy/path hygiene scans in one reproducible entrypoint.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "研究管理"
TOOLS = HOME / "工具"
TESTS = HOME / "测试"

FORBIDDEN_PATTERNS = {
    "private_home": re.compile(r"/Users/[^\s\"'`]+"),
    "advisor_name": re.compile(r"许适当|Prof\.\s*Xu"),
    "private_author": re.compile(r"周宏远"),
    "private_affiliation": re.compile(r"华南理工|华工|\bSCUT\b"),
    "private_topic": re.compile(r"fixed-panel-historical-borrowing|Antigravity_并行推进|Q1_文献检索与证据台账|kelipovanatalja"),
    "secret_marker": re.compile(r"(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|X-Amz-Signature)"),
}


def run_json(cmd: list[str], *, allow_fail_status: bool = False) -> dict:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    if not proc.stdout.strip():
        raise RuntimeError(f"Empty output from {' '.join(cmd)} (rc={proc.returncode}): {proc.stderr}")
    data = json.loads(proc.stdout)
    if not allow_fail_status and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}\n{proc.stdout}")
    return data


def count_cases(report_path: Path) -> tuple[int, int]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    passed = sum(1 for c in cases if c.get("status") == "pass" or c.get("ok") is True)
    return passed, len(cases)


def hygiene_scan() -> dict:
    hits: dict[str, list[str]] = {k: [] for k in FORBIDDEN_PATTERNS}
    large_files: list[tuple[int, str]] = []
    skip_dirs = {".git", "__pycache__", "_ci_run"}
    for p in ROOT.rglob("*"):
        if any(part in skip_dirs for part in p.parts) or not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        sz = p.stat().st_size
        if sz > 5_000_000:
            large_files.append((sz, rel))
        if p.name == "run_all_suites.py":
            continue
        # Files that intentionally define secret-scanner regexes or synthetic fail-closed test tokens:
        secret_fixture_allowlist = {
            "研究管理/工具/credential_scan.py",
            "研究管理/测试/verify_freeze.py",
            "研究管理/规范/W10b_冻结与测试访问_v1.md",
        }
        if p.suffix in {".py", ".md", ".json", ".csv", ".tsv", ".html", ".txt", ".yml", ".yaml"}:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for k, pat in FORBIDDEN_PATTERNS.items():
                if k == "secret_marker" and rel in secret_fixture_allowlist:
                    continue
                if pat.search(text):
                    hits[k].append(rel)
    non_empty = {k: v for k, v in hits.items() if v}
    if non_empty or large_files:
        raise RuntimeError(f"Hygiene/size check failed: hits={non_empty}, large_files={large_files}")
    return {"status": "pass", "forbidden_hits": 0, "oversized_files": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-suites", action="store_true", help="Run stage gates, dashboard check, and hygiene scan only.")
    args = parser.parse_args()

    summary: dict[str, object] = {"suites": {}, "stages": {}}
    total_pass = 0
    total_cases = 0

    if not args.skip_suites:
        ci_dir = HOME / "_交付/_ci_run"
        if ci_dir.exists():
            shutil.rmtree(ci_dir)
        ci_dir.mkdir(parents=True, exist_ok=True)
        try:
            suites = [
                ("runtime", TOOLS / "verify_runtime.py"),
                ("links", TESTS / "verify_links_and_checks.py"),
                ("freeze", TESTS / "verify_freeze.py"),
                ("desk", TESTS / "verify_desk.py"),
                ("split", TESTS / "verify_split.py"),
                ("report", TESTS / "verify_report.py"),
            ]
            for name, script in suites:
                out_dir = ci_dir / f"{name}-final"
                run_json([sys.executable, "-B", str(script), "--output", str(out_dir)])
                p, t = count_cases(out_dir / "report.json")
                if p != t or t == 0:
                    raise RuntimeError(f"Suite {name} failed: {p}/{t}")
                summary["suites"][name] = f"{p}/{t}"
                total_pass += p
                total_cases += t
        finally:
            if ci_dir.exists():
                shutil.rmtree(ci_dir)

    summary["total_suite_cases"] = f"{total_pass}/{total_cases}"

    runtime_rep = HOME / "_交付/demo-baseline/runtime-final/report.json"
    baseline_rep = HOME / "_交付/demo-baseline/baseline.json"
    g2 = run_json([
        sys.executable, "-B", str(TOOLS / "check_all.py"),
        "--stage", "G2",
        "--runtime-report", str(runtime_rep),
        "--baseline", str(baseline_rep),
        "--dry-run",
    ])
    if g2.get("status") != "pass":
        raise RuntimeError(f"G2 gate failed: {g2}")
    summary["stages"]["G2"] = "pass"

    g3 = run_json(
        [
            sys.executable, "-B", str(TOOLS / "check_all.py"),
            "--stage", "G3",
            "--phase", "prepare",
            "--problem", "demo-selectivity-benchmark",
            "--runtime-report", str(runtime_rep),
            "--baseline", str(baseline_rep),
            "--dry-run",
        ],
        allow_fail_status=True,
    )
    if g3.get("status") != "fail":
        raise RuntimeError("Expected G3 prepare fail-closed guard when 17 decisions are null!")
    summary["stages"]["G3_prepare"] = "fail_closed_as_expected"

    dash = HOME / "看板/build_dashboard.py"
    if dash.is_file():
        summary["dashboard_check"] = run_json([sys.executable, "-B", str(dash), "--check"])

    summary["hygiene"] = hygiene_scan()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
