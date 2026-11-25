"""Generate an immutable diagnostic/pending eligibility view, never a training split."""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import math
import sys
from pathlib import Path

from common import cli, read, ref, require, sha, verify_ref, write
from register_sources import root_path


FIELDS = ['problem_id', 'eligibility_id', 'measurement_id', 'intended_use', 'decision',
          'reason_codes', 'evidence_refs', 'review_status', 'missing_reason']
NEEDED = {'measurement_id', 'construct_id', 'compound_id', 'value_status', 'measurement_type',
          'is_verified_kd_point', 'is_strict_clean_kd_point', 'pkd_point', 'pkd_lower', 'pkd_upper',
          'lower_strict', 'upper_strict', 'source_locator', 'lineage_id', 'layer_id'}


def classify(row, intended_use):
    require(intended_use in {'diagnostic', 'point_training'}, 'unsupported preview use')
    for field in ('is_verified_kd_point', 'is_strict_clean_kd_point'):
        require(row[field] in {'True', 'False'}, 'unexpected upstream boolean encoding')
    strict = row['is_strict_clean_kd_point'] == 'True'
    verified = row['is_verified_kd_point'] == 'True'
    if strict or verified:
        require(verified and 'Kd' in row['measurement_type'] and
                row['value_status'] in {'point_estimate', 'point_estimate_high_kd_tail'},
                'invalid value promoted to verified Kd')
        require(row['pkd_point'] != '' and math.isfinite(float(row['pkd_point'])), 'verified point missing/invalid')
        # Upstream represents an exact point as a closed singleton interval.
        require(row['pkd_lower'] and row['pkd_upper'] and
                float(row['pkd_lower']) == float(row['pkd_point']) == float(row['pkd_upper']) and
                row['lower_strict'] == row['upper_strict'] == 'False',
                'verified point must have equal closed interval bounds')
        require(not strict or row['value_status'] == 'point_estimate', 'high tail promoted to strict point')
    else:
        require(not row['pkd_point'], 'non-verified measurement carries effective point')
    if intended_use == 'diagnostic':
        return 'include', ['audit_trace_only'], ''
    if strict:
        return 'pending', ['endpoint_candidate_only', 'research_choices_unconfirmed'], 'not_checked'
    return 'exclude', ['not_strict_verified_kd_point'], ''


def generate(root, rules_path, output, dry_run=False):
    rules = read(rules_path)
    require(rules['schema_version'] == 'eligibility-preview.v1', 'only preview rules supported')
    require(rules['mode'] == 'diagnostic_only' and rules['formal_use_allowed'] is False,
            'formal admission is not supported by this generator')
    require(rules['intended_uses'] == ['diagnostic', 'point_training'], 'unsupported uses/order')
    verify_ref(rules['design_ref'], root)
    inputs = [verify_ref(x, root) for x in rules['source_snapshot_refs']]
    measurements = verify_ref(rules['measurements_ref'], root)
    before = {p: sha(p) for p in [rules_path, *inputs, measurements]}
    content = io.StringIO(newline='') if not dry_run else None
    writer = csv.DictWriter(content, fieldnames=FIELDS, lineterminator='\n') if content is not None else None
    if writer:
        writer.writeheader()
    ids, counts = set(), collections.Counter()
    with measurements.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        require(NEEDED <= set(reader.fieldnames or []), 'upstream measurement columns missing')
        for line, row in enumerate(reader, start=2):
            mid = row['measurement_id']
            require(mid and mid not in ids, 'duplicate/missing measurement ID')
            ids.add(mid)
            for use in rules['intended_uses']:
                decision, reasons, missing = classify(row, use)
                counts[use + ':' + decision] += 1
                if writer:
                    writer.writerow(dict(problem_id=rules['problem_id'], eligibility_id=rules['eligibility_id'],
                                         measurement_id=mid, intended_use=use, decision=decision,
                                         reason_codes=json.dumps(reasons, separators=(',', ':')),
                                         evidence_refs=json.dumps([{'source_id': 'SRC-AUDIT-measurements', 'line': line}], separators=(',', ':')),
                                         review_status='diagnostic_only', missing_reason=missing))
    for path, digest in before.items():
        require(sha(path) == digest, 'input changed during eligibility build')
    result = {'schema_version': 'eligibility-preview-manifest.v1', 'status': 'pass',
              'formal_use_allowed': False, 'rules_ref': ref(rules_path, root),
              'source_rows': len(ids), 'view_rows': sum(counts.values()), 'counts': dict(counts),
              'primary_key': ['eligibility_id', 'measurement_id', 'intended_use'],
              'generator_ref': ref(Path(__file__), root)}
    if not dry_run:
        destination = output / '数据资格表.csv'
        write(destination, content.getvalue().encode('utf-8'))
        result['view_ref'] = ref(destination, root)
        write(output / 'manifest.json', result)
    return result


def verify_view(root, manifest_path):
    manifest = read(manifest_path)
    rules_path = verify_ref(manifest['rules_ref'], root)
    view_path = verify_ref(manifest['view_ref'], root)
    verify_ref(manifest['generator_ref'], root)
    expected = generate(root, rules_path, view_path.parent, dry_run=True)
    require(manifest['counts'] == expected['counts'] and manifest['source_rows'] == expected['source_rows'],
            'view counts differ from actual sources')
    rules = read(rules_path)
    source = verify_ref(rules['measurements_ref'], root)
    counts, keys = collections.Counter(), set()
    with view_path.open(encoding='utf-8', newline='') as vf, source.open(encoding='utf-8-sig', newline='') as sf:
        view, rows = csv.DictReader(vf), csv.DictReader(sf)
        require(view.fieldnames == FIELDS, 'wrong view columns')
        for line, row in enumerate(rows, start=2):
            for use in rules['intended_uses']:
                item = next(view, None)
                require(item is not None, 'view truncated')
                key = (item['eligibility_id'], item['measurement_id'], item['intended_use'])
                require(key not in keys and key == (rules['eligibility_id'], row['measurement_id'], use), 'view identity mismatch')
                keys.add(key)
                decision, reasons, missing = classify(row, use)
                require(item['decision'] == decision and json.loads(item['reason_codes']) == reasons and
                        item['missing_reason'] == missing and item['problem_id'] == rules['problem_id'] and
                        item['review_status'] == 'diagnostic_only' and
                        json.loads(item['evidence_refs']) == [{'source_id': 'SRC-AUDIT-measurements', 'line': line}],
                        'view decision/evidence differs from rules')
                counts[use + ':' + decision] += 1
        require(next(view, None) is None, 'extra view rows')
    require(len(keys) == manifest['view_rows'] and dict(counts) == manifest['counts'], 'view counts invalid')
    require(manifest['formal_use_allowed'] is False, 'preview cannot authorize training')
    return manifest['counts']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rules', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--verify', type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.verify:
        return {'status': 'pass', 'counts': verify_view(root_path(), a.verify)}
    require(a.rules is not None and a.output is not None, '--rules and --output required')
    return generate(root_path(), a.rules.resolve(), a.output.resolve(), a.dry_run)


if __name__ == '__main__':
    sys.exit(cli(main))
