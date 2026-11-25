"""Explicit stage checks. A missing required check fails; never silently skips it."""
from __future__ import annotations

import argparse
import ast
import csv
import sys
from pathlib import Path

from build_eligibility import verify_view
from common import cli, now, read, require, verify_ref, write
from evidence import Catalog
from freeze_protocol import verify_freeze
from make_report import verify_all_reports
from register_sources import root_path, verify as verify_registry
from snapshot import verify as verify_snapshot
from topics import dossier, g3_inputs, select_topic, topic_dirs
from verify_runtime import case_names


def literature_sources(root, registry):
    catalog = Catalog(root, registry); ledger, _ = catalog.ledger()
    topics = []
    for folder in topic_dirs(root / '研究管理').values():
        data = catalog.comparison(folder, ledger)
        topics.append({'problem_id':folder.name,'connected':data['connected'],
                       'comparison_rows':len(data['comparisons']),'edge_rows':len(data['relations'])})
    return {'comparison_rows':sum(t['comparison_rows'] for t in topics),'edge_rows':sum(t['edge_rows'] for t in topics),
            'topics':topics,'scope':'已接入的逐题比较行与关系端点；无新科研复审'}


def validate_runtime(path, home):
    report = read(path)
    require(report['schema_version'] == 'runtime-evidence.v1' and report['status'] == 'pass', 'runtime suite failed')
    cases = report['cases']
    require(sorted(x['case'] for x in cases) == case_names() and all(x['status'] == 'pass' for x in cases),
            'required runtime cases missing/duplicated/failed')
    expected = {str(p.relative_to(home)) for p in (home / '工具').glob('*.py')}
    expected.add('规范/词表.json')
    require({x['path'] for x in report['code_refs']} == expected, 'runtime code evidence incomplete')
    for item in report['code_refs']:
        verify_ref(item, home)
    require(report['evidence_files'], 'runtime evidence missing')
    for item in report['evidence_files']:
        verify_ref(item, path.parent)
    return {'passed': len(cases), 'scope': report['scope']}


def run(stage, phase, runtime_path, baseline_path, problem=None):
    root = root_path(); home = root / '研究管理'; checks = []
    registry = read(home / '来源登记.json')
    requirements = read(home / '规范/阶段要求.json')

    scope = {'level':'workspace' if stage != 'G3' else 'topic', 'problem_id':None}
    def check(key, fn):
        try:
            detail = fn()
            checks.append({'check': key, 'status': 'pass', 'detail': detail})
            return True
        except (ValueError, OSError, KeyError, TypeError, SyntaxError, csv.Error) as exc:
            checks.append({'check': key, 'status': 'fail', 'reason': str(exc)})
            return False

    def state():
        folders = topic_dirs(home)
        records = [dossier(root, p) for p in folders.values()]
        index = None
        topics = []
        for rec in records:
            audit = rec['state_audit']
            for item in audit['card_refs']:
                if index is None:
                    index = Catalog(root, registry).full_text_index()
                card = index.lookup(item['paper_id'])
                require(card['status'] == 'exact_card', '状态引用的当前文献编号缺少主卡：' + item['paper_id'])
                if item['status'] == '已核验':
                    require(card['review_status'] == 'full_text_reviewed'
                            and card['quantitative_check_status'] == 'selected_results_verified'
                            and card['verified_result_count'] > 0, '主卡核验范围不足，须标待审：' + item['paper_id'])
            topics.append({'problem_id':rec['problem_id'], 'sections':len(rec['sections']),
                           'recorded_decisions':len(rec['decisions']), 'confirmed_state_lines':audit['confirmed_state_lines'],
                           'checked_card_refs':len(audit['card_refs'])})
        return {'registered_problems':len(records), 'topics':topics}

    def protected():
        result = verify_snapshot(read(baseline_path), root)
        require(result['status'] == 'pass', 'protected input changed: ' + ', '.join(result['changes']))
        return result

    def syntax():
        count = 0
        for path in (home / '工具').glob('*.py'):
            ast.parse(path.read_text(), filename=str(path)); count += 1
        require(sys.version_info[:2] >= (3, 12), 'use declared Python 3.12+')
        return {'parsed_tools': count, 'python': sys.version.split()[0]}

    check('V19.state', state)
    check('V20.protected', protected)
    check('V21.sources', lambda: {'source_count': verify_registry(registry, root)})
    check('V13.compare/V15.ledger_refs', lambda: literature_sources(root, registry))
    check('V31.reports', lambda: verify_all_reports(root, check_views=True))
    check('V30.syntax', syntax)
    if stage in {'G2', 'G3'}:
        def runtime():
            require(runtime_path is not None, '--runtime-report is required for G2/G3')
            return validate_runtime(runtime_path, home)
        check('G2.runtime', runtime)
    if stage == 'G3':
        context = {}
        def select_inputs():
            folder = select_topic(home, problem)
            scope.update(problem_id=folder.name, selection='explicit' if problem else 'sole_registered')
            design, manifest = g3_inputs(root, folder)
            context.update(design=design, manifest=manifest, dossier=dossier(root, folder))
            return {'problem_id':folder.name,'protocol_id':design['protocol_id'],'version':design['version'],
                    'eligibility_manifest':str(manifest.relative_to(root))}

        def decisions_ready():
            design = context['design']
            missing = [k for k in requirements['required_decisions'] if design['decisions'].get(k) is None]
            require(not missing, 'unconfirmed research choices: ' + ', '.join(missing))
            refs = design['decision_refs']; confirmed = context['dossier']['decisions']
            require(isinstance(refs, list) and refs and confirmed, 'scientific confirmation evidence absent')
            for r in refs:
                verify_ref(r, root)
                require(any((v['confirmation_ref']['path'],v['confirmation_ref']['sha256']) ==
                            (r['path'],r['sha256']) for v in confirmed), 'confirmation not registered for this problem')
            return True

        def scientific_gate():
            require(requirements['formal_execution_enabled'],
                    'formal gate not implemented: ' + '; '.join(requirements['unimplemented']))
            # Do not allow editing the configuration alone to claim implementation.
            raise ValueError('new scientific validator implementation and evidence required before enabling G3')

        if check('G3.topic_inputs', select_inputs):
            check('V09/V10.preview', lambda: verify_view(root, context['manifest']))
            check('G3.scientific_decisions', decisions_ready)
            def frozen_protocol():
                draft = context['design']
                record = verify_freeze(root, draft['problem_id'], draft['protocol_id'], draft['version'])
                return {'version':draft['version'], 'frozen_at':record['frozen_at']}
            check('G3.frozen_protocol', frozen_protocol)
        else:
            for key in ('V09/V10.preview','G3.scientific_decisions','G3.frozen_protocol'):
                checks.append({'check':key,'status':'blocked','reason':'G3.topic_inputs failed; no substitute topic'})
        check('V11.formal_split', lambda: require(False,
              'no approved real dataset, role policy or formal split; check_split.py verifies candidate structure only'))
        check('G3.formal_gate', scientific_gate)
        if phase == 'result':
            check('G3.result_admission', lambda: require(False, 'no implemented admission or confirmatory runs'))
    return {'schema_version': 'stage-report.v1', 'checked_at': now(), 'stage': stage, 'phase': phase,
            'scope':scope,
            'status': 'pass' if all(x['status'] == 'pass' for x in checks) else 'fail',
            'formal_use_allowed': False, 'checks': checks,
            'not_applicable_in_stage': ['G3 scientific gates'] if stage in {'G1', 'G2'} else [],
            'root_project_validation': 'separate; known failures are not overridden by this report'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=['G1', 'G2', 'G3'], required=True)
    p.add_argument('--phase', choices=['prepare', 'result'], default='prepare')
    p.add_argument('--problem', help='G3 需选择课题；只有一个时兼容原命令。G1/G2 始终为全局校验。')
    p.add_argument('--runtime-report', type=Path)
    p.add_argument('--baseline', type=Path, default=root_path() / '研究管理/_交付/demo-baseline/baseline.json')
    p.add_argument('--output', type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.problem is not None and a.stage != 'G3':
        p.error('--problem 只适用于 G3')
    result = run(a.stage, a.phase, a.runtime_report, a.baseline, a.problem)
    if a.output and not a.dry_run:
        write(a.output, result)
    return result


if __name__ == '__main__':
    sys.exit(cli(main))
