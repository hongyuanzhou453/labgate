"""Create/extend an immutable run chain in a portable artifact bundle."""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import sys
import uuid
from pathlib import Path

from common import cli, decode_json, encoded, lock, now, read, require, sha, under, write
from credential_scan import scan_bundle, scan_bytes
from freeze_protocol import authorize_run, record_run_exposure
from register_sources import root_path
from run_contract import IDENTITY, eval_sets, preflight_identity, validate, verify_files


def preflight(bundle, experiment, *, started_at=None):
    scan_bundle(bundle)
    if isinstance(experiment, (str, Path)):
        experiment, _ = read_experiment(bundle, experiment)
    else:
        scan_bytes(encoded(experiment), name='experiment.json')
    return check_experiment(bundle, experiment, started_at)


def check_experiment(bundle, experiment, started_at=None):
    if 'final_test' in (eval_sets(experiment) or ()):
        proposed = started_at if started_at is not None else experiment.get('started_at', now())
        authorize_run(root_path(), experiment, proposed)
    files = verify_files(experiment, bundle, admitting=True)
    protocol = read(under(bundle, experiment['protocol_ref']['path']))
    require(protocol.get('problem_id') == experiment['problem_id'], 'protocol problem differs')
    dataset = read(under(bundle, experiment['dataset_ref']['path']))
    split = read(under(bundle, experiment['split_ref']['path']))
    config = read(under(bundle, experiment['config_ref']['path']))
    require(protocol.get('protocol_id') == experiment['protocol_ref'].get('protocol_id') and
            protocol.get('version') == experiment['protocol_ref'].get('version'), 'protocol identity differs')
    require(dataset.get('dataset_id') == experiment['dataset_ref'].get('dataset_id') and
            split.get('split_id') == experiment['split_ref'].get('split_id'), 'data/split identity differs')
    hypotheses = {(x['hypothesis_id'], x['hypothesis_version']) for x in protocol.get('hypotheses', [])}
    require(all((x['hypothesis_id'], x['hypothesis_version']) in hypotheses for x in experiment['hypothesis_refs']),
            'hypothesis/version not declared in protocol')
    methods = {x['method_id']: x for x in protocol.get('methods', [])}
    require(experiment['method_id'] in methods and
            experiment['group_id'] in methods[experiment['method_id']]['group_ids'], 'method/group not declared in protocol')
    require(dataset.get('problem_id') == experiment['problem_id'], 'dataset problem differs')
    require(split.get('dataset_manifest_sha256') == experiment['dataset_ref']['sha256'],
            'split/dataset binding differs')
    require(config.get('seed') == experiment['seed'] and config.get('method_id') == experiment['method_id'],
            'configuration identity differs')
    require(protocol.get('dataset_ref') == experiment['dataset_ref'] and
            protocol.get('split_ref') == experiment['split_ref'], 'protocol input bindings differ')
    require(experiment['env']['python'] == '.'.join(map(str, sys.version_info[:3])),
            'declared Python differs from actual preflight environment')
    return {'schema_version': 'preflight.v1', 'status': 'pass', 'checked_at': now(),
            'experiment_sha256': None, 'files': files, 'run_kind': experiment['run_kind'],
            'scientific_identity': preflight_identity(experiment),
            'actual_python': sys.version, 'scope': 'portable local/posix bundle; not a target GPU certification'}


def start(bundle, experiment_path, *, dry_run=False):
    bundle = Path(bundle).resolve()
    experiment_path = Path(experiment_path).resolve()
    scan_bundle(bundle)
    experiment, experiment_sha = read_experiment(bundle, experiment_path)
    report = check_experiment(bundle, experiment)
    report['experiment_sha256'] = experiment_sha
    uid, t = str(uuid.uuid4()), now()
    report_path = bundle / 'preflight' / f'{uid}.json'
    record = {k: copy.deepcopy(experiment.get(k)) for k in IDENTITY}
    if 'eval_sets' in experiment:
        record['eval_sets'] = experiment['eval_sets']
    record.update(schema_version='run.v0.2', run_id=uid, started_at=t,
                  preflight_report_ref={'path': str(report_path.relative_to(bundle)),
                                        'sha256': hashlib.sha256(encoded(report)).hexdigest()},
                  revision=0, parent_record_sha256=None, revision_kind='start', actor='training_entry',
                  change_reason=None, evidence_refs=[], recorded_at=t, ended_at=None,
                  last_heartbeat_at=t, status='running', gpu_hours=None,
                  resource_accounting_status='unknown', failure_reason=None, metrics=None,
                  predictions=None, artifacts=[], prediction_freeze_ref=None, note='',
                  missing_reasons=dict(experiment.get('missing_reasons', {}), gpu_hours='not_yet_available'))
    for key in ('baseline_id', 'fold_id', 'budget_condition', 'repo', 'commit'):
        if record[key] is None:
            record['missing_reasons'].setdefault(key, 'not_applicable' if record['run_kind'] == 'smoke' else 'not_checked')
    for key, value in record['env'].items():
        if value is None:
            record['missing_reasons'].setdefault('env.' + key, 'not_applicable' if record['run_kind'] == 'smoke' else 'not_checked')
    validate(record)
    with contextlib.nullcontext() if dry_run else lock(bundle / '.logger.lock'):
        scan_bundle(bundle)
        require(sha(experiment_path) == experiment_sha, 'experiment changed during start')
        verify_files(experiment, bundle, admitting=True)
        if 'final_test' in record.get('eval_sets', ()):
            # Charge access before publishing the start. An interrupted publication can
            # overcount conservatively, but cannot release an unaccounted test access.
            record_run_exposure(root_path(), record, dry_run=dry_run)
        if not dry_run:
            write(report_path, report)
            write(bundle / 'records' / uid / '0.json', record)
    return {'status': 'pass', 'dry_run': dry_run, 'run_id': uid,
            'record': f'records/{uid}/0.json'}


def update(bundle, previous_path, updates_path, *, dry_run=False):
    bundle = Path(bundle).resolve()
    previous_path, updates_path = Path(previous_path).resolve(), Path(updates_path).resolve()
    require(previous_path.is_relative_to(bundle) and updates_path.is_relative_to(bundle), 'paths outside bundle')
    with_context = lock(bundle / '.logger.lock') if not dry_run else contextlib.nullcontext()
    with with_context:
        scan_bundle(bundle)
        previous, updates = read(previous_path), read(updates_path)
        # Validate the entire history, not just a caller-supplied previous object.
        parent, parent_sha = None, None
        for i in range(previous['revision'] + 1):
            p = bundle / 'records' / previous['run_id'] / f'{i}.json'
            item = read(p)
            validate(item, parent, parent_sha)
            parent, parent_sha = item, sha(p)
        require(parent == previous and parent_sha == sha(previous_path), 'previous not in canonical chain')
        record = copy.deepcopy(previous)
        record.update(updates)
        record.update(revision=previous['revision'] + 1, parent_record_sha256=parent_sha, recorded_at=now())
        if record['gpu_hours'] is not None:
            record['missing_reasons'].pop('gpu_hours', None)
        validate(record, previous, parent_sha)
        verify_files(record, bundle, admitting=True)
        if 'final_test' in record.get('eval_sets', ()):
            record_run_exposure(root_path(), record, dry_run=dry_run)
        path = bundle / 'records' / record['run_id'] / f'{record["revision"]}.json'
        if not dry_run:
            write(path, record)
        return {'status': 'pass', 'dry_run': dry_run, 'record': str(path.relative_to(bundle))}


def read_experiment(bundle, path):
    path = Path(path)
    scan_bytes(b'', name=str(path))
    require(path.resolve().is_relative_to(Path(bundle).resolve()), 'experiment must be inside bundle')
    # A malformed JSON key or path may itself contain a credential. Scan before parsing.
    blob = path.read_bytes()
    scan_bytes(blob, name=path.name)
    return decode_json(blob), hashlib.sha256(blob).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['preflight', 'start', 'progress', 'finish'])
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--experiment', type=Path)
    p.add_argument('--previous', type=Path)
    p.add_argument('--updates', type=Path)
    p.add_argument('--started-at', help='preflight only: proposed UTC/Z run start')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    require(a.started_at is None or a.command == 'preflight', '--started-at is for preflight only')
    if a.command in {'preflight', 'start'}:
        require(a.experiment is not None, '--experiment required')
        if a.command == 'preflight':
            return preflight(a.bundle, a.experiment, started_at=a.started_at)
        return start(a.bundle, a.experiment, dry_run=a.dry_run)
    require(a.previous is not None and a.updates is not None, '--previous and --updates required')
    return update(a.bundle, a.previous, a.updates, dry_run=a.dry_run)


if __name__ == '__main__':
    sys.exit(cli(main))
