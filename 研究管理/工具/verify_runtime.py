"""Run isolated CLI, fault-window and concurrency checks; retain all evidence."""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

from common import cli, now, read, ref, require, sha, write
from run_contract import ENV_FIELDS, formal_record
from sync_runs import committed_records, ingest, recover


class Suite:
    def __init__(self, output):
        self.output = output
        self.tools = Path(__file__).resolve().parent
        self.commands = []

    def command(self, name, *args, success=True):
        cmd = [sys.executable, '-B', str(self.tools / name), *map(str, args)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.commands.append({'command': cmd, 'returncode': proc.returncode,
                              'stdout': proc.stdout, 'stderr': proc.stderr})
        require((proc.returncode == 0) == success, f'unexpected CLI exit: {name}: {proc.stdout} {proc.stderr}')
        return json.loads(proc.stdout)

    def bundle(self, name, problem='fixture-one'):
        b = self.output / self.case / name
        write(b / 'data/labels.csv', b'measurement_id,value\nM1,0.0\nM2,1.0\n')
        write(b / 'data.json', {'dataset_id': 'D-fixture', 'problem_id': problem,
                              'files': [ref(b / 'data/labels.csv', b)]})
        write(b / 'split.json', {'split_id': 'S-fixture', 'dataset_manifest_sha256': sha(b / 'data.json'),
                               'fixture_only': True})
        write(b / 'config.json', {'method_id': 'constant-fixture', 'seed': 0})
        write(b / 'protocol.json', {'problem_id': problem, 'protocol_id': 'FIXTURE', 'version': '1',
                                   'hypotheses': [{'hypothesis_id': 'H1', 'hypothesis_version': 'fixture-1'}],
                                   'dataset_ref': dict(ref(b / 'data.json', b), dataset_id='D-fixture'),
                                   'split_ref': dict(ref(b / 'split.json', b), split_id='S-fixture'),
                                   'methods': [{'method_id': 'constant-fixture', 'group_ids': ['G1']}],
                                   'fixture_only': True})
        env = dict.fromkeys(ENV_FIELDS)
        env.update(python='.'.join(map(str, sys.version_info[:3])), framework='stdlib',
                   framework_version='stdlib', gpu_count=0, determinism_settings={'seed': 0})
        exp = {'schema_version': 'run.v0.2', 'problem_id': problem, 'run_kind': 'smoke',
               'hypothesis_refs': [{'hypothesis_id': 'H1', 'hypothesis_version': 'fixture-1'}],
               'protocol_ref': dict(ref(b / 'protocol.json', b), protocol_id='FIXTURE', version='1'),
               'dataset_ref': dict(ref(b / 'data.json', b), dataset_id='D-fixture'),
               'split_ref': dict(ref(b / 'split.json', b), split_id='S-fixture'), 'config_ref': ref(b / 'config.json', b),
               'repo': None, 'commit': None, 'dirty': False, 'method_id': 'constant-fixture',
               'group_id': 'G1', 'baseline_id': None, 'control_ids': [], 'seed': 0, 'fold_id': 'F0',
               'budget_condition': 'fixture-only', 'env': env, 'platform': 'local_fixture',
               'resumed_from': None, 'resume_checkpoint_ref': None}
        write(b / 'experiment.json', exp)
        return b

    def start(self, b):
        value = self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'experiment.json')
        return b / value['record']

    def update(self, b, prev, updates, name='updates.json', success=True, dry=False):
        write(b / name, updates)
        args = ['finish' if updates.get('status') in {'done', 'failed', 'interrupted'} else 'progress',
                '--bundle', b, '--previous', prev, '--updates', b / name]
        if dry:
            args.append('--dry-run')
        value = self.command('run_logger.py', *args, success=success)
        return b / value['record'] if success else value

    def finish(self, b, previous):
        uid = read(previous)['run_id']
        write(b / 'metrics.csv', b'metric_id,endpoint,split_id,fold_id,value,unit,n_measurements,n_compounds,n_groups,aggregation\nMAE,fixture,S-fixture,F0,0.0,arbitrary,2,2,2,mean\n')
        write(b / 'predictions.csv', ('prediction_id,run_id,fold_id,measurement_ids,compound_id,construct_ids,group_id,role,endpoint,prediction,target_ref,target_status\n'
                                    f'P1,{uid},F0,["M1"],C1,["T1"],S1,heldout_eval,fixture,0.0,M1,point\n').encode())
        return self.update(b, previous, {'status': 'done', 'revision_kind': 'finish', 'ended_at': now(),
                                        'metrics': ref(b / 'metrics.csv', b),
                                        'predictions': ref(b / 'predictions.csv', b),
                                        'gpu_hours': 0.0, 'resource_accounting_status': 'measured'})

    def sync(self, b, store, record=None, success=True, dry=False):
        args = ['--input', b, '--store', store]
        if record:
            args += ['--record', record]
        if dry:
            args.append('--dry-run')
        return self.command('sync_runs.py', *args, success=success)

    def expect_error(self, func, fragment):
        try:
            func()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            require(fragment in str(exc), f'wrong failure: {exc}')
        else:
            raise ValueError('expected rejection did not occur: ' + fragment)

    def inventory(self, path):
        if not path.exists():
            return {}
        return {str(p.relative_to(path)): sha(p) if p.is_file() else 'dir'
                for p in path.rglob('*')}

    def case_lifecycle_idempotence(self):
        b = self.bundle('bundle'); store = b.parent / 'store'
        r0 = self.start(b)
        self.sync(b, store)
        r1 = self.update(b, r0, {'revision_kind': 'progress', 'gpu_hours': 0.0,
                                'resource_accounting_status': 'measured'}, name='progress.json')
        self.finish(b, r1)
        self.sync(b, store)
        before = self.inventory(store)
        result = self.sync(b, store)
        require(all(x['action'] == 'noop' for x in result['actions']), 'duplicate was accepted again')
        require(before == self.inventory(store), 'idempotent sync changed store')
        self.sync(b, store, r0)
        rows = [json.loads(x) for x in (store / 'runs.jsonl').read_text().splitlines()]
        require(len(rows) == 1 and rows[0]['revision'] == 2 and rows[0]['status'] == 'done', 'index rolled back')
        require(len((store / '同步日志.jsonl').read_text().splitlines()) == 3, 'duplicate transaction log')

    def case_conflict_and_gap(self):
        b = self.bundle('bundle'); store = b.parent / 'store'
        r0 = self.start(b); self.sync(b, store)
        conflict = read(r0); conflict['note'] = 'changed same revision'
        write(b / 'conflict.json', conflict)
        before = self.inventory(store)
        self.sync(b, store, b / 'conflict.json', success=False)
        require(before == self.inventory(store), 'conflict altered committed store')
        r1 = self.update(b, r0, {'revision_kind': 'progress'}, name='progress.json')
        self.sync(b, b.parent / 'missing-parent', r1, success=False)
        require(not (b.parent / 'missing-parent/runs.jsonl').exists(), 'gap published an index')

    def case_identity_and_types(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        mutations = [('seed', 1), ('seed', False), ('commit', 'a' * 40), ('dirty', 'false'),
                     ('config_ref', ref(b / 'data.json', b)), ('run_kind', 'exploratory'),
                     ('dataset_ref', ref(b / 'split.json', b)), ('problem_id', 'other')]
        for i, (key, value) in enumerate(mutations):
            self.update(b, r0, {'revision_kind': 'progress', key: value}, name=f'bad-{i}.json', success=False)
        require(len(list((b / 'records').glob('*/*.json'))) == 1, 'invalid revision persisted')

    def case_running_failure_and_nulls(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        require(read(r0)['failure_reason'] is None, 'running has false failure')
        self.update(b, r0, {'status': 'failed', 'revision_kind': 'finish', 'ended_at': now()},
                    name='no-reason.json', success=False)
        r1 = self.update(b, r0, {'status': 'failed', 'revision_kind': 'finish', 'ended_at': now(),
                                'failure_reason': 'fixture process error'})
        self.sync(b, b.parent / 'store')
        require(read(r1)['metrics'] is None, 'failed run fabricated metric')

    def case_resource_and_result_types(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        r1 = self.update(b, r0, {'revision_kind': 'progress', 'gpu_hours': 1.0,
                                'resource_accounting_status': 'measured'}, name='resource.json')
        for i, hours in enumerate([None, -1, 0.5]):
            self.update(b, r1, {'revision_kind': 'progress', 'gpu_hours': hours,
                                'resource_accounting_status': 'unknown' if hours is None else 'measured',
                                'missing_reasons': dict(read(r1)['missing_reasons'], gpu_hours='not_checked')},
                        name=f'clear-resource-{i}.json', success=False)
        write(b / 'invalid-metrics.csv', b'metric_id,endpoint,split_id,fold_id,value,unit,n_measurements,n_compounds,n_groups,aggregation\nMAE,fixture,S-fixture,F0,nan,arbitrary,2,2,2,mean\n')
        write(b / 'invalid-predictions.csv', b'wrong_column\n1\n')
        self.update(b, r1, {'status': 'done', 'revision_kind': 'finish', 'ended_at': now(),
                            'metrics': ref(b / 'invalid-metrics.csv', b),
                            'predictions': ref(b / 'invalid-predictions.csv', b)},
                    name='invalid-done.json', success=False)

    def case_terminal_and_correction(self):
        b = self.bundle('bundle'); r0 = self.start(b); final = self.finish(b, r0)
        self.update(b, final, {'status': 'running', 'revision_kind': 'progress', 'ended_at': None},
                    name='reopen.json', success=False)
        write(b / 'correction-evidence.txt', b'fixture clarification')
        patch = {'revision_kind': 'metadata_correction', 'actor': 'coordinator',
                 'change_reason': 'typo', 'evidence_refs': [ref(b / 'correction-evidence.txt', b)], 'note': 'clarified'}
        r2 = self.update(b, final, patch, name='correction.json')
        require(read(r2)['status'] == 'done', 'correction changed terminal state')
        patch['metrics'] = ref(b / 'config.json', b)
        self.update(b, r2, patch, name='metric-rewrite.json', success=False)

    def case_interruption_resume(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        patch = {'status': 'interrupted', 'revision_kind': 'interrupt', 'ended_at': now(),
                 'failure_reason': 'fixture loss of worker', 'actor': 'coordinator'}
        self.update(b, r0, patch, name='no-evidence.json', success=False)
        write(b / 'scheduler.log', b'fixture worker exited')
        write(b / 'checkpoint.bin', b'fixture-checkpoint-1')
        patch.update(change_reason='scheduler confirmed loss', evidence_refs=[ref(b / 'scheduler.log', b)],
                     artifacts=[dict(ref(b / 'checkpoint.bin', b), kind='checkpoint', availability='local')])
        self.update(b, r0, patch)
        store = b.parent / 'store'; self.sync(b, store)
        restored = store / '运行' / read(r0)['run_id'] / 'files/checkpoint.bin'
        require(restored.read_bytes() == b'fixture-checkpoint-1', 'checkpoint round-trip differed')
        exp = read(b / 'experiment.json')
        exp.update(resumed_from=read(r0)['run_id'], resume_checkpoint_ref=ref(b / 'checkpoint.bin', b))
        write(b / 'resume.json', exp)
        result = self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'resume.json')
        require(result['run_id'] != read(r0)['run_id'], 'resume reused run id')
        self.sync(b, store)
        require(len((store / 'runs.jsonl').read_text().splitlines()) == 2, 'resume erased original')

    def case_actual_file_tamper(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        label = b / 'data/labels.csv'; stat = label.stat()
        write(label, b'measurement_id,value\nM1,9.0\nM2,1.0\n', replace=True)
        os.utime(label, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.command('run_logger.py', 'preflight', '--bundle', b, '--experiment', b / 'experiment.json', success=False)
        self.sync(b, b.parent / 'store', r0, success=False)

    def case_path_escape(self):
        b = self.bundle('bundle'); r0 = self.start(b)
        outside = b.parent / 'outside.json'; write(outside, {'fixture': True})
        record = read(r0)
        record['config_ref'] = {'path': '../outside.json', 'sha256': sha(outside)}
        write(b / 'traversal.json', record)
        self.sync(b, b.parent / 'store1', b / 'traversal.json', success=False)
        (b / 'escape.json').symlink_to(outside)
        record['config_ref']['path'] = 'escape.json'; write(b / 'symlink.json', record)
        self.sync(b, b.parent / 'store2', b / 'symlink.json', success=False)

    def case_dry_run_no_write(self):
        b = self.bundle('bundle'); before = self.inventory(b)
        self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'experiment.json', '--dry-run')
        require(before == self.inventory(b), 'start dry-run wrote files')
        r0 = self.start(b); store = b.parent / 'absent-store'
        self.sync(b, store, dry=True); require(not store.exists(), 'sync dry-run made store/lock')
        write(b / 'progress.json', {'revision_kind': 'progress'})
        before = self.inventory(b)
        self.command('run_logger.py', 'progress', '--bundle', b, '--previous', r0,
                     '--updates', b / 'progress.json', '--dry-run')
        require(before == self.inventory(b), 'progress dry-run wrote files')

    def case_recovery_windows(self):
        for stage in ('prepared', 'files', 'committed'):
            b = self.bundle(stage); self.start(b); store = b.parent / ('store-' + stage)
            script = 'import sys;sys.path.insert(0,sys.argv[1]);from sync_runs import ingest;ingest(sys.argv[2],sys.argv[3],fault=sys.argv[4])'
            cmd = [sys.executable, '-B', '-c', script, str(self.tools), str(b), str(store), stage]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            self.commands.append({'command': cmd, 'returncode': proc.returncode, 'stderr': proc.stderr})
            require(proc.returncode != 0 and 'injected crash' in proc.stderr, 'fault not injected')
            require(not (store / 'runs.jsonl').exists(), 'index preceded committed publication')
            result = recover(store)
            if stage == 'prepared':
                require(result['status'] == 'fail' and result['pending_requires_original_bundle'], 'lost input not reported')
                self.sync(b, store)
            else:
                require(result['status'] == 'pass', 'durable transaction did not recover')
            before = self.inventory(store)
            recover(store); self.sync(b, store)
            require(before == self.inventory(store), 'repeat recovery added facts')

    def case_concurrency(self):
        b1 = self.bundle('one'); b2 = self.bundle('two', problem='fixture-two')
        r1 = self.start(b1); r2 = self.start(b2); store = b1.parent / 'store'
        jobs = []
        for b in (b1, b2, b1, b2):
            cmd = [sys.executable, '-B', str(self.tools / 'sync_runs.py'), '--input', str(b), '--store', str(store)]
            jobs.append((cmd, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
        for cmd, proc in jobs:
            out, err = proc.communicate(timeout=20)
            self.commands.append({'command': cmd, 'returncode': proc.returncode, 'stdout': out, 'stderr': err})
            require(proc.returncode == 0, 'parallel ingestion failed: ' + err + out)
        rows, histories = committed_records(store)
        require(len(rows) == len(histories) == 2, 'concurrent runs overwritten')
        require({x['problem_id'] for x in rows.values()} == {'fixture-one', 'fixture-two'}, 'H1 lost problem scope')
        require(len((store / '同步日志.jsonl').read_text().splitlines()) == 2, 'parallel duplicate created facts')
        require(read(r1)['run_id'] != read(r2)['run_id'], 'UUID collision')

    def case_concurrent_same_revision_conflict(self):
        b = self.bundle('bundle'); r0 = self.start(b); store = b.parent / 'store'
        second = read(r0); second['note'] = 'conflicting metadata at same revision'
        write(b / 'other.json', second)
        jobs = []
        for record in (r0, b / 'other.json'):
            cmd = [sys.executable, '-B', str(self.tools / 'sync_runs.py'), '--input', str(b),
                   '--store', str(store), '--record', str(record)]
            jobs.append((cmd, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
        codes = []
        for cmd, proc in jobs:
            out, err = proc.communicate(timeout=20); codes.append(proc.returncode)
            self.commands.append({'command': cmd, 'returncode': proc.returncode, 'stdout': out, 'stderr': err})
        require(sorted(codes) == [0, 1], 'parallel same-ID conflict not rejected')
        require(len(committed_records(store)[0]) == 1, 'conflict made duplicate rows')

    def case_eligibility_semantics(self):
        from build_eligibility import NEEDED, classify, generate, verify_view
        b = self.output / self.case / 'bundle'
        rows = []
        cases = [('point_estimate', 'KINOMEscan_Kd', 'True', 'True', '8.0', '', 'pending'),
                 ('upper_bound_pkd', 'ITC_Kd', 'False', 'False', '', '4.0', 'exclude'),
                 ('downstream_imputed', 'KINOMEscan_Kd_or_imputed', 'False', 'False', '', '', 'exclude'),
                 ('unverified_meaning', 'KINOMEscan_KdELECT', 'False', 'False', '', '', 'exclude'),
                 ('point_estimate', 'AlphaLISA_pIC50', 'False', 'False', '', '', 'exclude'),
                 ('point_estimate_high_kd_tail', 'KINOMEscan_Kd', 'True', 'False', '4.0', '', 'exclude')]
        for i, (status, endpoint, verified, strict, value, upper, decision) in enumerate(cases):
            row = {k: '' for k in NEEDED}
            row.update(measurement_id='M' + str(i), value_status=status, measurement_type=endpoint,
                       is_verified_kd_point=verified, is_strict_clean_kd_point=strict,
                       pkd_point=value, pkd_upper=upper, lower_strict='False', upper_strict='False')
            if verified == 'True':
                row.update(pkd_lower=value, pkd_upper=value)
            require(classify(row, 'point_training')[0] == decision, 'endpoint decision wrong')
            require(classify(row, 'diagnostic')[0] == 'include', 'diagnostic provenance lost')
            rows.append(row)
        bad = dict(rows[3], is_verified_kd_point='True', pkd_point='9.0')
        self.expect_error(lambda: classify(bad, 'point_training'), 'promoted to verified Kd')
        bad_bound = dict(rows[0], upper_strict='True')
        self.expect_error(lambda: classify(bad_bound, 'point_training'), 'equal closed interval')
        buf = io.StringIO(); writer = csv.DictWriter(buf, fieldnames=sorted(NEEDED), lineterminator='\n')
        writer.writeheader(); writer.writerows(rows); write(b / 'source.csv', buf.getvalue().encode())
        write(b / 'design.json', {'status': 'fixture draft'})
        rules = {'schema_version': 'eligibility-preview.v1', 'mode': 'diagnostic_only',
                 'formal_use_allowed': False, 'problem_id': 'fixture', 'eligibility_id': 'E-fixture',
                 'intended_uses': ['diagnostic', 'point_training'], 'design_ref': ref(b / 'design.json', b),
                 'source_snapshot_refs': [ref(b / 'source.csv', b)], 'measurements_ref': ref(b / 'source.csv', b)}
        # Use a workspace root containing the tool for the generator's provenance reference.
        root = self.tools.parents[1]
        rules['design_ref'] = ref(b / 'design.json', root)
        rules['source_snapshot_refs'] = [ref(b / 'source.csv', root)]
        rules['measurements_ref'] = ref(b / 'source.csv', root)
        write(b / 'rules.json', rules)
        before = self.inventory(b)
        preview = generate(root, b / 'rules.json', b / 'view', dry_run=True)
        require(before == self.inventory(b), 'eligibility dry-run wrote files')
        require(preview['counts'] == {'diagnostic:include': 6, 'point_training:pending': 1,
                                      'point_training:exclude': 5}, 'preview counts differ')
        generate(root, b / 'rules.json', b / 'view')
        verify_view(root, b / 'view/manifest.json')
        rules['formal_use_allowed'] = True; write(b / 'formal-rules.json', rules)
        self.expect_error(lambda: generate(root, b / 'formal-rules.json', b / 'formal', dry_run=True),
                          'formal admission is not supported')

    def case_rebuild_tamper(self):
        b = self.bundle('bundle'); r0 = self.start(b); store = b.parent / 'store'; self.sync(b, store)
        original = (store / 'runs.jsonl').read_bytes()
        write(store / 'runs.jsonl', b'broken derived cache', replace=True)
        recover(store)
        require((store / 'runs.jsonl').read_bytes() == original, 'index rebuild failed')
        durable = store / '运行' / read(r0)['run_id'] / 'files/data/labels.csv'
        write(durable, b'corrupt local copy', replace=True)
        self.expect_error(lambda: recover(store), 'hash mismatch')

    def case_formal_admission_closed(self):
        b = self.bundle('bundle'); r0 = self.start(b); done = self.finish(b, r0)
        self.expect_error(lambda: formal_record(read(done)), 'not a clean completed confirmatory')
        exp = read(b / 'experiment.json'); exp['run_kind'] = 'confirmatory'
        write(b / 'confirmatory.json', exp)
        self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'confirmatory.json', success=False)

    def case_json_and_binding(self):
        b = self.bundle('bundle')
        text = (b / 'experiment.json').read_text()
        write(b / 'nan.json', text.replace('"seed": 0', '"seed": NaN', 1).encode())
        self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'nan.json', success=False)
        write(b / 'duplicate.json', b'{"seed":0,"seed":1}')
        self.expect_error(lambda: read(b / 'duplicate.json'), 'duplicate JSON key')
        exp = read(b / 'experiment.json'); exp['seed'] = 1; write(b / 'mismatch.json', exp)
        self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / 'mismatch.json', success=False)

    def case_foreign_keys(self):
        b = self.bundle('bundle')
        for name, key, value in [('hypothesis', 'hypothesis_refs', [{'hypothesis_id': 'H1', 'hypothesis_version': 'unknown'}]),
                                 ('group', 'group_id', 'UNKNOWN')]:
            exp = read(b / 'experiment.json'); exp[key] = value; write(b / (name + '.json'), exp)
            self.command('run_logger.py', 'start', '--bundle', b, '--experiment', b / (name + '.json'), success=False)


def case_names():
    return sorted(n for n in dir(Suite) if n.startswith('case_'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    require(not a.output.exists(), 'use a new output directory; old evidence is retained')
    if a.dry_run:
        return {'status': 'pass', 'writes': False, 'cases': case_names()}
    output = a.output.resolve(); suite = Suite(output); cases = []
    for name in case_names():
        suite.case = name
        try:
            getattr(suite, name)()
            cases.append({'case': name, 'status': 'pass'})
        except Exception:
            cases.append({'case': name, 'status': 'fail', 'error': traceback.format_exc()})
    def _scrub(val):
        if isinstance(val, str):
            return (val.replace(str(output), '<output>')
                       .replace(str(suite.tools.parents[1]), '<repo>')
                       .replace(sys.executable, 'python3'))
        if isinstance(val, list):
            return [_scrub(x) for x in val]
        if isinstance(val, dict):
            return {k: _scrub(v) for k, v in val.items()}
        return val
    write(output / 'commands.json', _scrub(suite.commands))
    report = {'schema_version': 'runtime-evidence.v1', 'checked_at': now(),
              'status': 'pass' if all(c['status'] == 'pass' for c in cases) else 'fail',
              'python': sys.version, 'platform': sys.platform, 'cases': cases,
              'scope': 'POSIX localhost fixtures; no real training, target GPU or scientific certification',
              'code_refs': [ref(p, suite.tools.parent) for p in
                            [*sorted(suite.tools.glob('*.py')), suite.tools.parent/'规范/词表.json']],
              'evidence_files': [ref(p, output) for p in sorted(output.rglob('*')) if p.is_file() and not p.is_symlink()]}
    write(output / 'report.json', report)
    return {'status': report['status'], 'passed': sum(x['status'] == 'pass' for x in cases),
            'total': len(cases), 'report': str(output / 'report.json'),
            'failed': [x['case'] for x in cases if x['status'] != 'pass']}


if __name__ == '__main__':
    sys.exit(cli(main))
