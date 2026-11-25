"""在两个合成卷宗上通过公开 CLI 验证 W10b、V07 与 V29。"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    home = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() or not out.parent.is_dir() or not out.parent.is_relative_to(home/'_交付'):
        parser.error('请在已有交付轮目录下指定全新的验收子目录')
    if args.dry_run:
        print(json.dumps({'status':'pass', 'writes':False, 'scope':'two synthetic topics'}))
        return 0

    sys.path.insert(0, str(home/'工具'))
    from common import cli, closure, now, read, ref, require, sha, write
    from verify_runtime import Suite

    root = out/'workspace'
    fixture_home = root/'研究管理'
    problems = fixture_home/'问题'
    store = out/'store'
    registered = '2025-01-01T00:00:00Z'
    cases, commands = [], []
    adapter = Suite(out)
    adapter.case = 'bundles'

    def put(path, value):
        write(path, value, replace=True)

    def inventory(path):
        return {str(p.relative_to(path)):sha(p) if p.is_file() else 'dir' for p in path.rglob('*')}

    def command(tool, *argv, ok=True, markers=(), tool_home=None):
        active_home = tool_home or fixture_home
        proc = subprocess.run([sys.executable, '-B', str(active_home/'工具'/tool), *map(str, argv)],
                              cwd=root, capture_output=True, text=True, timeout=30)
        require(all(s not in (proc.stdout + proc.stderr) for s in markers),
                'credential marker leaked in CLI output')
        require(not proc.stderr, f'{tool} emitted a diagnostic outside JSON')
        result = json.loads(proc.stdout)
        commands.append({'tool':tool, 'tool_home':str(active_home.relative_to(out)),
                         'args':list(map(str, argv)), 'exit':proc.returncode,
                         'status':result.get('status'), 'error':result.get('error')})
        require(proc.returncode == (0 if ok else 1) and result.get('status') == ('pass' if ok else 'fail'),
                f'unexpected {tool} outcome at command {len(commands)}: {result.get("error")}')
        return result

    def desk(*argv, **options):
        return command('research.py', *argv, **options)

    def freeze(op, pid='alpha', version='v1', *extra, **options):
        return desk('freeze', op, '--problem', pid, '--protocol', 'P', '--version', version, *extra, **options)

    def prior(pid, *extra, event='observed-before-freeze', **options):
        return desk('exposure', 'record', '--problem', pid, '--protocol', 'P', '--version', 'v1',
                    '--dataset-id', 'D', '--split-id', 'S', '--eval-set', 'final_test', '--event-id', event,
                    '--accessed-at', registered, *extra, **options)

    def events(pid='alpha', *extra):
        return desk('exposure', 'list', '--problem', pid, *extra)['events']

    def expect(label, passed=True):
        require(passed, f'failed: {label}')
        cases.append({'case':label, 'status':'pass', 'through_command':len(commands)})

    def make_topic(pid, versions):
        desk('new', pid, '--title', f'Synthetic topic {pid}')
        folder = problems/pid
        confirmation = folder/'确认/审批.json'
        write(confirmation, {'problem_id':pid, 'fixture_only':True, 'confirmed_by':'fixture reviewer'})
        decision = {'problem_id':pid, 'confirmed_by':'fixture reviewer', 'confirmation_ref':ref(confirmation, root)}
        put(folder/'决策.json', {'schema_version':'decisions.v1','problem_id':pid,'decisions':[decision]})
        labels = folder/'输入/labels.csv'
        write(labels, b'id,label\nexample,0\n')
        data = folder/'输入/manifest.json'
        write(data, {'problem_id':pid, 'dataset_id':'D', 'files':[ref(labels, root)]})
        split = folder/'输入/split.json'
        write(split, {'problem_id':pid, 'split_id':'S', 'dataset_manifest_sha256':sha(data)})
        table = io.StringIO(newline='')
        writer = csv.writer(table, lineterminator='\n')
        writer.writerow(['problem_id','hypothesis_id','hypothesis_version','protocol_id',
                         'protocol_version','statement','registered_at','analysis_plan'])
        for version in versions:
            writer.writerow([pid, 'H1', version, 'P', version, 'Synthetic H1', registered, 'Compare with zero.'])
            write(folder/'协议/P'/version/'design.json', {
                'schema_version':'design-draft.v0.2', 'problem_id':pid, 'protocol_id':'P',
                'version':version, 'status':'draft', 'formal_use_allowed':False,
                'hypotheses':[{'hypothesis_id':'H1','hypothesis_version':version}],
                'methods':[{'method_id':'constant-fixture','group_ids':['G1']}],
                'decisions':{'effect_threshold':0}, 'decision_refs':[decision['confirmation_ref']],
                'dataset_ref':dict(ref(data, root), dataset_id='D'),
                'split_ref':dict(ref(split, root), split_id='S')})
        write(folder/'假设.csv', table.getvalue().encode())

    def bundle(name, pid='alpha', version='v1', roles=('dev', 'final_test')):
        base = adapter.bundle(name, problem=pid)
        proto = problems/pid/'协议/P'/version/'design.json'
        design = read(proto)
        for item in closure(design, root):
            write(base/item['path'], (root/item['path']).read_bytes())
        put(base/'protocol.json', proto.read_bytes())
        exp = read(base/'experiment.json')
        exp.update(protocol_ref=dict(ref(base/'protocol.json', base), protocol_id='P', version=version),
                   hypothesis_refs=design['hypotheses'], dataset_ref=design['dataset_ref'],
                   split_ref=design['split_ref'], eval_sets=list(roles))
        put(base/'experiment.json', exp)
        return base

    def logger(base, op, *extra, exp='experiment.json', **options):
        return command('run_logger.py', op, '--bundle', base, '--experiment', base/exp, *extra, **options)

    def sync(base, target, *extra, **options):
        return command('sync_runs.py', '--input', base, '--store', target, *extra, **options)

    def exercise():
        problems.mkdir(parents=True)
        for path in sorted((home/'工具').glob('*.py')):
            write(fixture_home/'工具'/path.name, path.read_bytes())
        for name in ('AGENTS.md', '规范/词表.json', '规范/阶段要求.json'):
            write(fixture_home/name, (home/name).read_bytes())
        make_topic('alpha', ('v1', 'v2'))
        make_topic('beta', ('v1',))
        alpha, beta = problems/'alpha', problems/'beta'

        before = inventory(root)
        freeze('create', 'alpha', 'v1', '--dry-run')
        prior('alpha', '--dry-run')
        desk('exposure', 'verify', '--problem', 'alpha', '--dry-run')
        expect('freeze and exposure previews create no files, directories or locks', before == inventory(root))
        prior('alpha')
        prior('alpha')
        prior_events = events()
        expect('prior exposure can precede a freeze and retries retain its original input bytes',
               len(prior_events) == 1 and prior_events[0]['dataset_sha256'] == sha(alpha/'输入/manifest.json')
               and prior_events[0]['split_sha256'] == sha(alpha/'输入/split.json')
               and prior_events[0]['protocol_sha256'] == sha(alpha/'协议/P/v1/design.json'))

        first = freeze('create')['freeze']
        original = inventory(alpha)
        expect('freeze rereads identical bindings and repeated create is immutable',
               freeze('verify')['freeze'] == first and freeze('create')['freeze'] == first
               and original == inventory(alpha))

        for rel in ('协议/P/v1/design.json', '输入/manifest.json', '输入/split.json', '输入/labels.csv',
                    '假设.csv', '确认/审批.json', '决策.json'):
            path = alpha/rel
            clean = path.read_bytes()
            put(path, clean+b'\n')
            freeze('verify', ok=False)
            put(path, clean)
            expect(f'changed upstream bytes rejected: {rel}', freeze('verify')['freeze'] == first)

        register = beta/'决策.json'
        local_register = read(register)
        put(register, dict(local_register, decisions=[]))
        freeze('create', 'beta', ok=False)
        expect('missing topic confirmation blocks freeze', not (beta/'协议/P/v1/freeze.json').exists())
        beta_proto = beta/'协议/P/v1/design.json'
        local_proto = read(beta_proto)
        foreign = read(alpha/'决策.json')['decisions'][0]['confirmation_ref']
        # Both pointers agree on the foreign file and its genuine hash.
        put(beta_proto, dict(local_proto, decision_refs=[foreign]))
        put(register, dict(local_register, decisions=[dict(local_register['decisions'][0], confirmation_ref=foreign)]))
        freeze('create', 'beta', ok=False)
        expect('matching foreign confirmation refs cannot authorize a second topic')
        put(beta_proto, dict(local_proto, threshold_registered_at='2099-01-01T00:00:00Z'))
        put(register, local_register)
        freeze('create', 'beta', ok=False)
        expect('independently registered threshold cannot postdate freezing')
        put(beta_proto, local_proto)
        hypothesis = beta/'假设.csv'
        old = hypothesis.read_bytes()
        put(hypothesis, old.replace(registered.encode(), b'2099-01-01T00:00:00Z'))
        freeze('create', 'beta', ok=False)
        expect('registration after freezing rejected')
        put(hypothesis, old)
        b = bundle('beta', pid='beta')
        logger(b, 'preflight', ok=False)
        freeze('create', 'beta')
        expect('same protocol and dataset IDs on another topic require its own freeze',
               logger(b, 'preflight')['status'] == 'pass' and not events('beta'))

        base = bundle('alpha-v1')
        experiment = read(base/'experiment.json')
        for label, moment in [('before', registered), ('equal', first['frozen_at'])]:
            put(base/'planned.json', dict(experiment, started_at=moment))
            rejection = logger(base, 'preflight', exp='planned.json', ok=False)
            expect(f'preflight blocks start {label} freeze', 'V07' in rejection['error'])
        for value in ([], ['final_test','final_test'], ['bogus'], 'dev'):
            put(base/'bad-roles.json', dict(experiment, eval_sets=value))
            logger(base, 'preflight', exp='bad-roles.json', ok=False)
        expect('empty, duplicate, unknown and scalar eval_sets rejected')
        before = inventory(out)
        logger(base, 'start', '--dry-run')
        expect('start dry run creates no event or lock', before == inventory(out))
        record_path = base/logger(base, 'start')['record']
        record = read(record_path)
        initial = events()
        expect('start appends one event with the run identity',
               [e['exposure_kind'] for e in initial] == ['prior_test_exposure','run_access']
               and initial[1]['run_id'] == record['run_id'])
        before = inventory(out)
        sync(base, store, '--dry-run')
        expect('sync dry run creates no store or event', before == inventory(out))

        # Forge a self-consistent portable preflight receipt that predates the trusted freeze.
        forged = out/'bundles/forged-time'
        shutil.copytree(base, forged)
        rpath = forged/record_path.relative_to(base)
        report_path = forged/record['preflight_report_ref']['path']
        for label, moment in [('earlier', registered), ('equal', first['frozen_at'])]:
            put(report_path, dict(read(report_path), checked_at=moment))
            put(rpath, dict(record, started_at=moment, recorded_at=moment, last_heartbeat_at=moment,
                            preflight_report_ref=ref(report_path, forged)))
            rejection = sync(forged, out/'reject-time', ok=False)
            expect(f'sync rejects {label} start despite matching receipt',
                   'V07' in rejection['error'] and not (out/'reject-time/transactions').exists()
                   and events() == initial)
        reg = alpha/'决策.json'
        old = reg.read_bytes()
        put(reg, dict(read(reg), decisions=[]))
        logger(base, 'preflight', ok=False)
        sync(base, out/'reject-confirmation', ok=False)
        put(reg, old)
        expect('removing authoritative confirmation blocks preflight and sync', events() == initial)
        sync(base, store)
        sync(base, store)
        expect('repeated sync of the same start has exactly one exposure', events() == initial)

        dev = bundle('dev', roles=('dev',))
        dev_record = dev/logger(dev, 'start')['record']
        sync(dev, store)
        expect('dev-only run creates no final-test exposure', events() == initial)
        predictions = dev/'intermediate-predictions.csv'
        write(predictions, b'role\nheldout_eval\n')
        updates = dev/'progress.json'
        write(updates, {'revision_kind':'progress', 'predictions':ref(predictions, dev)})
        command('run_logger.py','progress','--bundle',dev,'--previous',dev_record,'--updates',updates,ok=False)
        expect('dev declaration cannot pass off held-out predictions as development results')
        prediction_tsv = dev/'predictions.TSV'
        metrics_tsv = dev/'metrics.TSV'
        uid = read(dev_record)['run_id']
        write(prediction_tsv, ('prediction_id\trun_id\tfold_id\tmeasurement_ids\tcompound_id\tconstruct_ids\t'
                               'group_id\trole\tendpoint\tprediction\ttarget_ref\ttarget_status\n'
                               f'P1\t{uid}\tF0\t["M1"]\tC1\t["T1"]\tG1\tdev\tfixture\t0\tM1\tpoint\n').encode())
        write(metrics_tsv, b'metric_id\tendpoint\tsplit_id\tfold_id\tvalue\tunit\tn_measurements\t'
                           b'n_compounds\tn_groups\taggregation\nMAE\tfixture\tS\tF0\t0\tau\t1\t1\t1\tmean\n')
        progress = {'revision_kind':'progress', 'predictions':ref(prediction_tsv, dev)}
        put(updates, progress)
        command('run_logger.py','progress','--bundle',dev,'--previous',dev_record,'--updates',updates,'--dry-run')
        put(updates, dict(progress, status='done', revision_kind='finish', ended_at=now(), metrics=ref(metrics_tsv, dev)))
        command('run_logger.py','finish','--bundle',dev,'--previous',dev_record,'--updates',updates,'--dry-run')
        expect('uppercase TSV preserves columns and evaluation roles at progress and completion')
        freeze('create', 'alpha', 'v2')
        v2 = bundle('alpha-v2', version='v2', roles=('final_test',))
        v2_id = logger(v2, 'start')['run_id']
        sync(v2, store)
        history = events('alpha', '--dataset-id', 'D', '--split-id', 'S', '--eval-set', 'final_test')
        expect('v1 prior and run access survive v2 with a distinct run identity',
               history[:2] == initial and len(history) == 3 and history[-1]['protocol_version'] == 'v2'
               and history[-1]['run_id'] == v2_id and not events('beta'))
        expect('dataset, split and eval-set filters narrow only this topic’s verified history',
               not events('alpha', '--dataset-id', 'other') and not events('alpha', '--split-id', 'other')
               and not events('alpha', '--eval-set', 'dev'))

        # An independently started bundle reaches the central topic with no local access event.
        remote = out/'offline-workspace/研究管理'
        shutil.copytree(fixture_home, remote)
        remote_id = logger(b, 'start', tool_home=remote)['run_id']
        expect('offline start records access in its own workspace', not events('beta'))
        before = inventory(out)
        sync(b, store, '--dry-run')
        expect('dry sync of an unregistered remote access leaves the topic untouched', inventory(out) == before)
        sync(b, store)
        sync(b, store)
        beta_events = events('beta')
        expect('sync itself records a remote run once', len(beta_events) == 1 and beta_events[0]['run_id'] == remote_id)

        # Normal topic evolution can invalidate an old freeze, but never erases observed history.
        csv_path = alpha/'假设.csv'
        clean = csv_path.read_bytes()
        put(csv_path, clean+b'\n')
        freeze('verify', ok=False)
        expect('historical exposure is queryable after old inputs drift', events() == history)
        put(csv_path, clean)

        samples = [
            ('github.log', 'ghp_' + 'F'*36), ('openai.txt', 'sk-' + 'F'*48),
            ('aws.csv', 'AKIA' + 'F'*16), ('auth.log', 'Bearer ' + 'Q'*32),
            ('key.json', json.dumps({'api_key':'ExampleFixtureValue13579'})),
            ('signed.txt', 'https://example.invalid/item?X-Amz-Signature=' + 'a'*64),
            ('google.log', 'https://example.invalid/item?X-Goog-Signature=' + 'b'*64),
            ('signed.csv', 'https://example.invalid/item?Signature=' + 'Z'*32)]
        for name, value in samples:
            contaminated = out/'contaminated'/name.split('.')[0]/name.split('.')[1]
            shutil.copytree(dev, contaminated)
            write(contaminated/'unreferenced'/name, value.encode())
            markers = [value, value[-16:], 'ExampleFixtureValue13579']
            rejected = out/'reject-secret'
            left = logger(contaminated, 'preflight', ok=False, markers=markers)
            right = sync(contaminated, rejected, ok=False, markers=markers)
            expect(f'V29 rejects unreferenced {name} in both commands without echo',
                   all('V29' in r['error'] for r in (left, right)) and not (rejected/'transactions').exists())

        desk('exposure', 'verify', '--problem', 'alpha')
        last = sorted((alpha/'测试暴露').glob('*.json'))[-1]
        saved = last.read_bytes()
        put(last, saved+b'\n')
        desk('exposure', 'verify', '--problem', 'alpha', ok=False)
        desk('exposure', 'list', '--problem', 'alpha', '--dataset-id', 'unrelated', ok=False)
        prior('alpha', event='cannot-append', ok=False)
        put(last, saved)
        expect('corrupt historical bytes block verification, filtered queries and append',
               events() == history)
        saved_tail = out/'simulated-tail-loss.json'
        last.rename(saved_tail)
        desk('exposure', 'verify', '--problem', 'alpha', ok=False)
        prior('alpha', event='cannot-reset-tail', ok=False)
        saved_tail.rename(last)
        expect('independent head detects a missing tail before the next append', events() == history)
        return 'pass'

    error = None
    try:
        status = exercise()
    except (ValueError, OSError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        status, error = 'fail', str(exc)
    write(out/'commands.json', commands)
    report = {'schema_version':'freeze-tests.v1', 'status':status, 'checked_at':now(), 'error':error,
              'scope':'two synthetic topics on local POSIX', 'formal_use_allowed':False,
              'cases':cases, 'commands_ref':ref(out/'commands.json', out),
              'code_refs':[ref(p, home) for p in [Path(__file__).resolve(), *sorted((home/'工具').glob('*.py')),
                                                  home/'规范/词表.json']]}
    write(out/'report.json', report)
    return cli(lambda: {'status':status, 'passed':len(cases), 'report':str(out/'report.json'), 'error':error})


if __name__ == '__main__':
    sys.exit(main())
