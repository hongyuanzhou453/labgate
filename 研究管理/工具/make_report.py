"""Generate derived weekly reports with parent-week carryover and next-week feasibility guards."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

from common import cli, lock, now, read, ref, require, safe_id, under, verify_ref, write
from register_sources import root_path
from sync_runs import committed_records, transactions
from topics import dossier, local_file, topic_dirs


MARKER_PREFIX = '<!-- generated: make_report.py; source='
OUTCOMES = {'done', 'partial', 'deferred', 'blocked', 'dropped'}
COMPLETION_LABELS = {'全部完成', '部分完成', '未完成', '顺延', '不适用'}
STATUS_LEVELS = {'正常', '有风险', '阻塞'}
TASK_KINDS = {
    'advisor_alignment', 'literature_check', 'data_audit', 'scenario_abstraction',
    'dev_exploration', 'system_engineering', 'formal_experiment', 'result_analysis',
}
EFFORT_POINTS = {'S': 1, 'M': 2, 'L': 3}
COMMITMENTS = {'committed', 'conditional'}
DEPENDENCIES = {'self_contained', 'awaits_pi', 'awaits_external'}
DEFAULT_BUDGET = {
    'max_committed_tasks': 3,
    'max_effort_points': 6,
    'max_large_tasks': 1,
    'max_conditional_tasks': 2,
}
HARD_BUDGET_CEILING = {
    'max_committed_tasks': 4,
    'max_effort_points': 8,
    'max_large_tasks': 1,
    'max_conditional_tasks': 2,
}
OUTCOME_CN = {
    'done': '已完成',
    'partial': '部分完成',
    'deferred': '顺延',
    'blocked': '受阻',
    'dropped': '取消/收敛',
}
EFFORT_CN = {'S': 'S（≤2h）', 'M': 'M（0.5–1d）', 'L': 'L（2–3d）'}
DEP_CN = {'self_contained': '自主可闭环', 'awaits_pi': '需导师确认', 'awaits_external': '依赖外部输入'}


def parse_week_id(week_id):
    require(isinstance(week_id, str), 'week_id 必须为字符串')
    m = re.fullmatch(r'(\d{4})-W(0[1-9]|[1-4]\d|5[0-3])', week_id)
    require(m is not None, f'week_id 格式须为 YYYY-Www：{week_id}')
    year, week = int(m.group(1)), int(m.group(2))
    try:
        start = dt.date.fromisocalendar(year, week, 1)
        end = dt.date.fromisocalendar(year, week, 7)
    except ValueError as exc:
        raise ValueError(f'无效的 ISO 周编号：{week_id}') from exc
    require((start.isocalendar().year, start.isocalendar().week) == (year, week), f'ISO 周越界：{week_id}')
    return year, week, start, end


def parse_iso_date(value, field_name):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), f'{field_name} 须为 YYYY-MM-DD：{value}')
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{field_name} 日期无效：{value}') from exc


def next_week_window(week_id):
    _, _, _, end = parse_week_id(week_id)
    n_start = end + dt.timedelta(days=1)
    n_end = end + dt.timedelta(days=7)
    iso = n_start.isocalendar()
    return f'{iso.year}-W{iso.week:02d}', n_start, n_end


def prev_week_id(week_id):
    _, _, start, _ = parse_week_id(week_id)
    p_end = start - dt.timedelta(days=1)
    iso = p_end.isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def non_empty_str(value, field, min_len=1):
    require(isinstance(value, str) and len(value.strip()) >= min_len, f'{field} 不能为空或过短（至少 {min_len} 字）')
    return value.strip()


def cn_month_day(d):
    return f'{d.month} 月 {d.day} 日'


def bundle_dir(root, ctx):
    return root / '周报' / f"{ctx['week_id']}_{ctx['author']}"


def bundle_zh_path(root, ctx):
    return bundle_dir(root, ctx) / f"周报_{ctx['week_id']}_{ctx['author']}.md"


def bundle_en_path(root, ctx):
    return bundle_dir(root, ctx) / f"导师进展邮件_{ctx['week_id']}_en.md"


def bundle_manifest_path(root, ctx):
    return bundle_dir(root, ctx) / '目录与来源说明.md'


def bundle_materials_dir(root, ctx):
    return bundle_dir(root, ctx) / '相关材料'


def validate_bundle_filename(name):
    require(isinstance(name, str), 'bundle_name 必须为字符串')
    cleaned = name.strip()
    require(cleaned == name and len(cleaned) >= 3, f'bundle_name 不能为空白、过短或含首尾空格：{name!r}')
    require('/' not in cleaned and '\\' not in cleaned and '..' not in cleaned,
            f'bundle_name 不得包含路径分隔符或 ..：{name}')
    require(not cleaned.startswith('.'), f'bundle_name 不得以点号开头：{name}')
    require(not any(ord(ch) < 32 for ch in cleaned), f'bundle_name 不得包含控制字符：{name!r}')
    require(Path(cleaned).name == cleaned, f'bundle_name 必须为单层文件名：{name}')
    return cleaned


def safe_bundle_filename(explicit_name, index, label, orig_path, used_names=None):
    used = used_names if used_names is not None else set()
    if explicit_name is not None:
        name = validate_bundle_filename(explicit_name)
        require(name not in used, f'不同来源映射到重复的 bundle_name：{name}')
        return name
    clean_label = re.sub(r'[\\/:*?"<>|\s]+', '_', label.strip()).strip('_')
    if not clean_label or clean_label.startswith('.'):
        clean_label = orig_path.stem.strip('.') or f'item_{index:02d}'
    suffix = orig_path.suffix
    candidate = f'{index:02d}_{clean_label}{suffix}'
    if candidate in used:
        stem = re.sub(r'[\\/:*?"<>|\s]+', '_', orig_path.stem).strip('_') or f'src_{index:02d}'
        candidate = f'{index:02d}_{clean_label}_{stem}{suffix}'
    counter = 2
    while candidate in used:
        candidate = f'{index:02d}_{clean_label}_{counter}{suffix}'
        counter += 1
    return validate_bundle_filename(candidate)


PROSE_FORBIDDEN_PATTERNS = (
    (r'待填写', '包含未替换的模板占位词“待填写”'),
    (r'\bW\d{2}-[A-Z]\d+\b', '正文不得暴露内部任务编号（如 W49-T1）'),
    (r'\bBL-[A-Z0-9-]+\b', '正文不得暴露内部暂缓编号（如 BL-xxx）'),
    (r'[〔〕]|【确定推进】|【条件触发】|｜', '正文不得使用机器状态戳或竖线拼接标签'),
    (r'\b(?:verify_project|make_report|check_all|research|run_logger|sync_runs|build_index|build_db|query_db)\.py\b',
     '正文不得直接罗列内部脚本文件名，请改用研究动作自然表述'),
    (r'(?:\b(?:runs|results|index)\.jsonl\b|\b(?:freeze|design)\.json\b|决策\.json|来源登记\.json|AGENT_RETRIEVAL_GUIDE)',
     '正文不得直接暴露内部 JSON/JSONL/指南文件名，请改用自然中文表述'),
    (r'\b(?:SQLite|FTS5)\b', '正文面向科研阅读，避免堆砌底层数据库实现术语'),
    (r'(?:\bG[123]\b|\bGate-3[AB]\b|\bW10[ab]?\b|\bV\d{2}\.[A-Za-z_]+\b)',
     '正文不得夹杂内部阶段门禁或工作包代号（如 G1/G2、Gate-3A、W10b、V31）'),
    (r'\b(?:PASS|FAIL)\s*\d+', '正文不得直接粘贴校验器 PASS/FAIL 计数日志'),
)


def check_human_prose(text, field):
    for pattern, reason in PROSE_FORBIDDEN_PATTERNS:
        m = re.search(pattern, text)
        if m is not None:
            raise ValueError(f'{field} 违反人读行文规范（命中 `{m.group(0)}`）：{reason}；详见 规范/周报契约_v1.md 第 5 节')
    return text


def parse_legacy_markdown(path, expected_week_id):
    lines = path.read_text(encoding='utf-8').splitlines()
    require(lines and lines[0].startswith('# 周报'), f'历史周报缺少标题：{path.name}')
    short_week = expected_week_id.split('-')[1]
    require(short_week in lines[0], f'历史周报标题周次与 {expected_week_id} 不符：{lines[0]}')
    in_projects = False
    current = None
    tracks = []
    for raw in lines[1:]:
        line = raw.strip()
        if line.startswith('## '):
            in_projects = (line == '## 项目')
            current = None
            continue
        if not in_projects:
            continue
        if line.startswith('### '):
            heading = line[4:].strip()
            idx = str(len(tracks) + 1)
            m = re.match(r'^(\d+)\s+', heading)
            key = m.group(1) if m else idx
            current = {'track_key': key, 'heading': heading, 'raw_next_plan': '', 'items': []}
            tracks.append(current)
        elif current is not None and line.startswith('- 下周计划：'):
            plan_text = line.removeprefix('- 下周计划：').strip()
            current['raw_next_plan'] = plan_text
            if plan_text and plan_text not in {'未设定。', '未设定', '无', '无。'}:
                clauses = [c.strip().rstrip('。') for c in re.split(r'[；;]+', plan_text) if c.strip().rstrip('。')]
                for i, clause in enumerate(clauses, 1):
                    current['items'].append({
                        'item_id': f'{expected_week_id}-P{current["track_key"]}-{i}',
                        'summary': clause,
                    })
    require(bool(tracks), f'历史周报未解析出任何项目小节：{path.name}')
    return {
        'week_id': expected_week_id,
        'kind': 'legacy_markdown',
        'tracks': {t['track_key']: t for t in tracks},
    }


def load_parent(root, parent_ref, current_week_id):
    require(isinstance(parent_ref, dict), 'parent_ref 必须为对象或 null')
    kind = parent_ref.get('kind')
    require(kind in {'legacy_markdown', 'weekly_record'}, f'不支持的 parent_ref.kind：{kind}')
    p_week = parent_ref.get('week_id')
    parse_week_id(p_week)
    expected_prev = prev_week_id(current_week_id)
    allow_gap = bool(parent_ref.get('allow_week_gap', False))
    require(p_week == expected_prev or allow_gap,
            f'parent_ref 周次（{p_week}）不是当前周（{current_week_id}）紧邻上周（{expected_prev}）；跨周合并须显式设 allow_week_gap=true 并说明')
    if allow_gap:
        non_empty_str(parent_ref.get('gap_reason'), 'parent_ref.gap_reason', min_len=4)
    path = verify_ref(parent_ref, root)
    if kind == 'legacy_markdown':
        return parse_legacy_markdown(path, p_week)
    data = read(path)
    require(isinstance(data, dict) and data.get('schema_version') == 'weekly-record.v1'
            and data.get('week_id') == p_week, f'父周记录身份不匹配：{path}')
    tracks = {}
    for t in data.get('tracks', []):
        tid = safe_id(t['track_id'])
        plan_items = t.get('next_week_plan', [])
        raw_parts = []
        items = []
        for item in plan_items:
            iid = safe_id(item['task_id'])
            due_cn = cn_month_day(parse_iso_date(item['due_date'], 'due_date'))
            clean_sum = item['summary'].strip().rstrip('。')
            if item.get('commitment') == 'conditional':
                summary = f'{due_cn}前（待前置确认后推进）{clean_sum}'
            else:
                summary = f'{due_cn}前{clean_sum}'
            raw_parts.append(summary)
            items.append({'item_id': iid, 'summary': summary, 'commitment': item.get('commitment', 'committed')})
        tracks[tid] = {
            'track_key': tid,
            'heading': t['title'],
            'raw_next_plan': ('；'.join(raw_parts) + '。') if raw_parts else '未设定。',
            'items': items,
        }
    return {'week_id': p_week, 'kind': 'weekly_record', 'tracks': tracks}


def collect_run_diagnostics(home, problems, start_date, end_date, as_of_date):
    store = home / '实验登记'
    cutoff = str(max(end_date, as_of_date))
    summary = {
        'store_present': False,
        'by_problem': {p: {'exploratory': 0, 'confirmatory': 0, 'in_period': 0} for p in problems},
        'registered_research_runs': 0,
        'in_period_research_runs': 0,
        'smoke_or_fixture': 0,
        'running_alerts': [],
        'group_gpu_hours': collections.Counter(),
        'prepared_transactions': 0,
    }
    if not store.exists() and not store.is_symlink():
        return summary
    require(store.is_dir() and not store.is_symlink(), '实验登记目录非法')
    before = transactions(store)
    _, all_records = committed_records(store)
    require(before == transactions(store), '读取实验登记期间发生并发变更，请重试')
    summary['store_present'] = True
    summary['prepared_transactions'] = sum(
        t['state'] == 'prepared' and (t.get('prepared_at') or '')[:10] <= cutoff
        for _, t in before
    )
    for uid, rev_list in sorted(all_records.items()):
        eligible = [item for item, _ in rev_list if (item.get('recorded_at') or item.get('started_at') or '')[:10] <= cutoff]
        if not eligible:
            continue
        r = eligible[-1]
        if r['run_kind'] == 'smoke' or r['platform'] == 'local_fixture':
            summary['smoke_or_fixture'] += 1
            continue
        pid = r['problem_id']
        require(pid in summary['by_problem'], f'已提交运行指向未知课题：{pid}')
        summary['registered_research_runs'] += 1
        summary['by_problem'][pid][r['run_kind']] += 1
        ts_str = (r.get('ended_at') or r.get('recorded_at') or r.get('started_at') or '')[:10]
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', ts_str):
            d = dt.date.fromisoformat(ts_str)
            if start_date <= d <= end_date:
                summary['in_period_research_runs'] += 1
                summary['by_problem'][pid]['in_period'] += 1
        if r.get('status') == 'running':
            summary['running_alerts'].append({
                'run_id': r['run_id'],
                'problem_id': pid,
                'method_id': r.get('method_id'),
                'started_at': r.get('started_at'),
            })
        if r.get('resource_accounting_status') == 'measured' and isinstance(r.get('gpu_hours'), (int, float)):
            summary['group_gpu_hours'][(pid, r.get('group_id') or 'UNGROUPED')] += float(r['gpu_hours'])
    return summary


def problem_gate_snapshot(root, home):
    requirements = read(local_file(home, '规范/阶段要求.json'))
    req_decisions = list(requirements.get('required_decisions', []))
    formal_enabled = bool(requirements.get('formal_execution_enabled', False))
    snapshots = {}
    for pid, folder in topic_dirs(home).items():
        d = dossier(root, folder)
        null_decisions = []
        core_missing = []
        design_decisions = {}
        if d['designs']:
            latest_design = read(root / d['designs'][-1]['path'])
            design_decisions = latest_design.get('decisions', {})
            null_decisions = [k for k in req_decisions if design_decisions.get(k) is None]
            core_missing = [k for k in ('target_offtarget', 'endpoint', 'source_selection')
                            if design_decisions.get(k) is None]
        else:
            null_decisions = list(req_decisions)
            core_missing = ['target_offtarget', 'endpoint', 'source_selection']
        budget_limits = {}
        for b in d['budgets']:
            lim = b['planned_limit'].strip()
            if lim != '':
                try:
                    budget_limits[b['budget_id']] = float(lim)
                except ValueError:
                    pass
        snapshots[pid] = {
            'dossier': d,
            'formal_ready': formal_enabled and not null_decisions and bool(d['decisions']),
            'confirmed_decisions': len(d['decisions']),
            'null_decisions': null_decisions,
            'core_missing': core_missing,
            'budget_limits': budget_limits,
        }
    return snapshots


def normalize_budget(raw):
    budget = dict(DEFAULT_BUDGET)
    if raw is not None:
        require(isinstance(raw, dict), 'capacity_budget 必须为对象')
        require(set(raw) <= set(DEFAULT_BUDGET), f'未知的容量预算字段：{set(raw) - set(DEFAULT_BUDGET)}')
        for k, v in raw.items():
            require(type(v) is int and 0 <= v <= HARD_BUDGET_CEILING[k],
                    f'capacity_budget.{k}={v} 超出系统硬上限 {HARD_BUDGET_CEILING[k]}')
            budget[k] = v
    return budget


def english_word_count(text):
    return len(re.findall(r"[A-Za-z0-9]+(?:[-'/][A-Za-z0-9]+)*", text))


def validate_record(root, record_path):
    home = root / '研究管理'
    data = read(record_path)
    require(isinstance(data, dict) and data.get('schema_version') == 'weekly-record.v1',
            f'周报记录 schema_version 有误：{record_path.name}')
    week_id = data.get('week_id')
    _, week_num, start_date, end_date = parse_week_id(week_id)
    require(record_path.name == f'{week_id}.json', f'文件名须与 week_id 一致：{record_path.name} vs {week_id}.json')
    require(parse_iso_date(data.get('period_start'), 'period_start') == start_date, 'period_start 与 ISO 周一不符')
    require(parse_iso_date(data.get('period_end'), 'period_end') == end_date, 'period_end 与 ISO 周日不符')
    as_of = parse_iso_date(data.get('as_of_date'), 'as_of_date')
    require(as_of >= start_date, f'as_of_date ({as_of}) 早于本周起点 ({start_date})')
    author = non_empty_str(data.get('author'), 'author', min_len=2)
    require(re.fullmatch(r'[^\s/\\]+', author) is not None, 'author 不得包含空白或路径分隔符')
    one_liner = check_human_prose(non_empty_str(data.get('one_line_summary'), 'one_line_summary', min_len=8), 'one_line_summary')
    status_note = check_human_prose(non_empty_str(data.get('header_note'), 'header_note', min_len=6), 'header_note')

    is_initial = bool(data.get('is_initial_week', False))
    parent_ref = data.get('parent_ref')
    if is_initial:
        require(parent_ref is None, '首周记录 is_initial_week=true 时 parent_ref 须为 null')
        parent = None
    else:
        require(parent_ref is not None, '非首周记录必须提供 parent_ref 承接上周周报')
        parent = load_parent(root, parent_ref, week_id)

    problems = problem_gate_snapshot(root, home)
    run_diag = collect_run_diagnostics(home, problems, start_date, end_date, as_of)
    next_wid, next_start, next_end = next_week_window(week_id)
    budget = normalize_budget(data.get('capacity_budget'))

    # Validate deferred_backlog first so reconciliation can reference backlog IDs
    backlog = data.get('deferred_backlog', [])
    require(isinstance(backlog, list), 'deferred_backlog 必须为数组')
    backlog_by_id = {}
    for item in backlog:
        require(isinstance(item, dict), 'deferred_backlog 元素须为对象')
        bid = safe_id(item.get('backlog_id'))
        require(bid not in backlog_by_id, f'重复的 backlog_id：{bid}')
        check_human_prose(non_empty_str(item.get('summary'), f'backlog[{bid}].summary', min_len=6), f'backlog[{bid}].summary')
        check_human_prose(non_empty_str(item.get('reason'), f'backlog[{bid}].reason', min_len=4), f'backlog[{bid}].reason')
        check_human_prose(non_empty_str(item.get('reopen_condition'), f'backlog[{bid}].reopen_condition', min_len=4),
                          f'backlog[{bid}].reopen_condition')
        pid = item.get('problem_id')
        if pid is not None:
            require(pid in problems, f'backlog[{bid}] 指向未登记课题：{pid}')
        backlog_by_id[bid] = item

    tracks = data.get('tracks')
    require(isinstance(tracks, list) and tracks, 'tracks 至少包含一个项目条目')

    # Pre-collect all next_week_plan task IDs across tracks to check uniqueness & carryover links
    all_next_tasks = {}
    for t in tracks:
        require(isinstance(t, dict), 'track 必须为对象')
        plan = t.get('next_week_plan', [])
        require(isinstance(plan, list), 'next_week_plan 必须为数组')
        for task in plan:
            require(isinstance(task, dict), 'next_week_plan 元素须为对象')
            tid = safe_id(task.get('task_id'))
            require(tid not in all_next_tasks, f'重复的下周 task_id：{tid}')
            all_next_tasks[tid] = task

    bound_parent_keys = set()
    track_ids = set()
    committed_count = 0
    conditional_count = 0
    committed_points = 0
    large_count = 0
    enriched_tracks = []
    raw_bundle_refs = {}

    def register_bundle_ref(item, context_field):
        label = non_empty_str(item.get('label'), f'{context_field}.label', min_len=2)
        p = verify_ref(item, root)
        rel_path = str(p.relative_to(root))
        raw_purpose = item.get('purpose')
        purpose = non_empty_str(raw_purpose, f'{context_field}.purpose', min_len=2) if raw_purpose is not None else label
        raw_bname = item.get('bundle_name')
        explicit_name = validate_bundle_filename(raw_bname) if raw_bname is not None else None
        if rel_path in raw_bundle_refs:
            prev = raw_bundle_refs[rel_path]
            if explicit_name is not None:
                if prev['explicit_name'] is not None and prev['explicit_name'] != explicit_name:
                    raise ValueError(
                        f'同一规范源路径 {rel_path} 指定了冲突的 bundle_name：{prev["explicit_name"]} vs {explicit_name}'
                    )
                prev['explicit_name'] = explicit_name
            if raw_purpose is not None and prev['explicit_purpose'] is None:
                prev['purpose'] = purpose
                prev['explicit_purpose'] = purpose
        else:
            raw_bundle_refs[rel_path] = {
                'path': rel_path,
                'src_path': p,
                'label': label,
                'purpose': purpose,
                'explicit_purpose': purpose if raw_purpose is not None else None,
                'explicit_name': explicit_name,
                'bytes': p.stat().st_size,
                'sha256': item['sha256'],
            }
        return {'label': label, 'path': rel_path, 'sha256': item['sha256']}

    for idx, t in enumerate(tracks, 1):
        tid = safe_id(t.get('track_id'))
        require(tid not in track_ids, f'重复的 track_id：{tid}')
        track_ids.add(tid)
        title = non_empty_str(t.get('title'), f'tracks[{tid}].title', min_len=3)
        pid = t.get('problem_id')
        prob_info = None
        if pid is not None:
            require(pid in problems, f'track[{tid}] 绑定了未登记的课题：{pid}')
            prob_info = problems[pid]

        # 1. Parent carryover validation
        require('last_week_plan' not in t, f'track[{tid}] 禁止手填 last_week_plan；上周计划必须由 parent_ref 自动承接')
        p_key = t.get('parent_track_key')
        reconcile = t.get('carryover_reconciliation', [])
        require(isinstance(reconcile, list), f'track[{tid}].carryover_reconciliation 须为数组')
        completion_label = non_empty_str(t.get('completion_status'), f'track[{tid}].completion_status')
        require(completion_label in COMPLETION_LABELS,
                f'track[{tid}].completion_status 须为 { sorted(COMPLETION_LABELS) } 之一：{completion_label}')

        if parent is None or p_key is None:
            require(not reconcile, f'track[{tid}] 无父周对应项时 carryover_reconciliation 须为空')
            require(completion_label == '不适用', f'无上周计划的项目 completion_status 须为“不适用”：{tid}')
            last_week_plan_text = '未设定。'
            reconcile_summary = '无上周计划可对照。'
        else:
            require(isinstance(p_key, str) and p_key in parent['tracks'],
                    f'track[{tid}].parent_track_key={p_key} 在父周（{parent["week_id"]}）中不存在；可选：{list(parent["tracks"])}')
            require(p_key not in bound_parent_keys, f'父周项目 {p_key} 被多个本周项目重复承接')
            bound_parent_keys.add(p_key)
            p_track = parent['tracks'][p_key]
            last_week_plan_text = p_track['raw_next_plan'] or '未设定。'
            expected_items = {x['item_id']: x for x in p_track['items']}
            if not expected_items:
                require(not reconcile and completion_label == '不适用',
                        f'track[{tid}] 父周无具体下周计划条目，completion_status 须为“不适用”')
                reconcile_summary = '上周未设定具体计划条目。'
            else:
                seen_items = set()
                outcomes = []
                clause_notes = []
                for entry in reconcile:
                    require(isinstance(entry, dict), f'track[{tid}] 对账项须为对象')
                    iid = entry.get('parent_item_id')
                    require(iid in expected_items and iid not in seen_items,
                            f'track[{tid}] 对账项未知或重复：{iid}；应覆盖 {list(expected_items)}')
                    seen_items.add(iid)
                    outcome = entry.get('outcome')
                    require(outcome in OUTCOMES,
                            f'对账项 {iid} outcome 无效（若为 init 模板请先填写实际结果）：{outcome}')
                    note = non_empty_str(entry.get('note'), f'reconcile[{iid}].note', min_len=4)
                    for eref in entry.get('evidence_refs', []):
                        verify_ref(eref, root)
                    cont_task = entry.get('continued_by_task_id')
                    def_id = entry.get('deferred_to_backlog_id')
                    if outcome in {'partial', 'deferred', 'blocked'}:
                        has_cont = isinstance(cont_task, str) and cont_task in all_next_tasks
                        has_def = isinstance(def_id, str) and def_id in backlog_by_id
                        require(has_cont or has_def,
                                f'未完结的上周计划项 {iid}（{outcome}）必须指定有效的 continued_by_task_id 或 deferred_to_backlog_id，不得静默丢弃')
                    else:
                        require(cont_task is None or cont_task in all_next_tasks, f'{iid} continued_by_task_id 无效')
                        require(def_id is None or def_id in backlog_by_id, f'{iid} deferred_to_backlog_id 无效')
                    outcomes.append(outcome)
                    clause_notes.append(note.rstrip('。'))
                require(seen_items == set(expected_items),
                        f'track[{tid}] 未完整对账上周计划条目，缺失：{sorted(set(expected_items) - seen_items)}')
                if all(o == 'done' for o in outcomes):
                    require(completion_label == '全部完成', f'track[{tid}] 所有条目均 done 时 completion_status 须为“全部完成”')
                elif any(o in {'done', 'partial'} for o in outcomes):
                    require(completion_label == '部分完成', f'track[{tid}] 存在未完结条目时不得标为“全部完成”，应标“部分完成”')
                else:
                    require(completion_label in {'未完成', '顺延'}, f'track[{tid}] 无完成项时须标“未完成”或“顺延”')
                custom_comp = t.get('completion_summary')
                if prob_info is not None:
                    require(custom_comp is not None,
                            f'track[{tid}] 为绑定课题的核心项目，必须提供连贯因果句 completion_summary，禁止机械拼接子项备注')
                if custom_comp is not None:
                    reconcile_summary = check_human_prose(
                        non_empty_str(custom_comp, f'track[{tid}].completion_summary', min_len=12),
                        f'track[{tid}].completion_summary',
                    )
                else:
                    reconcile_summary = check_human_prose('；'.join(clause_notes) + '。', f'track[{tid}].reconcile_notes')

        # 2. Progress, status & deliverable refs validation
        progress = check_human_prose(non_empty_str(t.get('this_week_progress'), f'track[{tid}].this_week_progress', min_len=10),
                                     f'track[{tid}].this_week_progress')
        raw_points = t.get('progress_points')
        if prob_info is not None:
            require(raw_points is not None,
                    f'track[{tid}] 为核心课题项目，本周进展必须提供 2–4 条递进要点 progress_points（问题切入→核查证据→方案推论），不得单纯罗列单段文字')
        progress_points = None
        if raw_points is not None:
            require(isinstance(raw_points, list) and 2 <= len(raw_points) <= 4,
                    f'track[{tid}].progress_points 须包含 2–4 条有逻辑递进的要点')
            progress_points = [
                check_human_prose(non_empty_str(p, f'track[{tid}].progress_points[]', min_len=12),
                                  f'track[{tid}].progress_points[]')
                for p in raw_points
            ]
        status_level = non_empty_str(t.get('status_level'), f'track[{tid}].status_level')
        require(status_level in STATUS_LEVELS, f'track[{tid}].status_level 须为 {sorted(STATUS_LEVELS)} 之一')
        status_detail = check_human_prose(non_empty_str(t.get('status_reason'), f'track[{tid}].status_reason', min_len=6),
                                          f'track[{tid}].status_reason')
        combined_prog = progress + ' ' + ' '.join(progress_points or [])
        if prob_info is not None and prob_info['confirmed_decisions'] == 0 and run_diag['by_problem'][pid]['exploratory'] + run_diag['by_problem'][pid]['confirmatory'] == 0:
            require('已完成实验' not in combined_prog and '验证通过H1' not in combined_prog,
                    f'track[{tid}] 对应课题尚无正式决定或研究运行，进展描述不得声称已完成模型实验')

        deliverables = t.get('deliverable_refs', [])
        require(isinstance(deliverables, list) and deliverables, f'track[{tid}] 须至少提供一项可核验的 deliverable_refs')
        verified_links = [
            register_bundle_ref(dref, f'track[{tid}].deliverable_refs')
            for dref in deliverables
        ]

        # 3. Next-week plan feasibility & realism guards
        validated_tasks = []
        for task in t.get('next_week_plan', []):
            task_id = safe_id(task['task_id'])
            t_summary = check_human_prose(non_empty_str(task.get('summary'), f'task[{task_id}].summary', min_len=8),
                                          f'task[{task_id}].summary')
            kind = task.get('kind')
            require(kind in TASK_KINDS, f'task[{task_id}].kind 无效：{kind}')
            effort = task.get('effort')
            require(effort in EFFORT_POINTS, f'task[{task_id}].effort 须为 S/M/L：{effort}')
            commitment = task.get('commitment')
            require(commitment in COMMITMENTS, f'task[{task_id}].commitment 须为 committed/conditional：{commitment}')
            dependency = task.get('dependency')
            require(dependency in DEPENDENCIES, f'task[{task_id}].dependency 无效：{dependency}')
            due = parse_iso_date(task.get('due_date'), f'task[{task_id}].due_date')
            require(next_start <= due <= next_end,
                    f'task[{task_id}].due_date={due} 不在目标下周窗口 [{next_start}, {next_end}]（{next_wid}）内')
            criterion = non_empty_str(task.get('deliverable_or_criterion'),
                                      f'task[{task_id}].deliverable_or_criterion', min_len=6)
            fallback = task.get('if_blocked_fallback')
            trigger = task.get('trigger_condition')

            # Scientific gate readiness guard
            if prob_info is not None:
                if kind in {'formal_experiment', 'result_analysis'} and not prob_info['formal_ready']:
                    raise ValueError(
                        f'task[{task_id}] 违反科研门禁：课题 {pid} 仍有 {len(prob_info["null_decisions"])} 项未确认决定且未完成正式冻结，'
                        f'下周不得排入 {kind} 任务'
                    )
                if kind == 'dev_exploration' and prob_info['core_missing'] and commitment == 'committed':
                    raise ValueError(
                        f'task[{task_id}] 预期不合理：课题 {pid} 尚未确认 {prob_info["core_missing"]}，'
                        f'dev_exploration 不得作为无条件 committed 任务，请改为 conditional 或转入 deferred_backlog'
                    )

            # External blocker & commitment consistency guard
            if dependency in {'awaits_pi', 'awaits_external'}:
                if commitment == 'committed':
                    non_empty_str(fallback, f'task[{task_id}].if_blocked_fallback（依赖外部输入却设为 committed 时必填兜底动作）', min_len=6)
                else:
                    non_empty_str(trigger or fallback, f'task[{task_id}].trigger_condition（conditional 任务必填触发条件）', min_len=6)
            elif commitment == 'conditional':
                non_empty_str(trigger, f'task[{task_id}].trigger_condition（自主任务设为 conditional 时须写明前置触发条件）', min_len=6)

            if effort == 'L':
                large_count += 1
            if commitment == 'committed':
                committed_count += 1
                committed_points += EFFORT_POINTS[effort]
            else:
                conditional_count += 1

            clean_fb = fallback.strip() if isinstance(fallback, str) and fallback.strip() else None
            clean_tr = trigger.strip() if isinstance(trigger, str) and trigger.strip() else None
            if clean_fb is not None:
                check_human_prose(clean_fb, f'task[{task_id}].if_blocked_fallback')
            if clean_tr is not None:
                check_human_prose(clean_tr, f'task[{task_id}].trigger_condition')

            validated_tasks.append({
                'task_id': task_id,
                'summary': t_summary,
                'kind': kind,
                'effort': effort,
                'points': EFFORT_POINTS[effort],
                'commitment': commitment,
                'dependency': dependency,
                'due_date': str(due),
                'deliverable_or_criterion': criterion,
                'if_blocked_fallback': clean_fb,
                'trigger_condition': clean_tr,
            })

        enriched_tracks.append({
            'index': idx,
            'track_id': tid,
            'title': title,
            'problem_id': pid,
            'last_week_plan_text': last_week_plan_text,
            'completion_status': completion_label,
            'reconcile_summary': reconcile_summary,
            'this_week_progress': progress,
            'progress_points': progress_points,
            'status_level': status_level,
            'status_reason': status_detail,
            'verified_links': verified_links,
            'next_week_plan': validated_tasks,
        })

    # Ensure no parent track with planned items was silently dropped
    if parent is not None:
        closed_map = {}
        for c in data.get('closed_parent_tracks', []):
            require(isinstance(c, dict), 'closed_parent_tracks 元素须为对象')
            ck = non_empty_str(c.get('parent_track_key'), 'closed_parent_tracks.parent_track_key')
            closed_map[ck] = non_empty_str(c.get('reason'), f'closed_parent_tracks[{ck}].reason', min_len=6)
        for pk, p_track in parent['tracks'].items():
            if p_track['items'] and pk not in bound_parent_keys:
                require(pk in closed_map,
                        f'父周（{parent["week_id"]}）项目 {pk}（{p_track["heading"]}）含未对账下周计划，不得在本周静默漏掉')

    # Check global weekly capacity budget
    require(committed_count <= budget['max_committed_tasks'],
            f'下周确定承诺任务数（{committed_count}）超出上限（{budget["max_committed_tasks"]}）；请精简或移入 deferred_backlog')
    require(committed_points <= budget['max_effort_points'],
            f'下周确定承诺工作量点数（{committed_points} 点）超出上限（{budget["max_effort_points"]} 点）；请控制单周负载')
    require(large_count <= budget['max_large_tasks'],
            f'下周大体量任务 L 数量（{large_count}）超出上限（{budget["max_large_tasks"]}）；单周最多安排 1 项 L 级任务')
    require(conditional_count <= budget['max_conditional_tasks'],
            f'下周条件触发任务数（{conditional_count}）超出上限（{budget["max_conditional_tasks"]}）')
    require(committed_count >= 1, '下周至少须安排 1 项自主或带兜底的 committed 任务')

    # Validate decisions_needed & outputs
    decisions_needed = data.get('decisions_needed', [])
    require(isinstance(decisions_needed, list), 'decisions_needed 须为数组')
    checked_decisions = []
    for dec in decisions_needed:
        require(isinstance(dec, dict), 'decisions_needed 元素须为对象')
        checked_decisions.append({
            'matter': check_human_prose(non_empty_str(dec.get('matter'), 'decisions_needed.matter', min_len=4),
                                        'decisions_needed.matter'),
            'who': check_human_prose(non_empty_str(dec.get('who'), 'decisions_needed.who', min_len=2),
                                     'decisions_needed.who'),
            'options_and_recommendation': check_human_prose(
                non_empty_str(dec.get('options_and_recommendation'), 'decisions_needed.options_and_recommendation', min_len=8),
                'decisions_needed.options_and_recommendation',
            ),
            'needed_by': check_human_prose(non_empty_str(dec.get('needed_by'), 'decisions_needed.needed_by', min_len=4),
                                           'decisions_needed.needed_by'),
        })

    other_outputs = [check_human_prose(non_empty_str(x, 'other_outputs[]', min_len=3), 'other_outputs[]')
                     for x in data.get('other_outputs', [])]

    advisor_en = data.get('advisor_email_en')
    checked_en = None
    if advisor_en is not None:
        require(isinstance(advisor_en, dict), 'advisor_email_en 须为对象')
        subj = non_empty_str(advisor_en.get('subject'), 'advisor_email_en.subject', min_len=6)
        body = non_empty_str(advisor_en.get('body'), 'advisor_email_en.body', min_len=80)
        wc = english_word_count(body)
        require(150 <= wc <= 350, f'英文导师进展邮件词数须在 150–350 词之间（当前 {wc} 词）')
        checked_en = {'subject': subj, 'body': body, 'word_count': wc}

    raw_extra = data.get('extra_bundle_refs', [])
    require(isinstance(raw_extra, list), 'extra_bundle_refs 须为数组')
    for idx_ex, ex in enumerate(raw_extra):
        require(isinstance(ex, dict), f'extra_bundle_refs[{idx_ex}] 须为对象')
        register_bundle_ref(ex, f'extra_bundle_refs[{idx_ex}]')

    raw_notes = data.get('bundle_notes', [])
    require(isinstance(raw_notes, list), 'bundle_notes 须为数组')
    bundle_notes = [
        non_empty_str(note, f'bundle_notes[{i}]', min_len=6)
        for i, note in enumerate(raw_notes)
    ]

    used_bundle_names = set()
    for entry in raw_bundle_refs.values():
        if entry['explicit_name'] is not None:
            require(entry['explicit_name'] not in used_bundle_names,
                    f'不同来源映射到重复的 bundle_name：{entry["explicit_name"]}')
            used_bundle_names.add(entry['explicit_name'])

    bundle_items = []
    bundle_rel_by_path = {}
    for index, entry in enumerate(raw_bundle_refs.values(), 1):
        if entry['explicit_name'] is not None:
            bname = entry['explicit_name']
        else:
            bname = safe_bundle_filename(None, index, entry['label'], entry['src_path'], used_bundle_names)
            used_bundle_names.add(bname)
        brel = f'相关材料/{bname}'
        bundle_rel_by_path[entry['path']] = brel
        bundle_items.append({
            'bundle_name': bname,
            'bundle_rel': brel,
            'label': entry['label'],
            'purpose': entry['purpose'],
            'path': entry['path'],
            'bytes': entry['bytes'],
            'sha256': entry['sha256'],
            'src_path': entry['src_path'],
        })

    for tr in enriched_tracks:
        for link in tr['verified_links']:
            link['bundle_rel'] = bundle_rel_by_path[link['path']]

    return {
        'week_id': week_id,
        'week_num': week_num,
        'period_start': start_date,
        'period_end': end_date,
        'as_of_date': as_of,
        'next_week_id': next_wid,
        'next_start': next_start,
        'next_end': next_end,
        'author': author,
        'header_note': status_note,
        'one_line_summary': one_liner,
        'source_rel': str(record_path.relative_to(root)),
        'parent': parent,
        'tracks': enriched_tracks,
        'other_outputs': other_outputs,
        'decisions_needed': checked_decisions,
        'deferred_backlog': backlog,
        'bundle_items': bundle_items,
        'bundle_notes': bundle_notes,
        'capacity_usage': {
            'committed_tasks': committed_count,
            'max_committed_tasks': budget['max_committed_tasks'],
            'committed_points': committed_points,
            'max_effort_points': budget['max_effort_points'],
            'large_tasks': large_count,
            'max_large_tasks': budget['max_large_tasks'],
            'conditional_tasks': conditional_count,
            'max_conditional_tasks': budget['max_conditional_tasks'],
        },
        'problems': problems,
        'run_diag': run_diag,
        'advisor_email_en': checked_en,
    }


def rel_link_from_report(root_rel_target):
    return f'../../../{root_rel_target}'


def format_human_task(task):
    due_cn = cn_month_day(dt.date.fromisoformat(task['due_date']))
    text = task['summary'].rstrip('。')
    if task['commitment'] == 'conditional':
        cond = (task['trigger_condition'] or '待前置事项确认后启动').rstrip('。')
        return f'{due_cn}前（{cond}）：{text}。'
    if task['if_blocked_fallback']:
        fb = task['if_blocked_fallback'].rstrip('。')
        return f'{due_cn}前：{text}（{fb}）。'
    return f'{due_cn}前：{text}。'


def render_zh_markdown(ctx, *, bundle_mode=False):
    p_str = f"{ctx['period_start'].strftime('%m.%d')}–{ctx['period_end'].strftime('%m.%d')}"
    tag = '; bundle' if bundle_mode else ''
    marker = f"{MARKER_PREFIX}{ctx['source_rel']}{tag}; edit canonical JSON and linked sources -->"
    as_of_cn = cn_month_day(ctx['as_of_date'])
    lines = [
        f"# 周报 · W{ctx['week_num']:02d}（{p_str}）· {ctx['author']}",
        '',
        marker,
        '',
        f"> 审阅稿，进展截至 {as_of_cn}。{ctx['header_note']}",
        '',
        '## 本周一句话',
        '',
        ctx['one_line_summary'],
        '',
        '## 项目',
    ]

    for tr in ctx['tracks']:
        comp_disp = '部分' if tr['completion_status'] == '部分完成' else ('完成' if tr['completion_status'] == '全部完成' else tr['completion_status'])
        lines += [
            '',
            f"### {tr['index']} {tr['title']}",
            '',
            f"- 上周计划：{tr['last_week_plan_text']}",
            f"- 完成情况：{comp_disp}——{tr['reconcile_summary']}",
        ]
        if tr['progress_points']:
            lines.append(f"- 本周进展：{tr['this_week_progress']}")
            for pt in tr['progress_points']:
                lines.append(f"  - {pt}")
        else:
            lines.append(f"- 本周进展：{tr['this_week_progress']}")
        lines.append(f"- 状态：{tr['status_level']}——{tr['status_reason']}")

        if not tr['next_week_plan']:
            lines.append('- 下周计划：暂不安排新增任务，优先保障主线选题与数据确认。')
        elif len(tr['next_week_plan']) == 1:
            lines.append(f"- 下周计划：{format_human_task(tr['next_week_plan'][0])}")
        else:
            lines.append('- 下周计划：')
            for i, task in enumerate(tr['next_week_plan'], 1):
                lines.append(f"  {i}. {format_human_task(task)}")
        if bundle_mode:
            link_str = '、'.join(f"[{x['label']}]({x['bundle_rel']})" for x in tr['verified_links'])
        else:
            link_str = '、'.join(f"[{x['label']}]({rel_link_from_report(x['path'])})" for x in tr['verified_links'])
        lines.append(f'- 数据或文稿链接：{link_str}。')

    if ctx['other_outputs']:
        lines += ['', '## 论文与产出', '']
        lines.append('- 其他产出：' + '；'.join(ctx['other_outputs']) + '。')

    if ctx['decisions_needed']:
        lines += ['', '## 需要决定或协助']
        for i, dec in enumerate(ctx['decisions_needed'], 1):
            prefix = f'（{i}）' if len(ctx['decisions_needed']) > 1 else ''
            lines += [
                '',
                f"- {prefix}事项：{dec['matter']}",
                f"- 找谁：{dec['who']}",
                f"- 选项和我的建议：{dec['options_and_recommendation']}",
                f"- 最晚什么时候要：{dec['needed_by']}",
            ]

    if ctx['deferred_backlog']:
        lines += ['', '## 暂缓事项', '']
        for b in ctx['deferred_backlog']:
            s = b['summary'].rstrip('。')
            r = b['reason'].rstrip('。')
            c = b['reopen_condition'].rstrip('。')
            lines.append(f"- {s}：{r}；待{c}时再安排。")

    lines.append('')
    rendered_text = '\n'.join(lines)
    # Final defense: strip generated HTML comment & link targets, verify rendered prose is 100% clean
    prose_only = '\n'.join(l for l in lines if not l.startswith('<!--'))
    prose_only = re.sub(r'\]\([^)]+\)', ']', prose_only)
    check_human_prose(prose_only, 'rendered_zh_markdown')
    return rendered_text.encode('utf-8')


def render_en_markdown(ctx, *, bundle_mode=False):
    en = ctx['advisor_email_en']
    require(en is not None, f"{ctx['week_id']} 未配置 advisor_email_en，无法输出英文进展稿")
    tag = 'bundle-advisor-en' if bundle_mode else 'advisor-en'
    marker = f"{MARKER_PREFIX}{ctx['source_rel']}; {tag}; edit canonical JSON -->"
    lines = [
        f"# Advisor Progress Update · {ctx['week_id']} ({ctx['period_start'].isoformat()} to {ctx['period_end'].isoformat()})",
        '',
        marker,
        '',
        f"- **Subject**: {en['subject']}",
        f"- **Word Count**: {en['word_count']} words (target ~150–350 words)",
        '',
        '---',
        '',
        en['body'],
        '',
    ]
    return '\n'.join(lines).encode('utf-8')


def render_bundle_manifest(ctx, bundle_zh_bytes, bundle_en_bytes):
    marker = f"{MARKER_PREFIX}{ctx['source_rel']}; bundle-manifest; edit canonical JSON -->"
    zh_name = f"周报_{ctx['week_id']}_{ctx['author']}.md"
    zh_sha = hashlib.sha256(bundle_zh_bytes).hexdigest()
    rows = [(
        zh_name,
        zh_name,
        f"{ctx['week_id']} 主周报审阅稿（包内相对链接适配版）",
        f"`{ctx['source_rel']}`（派生自同一规范记录）",
        len(bundle_zh_bytes),
        zh_sha,
    )]
    if bundle_en_bytes is not None:
        en_name = f"导师进展邮件_{ctx['week_id']}_en.md"
        en_sha = hashlib.sha256(bundle_en_bytes).hexdigest()
        rows.append((
            en_name,
            en_name,
            f"{ctx['week_id']} 导师英文进展邮件草稿（{ctx['advisor_email_en']['word_count']} 词）",
            f"`{ctx['source_rel']}`（`advisor_email_en`）",
            len(bundle_en_bytes),
            en_sha,
        ))
    for item in ctx['bundle_items']:
        rows.append((
            item['bundle_rel'],
            item['bundle_rel'],
            item['purpose'],
            f"`{item['path']}`",
            item['bytes'],
            item['sha256'],
        ))
    lines = [
        f"# {ctx['week_id']} 周报交付文件夹：目录与来源说明",
        '',
        marker,
        '',
        f"本文件夹为 **{ctx['week_id']}（{ctx['period_start'].isoformat()} 至 {ctx['period_end'].isoformat()}，截至 {ctx['as_of_date'].isoformat()}）** 周报的自包含阅读与交付打包视图。",
        '',
        f"- **主周报文稿**：[{zh_name}]({zh_name}) 与 `{ctx['source_rel']}` 及单文件兼容视图保持同一科研正文口径，并将文内“数据或文稿链接”指向本包内 `相关材料/` 相对路径，便于整包阅读或发送。",
        '- **原位保留原则**：`相关材料/` 下文件均为工作区规范源的只读逐字节副本；原规范源仍保留在工作区原位，未被移动、改名或修改。若需修订周报正文或计划，请编辑规范源后重新渲染。',
        '',
        '## 文件清单与校验信息',
        '',
        '| 包内路径 | 用途 | 工作区规范源路径 | 字节数 | SHA-256 |',
        '| --- | --- | --- | ---: | --- |',
    ]
    for disp, target, purpose, src_desc, nbytes, digest in rows:
        p_cell = purpose.replace('|', '\\|')
        lines.append(f"| [{disp}]({target}) | {p_cell} | {src_desc} | {nbytes} | `{digest}` |")
    lines += [
        '',
        '## 边界说明',
        '',
        '1. 本清单中的工作区规范源均保持原位只读；包内 `相关材料/` 副本与对应规范源的字节内容及 SHA-256 严格一致。',
        '2. 复制入 `相关材料/` 的原始 Markdown 文档内部可能仍含指向工作区更深层目录的相对链接；如需回查底层全量数据或脚本，请按上表中的“工作区规范源路径”在工作区原位查找。',
    ]
    for idx, note in enumerate(ctx['bundle_notes'], 3):
        lines.append(f'{idx}. {note}')
    lines.append('')
    return '\n'.join(lines).encode('utf-8')


def zh_output_path(home, ctx):
    return home / '派生/周报' / f"周报_{ctx['week_id']}_{ctx['author']}.md"


def en_output_path(home, ctx):
    return home / '派生/周报' / f"导师进展邮件_{ctx['week_id']}_en.md"


def publish_derived(dest, content, *, refresh=False, check=False, dry=False):
    require(not dest.is_symlink(), f'派生周报路径不得为符号链接：{dest}')
    prev = dest.read_bytes() if dest.exists() else None
    if prev is not None:
        head = b'\n'.join(prev.splitlines()[:5])
        require(MARKER_PREFIX.encode('utf-8') in head, f'目标文件缺少生成标记，拒绝覆盖人工文件：{dest.name}')
    state = 'absent' if prev is None else ('current' if prev == content else 'stale')
    wrote = False
    if refresh and not dry and state != 'current':
        write(dest, content, replace=True)
        require(dest.read_bytes() == content, f'派生文件回读不一致：{dest.name}')
        state = 'current'
        wrote = True
    if check and state != 'current':
        raise ValueError(f'派生周报未发布或已过期，请带 --refresh 重新生成：{dest.name} ({state})')
    return state, wrote


def publish_bundle_materials(mat_dir, bundle_items, *, refresh=False, check=False, dry=False):
    require(not mat_dir.is_symlink(), f'相关材料目录不得为符号链接：{mat_dir}')
    if mat_dir.exists():
        require(mat_dir.is_dir(), f'相关材料路径不是目录：{mat_dir}')
    expected_names = {item['bundle_name'] for item in bundle_items}
    orphans = []
    if mat_dir.exists():
        for entry in sorted(mat_dir.iterdir(), key=lambda p: p.name):
            if entry.name == '.DS_Store':
                continue
            if entry.is_symlink() or not entry.is_file() or entry.name not in expected_names:
                orphans.append(entry.name)
    if refresh and not dry and orphans:
        raise ValueError(f'相关材料目录存在未登记的孤立文件或目录，请人工核查后清理：{orphans}')
    if check and orphans:
        raise ValueError(f'相关材料目录存在未登记的孤立文件或目录：{orphans}')

    wrote = 0
    states = []
    for item in bundle_items:
        dest = mat_dir / item['bundle_name']
        require(not dest.is_symlink(), f'相关材料副本不得为符号链接：{dest}')
        src_bytes = item['src_path'].read_bytes()
        require(len(src_bytes) == item['bytes'] and hashlib.sha256(src_bytes).hexdigest() == item['sha256'],
                f"规范源在打包期间发生变化：{item['path']}")
        prev = dest.read_bytes() if dest.exists() else None
        st = 'absent' if prev is None else ('current' if prev == src_bytes else 'stale')
        if refresh and not dry and st != 'current':
            write(dest, src_bytes, replace=True)
            require(dest.read_bytes() == src_bytes, f'相关材料副本回读不一致：{dest.name}')
            st = 'current'
            wrote += 1
        states.append(st)

    if orphans:
        mat_state = 'stale'
    elif states and all(s == 'current' for s in states):
        mat_state = 'current'
    elif all(s == 'absent' for s in states):
        mat_state = 'absent'
    else:
        mat_state = 'stale'

    if check and mat_state != 'current':
        raise ValueError(f'周报相关材料缺失或已过期，请带 --refresh 重新生成：{mat_dir.name} ({mat_state})')
    return mat_state, wrote


def record_files(home):
    folder = home / '周报记录'
    if not folder.exists() and not folder.is_symlink():
        return {}
    require(folder.is_dir() and not folder.is_symlink(), '周报记录位置不是真实目录')
    out = {}
    for p in sorted(folder.glob('*.json')):
        require(not p.is_symlink(), f'周报记录不得为符号链接：{p.name}')
        wid = p.stem
        parse_week_id(wid)
        out[wid] = p
    return out


def render_week(root, week_id, *, refresh=False, check=False, advisor_en=False, dry=False):
    home = root / '研究管理'
    records = record_files(home)
    require(week_id in records, f'未找到周报记录：{week_id}（当前已登记：{list(records) or "无"}）')
    rec_path = records[week_id]

    def build_once():
        ctx = validate_record(root, rec_path)
        zh_bytes = render_zh_markdown(ctx, bundle_mode=False)
        b_zh_bytes = render_zh_markdown(ctx, bundle_mode=True)
        has_en = advisor_en or ctx['advisor_email_en'] is not None
        en_bytes = render_en_markdown(ctx, bundle_mode=False) if has_en else None
        b_en_bytes = render_en_markdown(ctx, bundle_mode=True) if has_en else None
        b_mf_bytes = render_bundle_manifest(ctx, b_zh_bytes, b_en_bytes)
        return ctx, zh_bytes, en_bytes, b_zh_bytes, b_en_bytes, b_mf_bytes

    def publish_all(ctx, zh_bytes, en_bytes, b_zh_bytes, b_en_bytes, b_mf_bytes, *, do_refresh):
        zh_dest = zh_output_path(home, ctx)
        zh_state, zh_wrote = publish_derived(zh_dest, zh_bytes, refresh=do_refresh, check=check, dry=not do_refresh)
        en_info = None
        if en_bytes is not None:
            en_dest = en_output_path(home, ctx)
            en_state, en_wrote = publish_derived(en_dest, en_bytes, refresh=do_refresh, check=check, dry=not do_refresh)
            en_info = {'path': str(en_dest.relative_to(root)), 'view': en_state, 'wrote': en_wrote,
                       'word_count': ctx['advisor_email_en']['word_count']}

        bdir = bundle_dir(root, ctx)
        require(not bdir.is_symlink(), f'周报打包目录不得为符号链接：{bdir}')
        if bdir.exists():
            require(bdir.is_dir(), f'周报打包路径不是目录：{bdir}')
        b_zh_dest = bundle_zh_path(root, ctx)
        b_zh_state, b_zh_wrote = publish_derived(b_zh_dest, b_zh_bytes, refresh=do_refresh, check=check, dry=not do_refresh)
        b_en_info = None
        b_en_dest = None
        if b_en_bytes is not None:
            b_en_dest = bundle_en_path(root, ctx)
            b_en_state, b_en_wrote = publish_derived(b_en_dest, b_en_bytes, refresh=do_refresh, check=check, dry=not do_refresh)
            b_en_info = {'path': str(b_en_dest.relative_to(root)), 'view': b_en_state, 'wrote': b_en_wrote,
                         'word_count': ctx['advisor_email_en']['word_count']}
        b_mf_dest = bundle_manifest_path(root, ctx)
        b_mf_state, b_mf_wrote = publish_derived(b_mf_dest, b_mf_bytes, refresh=do_refresh, check=check, dry=not do_refresh)
        mat_dir = bundle_materials_dir(root, ctx)
        mat_state, mat_wrote = publish_bundle_materials(
            mat_dir, ctx['bundle_items'], refresh=do_refresh, check=check, dry=not do_refresh
        )
        if (check or do_refresh) and bdir.exists():
            expected_mds = {b_zh_dest, b_mf_dest}
            if b_en_dest is not None:
                expected_mds.add(b_en_dest)
            for f in sorted(bdir.glob('*.md')):
                require(f in expected_mds, f'周报打包目录中存在未登记的孤立 Markdown：{f.name}')
        states = [b_zh_state, b_mf_state, mat_state]
        if b_en_info is not None:
            states.append(b_en_info['view'])
        if all(s == 'current' for s in states):
            bundle_view = 'current'
        elif all(s == 'absent' for s in states):
            bundle_view = 'absent'
        else:
            bundle_view = 'stale'
        bundle_info = {
            'dir': str(bdir.relative_to(root)),
            'view': bundle_view,
            'report': {'path': str(b_zh_dest.relative_to(root)), 'view': b_zh_state, 'wrote': b_zh_wrote},
            'advisor_en': b_en_info,
            'manifest': {'path': str(b_mf_dest.relative_to(root)), 'view': b_mf_state, 'wrote': b_mf_wrote},
            'materials_count': len(ctx['bundle_items']),
            'materials_view': mat_state,
            'materials_wrote': mat_wrote,
        }
        return zh_dest, zh_state, zh_wrote, en_info, bundle_info

    if refresh and not dry:
        with lock(under(home, '.reports.lock')):
            b1 = build_once()
            b2 = build_once()
            require(b1[1:] == b2[1:], '生成周报期间上游输入发生变化，请重试')
            ctx = b1[0]
            zh_dest, zh_state, zh_wrote, en_info, bundle_info = publish_all(*b1, do_refresh=True)
    else:
        b1 = build_once()
        ctx = b1[0]
        zh_dest, zh_state, zh_wrote, en_info, bundle_info = publish_all(*b1, do_refresh=False)

    rd = ctx['run_diag']
    overruns = []
    for pid, pinfo in sorted(ctx['problems'].items()):
        if 'BUD-GPU' in pinfo['budget_limits']:
            total_gpu = sum(hrs for (p, _), hrs in rd['group_gpu_hours'].items() if p == pid)
            lim = pinfo['budget_limits']['BUD-GPU']
            if total_gpu > lim:
                overruns.append({'problem_id': pid, 'budget_id': 'BUD-GPU', 'actual': total_gpu, 'limit': lim})

    return {
        'status': 'pass',
        'week_id': week_id,
        'next_week_id': ctx['next_week_id'],
        'dry_run': dry,
        'source': ctx['source_rel'],
        'parent_week': ctx['parent']['week_id'] if ctx['parent'] else None,
        'report': {'path': str(zh_dest.relative_to(root)), 'view': zh_state, 'wrote': zh_wrote},
        'advisor_en': en_info,
        'bundle': bundle_info,
        'capacity_usage': ctx['capacity_usage'],
        'run_diagnostics': {
            'in_period_research_runs': rd['in_period_research_runs'],
            'registered_research_runs': rd['registered_research_runs'],
            'running_alerts': rd['running_alerts'],
            'budget_overruns': overruns,
        },
    }


def verify_all_reports(root, *, check_views=True):
    home = root / '研究管理'
    records = record_files(home)
    checked = []
    for wid in sorted(records):
        res = render_week(root, wid, refresh=False, check=check_views, dry=True)
        checked.append({
            'week_id': wid,
            'parent_week': res['parent_week'],
            'next_week_id': res['next_week_id'],
            'report_view': res['report']['view'],
            'advisor_en_view': res['advisor_en']['view'] if res['advisor_en'] else None,
            'bundle_dir': res['bundle']['dir'],
            'bundle_view': res['bundle']['view'],
            'bundle_materials_count': res['bundle']['materials_count'],
            'committed_tasks': res['capacity_usage']['committed_tasks'],
            'committed_points': res['capacity_usage']['committed_points'],
        })
    # Also ensure no orphan unbacked files sit in 派生/周报
    derived_dir = home / '派生/周报'
    if derived_dir.exists():
        require(derived_dir.is_dir() and not derived_dir.is_symlink(), '派生/周报 目录非法')
        expected_paths = set()
        for wid, p in records.items():
            ctx = validate_record(root, p)
            expected_paths.add(zh_output_path(home, ctx))
            if ctx['advisor_email_en'] is not None:
                expected_paths.add(en_output_path(home, ctx))
        for f in sorted(derived_dir.glob('*.md')):
            require(f in expected_paths, f'派生/周报 中存在无对应周报记录的孤立文件：{f.name}')
    return {'status': 'pass', 'registered_weeks': len(checked), 'weeks': checked}


def init_week(root, week_id, *, from_week=None, from_markdown=None, author='Researcher', dry=False):
    home = root / '研究管理'
    _, _, start_date, end_date = parse_week_id(week_id)
    dest = home / '周报记录' / f'{week_id}.json'
    require(not dest.exists(), f'周报记录已存在，不覆盖：{dest.relative_to(root)}')

    p_wid = from_week or prev_week_id(week_id)
    parse_week_id(p_wid)
    if from_markdown is not None:
        md_path = under(root, from_markdown)
        require(md_path.is_file(), f'指定的历史周报不存在：{from_markdown}')
        parent_ref = dict(ref(md_path, root), kind='legacy_markdown', week_id=p_wid)
        parent = parse_legacy_markdown(md_path, p_wid)
        prev_data = None
    else:
        records = record_files(home)
        require(p_wid in records, f'未找到用于承接的父周记录 {p_wid}；若是首次接入旧 Markdown 请加 --from-markdown')
        p_path = records[p_wid]
        parent_ref = dict(ref(p_path, root), kind='weekly_record', week_id=p_wid)
        parent = load_parent(root, parent_ref, week_id)
        prev_data = read(p_path)
        author = prev_data.get('author', author)

    tracks = []
    if prev_data is not None:
        for t in prev_data.get('tracks', []):
            tid = t['track_id']
            p_track = parent['tracks'][tid]
            tracks.append({
                'track_id': tid,
                'title': t['title'],
                'problem_id': t.get('problem_id'),
                'parent_track_key': tid,
                'completion_status': '部分完成' if p_track['items'] else '不适用',
                'completion_summary': '待填写：用连贯因果句说明哪部分已完成、哪部分未完结及其研究原因。' if p_track['items'] else None,
                'carryover_reconciliation': [
                    {
                        'parent_item_id': item['item_id'],
                        'outcome': 'pending_fill',
                        'note': f"待核对上周任务：{item['summary']}",
                        'evidence_refs': [],
                        'continued_by_task_id': None,
                        'deferred_to_backlog_id': None,
                    }
                    for item in p_track['items']
                ],
                'this_week_progress': '待填写：用一句总起说明本周聚焦解决的具体问题。',
                'progress_points': (
                    [
                        '**核查与工作**：待填写本周开展了哪些数据、文献或实验核查。',
                        '**关键发现与依据**：待填写观察到的核心数字、反例或限制条件。',
                        '**对方案的推论**：待填写上述发现如何改变当前判断并导出下周安排。',
                    ]
                    if t.get('problem_id') is not None else None
                ),
                'status_level': t.get('status_level', '正常'),
                'status_reason': '待填写当前状态依据与阻塞边界。',
                'deliverable_refs': list(t.get('deliverable_refs', [])[:1]),
                'next_week_plan': [],
            })
        carried_backlog = list(prev_data.get('deferred_backlog', []))
    else:
        for pk, p_track in parent['tracks'].items():
            tracks.append({
                'track_id': f'track-{pk}',
                'title': re.sub(r'^\d+\s+', '', p_track['heading']),
                'problem_id': None,
                'parent_track_key': pk,
                'completion_status': '部分完成' if p_track['items'] else '不适用',
                'completion_summary': '待填写：用连贯因果句说明哪部分已完成、哪部分未完结及其研究原因。' if p_track['items'] else None,
                'carryover_reconciliation': [
                    {
                        'parent_item_id': item['item_id'],
                        'outcome': 'pending_fill',
                        'note': f"待核对上周任务：{item['summary']}",
                        'evidence_refs': [],
                        'continued_by_task_id': None,
                        'deferred_to_backlog_id': None,
                    }
                    for item in p_track['items']
                ],
                'this_week_progress': '待填写：用一句总起说明本周聚焦解决的具体问题。',
                'progress_points': None,
                'status_level': '正常',
                'status_reason': '待填写当前状态依据与阻塞边界。',
                'deliverable_refs': [],
                'next_week_plan': [],
            })
        carried_backlog = []

    skeleton = {
        'schema_version': 'weekly-record.v1',
        'week_id': week_id,
        'period_start': str(start_date),
        'period_end': str(end_date),
        'as_of_date': str(start_date),
        'author': author,
        'is_initial_week': False,
        'parent_ref': parent_ref,
        'header_note': f'上周计划沿用 {p_wid.split("-")[1]}；请逐项核实对账结果并替换全部待填写提示。',
        'one_line_summary': '待填写本周一句话核心结论。',
        'capacity_budget': dict(DEFAULT_BUDGET),
        'tracks': tracks,
        'extra_bundle_refs': [],
        'bundle_notes': [],
        'other_outputs': [],
        'decisions_needed': [],
        'deferred_backlog': carried_backlog,
        'advisor_email_en': None,
    }
    if not dry:
        with lock(under(home, '.reports.lock')):
            require(not dest.exists(), f'并发创建冲突：{dest.name}')
            write(dest, skeleton)
    return {
        'status': 'pass',
        'dry_run': dry,
        'week_id': week_id,
        'parent_week': p_wid,
        'output': str(dest.relative_to(root)),
        'reconciliation_items_seeded': sum(len(t['carryover_reconciliation']) for t in tracks),
        'note': '模板中 outcome=pending_fill 须核实填写后方可通过 check/render 发布',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)

    p_init = sub.add_parser('init', help='承接上周周报，初始化新一周周报记录骨架')
    p_init.add_argument('--week', required=True, help='ISO 周编号，如 2026-W49')
    p_init.add_argument('--from-week', help='父周编号，默认自动取紧邻上一周')
    p_init.add_argument('--from-markdown', help='首次从根目录历史周报 Markdown 承接时的相对路径')
    p_init.add_argument('--author', default='Researcher')
    p_init.add_argument('--dry-run', action='store_true')

    p_render = sub.add_parser('render', help='校验周报承接与下周预期门禁，生成或检查派生周报')
    p_render.add_argument('--week', required=True, help='ISO 周编号，如 2026-W48')
    mode = p_render.add_mutually_exclusive_group()
    mode.add_argument('--refresh', action='store_true')
    mode.add_argument('--check-view', action='store_true')
    p_render.add_argument('--advisor-en', action='store_true')
    p_render.add_argument('--dry-run', action='store_true')

    p_check = sub.add_parser('check', help='校验全部已登记周报承接链与已发布派生文件')
    p_check.add_argument('--week', help='仅检查指定周')
    p_check.add_argument('--no-view-check', action='store_true')
    p_check.add_argument('--dry-run', action='store_true')

    p_list = sub.add_parser('list', help='列出已登记周报记录与状态')
    p_list.add_argument('--dry-run', action='store_true')

    args = parser.parse_args()
    root = root_path()
    if args.op == 'init':
        return init_week(root, args.week, from_week=args.from_week,
                         from_markdown=args.from_markdown, author=args.author, dry=args.dry_run)
    if args.op == 'render':
        return render_week(root, args.week, refresh=args.refresh, check=args.check_view,
                           advisor_en=args.advisor_en, dry=args.dry_run)
    if args.op == 'check':
        if args.week:
            return render_week(root, args.week, refresh=False, check=not args.no_view_check, dry=True)
        return verify_all_reports(root, check_views=not args.no_view_check)
    return verify_all_reports(root, check_views=False)


if __name__ == '__main__':
    sys.exit(cli(main))
