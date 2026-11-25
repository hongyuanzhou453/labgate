"""Immutable scientific identity and lifecycle validation for v0.2 records."""
from __future__ import annotations

import csv
import json
import re

from common import closure, finite_number, read, require, run_id, safe_id, timestamp, under
from vocabulary import choices

IDENTITY = (
    'schema_version', 'run_id', 'problem_id', 'run_kind', 'hypothesis_refs', 'protocol_ref',
    'dataset_ref', 'split_ref', 'config_ref', 'repo', 'commit', 'dirty', 'method_id',
    'group_id', 'baseline_id', 'control_ids', 'seed', 'fold_id', 'budget_condition',
    'env', 'platform', 'started_at', 'resumed_from', 'resume_checkpoint_ref',
    'preflight_report_ref',
)
FIELDS = set(IDENTITY) | {
    'revision', 'parent_record_sha256', 'revision_kind', 'actor', 'change_reason',
    'evidence_refs', 'recorded_at', 'ended_at', 'last_heartbeat_at', 'status',
    'gpu_hours', 'resource_accounting_status', 'failure_reason', 'metrics', 'predictions',
    'artifacts', 'prediction_freeze_ref', 'note', 'missing_reasons',
}
ENV_FIELDS = {'lockfile_sha256', 'resolved_env_sha256', 'python', 'framework',
              'framework_version', 'cuda', 'driver', 'gpu_model', 'gpu_count',
              'determinism_settings'}


def eval_sets(record):
    """An absent v0.2 field means undeclared, never evidence of no test access."""
    if 'eval_sets' not in record:
        return None
    roles = record['eval_sets']
    allowed = choices('eval_set_kind')
    require(isinstance(roles, list) and roles and
            all(isinstance(role, str) and role in allowed for role in roles) and
            len(roles) == len(set(roles)), 'eval_sets must list distinct supported evaluation sets')
    return roles


def preflight_identity(record):
    identity = {k: record.get(k) for k in IDENTITY
                if k not in {'schema_version', 'run_id', 'started_at', 'preflight_report_ref'}}
    if 'eval_sets' in record:
        identity['eval_sets'] = record['eval_sets']
    return identity


def file_reference(value):
    require(isinstance(value, dict) and isinstance(value.get('path'), str), 'file path required')
    require(isinstance(value.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'complete SHA-256 required')
    if 'bytes' in value:
        require(type(value['bytes']) is int and value['bytes'] >= 0, 'invalid byte count')


def validate(record, previous=None, previous_sha=None):
    require(isinstance(record, dict), 'record object required')
    require(set(record) - {'eval_sets'} == FIELDS, 'required or unexpected run record fields')
    eval_sets(record)
    require(record['schema_version'] == 'run.v0.2', 'unsupported run schema')
    run_id(record['run_id'])
    for key in ('problem_id', 'method_id', 'group_id'):
        safe_id(record[key])
    require(record['run_kind'] in choices('run_kind'), 'bad run kind')
    require(type(record['revision']) is int and record['revision'] >= 0, 'invalid revision')
    require(type(record['seed']) is int, 'integer seed required')
    require(type(record['dirty']) is bool, 'boolean dirty required')
    require(record['platform'] in choices('platform'), 'bad platform')
    require(record['platform'] != 'local_fixture' or record['run_kind'] == 'smoke',
            'local_fixture only allows smoke')
    require(isinstance(record['hypothesis_refs'], list) and isinstance(record['control_ids'], list),
            'reference lists required')
    for h in record['hypothesis_refs']:
        require(isinstance(h, dict) and h.get('hypothesis_id') and h.get('hypothesis_version'),
                'hypothesis identity/version missing')
    require(isinstance(record['env'], dict) and isinstance(record['missing_reasons'], dict),
            'env and missing_reasons objects required')
    require(ENV_FIELDS <= set(record['env']), 'environment fields incomplete')
    require(isinstance(record['env']['python'], str) and record['env']['python'], 'Python version required')
    require(isinstance(record['env']['determinism_settings'], dict), 'determinism settings object required')
    for key in ('lockfile_sha256', 'resolved_env_sha256'):
        value = record['env'][key]
        require(value is None or (isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)),
                'invalid environment hash')
    require(record['env']['gpu_count'] is None or
            (type(record['env']['gpu_count']) is int and record['env']['gpu_count'] >= 0), 'bad gpu count')
    missing = choices('missing_reasons')
    for key, reason in record['missing_reasons'].items():
        require(isinstance(key, str) and reason in missing, 'invalid missing reason')
    for key in ('baseline_id', 'fold_id', 'budget_condition', 'repo', 'commit'):
        value = record[key]
        require(value is None or (isinstance(value, str) and value.strip()), f'bad {key}')
        if value is None:
            require(key in record['missing_reasons'], f'{key} needs missing reason')
    for key, value in record['env'].items():
        if value is None:
            require('env.' + key in record['missing_reasons'], f'env.{key} needs missing reason')
    for value in record['control_ids']:
        safe_id(value)
    require(len(record['control_ids']) == len(set(record['control_ids'])), 'duplicate controls')
    require(isinstance(record['artifacts'], list) and isinstance(record['evidence_refs'], list),
            'artifacts/evidence lists required')
    require(record['actor'] in choices('actor'), 'unrecognized actor')
    require(isinstance(record['note'], str), 'note string required')
    start, current = timestamp(record['started_at']), timestamp(record['recorded_at'])
    require(current >= start, 'record predates run')
    if record['last_heartbeat_at'] is not None:
        require(start <= timestamp(record['last_heartbeat_at']) <= current, 'invalid heartbeat time')
    finite_number(record['gpu_hours'], optional=True)
    require(record['gpu_hours'] is None or record['gpu_hours'] >= 0, 'negative gpu hours')
    require(record['resource_accounting_status'] in choices('resource_accounting_status'),
            'bad resource accounting status')
    for key in ('protocol_ref', 'dataset_ref', 'split_ref', 'config_ref', 'preflight_report_ref'):
        file_reference(record[key])
    for key, identifier in [('protocol_ref', 'protocol_id'), ('dataset_ref', 'dataset_id'), ('split_ref', 'split_id')]:
        safe_id(record[key].get(identifier))
    require(isinstance(record['protocol_ref'].get('version'), str) and record['protocol_ref']['version'],
            'protocol version required')
    for key in ('metrics', 'predictions', 'resume_checkpoint_ref', 'prediction_freeze_ref'):
        if record[key] is not None:
            file_reference(record[key])
    for value in record['evidence_refs'] + record['artifacts']:
        file_reference(value)
    if record['gpu_hours'] is None:
        require('gpu_hours' in record['missing_reasons'] and
                record['resource_accounting_status'] in {'unknown', 'not_applicable'},
                'missing resource count requires reason and compatible status')
    else:
        require(record['resource_accounting_status'] in {'measured', 'estimated'},
                'numeric resource count requires measured or estimated status')
    status = record['status']
    require(status in choices('status'), 'bad status')
    if status == 'running':
        require(record['ended_at'] is None and record['failure_reason'] is None, 'running cannot have terminal fields')
    else:
        require(start <= timestamp(record['ended_at']) <= current, 'invalid terminal time')
        if status in {'failed', 'interrupted'}:
            require(isinstance(record['failure_reason'], str) and record['failure_reason'].strip(), 'failure reason required')
        else:
            require(record['failure_reason'] is None, 'done cannot carry failure reason')
            require(record['metrics'] is not None and record['predictions'] is not None,
                    'done requires metrics and predictions')
    if record['resumed_from'] is not None:
        run_id(record['resumed_from'])
        require(record['resumed_from'] != record['run_id'] and record['resume_checkpoint_ref'], 'invalid resume lineage')
    else:
        require(record['resume_checkpoint_ref'] is None, 'checkpoint without resume parent')
    if status == 'interrupted':
        require(record['change_reason'] and record['evidence_refs'], 'interruption needs explicit evidence')
    if record['run_kind'] == 'confirmatory':
        require(not record['dirty'] and record['hypothesis_refs'], 'confirmatory requires clean code and hypotheses')
        file_reference({'path': record['protocol_ref'].get('freeze_path'),
                        'sha256': record['protocol_ref'].get('freeze_sha256')})
        require(record['commit'] and re.fullmatch('[0-9a-f]{40,64}', record['commit']), 'full commit required')
    if previous is None:
        require(record['revision'] == 0 and record['parent_record_sha256'] is None,
                'first revision must be zero without parent')
        require(record['revision_kind'] == 'start' and status == 'running', 'first record must start running')
    else:
        require(record['revision'] == previous['revision'] + 1, 'revision gap')
        require(record['parent_record_sha256'] == previous_sha, 'parent hash mismatch')
        for key in IDENTITY:
            require(record[key] == previous[key], f'scientific identity changed: {key}')
        require(record.get('eval_sets') == previous.get('eval_sets'), 'scientific identity changed: eval_sets')
        require(current >= timestamp(previous['recorded_at']), 'record time moved backwards')
        if previous['gpu_hours'] is not None:
            require(record['gpu_hours'] is not None and record['gpu_hours'] >= previous['gpu_hours'],
                    'cumulative resource count decreased or cleared')
        if previous['status'] != 'running':
            require(record['revision_kind'] == 'metadata_correction', 'terminal run cannot reopen')
            changed = {k for k in record if record[k] != previous[k]}
            require(changed <= {'revision', 'parent_record_sha256', 'revision_kind', 'recorded_at',
                                'actor', 'change_reason', 'evidence_refs', 'note'},
                    'terminal scientific contents cannot change')
        else:
            expected = 'progress' if status == 'running' else ('interrupt' if status == 'interrupted' else 'finish')
            require(record['revision_kind'] == expected, 'kind/status mismatch')
    if record['actor'] == 'coordinator' or record['revision_kind'] == 'metadata_correction':
        require(record['change_reason'] and record['evidence_refs'], 'coordinator/correction needs evidence')
    return record


def verify_files(record, base, *, admitting=False):
    extra = []
    protocol = record.get('protocol_ref', {})
    if protocol.get('freeze_path'):
        extra.append({'path': protocol['freeze_path'], 'sha256': protocol.get('freeze_sha256')})
    files = closure([record, extra], base)
    roles = eval_sets(record)
    if admitting:
        require(record['run_kind'] != 'confirmatory',
                'confirmatory execution not enabled: G3 scientific approvals and validators required')
        if roles is None:
            design = read(under(base, protocol['path']))
            split = read(under(base, record['split_ref']['path']))
            require(design.get('fixture_only') is True and split.get('fixture_only') is True
                    and 'schema_version' not in design and record['run_kind'] in {'smoke', 'exploratory'},
                    'eval_sets required for new admissions outside legacy v0.2 fixtures')
        config = read(under(base, record['config_ref']['path']))
        if 'eval_sets' in config:
            require(roles is not None and set(eval_sets(config)) == set(roles),
                    'config eval_sets differs from declared access')
    if record.get('run_id'):
        report = read(under(base, record['preflight_report_ref']['path']))
        require(report.get('schema_version') == 'preflight.v1' and report.get('status') == 'pass',
                'invalid preflight report')
        require(report.get('scientific_identity') == preflight_identity(record), 'record differs from preflight identity')
        require(timestamp(report['checked_at']) <= timestamp(record['started_at']), 'preflight after start')
        check_roles = admitting or roles is not None
        if record['status'] == 'done':
            validate_results(record, base, check_roles=check_roles)
        elif check_roles and record.get('predictions'):
            path = under(base, record['predictions']['path'])
            with path.open(encoding='utf-8-sig', newline='') as stream:
                rows = csv.DictReader(stream, delimiter='\t' if path.suffix.lower() == '.tsv' else ',')
                for row in rows:
                    check_prediction_role(row.get('role'), roles)
    return files


def check_prediction_role(role, declared):
    # Old v0.2 synthetic results used heldout_eval without a test-access declaration.
    if declared is None:
        require(role != 'final_test', 'final_test prediction requires declared final_test access')
        return
    mapped = {'heldout_eval': 'final_test', 'rule_eval': 'dev', 'rule_dev': 'dev'}.get(role, role)
    require(mapped in declared, 'prediction role was not declared in eval_sets')


def validate_results(record, base, *, check_roles=True):
    """Validate result types/identities; scientific metric correctness is a later gate."""
    metrics_fields = {'metric_id', 'endpoint', 'split_id', 'fold_id', 'value', 'unit',
                      'n_measurements', 'n_compounds', 'n_groups', 'aggregation'}
    predictions_fields = {'prediction_id', 'run_id', 'fold_id', 'measurement_ids', 'compound_id',
                          'construct_ids', 'group_id', 'role', 'endpoint', 'prediction', 'target_ref', 'target_status'}
    for key, fields in [('metrics', metrics_fields), ('predictions', predictions_fields)]:
        path = under(base, record[key]['path'])
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f, delimiter='\t' if path.suffix.lower() == '.tsv' else ',')
            require(fields <= set(reader.fieldnames or []), f'{key} columns missing')
            identities = set()
            for row in reader:
                require(all(row.get(k) is not None and row[k].strip() for k in fields), f'{key} empty fields')
                require(row['fold_id'] == record['fold_id'], f'{key} fold differs')
                if key == 'metrics':
                    identity = tuple(row[k] for k in ('metric_id', 'endpoint', 'split_id', 'fold_id'))
                    finite_number(float(row['value']))
                    for count in ('n_measurements', 'n_compounds', 'n_groups'):
                        require(int(row[count]) >= 0, 'negative metric denominator')
                else:
                    if check_roles:
                        check_prediction_role(row['role'], record.get('eval_sets'))
                    identity = row['prediction_id']
                    require(row['run_id'] == record['run_id'], 'prediction run differs')
                    finite_number(float(row['prediction']))
                    for array in ('measurement_ids', 'construct_ids'):
                        values = json.loads(row[array])
                        require(isinstance(values, list) and values and
                                all(isinstance(x, str) and x for x in values), 'prediction entity array invalid')
                require(identity not in identities, f'duplicate {key} identity')
                identities.add(identity)
            require(identities, f'{key} empty output')


def formal_record(record):
    require(record['run_kind'] == 'confirmatory' and record['status'] == 'done' and not record['dirty'],
            'not a clean completed confirmatory run')
    raise ValueError('G3 result admission is not implemented; no formal results can be admitted')
