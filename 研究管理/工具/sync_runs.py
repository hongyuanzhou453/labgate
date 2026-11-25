"""Idempotent bundle ingestion. Committed journal precedes derived index."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from pathlib import Path

from common import cli, decode_json, lock, now, read, require, run_id, sha, under, write
from credential_scan import scan_bundle, scan_bytes
from freeze_protocol import authorize_run, record_run_exposure
from register_sources import root_path
from run_contract import eval_sets, validate, verify_files


def transactions(store):
    result = []
    for path in sorted((Path(store) / 'transactions').glob('*.json')):
        t = read(path)
        run_id(t['run_id'])
        require(type(t['revision']) is int and t['revision'] >= 0, 'bad transaction revision')
        require(t['transaction_id'] == f'{t["run_id"]}-{t["revision"]}', 'bad transaction identity')
        require(path.name == t['transaction_id'] + '.json', 'transaction filename differs')
        require(t['state'] in {'prepared', 'committed'}, 'bad transaction state')
        result.append((path, t))
    return result


def committed_records(store):
    store = Path(store)
    rows, all_records = {}, {}
    ts = sorted((t for _, t in transactions(store) if t['state'] == 'committed'),
                key=lambda t: (t['run_id'], t['revision']))
    for t in ts:
        uid = t['run_id']
        path = under(store, f'运行/{uid}/revisions/{t["revision"]}.json')
        require(path.is_file() and sha(path) == t['record_sha256'], 'committed record missing/tampered')
        item = read(path)
        require(item['run_id'] == uid and item['revision'] == t['revision'], 'transaction/record identity mismatch')
        prior = all_records.get(uid, [])
        validate(item, prior[-1][0] if prior else None, prior[-1][1] if prior else None)
        verify_files(item, under(store, f'运行/{uid}/files'))
        all_records.setdefault(uid, []).append((item, t['record_sha256']))
        rows[uid] = dict(item, record_sha256=t['record_sha256'], transaction_id=t['transaction_id'])
    return rows, all_records


def rebuild(store):
    rows, _ = committed_records(store)
    content = ''.join(json.dumps(rows[k], ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n'
                      for k in sorted(rows)).encode()
    write(Path(store) / 'runs.jsonl', content, replace=True)
    logs = [dict({k: t[k] for k in ('transaction_id', 'run_id', 'revision', 'record_sha256')},
                 started_at=t['prepared_at'], ended_at=t['committed_at'], action='commit',
                 status='committed', reason=None)
            for _, t in transactions(store) if t['state'] == 'committed']
    # Journal is authority; crash-safe log is a unique-by-transaction materialized view.
    log = ''.join(json.dumps(t, ensure_ascii=False, sort_keys=True) + '\n'
                  for t in sorted(logs, key=lambda x: x['transaction_id'])).encode()
    write(Path(store) / '同步日志.jsonl', log, replace=True)
    return rows


def ingest(bundle, store, *, record_path=None, dry_run=False, fault=None):
    bundle, store = Path(bundle).resolve(), Path(store).resolve()
    require(bundle != store and not store.is_relative_to(bundle) and not bundle.is_relative_to(store),
            'store and bundle must not overlap')
    scan_bundle(bundle)
    if record_path is not None:
        scan_bytes(b'', name=str(record_path))
    paths = [Path(record_path).resolve()] if record_path else list((bundle / 'records').glob('*/*.json'))
    require(paths, 'no run records supplied')
    for path in paths:
        require(path.is_relative_to(bundle), 'record outside bundle')
    inputs = []
    for path in paths:
        blob = path.read_bytes()
        scan_bytes(blob, name=path.name)
        record = decode_json(blob)
        if 'final_test' in (eval_sets(record) or ()):
            authorize_run(root_path(), record, record['started_at'])
        inputs.append((path, blob, record))
    inputs.sort(key=lambda entry: (entry[2]['run_id'], entry[2]['revision']))
    context = contextlib.nullcontext() if dry_run else lock(store / '.sync.lock')
    actions = []
    with context:
        _, history = committed_records(store)
        for path, blob, record in inputs:
            final_test = 'final_test' in record.get('eval_sets', ())
            uid, rev, digest = run_id(record['run_id']), record['revision'], hashlib.sha256(blob).hexdigest()
            require(type(rev) is int and rev >= 0, 'bad revision')
            prior = history.get(uid, [])
            if rev < len(prior):
                require(prior[rev][1] == digest, 'same revision different hash')
                if final_test:
                    verify_files(record, bundle, admitting=True)
                    record_run_exposure(root_path(), record, dry_run=dry_run)
                actions.append({'run_id': uid, 'revision': rev, 'action': 'noop'})
                continue
            validate(record, prior[-1][0] if prior else None, prior[-1][1] if prior else None)
            files = verify_files(record, bundle, admitting=True)
            if record['resumed_from']:
                parent = history.get(record['resumed_from'])
                require(parent and parent[-1][0]['status'] in {'failed', 'interrupted'},
                        'resume requires a committed interrupted/failed parent')
                checkpoint = record['resume_checkpoint_ref']['sha256']
                require(any(x.get('sha256') == checkpoint and x.get('kind') == 'checkpoint'
                            for x in parent[-1][0]['artifacts']), 'checkpoint is not registered on resume parent')
            txid = f'{uid}-{rev}'
            target = under(store, f'运行/{uid}/revisions/{rev}.json')
            txpath = under(store, f'transactions/{txid}.json')
            t = {'transaction_id': txid, 'run_id': uid, 'revision': rev,
                 'record_sha256': digest, 'state': 'prepared', 'prepared_at': now(),
                 'input_files': files, 'committed_at': None}
            if txpath.exists():
                old = read(txpath)
                require(old['record_sha256'] == digest and old['input_files'] == files, 'prepared transaction conflicts')
                t = old
            if final_test:
                record_run_exposure(root_path(), record, dry_run=True)
            if not dry_run:
                write(txpath, t)
                if fault == 'prepared':
                    raise ValueError('injected crash after prepared')
                for f in files:
                    source = under(bundle, f['path'])
                    payload = source.read_bytes()
                    scan_bytes(payload, name=source.name)
                    require(hashlib.sha256(payload).hexdigest() == f['sha256'], 'input changed during copy')
                    destination = under(store, f'运行/{uid}/files/{f["path"]}')
                    write(destination, payload)
                    require(sha(destination) == f['sha256'], 'copied bytes differ')
                require(path.read_bytes() == blob, 'record changed during ingestion')
                write(target, blob)
                verify_files(record, under(store, f'运行/{uid}/files'))
                if fault == 'files':
                    raise ValueError('injected crash after durable files')
                if final_test:
                    record_run_exposure(root_path(), record)
                t.update(state='committed', committed_at=now())
                write(txpath, t, replace=True)
                if fault == 'committed':
                    raise ValueError('injected crash before index')
            history.setdefault(uid, []).append((record, digest))
            actions.append({'run_id': uid, 'revision': rev, 'action': 'accepted'})
        if not dry_run:
            rebuild(store)
    return {'status': 'pass', 'dry_run': dry_run, 'actions': actions}


def recover(store, *, dry_run=False):
    store = Path(store).resolve()
    context = contextlib.nullcontext() if dry_run else lock(store / '.sync.lock')
    pending = []
    with context:
        _, history = committed_records(store)
        prepared = sorted(((p, t) for p, t in transactions(store) if t['state'] == 'prepared'),
                          key=lambda x: (x[1]['run_id'], x[1]['revision']))
        for path, t in prepared:
            uid, rev = t['run_id'], t['revision']
            recordpath = under(store, f'运行/{uid}/revisions/{rev}.json')
            if not recordpath.is_file():
                pending.append(t['transaction_id'])
                continue
            require(sha(recordpath) == t['record_sha256'], 'prepared bytes changed')
            scan_bundle(recordpath.parent.parent)
            record = read(recordpath)
            require(record['run_id'] == uid and record['revision'] == rev, 'prepared run identity mismatch')
            prior = history.get(uid, [])
            validate(record, prior[-1][0] if prior else None, prior[-1][1] if prior else None)
            verify_files(record, under(store, f'运行/{uid}/files'), admitting=True)
            if 'final_test' in record.get('eval_sets', ()):
                record_run_exposure(root_path(), record, dry_run=dry_run)
            if not dry_run:
                t.update(state='committed', committed_at=now())
                write(path, t, replace=True)
            history.setdefault(uid, []).append((record, t['record_sha256']))
        if not dry_run:
            rebuild(store)
    return {'status': 'fail' if pending else 'pass', 'dry_run': dry_run,
            'pending_requires_original_bundle': pending}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path)
    p.add_argument('--store', type=Path, required=True)
    p.add_argument('--record', type=Path)
    p.add_argument('--recover', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.recover:
        return recover(a.store, dry_run=a.dry_run)
    require(a.input is not None, '--input required')
    return ingest(a.input, a.store, record_path=a.record, dry_run=a.dry_run)


if __name__ == '__main__':
    sys.exit(cli(main))
