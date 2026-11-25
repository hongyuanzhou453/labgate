"""Isolated positive and negative verification suite for weekly report carryover and next-week feasibility guards."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path


def csv_bytes(fields, rows, sep=','):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter=sep, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def main():
    actual = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(actual / '工具'))
    from common import cli, now, read, ref, require, write
    from verify_runtime import Suite

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    out = args.output.resolve()
    if out.exists() or not out.parent.is_relative_to(actual / '_交付') or not out.parent.is_dir():
        p.error('请选择现有交付轮下未使用的测试子目录')
    if args.dry_run:
        print(json.dumps({
            'status': 'pass',
            'writes': False,
            'scope': 'parent carryover, completion consistency, 4 next-week feasibility guards, S01/S17 run rollup, view protection',
        }))
        return 0

    out.mkdir()
    root = out / 'workspace'
    home = root / '研究管理'
    commands = []
    cases = []

    def command(tool, *argv, code=0):
        cmd = [sys.executable, '-B', str(home / '工具' / tool), *map(str, argv)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=root)
        commands.append({'tool': tool, 'args': list(map(str, argv)), 'exit': r.returncode,
                         'stdout': r.stdout, 'stderr': r.stderr})
        require(r.returncode == code and not r.stderr,
                f'{tool} {argv}: unexpected exit {r.returncode}: {r.stdout} {r.stderr}')
        val = json.loads(r.stdout)
        require(isinstance(val, dict), 'non-JSON CLI output')
        return val

    def expect(name, cond):
        require(cond, f'assertion failed: {name}')
        cases.append({'case': name, 'status': 'pass', 'through_command': len(commands)})

    def put(path, val):
        write(path, val, replace=True)

    def setup_workspace():
        (home / '问题').mkdir(parents=True)
        for t in (actual / '工具').glob('*.py'):
            write(home / '工具' / t.name, t.read_bytes())
        for f in ('规范/接入映射.json', '规范/阶段要求.json', '规范/词表.json'):
            write(home / f, (actual / f).read_bytes())
        write(home / 'AGENTS.md', b'# Fixture workspace\n')
        # Create one problem dossier with unconfirmed decisions + GPU budget limit 1.5h
        command('research.py', 'new', 'alpha', '--title', '历史借用测试课题')
        alpha = home / '问题/alpha'
        hf = ['problem_id', 'hypothesis_id', 'hypothesis_version', 'protocol_id', 'protocol_version', 'statement']
        put(alpha / '假设.csv', csv_bytes(hf, [{
            'problem_id': 'alpha', 'hypothesis_id': 'H1', 'hypothesis_version': 'v1',
            'protocol_id': 'P1', 'protocol_version': 'draft-1', 'statement': 'fixture H1',
        }]))
        design = {
            'schema_version': 'design-draft.v0.2', 'problem_id': 'alpha', 'protocol_id': 'P1',
            'version': 'draft-1', 'status': 'draft', 'formal_use_allowed': False,
            'hypotheses': [{'hypothesis_id': 'H1', 'hypothesis_version': 'v1'}],
            'decisions': {
                'target_offtarget': None, 'endpoint': None, 'source_selection': None,
                'effect_threshold': None,
            },
            'decision_refs': [],
        }
        put(alpha / '协议/P1/draft-1/design.json', design)
        bf = ['problem_id', 'budget_id', 'planned_limit', 'actual_value', 'missing_reason']
        put(alpha / '预算.csv', csv_bytes(bf, [
            {'problem_id': 'alpha', 'budget_id': 'BUD-GPU', 'planned_limit': '1.5', 'actual_value': '', 'missing_reason': ''},
        ]))
        # Create a legacy W46 Markdown report and a verifiable evidence file
        put(root / 'notes/audit_note.md', '# 数据审计摘要\n\n已核 44/72 双侧点值。\n'.encode('utf-8'))
        legacy_w46 = (
            '# 周报 · W46（11.09–11.15）· Researcher\n\n'
            '> 审阅稿。\n\n'
            '## 本周一句话\n\n完成方法比较草稿。\n\n'
            '## 项目\n\n'
            '### 1 历史借用测试课题\n\n'
            '- 上周计划：未设定。\n'
            '- 完成情况：不适用。\n'
            '- 本周进展：完成比较表草稿。\n'
            '- 状态：有风险——待确认选题。\n'
            '- 下周计划：11 月 18 日前完成数据资格复核；11 月 20 日前与老师确认选题与端点。\n'
            '- 数据或文稿链接：[审计摘要](notes/audit_note.md)。\n'
        ).encode('utf-8')
        put(root / '周报_W46_测试.md', legacy_w46)

    def exercise():
        setup_workspace()

        # Case 1: dry-run init writes nothing; real init seeds pending_fill items from legacy markdown
        dry = command('make_report.py', 'init', '--week', '2026-W47', '--from-markdown', '周报_W46_测试.md', '--dry-run')
        expect('init dry-run writes neither record nor lock',
               dry['dry_run'] and dry['reconciliation_items_seeded'] == 2
               and not (home / '周报记录/2026-W47.json').exists()
               and not (home / '.reports.lock').exists()
               and not (root / '周报').exists())
        seeded = command('make_report.py', 'init', '--week', '2026-W47', '--from-markdown', '周报_W46_测试.md')
        rec47_path = home / '周报记录/2026-W47.json'
        expect('init from legacy markdown seeds 2 carryover clauses', seeded['reconciliation_items_seeded'] == 2 and rec47_path.is_file())

        # Case 2: unedited init skeleton is rejected (both 待填写 prose placeholder and pending_fill outcome)
        err_tmpl = command('make_report.py', 'check', '--week', '2026-W47', '--no-view-check', code=1)
        expect('unedited init prose placeholder is rejected', '待填写' in err_tmpl['error'])
        tmp_rec = read(rec47_path)
        tmp_rec.update(one_line_summary='已完成阶段性数据核查与资料整理。', header_note='上周计划沿用 W46。')
        put(rec47_path, tmp_rec)
        err_pending = command('make_report.py', 'check', '--week', '2026-W47', '--no-view-check', code=1)
        expect('pending_fill outcome is rejected before human review', 'pending_fill' in err_pending['error'])

        # Build valid W47 record (11.16-11.22, planning W48: 11.23-11.29)
        ev_ref = dict(ref(root / 'notes/audit_note.md', root), label='数据审计摘要')
        base47 = read(rec47_path)
        base47.update({
            'as_of_date': '2026-11-22',
            'one_line_summary': '完成数据资格复核与科研管理系统基础建设；毕设选题仍待与导师讨论确认。',
            'other_outputs': ['数据资格复核结论', '科研管理工作台首轮验收'],
            'decisions_needed': [{
                'matter': '确认毕设选题范围与起步端点（Kd vs Ki）',
                'who': 'PI 决定',
                'options_and_recommendation': '① 单库高一致面板收窄评价单位；② 多系列药化同系物源。建议结合可行性一页表讨论确定。',
                'needed_by': '2026-11-27 前',
            }],
            'deferred_backlog': [{
                'backlog_id': 'BL-CLOUD',
                'problem_id': 'alpha',
                'summary': '接入云端 GPU 训练仓库与持久存储通道',
                'reason': '当前尚处定题与数据核查阶段，无需提前消耗云端资源',
                'reopen_condition': '完成协议冻结且轻量基线跑通后启动',
            }],
            'advisor_email_en': {
                'subject': 'Weekly Progress Update (W47): Data Eligibility Audit and Thesis Topic Materials',
                'body': (
                    'Dear Prof. Advisor,\n\n'
                    'This week I completed the focused eligibility audit on the candidate kinase selectivity panels '
                    'and organized the thesis discussion materials on historical data borrowing under limited labels. '
                    'Specifically, we verified measurement types, censoring bounds, and proxy chemical series counts '
                    'without running any formal model training yet. In the Davis LOK/SLK panel, 44 of 72 ligands have '
                    'strict bilateral Kd points, but only 3 proxy groups contain at least two qualified ligands, '
                    'indicating that congeneric series size is the primary bottleneck rather than model complexity.\n\n'
                    'No formal scientific parameters, endpoints, or data splits have been finalized yet. Next week '
                    '(Nov 23 to Nov 29), I plan to prepare a concise one-page comparison between single-panel Kd '
                    'candidates and multi-series medicinal chemistry SAR sources, draft the scenario abstraction card, '
                    'and meet with you to confirm the thesis scope, target/off-target pair, and endpoint choice. '
                    'If our meeting is rescheduled, I will first audit multi-series coverage in Curate-DTI locally.\n\n'
                    'Best regards,\nAlex'
                ),
            },
        })
        tr = base47['tracks'][0]
        tr.update({
            'track_id': 'thesis-alpha',
            'title': '固定面板历史借用（毕设候选）',
            'problem_id': 'alpha',
            'completion_status': '部分完成',
            'completion_summary': '已完成候选面板双侧点值核查与方案整理；因发现单库多配体系列较少，待补齐备选路线对照后再与导师确定题目。',
            'carryover_reconciliation': [
                {
                    'parent_item_id': '2026-W46-P1-1',
                    'outcome': 'done',
                    'note': '完成数据资格复核与候选面板双侧覆盖实算',
                    'evidence_refs': [ref(root / 'notes/audit_note.md', root)],
                    'continued_by_task_id': None,
                    'deferred_to_backlog_id': None,
                },
                {
                    'parent_item_id': '2026-W46-P1-2',
                    'outcome': 'partial',
                    'note': '已备齐选题讨论材料与面板可行性对比，尚未与导师当面拍板最终题目与端点',
                    'evidence_refs': [],
                    'continued_by_task_id': 'W48-T1',
                    'deferred_to_backlog_id': None,
                },
            ],
            'this_week_progress': '围绕现有公开面板能否支撑按配体系列评估借用收益开展核查（尚无模型实验结果）：',
            'progress_points': [
                '**面板覆盖核查**：复核候选面板双侧精确点值，确认 Davis 中 LOK/SLK 有 44/72 个双侧配体，但含至少两个配体的同系物组仅有 3 组。',
                '**对方案的推论**：单库广谱面板不足以直接支撑多成员系列统计，需补充筛查多系列药化测定数据并与导师确认评价单位取舍。',
            ],
            'status_level': '有风险',
            'status_reason': '单库面板多成员系列数偏少，且选题与端点尚待导师确认。',
            'deliverable_refs': [ev_ref],
            'next_week_plan': [
                {
                    'task_id': 'W48-T1',
                    'summary': '整理单库面板与多系列药化数据一页对比，与导师讨论确认毕设范围与端点',
                    'kind': 'advisor_alignment',
                    'effort': 'M',
                    'commitment': 'committed',
                    'dependency': 'awaits_pi',
                    'due_date': '2026-11-27',
                    'deliverable_or_criterion': '一页数据源对比表与会议纪要；明确采用 Kd 或 Ki 及评价单位',
                    'if_blocked_fallback': '若讨论顺延，先独立完成多系列同系物覆盖只读清点表',
                },
                {
                    'task_id': 'W48-T2',
                    'summary': '编写固定面板历史借用二级场景抽象说明',
                    'kind': 'scenario_abstraction',
                    'effort': 'S',
                    'commitment': 'committed',
                    'dependency': 'self_contained',
                    'due_date': '2026-11-28',
                    'deliverable_or_criterion': '产出二级场景抽象草稿，明确抽掉的生物前提与回映射边界',
                    'if_blocked_fallback': None,
                },
                {
                    'task_id': 'W48-T3',
                    'summary': '按已确认的目标体系与端点隔离留出集，并在开发集运行轻量基线摸底误差波动',
                    'kind': 'dev_exploration',
                    'effort': 'M',
                    'commitment': 'conditional',
                    'dependency': 'awaits_pi',
                    'due_date': '2026-11-29',
                    'deliverable_or_criterion': '开发折单点基线方差记录（严禁触碰留出确认集）',
                    'trigger_condition': '待与导师确认目标体系、端点与数据来源范围后启动',
                    'if_blocked_fallback': None,
                },
            ],
        })

        # Negative guard tests before saving clean W47
        def test_mutation(mutator, expected_substr, label):
            bad = copy.deepcopy(base47)
            mutator(bad)
            put(rec47_path, bad)
            err = command('make_report.py', 'check', '--week', '2026-W47', '--no-view-check', code=1)
            expect(label, expected_substr in err['error'])

        # Case 3: Manual last_week_plan tampering rejected
        test_mutation(lambda d: d['tracks'][0].update(last_week_plan='篡改上周计划'),
                      '禁止手填 last_week_plan', 'rejects manual last_week_plan override')

        # Case 4: Silently dropping unfinished parent item rejected
        test_mutation(lambda d: d['tracks'][0]['carryover_reconciliation'][1].update(continued_by_task_id=None),
                      '不得静默丢弃', 'rejects unfinished parent item without continuation or backlog')

        # Case 5: Inflated completion_status ('全部完成' when an item is partial) rejected
        test_mutation(lambda d: d['tracks'][0].update(completion_status='全部完成'),
                      '部分完成', 'rejects claiming full completion when carryover has partial items')

        # Case 6: Out-of-window due_date rejected
        test_mutation(lambda d: d['tracks'][0]['next_week_plan'][0].update(due_date='2026-12-02'),
                      '不在目标下周窗口', 'rejects due_date outside next ISO week window')

        # Case 7: Scheduling formal_experiment before scientific gate is ready rejected
        test_mutation(lambda d: d['tracks'][0]['next_week_plan'][1].update(kind='formal_experiment'),
                      '违反科研门禁', 'rejects scheduling formal_experiment while decisions are unconfirmed')

        # Case 8: Unconditional committed dev_exploration while core T/O is null rejected
        test_mutation(lambda d: d['tracks'][0]['next_week_plan'][2].update(commitment='committed', dependency='self_contained'),
                      '预期不合理', 'rejects unconditional committed dev_exploration when T/O is null')

        # Case 9: Capacity overload (>6 effort points or >1 L task) rejected
        def overload(d):
            d['tracks'][0]['next_week_plan'][0]['effort'] = 'L'
            d['tracks'][0]['next_week_plan'][1]['effort'] = 'L'
        test_mutation(overload, '大体量任务 L 数量', 'rejects scheduling multiple large L tasks in one week')

        # Case 10: awaits_pi committed task without fallback rejected
        test_mutation(lambda d: d['tracks'][0]['next_week_plan'][0].update(if_blocked_fallback=''),
                      'if_blocked_fallback', 'rejects external-dependent committed task without fallback')

        # Case 11: Out-of-bounds English email word count rejected
        test_mutation(lambda d: d['advisor_email_en'].update(
            body='Dear Prof. Advisor, this is an intentionally brief fifty-word progress note that exceeds eighty characters '
                 'so it reaches the word count validator directly without tripping the minimum character length check first.'),
                      '词数须在 150–350', 'rejects English advisor email outside 150-350 word range')

        # Case 11b: Machine IDs / script filenames / gate codes in human prose rejected
        test_mutation(lambda d: d['tracks'][0]['progress_points'].__setitem__(0, '完成 verify_project.py 68 PASS 与 G1 刷新及 W48-T1'),
                      '违反人读行文规范', 'rejects internal script/gate/machine jargon in human prose')

        # Case 11c: Core problem track omitting layered progress_points or causal completion_summary rejected
        test_mutation(lambda d: d['tracks'][0].update(progress_points=None),
                      'progress_points', 'rejects core problem track lacking layered progress_points')
        test_mutation(lambda d: d['tracks'][0].update(completion_summary=None),
                      'completion_summary', 'rejects core problem track lacking causal completion_summary')

        # Case 12: Valid W47 renders Chinese + English reports cleanly, verbatim-inheriting W46 plan, and creates bundle folder
        put(rec47_path, base47)
        res47 = command('research.py', 'report', 'render', '--week', '2026-W47', '--refresh', '--advisor-en')
        zh47 = (home / '派生/周报/周报_2026-W47_Researcher.md').read_text(encoding='utf-8')
        en47 = (home / '派生/周报/导师进展邮件_2026-W47_en.md').read_text(encoding='utf-8')
        bdir47 = root / '周报/2026-W47_Researcher'
        b_zh47_path = bdir47 / '周报_2026-W47_Researcher.md'
        b_zh47 = b_zh47_path.read_text(encoding='utf-8')
        b_en47 = (bdir47 / '导师进展邮件_2026-W47_en.md').read_text(encoding='utf-8')
        b_mf47 = (bdir47 / '目录与来源说明.md').read_text(encoding='utf-8')
        b_mat47 = bdir47 / '相关材料/01_数据审计摘要.md'
        b_links47 = re.findall(r'\[[^\]]+\]\(([^)]+)\)', b_zh47)
        expect('valid W47 publishes Chinese & English derived files with verbatim parent plan and self-contained bundle',
               res47['report']['wrote'] and res47['advisor_en']['wrote']
               and res47['bundle']['view'] == 'current' and res47['bundle']['materials_count'] == 1
               and res47['bundle']['materials_wrote'] == 1
               and '11 月 18 日前完成数据资格复核；11 月 20 日前与老师确认选题与端点。' in zh47
               and 'Dear Prof. Advisor,' in en47
               and '../../../' not in b_zh47 and len(b_links47) == 1
               and (bdir47 / b_links47[0]).resolve() == b_mat47.resolve()
               and b_mat47.read_bytes() == (root / 'notes/audit_note.md').read_bytes()
               and 'Dear Prof. Advisor,' in b_en47
               and hashlib.sha256(b_zh47_path.read_bytes()).hexdigest() in b_mf47)

        # Case 13: Parent file tampering breaks check, hand-authored files and bundle copies/orphans are guarded
        orig_w46 = (root / '周报_W46_测试.md').read_bytes()
        put(root / '周报_W46_测试.md', orig_w46 + b'\n')
        expect('parent markdown hash drift fails check',
               'hash mismatch' in command('research.py', 'report', 'check', code=1)['error'])
        put(root / '周报_W46_测试.md', orig_w46)

        zh47_path = home / '派生/周报/周报_2026-W47_Researcher.md'
        saved_zh47 = zh47_path.read_bytes()
        put(zh47_path, b'# hand edited report\n')
        expect('refuses to overwrite hand-authored markdown without generated marker',
               '拒绝覆盖人工文件' in command('research.py', 'report', 'render', '--week', '2026-W47', '--refresh', code=1)['error']
               and zh47_path.read_bytes() == b'# hand edited report\n')
        put(zh47_path, saved_zh47)

        saved_b_zh47 = b_zh47_path.read_bytes()
        put(b_zh47_path, b'# hand edited bundle report\n')
        expect('refuses to overwrite hand-authored bundle markdown without generated marker',
               '拒绝覆盖人工文件' in command('research.py', 'report', 'render', '--week', '2026-W47', '--refresh', code=1)['error']
               and b_zh47_path.read_bytes() == b'# hand edited bundle report\n')
        put(b_zh47_path, saved_b_zh47)

        saved_b_mat47 = b_mat47.read_bytes()
        put(b_mat47, saved_b_mat47 + b'\ntampered\n')
        expect('tampered bundle material fails check and is repaired by refresh',
               '过期' in command('research.py', 'report', 'check', code=1)['error']
               and command('research.py', 'report', 'render', '--week', '2026-W47', '--refresh')['bundle']['materials_wrote'] == 1
               and b_mat47.read_bytes() == saved_b_mat47)

        orphan_mat = bdir47 / '相关材料/orphan.txt'
        put(orphan_mat, b'stray file\n')
        expect('orphan file in bundle materials is rejected by both check and refresh without silent deletion',
               '孤立文件' in command('research.py', 'report', 'check', code=1)['error']
               and '孤立文件' in command('research.py', 'report', 'render', '--week', '2026-W47', '--refresh', code=1)['error']
               and orphan_mat.exists())
        orphan_mat.unlink()

        # Case 14: Inject synthetic Exploratory running run (S01) + 2.0 GPUh finished run exceeding 1.5h limit (S17), then chain W48 from W47
        adapter = Suite(out)
        adapter.case = 'report_runs'
        store = home / '实验登记'
        b_done = adapter.bundle('explore_done', problem='alpha')
        exp_d = read(b_done / 'experiment.json')
        put(b_done / 'experiment.json', dict(exp_d, run_kind='exploratory', platform='server'))
        r0 = adapter.start(b_done)
        r1 = adapter.finish(b_done, r0)
        # Update finished run before sync with 2.0 GPUh to trigger S17 over-budget alert (> 1.5 GPUh)
        rec_done = read(r1)
        rec_done['gpu_hours'] = 2.0
        put(r1, rec_done)
        adapter.sync(b_done, store)

        b_run = adapter.bundle('explore_running', problem='alpha')
        exp_r = read(b_run / 'experiment.json')
        put(b_run / 'experiment.json', dict(exp_r, run_kind='exploratory', platform='server'))
        adapter.start(b_run)
        adapter.sync(b_run, store)

        # Init W48 from W47 JSON record
        seeded48 = command('research.py', 'report', 'init', '--week', '2026-W48')
        expect('W48 init from W47 JSON seeds all 3 W47 next-week tasks + carries backlog',
               seeded48['parent_week'] == '2026-W47' and seeded48['reconciliation_items_seeded'] == 3)
        rec48_path = home / '周报记录/2026-W48.json'
        w48 = read(rec48_path)
        w48.update({
            'as_of_date': '2026-11-24',
            'header_note': '上周计划沿用 W47；待下周与导师确认选题边界。',
            'one_line_summary': '完成二级场景抽象说明与多系列覆盖摸底；待导师确认后启动开发集验证。',
        })
        t48 = w48['tracks'][0]
        t48.update({
            'completion_status': '部分完成',
            'completion_summary': '已完成场景抽象草稿与多系列数据清点；因导师讨论顺延至下周初，开发集基线摸底暂未启动。',
            'carryover_reconciliation': [
                {'parent_item_id': 'W48-T1', 'outcome': 'deferred', 'note': '导师会议改期至下周初，已按兜底完成多系列清点',
                 'evidence_refs': [], 'continued_by_task_id': 'W49-T1', 'deferred_to_backlog_id': None},
                {'parent_item_id': 'W48-T2', 'outcome': 'done', 'note': '已完成二级场景抽象草稿',
                 'evidence_refs': [], 'continued_by_task_id': None, 'deferred_to_backlog_id': None},
                {'parent_item_id': 'W48-T3', 'outcome': 'blocked', 'note': '因目标体系未拍板，条件未触发，顺延至暂缓事项',
                 'evidence_refs': [], 'continued_by_task_id': None, 'deferred_to_backlog_id': 'BL-DEV'},
            ],
            'this_week_progress': '聚焦核实备选数据源的系列规模并整理场景抽象边界：',
            'progress_points': [
                '**场景抽象与数据清点**：完成 12 栏场景抽象草稿，明确把固定面板借用映射为分组留出多任务回归所需满足的生物前提。',
                '**后续排期收敛**：在目标体系确认前保持最终测试集封闭，将开发集误差摸底转入待确认后触发。',
            ],
            'status_level': '有风险',
            'status_reason': '待下周初与导师确认选题边界与测量端点。',
            'next_week_plan': [{
                'task_id': 'W49-T1',
                'summary': '与导师一对一沟通确认毕设题目范围、主数据源与测量端点',
                'kind': 'advisor_alignment',
                'effort': 'M',
                'commitment': 'committed',
                'dependency': 'awaits_pi',
                'due_date': '2026-12-02',
                'deliverable_or_criterion': '归档讨论确认纪要，明确主数据源与端点选择',
                'if_blocked_fallback': '若仍需补充材料，先独立完成合成正负对照评测流程自检',
            }],
        })
        w48['deferred_backlog'].append({
            'backlog_id': 'BL-DEV',
            'problem_id': 'alpha',
            'summary': '开发集单点基线方差摸底',
            'reason': '前置导师选题确认顺延',
            'reopen_condition': '确认目标体系与端点',
        })
        put(rec48_path, w48)
        res48 = command('research.py', 'report', 'render', '--week', '2026-W48', '--refresh')
        zh48 = (home / '派生/周报/周报_2026-W48_Researcher.md').read_text(encoding='utf-8')
        # Add an un-registered legacy archive folder under 周报/ and verify report check remains pass
        put(root / '周报/2026-W45_Researcher/周报_2026-W45_Researcher.md', b'# legacy W45\n')
        put(root / '周报/2026-W45_Researcher/相关材料/old.md', b'legacy material\n')
        all_ok = command('research.py', 'report', 'check')
        expect('W47->W48 chain and bundles pass check alongside legacy archive folder, keeping markdown clean and surfacing S01/S17',
               all_ok['registered_weeks'] == 2
               and all(w['bundle_view'] == 'current' for w in all_ok['weeks'])
               and '系统与运行自动核对' not in zh48
               and len(res48['run_diagnostics']['running_alerts']) == 1
               and len(res48['run_diagnostics']['budget_overruns']) == 1
               and res48['run_diagnostics']['budget_overruns'][0]['actual'] == 2.0)
        return 'pass'

    error = None
    try:
        status = exercise()
    except Exception as exc:
        status = 'fail'
        error = f'{type(exc).__name__}: {exc}'
    write(out / 'commands.json', commands)
    report = {
        'schema_version': 'weekly-report-tests.v1',
        'status': status,
        'checked_at': now(),
        'scope': 'isolated synthetic workspace; carryover, feasibility guards, S01/S17 diagnostics, view safety',
        'cases': cases,
        'error': error,
        'commands': ref(out / 'commands.json', out),
        'code_refs': [ref(q, actual) for q in [Path(__file__).resolve(), *(actual / '工具').glob('*.py'), actual/'规范/词表.json']],
    }
    write(out / 'report.json', report)
    return cli(lambda: {'status': status, 'passed': len(cases), 'error': error, 'report': str(out / 'report.json')})


if __name__ == '__main__':
    sys.exit(main())
