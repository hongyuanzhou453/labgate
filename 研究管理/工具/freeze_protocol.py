"""Freeze a topic's protocol inputs and preserve its cross-version test access history."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import re
import sys
from pathlib import Path

from common import (cli, closure, encoded, finite_number, lock, now, read, ref, require,
                    run_id, safe_id, sha, timestamp, under, verify_ref, write)
from register_sources import root_path
from topics import decision_register, draft_identity, local_file, select_topic, table
from vocabulary import choices


def topic(root, pid):
    folder = select_topic(Path(root) / '研究管理', safe_id(pid))
    require(folder == folder.resolve(), '课题目录不得使用路径别名')
    return folder


def direct(base, name):
    path = under(base, name)
    require(path == base / name, '冻结或暴露记录路径不得使用链接')
    return path


def version_dir(folder, protocol, version):
    return direct(folder, f'协议/{safe_id(protocol)}/{safe_id(version)}')


def own_input(root, folder, item):
    path = verify_ref(item, root)
    require(str(path.relative_to(root)) == item['path'], '输入不得使用路径别名')
    require(not path.is_relative_to(folder.parent) or path.is_relative_to(folder),
            '不能借用其他课题卷宗的输入或确认')
    return path


def protocol_inputs(root, folder, protocol, version):
    path = local_file(version_dir(folder, protocol, version), 'design.json')
    draft = draft_identity(path, folder)
    require(isinstance(draft.get('dataset_ref'), dict) and isinstance(draft.get('split_ref'), dict),
            '协议缺少数据快照或划分引用')
    dataset, split = draft['dataset_ref'], draft['split_ref']
    manifest = read(own_input(root, folder, dataset))
    roles = read(own_input(root, folder, split))
    require(isinstance(manifest, dict) and manifest.get('dataset_id') == safe_id(dataset.get('dataset_id'))
            and manifest.get('problem_id') == folder.name, '数据快照身份或课题不符')
    require(isinstance(roles, dict) and roles.get('split_id') == safe_id(split.get('split_id'))
            and roles.get('problem_id', folder.name) == folder.name
            and roles.get('dataset_manifest_sha256') == dataset['sha256'], '划分身份或快照哈希不符')
    return path, draft


def hypotheses(folder, draft, at):
    fields = ('problem_id', 'hypothesis_id', 'hypothesis_version', 'protocol_id',
              'protocol_version', 'statement', 'registered_at', 'analysis_plan')
    rows = table(local_file(folder, '假设.csv'), fields)
    require(isinstance(draft.get('hypotheses'), list) and draft['hypotheses'], '冻结需要已登记假设')
    expected = [(safe_id(h['hypothesis_id']), safe_id(h['hypothesis_version']))
                for h in draft['hypotheses']]
    require(len(set(expected)) == len(expected), '草稿重复引用假设')
    selected = {}
    seen = set()
    for row in rows:
        key = (safe_id(row['hypothesis_id']), safe_id(row['hypothesis_version']))
        require(row['problem_id'] == folder.name and key not in seen, '假设重复或跨题')
        seen.add(key)
        if (row['protocol_id'], row['protocol_version']) != (draft['protocol_id'], draft['version']):
            continue
        require(row['analysis_plan'].strip() and row['statement'].strip(), '假设缺少分析计划或陈述')
        require(timestamp(row['registered_at']) <= at, '假设或阈值登记晚于冻结')
        selected[key] = {k: row[k] for k in ('hypothesis_id', 'hypothesis_version', 'registered_at', 'analysis_plan')}
    require(set(expected) == set(selected), '草稿与本版本假设登记不一致')
    return [selected[key] for key in expected]


def confirmations(root, folder, draft):
    decisions = decision_register(root, folder)
    refs = draft.get('decision_refs')
    require(isinstance(refs, list) and refs, '缺少本题人工确认引用')
    for item in refs:
        own_input(root, folder, item)
        registered = [d for d in decisions
                      if all(d['confirmation_ref'].get(k) == item[k] for k in ('path', 'sha256'))
                      and ('protocol_id' not in d or (d['protocol_id'], d['protocol_version'])
                           == (draft['protocol_id'], draft['version']))]
        require(registered, '确认引用未在本题决策簿登记')
    return refs


def snapshot(root, folder, protocol, version, frozen_at):
    instant = timestamp(frozen_at)
    require(instant <= timestamp(now()), '冻结时间不能在未来')
    path, draft = protocol_inputs(root, folder, protocol, version)
    decisions = draft.get('decisions', {})
    require(isinstance(decisions, dict), '缺少协议决策及阈值')
    thresholds = {k: v for k, v in decisions.items() if k.endswith('_threshold') and v is not None}
    require('effect_threshold' in thresholds, '缺少已登记效应阈值')
    for value in thresholds.values():
        if isinstance(value, str):
            require(value.strip(), '阈值不能为空')
        elif isinstance(value, dict):
            finite_number(value.get('value'))
        else:
            finite_number(value)
    registered = hypotheses(folder, draft, instant)
    threshold_at = draft.get('threshold_registered_at',
                             max((h['registered_at'] for h in registered), key=timestamp))
    require(timestamp(threshold_at) <= instant, '阈值登记晚于冻结')
    approved = confirmations(root, folder, draft)
    inputs = [ref(path, root), ref(local_file(folder, '假设.csv'), root),
              ref(local_file(folder, '决策.json'), root)]
    return {'schema_version': 'freeze.v1', 'formal_use_allowed': False, 'problem_id': folder.name,
            'protocol_id': protocol, 'version': version, 'frozen_at': frozen_at,
            'protocol_ref': inputs[0], 'dataset_ref': draft['dataset_ref'], 'split_ref': draft['split_ref'],
            'hypotheses': registered, 'thresholds': thresholds, 'threshold_registered_at': threshold_at,
            'decision_refs': approved,
            'inputs': closure(inputs, root)}


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def verify_freeze(root, pid, protocol_id, version):
    root = Path(root).resolve()
    folder = topic(root, pid)
    path = local_file(version_dir(folder, protocol_id, version), 'freeze.json')
    record = read(path)
    require(isinstance(record, dict) and path.read_bytes() == encoded(record), '冻结记录字节格式已改变')
    calculated = snapshot(root, folder, protocol_id, version, record['frozen_at'])
    require(record == dict(calculated, record_sha256=digest(calculated)), '冻结记录或上游输入已变动')
    return record


def create_freeze(root, pid, protocol, version, dry):
    folder = topic(root, pid)
    path = direct(version_dir(folder, protocol, version), 'freeze.json')
    with contextlib.nullcontext() if dry else lock(direct(folder, '.freeze.lock')):
        if path.exists():
            return verify_freeze(root, pid, protocol, version)
        record = snapshot(root, folder, protocol, version, now())
        record['record_sha256'] = digest(record)
        if not dry:
            for item in record['inputs']:
                verify_ref(item, root)
            write(path, record)
    return record


def authorize_inputs(root, record, started_at):
    pid = record['problem_id']
    proto = record['protocol_ref']
    frozen = verify_freeze(root, pid, proto['protocol_id'], proto['version'])
    require(frozen['protocol_ref']['sha256'] == proto['sha256'], '运行协议与本题冻结哈希不同')
    for name, key in (('dataset_ref', 'dataset_id'), ('split_ref', 'split_id')):
        require(all(frozen[name][field] == record[name][field] for field in (key, 'sha256')),
                '运行数据或划分与本题冻结不同')
    bound = {(h['hypothesis_id'], h['hypothesis_version']) for h in frozen['hypotheses']}
    refs = record['hypothesis_refs']
    require(isinstance(refs, list) and refs and
            all((h['hypothesis_id'], h['hypothesis_version']) in bound for h in refs), '运行缺少本题已冻结的关联假设')
    started = timestamp(started_at)
    require(started > timestamp(frozen['frozen_at'])
            and started > timestamp(frozen['threshold_registered_at'])
            and all(started > timestamp(h['registered_at']) for h in frozen['hypotheses']),
            'V07: final_test started_at 必须严格晚于冻结及阈值登记')
    return frozen


GENESIS = {'sequence': 0, 'sha256': None}
EVENT_FIELDS = {'schema_version', 'sequence', 'previous_sha256', 'problem_id', 'event_id',
                'exposure_kind', 'protocol_id', 'protocol_version', 'dataset_id', 'split_id',
                'protocol_sha256', 'dataset_sha256', 'split_sha256',
                'eval_set', 'accessed_at', 'recorded_at', 'run_id', 'freeze_sha256'}


@contextlib.contextmanager
def journal_lock(folder, writing):
    path = direct(folder, '.exposure.lock')
    require(not path.exists() or path.is_file(), '暴露锁位置有误')
    if writing:
        with lock(path):
            yield
    elif path.exists():
        with path.open('rb') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            yield
    else:
        yield


def history(folder):
    directory = direct(folder, '测试暴露')
    anchor = direct(folder, '测试暴露.head.json')
    require(anchor.exists() or not directory.exists(), '暴露链缺少独立 HEAD')
    head = read(anchor) if anchor.exists() else GENESIS
    require(isinstance(head, dict) and set(head) == set(GENESIS) and type(head['sequence']) is int,
            '暴露链 HEAD 格式有误')
    events = []
    tip = GENESIS
    anchored = head == tip
    seen = set()
    for path in sorted(directory.iterdir()) if directory.exists() else ():
        if path.name.startswith('.') and path.name.endswith('.tmp'):
            continue  # unpublished atomic-write staging
        require(path.is_file() and not path.is_symlink(), '暴露链包含无效文件')
        seq = len(events) + 1
        checksum = sha(path)
        require(path.name == f'{seq:08d}-{checksum}.json', '暴露链中断或事件字节改变')
        event = read(path)
        require(set(event) == EVENT_FIELDS and event['schema_version'] == 'test-exposure.v1'
                and event['problem_id'] == folder.name and event['sequence'] == seq
                and event['previous_sha256'] == tip['sha256'], '暴露事件跨题或链序不符')
        for field in ('event_id', 'protocol_id', 'protocol_version', 'dataset_id', 'split_id'):
            safe_id(event[field])
        for field in ('protocol_sha256', 'dataset_sha256', 'split_sha256'):
            require(isinstance(event[field], str) and re.fullmatch('[0-9a-f]{64}', event[field]),
                    '暴露事件缺少协议、数据或划分的有效哈希')
        require(event['event_id'] not in seen and event['exposure_kind'] in choices('exposure_kind')
                and event['eval_set'] in choices('eval_set_kind'), '暴露事件身份或枚举无效')
        require(timestamp(event['accessed_at']) <= timestamp(event['recorded_at']), '暴露登记时间顺序有误')
        if event['exposure_kind'] == 'run_access':
            require(run_id(event['run_id']) == event['event_id'] and event['eval_set'] == 'final_test'
                    and isinstance(event['freeze_sha256'], str)
                    and re.fullmatch('[0-9a-f]{64}', event['freeze_sha256']), '运行暴露缺少冻结身份')
        else:
            require(event['run_id'] is None and event['freeze_sha256'] is None, '预登记不能冒充运行')
        seen.add(event['event_id'])
        events.append(event)
        tip = {'sequence': seq, 'sha256': checksum}
        anchored |= head == tip
    # A durable new event can precede HEAD by one atomic write after a crash.
    require(anchored and tip['sequence'] - head['sequence'] <= 1, '暴露链尾与独立 HEAD 不同或已截断')
    return events, tip, head


def append_event(folder, details, dry):
    events, tip, head = history(folder)
    head_path = direct(folder, '测试暴露.head.json')
    existing = next((e for e in events if e['event_id'] == details['event_id']), None)
    if existing:
        require(all(existing[k] == v for k, v in details.items()), '既有暴露事件不可覆盖')
        if not dry and head != tip:
            write(head_path, tip, replace=True)
        return existing
    event = dict(details, schema_version='test-exposure.v1', problem_id=folder.name,
                 sequence=tip['sequence'] + 1, previous_sha256=tip['sha256'], recorded_at=now())
    require(timestamp(event['accessed_at']) <= timestamp(event['recorded_at']), '不能登记未来的访问')
    if not dry:
        if head != tip or not head_path.exists():
            write(head_path, tip, replace=True)
        checksum = digest(event)
        destination = direct(folder, f"测试暴露/{event['sequence']:08d}-{checksum}.json")
        write(destination, event)
        write(head_path, {'sequence': event['sequence'], 'sha256': checksum}, replace=True)
    return event


def authorize_run(root, record, started_at):
    if 'final_test' not in record.get('eval_sets', []):
        return None
    folder = topic(root, record['problem_id'])
    with journal_lock(folder, False):
        history(folder)
        return authorize_inputs(root, record, started_at)


def record_run_exposure(root, record, *, dry_run=False):
    if 'final_test' not in record.get('eval_sets', []):
        return None
    folder = topic(root, record['problem_id'])
    with journal_lock(folder, not dry_run):
        frozen = authorize_inputs(root, record, record['started_at'])
        uid = run_id(record['run_id'])
        details = {'event_id': uid, 'run_id': uid, 'exposure_kind': 'run_access',
                   'protocol_id': frozen['protocol_id'], 'protocol_version': frozen['version'],
                   'dataset_id': frozen['dataset_ref']['dataset_id'], 'split_id': frozen['split_ref']['split_id'],
                   'protocol_sha256': frozen['protocol_ref']['sha256'], 'dataset_sha256': frozen['dataset_ref']['sha256'],
                   'split_sha256': frozen['split_ref']['sha256'],
                   'eval_set': 'final_test', 'accessed_at': record['started_at'], 'freeze_sha256': digest(frozen)}
        return append_event(folder, details, dry_run)


def add_commands(sub):
    for group, actions in (('freeze', ('create', 'verify')), ('exposure', ('list', 'record', 'verify'))):
        commands = sub.add_parser(group).add_subparsers(dest='action', required=True)
        for action in actions:
            parser = commands.add_parser(action)
            parser.add_argument('--problem', required=True)
            parser.add_argument('--dry-run', action='store_true')
            if group == 'freeze' or action == 'record':
                parser.add_argument('--protocol', required=True)
                parser.add_argument('--version', required=True)
            if group == 'exposure':
                for arg in ('dataset-id', 'split-id', 'eval-set'):
                    parser.add_argument('--' + arg, required=(action == 'record'))
                if action == 'record':
                    parser.add_argument('--event-id', required=True)
                    parser.add_argument('--accessed-at', required=True)


def dispatch(args, root):
    root = Path(root).resolve()
    result = {'status': 'pass', 'dry_run': args.dry_run, 'formal_use_allowed': False}
    if args.op == 'freeze':
        if args.action == 'create':
            frozen = create_freeze(root, args.problem, args.protocol, args.version, args.dry_run)
        else:
            frozen = verify_freeze(root, args.problem, args.protocol, args.version)
        return dict(result, freeze=frozen)
    folder = topic(root, args.problem)
    filters = {}
    for key in ('dataset_id', 'split_id', 'eval_set'):
        value = getattr(args, key)
        if value is not None:
            if key == 'eval_set':
                require(value in choices('eval_set_kind'), '未知的评价角色')
            else:
                safe_id(value)
            filters[key] = value
    if args.action == 'record':
        with journal_lock(folder, not args.dry_run):
            path, draft = protocol_inputs(root, folder, args.protocol, args.version)
            require(args.dataset_id == draft['dataset_ref']['dataset_id']
                    and args.split_id == draft['split_ref']['split_id'], '预登记快照或划分与协议不符')
            details = dict(filters, event_id=safe_id(args.event_id), run_id=None, exposure_kind='prior_test_exposure',
                           protocol_id=args.protocol, protocol_version=args.version, accessed_at=args.accessed_at,
                           protocol_sha256=sha(path), dataset_sha256=draft['dataset_ref']['sha256'],
                           split_sha256=draft['split_ref']['sha256'],
                           freeze_sha256=None)
            return dict(result, event=append_event(folder, details, args.dry_run))
    with journal_lock(folder, False):
        events, _, _ = history(folder)
    return dict(result, events=[e for e in events if all(e[k] == v for k, v in filters.items())],
                total_events=len(events))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_commands(parser.add_subparsers(dest='op', required=True))
    return dispatch(parser.parse_args(), root_path())


if __name__ == '__main__':
    sys.exit(cli(main))
