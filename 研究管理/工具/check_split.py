"""Read-only W7 check of a candidate under the strict fixed-panel profile."""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import Invalid, cli, encoded, read, require, run_id, sha, verify_ref
from vocabulary import choices, split_role_policy


EXCLUDED = 'excluded'
POOL = 'measurement_id compound_id construct_id observation_id endpoint'
GROUP = 'origin_scope compound_id local_group_id joint_group_id overlap_key'
ELIGIBILITY = 'measurement_id intended_use decision'
MEMBER = 'split_id fold_id measurement_id compound_id construct_id group_id role label_access intended_use origin_scope exclusion_reason'
PAIR = 'split_id fold_id pair_id role t_measurement_id o_measurement_id'


def distinct(items, label):
    require(isinstance(items, list) and items and all(isinstance(x, str) and x.strip() == x and x for x in items),
            f'missing/invalid {label}')
    require(len(set(items)) == len(items), f'duplicate {label}')
    return set(items)


def check_id(value, prefix):
    require(isinstance(value, str) and value.startswith(prefix + '-'), f'invalid {prefix} identifier')
    run_id(value[2:])  # same canonical UUID4 representation; the prefix denotes the record type


def table(base, binding, fields, label, paths):
    path = verify_ref(binding, base)
    require(path not in paths, f'same file used twice: {label}')
    paths[path] = binding['sha256']
    require(type(binding.get('bytes')) is int and type(binding.get('record_count')) is int,
            f'missing bytes/record_count: {label}')
    required = fields.split()
    with path.open(encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f, strict=True)
        require(rd.fieldnames is not None and sorted(rd.fieldnames) == sorted(required), f'wrong columns: {label}')
        count = 0
        try:
            for count, row in enumerate(rd, 1):
                require(None not in row and all(isinstance(v, str) and v.strip() == v and
                                                (v or k == 'exclusion_reason') for k, v in row.items()),
                        f'missing/extra/invalid cell: {label} row {count}')
                yield row
        except csv.Error as exc:
            raise Invalid(f'invalid CSV: {label}: {exc}') from exc
    require(count == binding['record_count'], f'file record_count mismatch: {label}')


class Components:
    """Connected components across all scope/group/identity links, including unused bridge ligands."""
    def __init__(self):
        self.parents = {}

    def root(self, key):
        p = self.parents
        p.setdefault(key, key)
        while key != p[key]:
            p[key] = p[p[key]]
            key = p[key]
        return key

    def join(self, *keys):
        a = self.root(keys[0])
        for k in keys[1:]:
            b = self.root(k)
            if a != b:
                self.parents[b] = a


def check_candidate(dataset_path, policy_path, split_path):
    roles = split_role_policy()
    active_roles = roles.keys() - {EXCLUDED}
    intended_uses = choices('split_intended_use')
    eligibility_decisions = choices('split_eligibility_decision')
    dataset_path, policy_path, split_path = (Path(p).resolve() for p in (dataset_path, policy_path, split_path))
    paths = {p: sha(p) for p in (dataset_path, policy_path, split_path)}
    require(len(paths) == 3, 'provide three distinct inputs')
    data, policy, split = map(read, (dataset_path, policy_path, split_path))
    for doc, schema in ((data, 'candidate-universe.v1'), (policy, 'candidate-policy.v1'),
                        (split, 'candidate-split.v1')):
        require(isinstance(doc, dict) and doc.get('schema_version') == schema and
                doc.get('formal_use_allowed') is False, f'expected candidate-only {schema}')
    check_id(data['dataset_id'], 'D'); check_id(split['split_id'], 'S')
    require(split['dataset_id'] == policy['dataset_id'] == data['dataset_id'] and
            split['dataset_manifest_sha256'] == policy['dataset_manifest_sha256'] == paths[dataset_path],
            'candidate/policy does not bind the supplied dataset')
    require(split['policy_sha256'] == paths[policy_path], 'candidate does not bind the supplied policy')
    require(policy['profile'] == 'fixed-panel-strict-paired.v1' and
            policy['allowed_shared_entities'] == ['construct_id'] and policy['support_policy'] == 'none',
            'unsupported role, overlap or support policy')
    folds = distinct(policy['fold_ids'], 'folds'); origins = distinct(policy['origin_scopes'], 'origin scopes')
    panel = policy['panel_origin']; target = policy['target_construct_id']; other = policy['offtarget_construct_id']
    distinct([target, other], 'panel constructs')
    require(panel in origins and policy['endpoint'] == 'strict_kd_point', 'unsupported panel origin or endpoint')
    require(isinstance(data['grouping_scheme'], str) and data['grouping_scheme'].strip() and
            data['grouping_scheme'] == policy['grouping_scheme'], 'grouping scheme mismatch')
    require(isinstance(data['pools'], dict) and set(data['pools']) == origins,
            'dataset does not cover exactly the policy origin scopes')
    dbase = dataset_path.parent; sbase = split_path.parent
    require(isinstance(data['source_data_refs'], dict) and set(data['source_data_refs']) == origins,
            'missing underlying data reference for an origin')
    for binding in data['source_data_refs'].values():
        require(isinstance(binding, dict) and type(binding.get('bytes')) is int, 'source byte count required')
        p = verify_ref(binding, dbase)  # byte identity only; never display sealed numerical labels
        require(p not in {dataset_path, policy_path, split_path}, 'source bytes must be separate from manifests')
        paths[p] = binding['sha256']  # a shared source file can legitimately cover multiple origin scopes
    pool = {}; groups = {}; decisions = {}; components = Components()
    for origin, binding in data['pools'].items():
        n = len(pool)
        for row in table(dbase, binding, POOL, f'pool {origin}', paths):
            mid = row['measurement_id']
            require(mid not in pool, f'duplicate measurement ID in source universe: {mid}')
            pool[mid] = (origin, row['compound_id'], row['construct_id'], row['observation_id'], row['endpoint'])
        require(len(pool) > n, f'empty origin roster: {origin}')
    for row in table(dbase, data['grouping_ref'], GROUP, 'grouping', paths):
        origin, cid = row['origin_scope'], row['compound_id']
        require(origin in origins and (origin, cid) not in groups, 'unknown scope/duplicate in grouping map')
        groups[origin, cid] = row['joint_group_id']
        components.join(('compound', cid), ('local', origin, row['local_group_id']),
                        ('joint', row['joint_group_id']), ('overlap', row['overlap_key']))
    require(all((o, c) in groups for o, c, *_ in pool.values()), 'grouping map misses a source ligand')
    for row in table(dbase, data['eligibility_ref'], ELIGIBILITY, 'eligibility', paths):
        key = (row['measurement_id'], row['intended_use'])
        require(key not in decisions and key[0] in pool and key[1] in intended_uses
                and row['decision'] in eligibility_decisions, 'invalid/duplicate eligibility row')
        decisions[key] = row['decision']

    require(isinstance(split['files'], dict) and set(split['files']) == {'members', 'pairs'}, 'expected members and pairs')
    require(split['pairs_from_members_sha256'] == split['files']['members']['sha256'],
            'pair table not bound to this member revision')
    members = {f: {} for f in folds}; role_components = {}; active_observations = {}
    panel_slots = defaultdict(dict); donor_counts = Counter()
    for row in table(sbase, split['files']['members'], MEMBER, 'members', paths):
        fold, mid, role = row['fold_id'], row['measurement_id'], row['role']
        require(row['split_id'] == split['split_id'] and fold in folds and mid in pool, 'unknown split/fold/measurement in members')
        require(mid not in members[fold], f'duplicate assignment in {fold}: {mid}')
        origin, cid, con, obs, endpoint = pool[mid]
        require((row['origin_scope'], row['compound_id'], row['construct_id'], row['group_id']) ==
                (origin, cid, con, groups[origin, cid]), f'source identity/group mismatch: {mid}')
        members[fold][mid] = role
        use, access = row['intended_use'], row['label_access']
        if role == EXCLUDED:
            require((use, access) == roles[role] and row['exclusion_reason'], f'exclusion lacks reason or permits access: {mid}')
            continue
        require(role in active_roles and not row['exclusion_reason'], f'unknown role or active row marked excluded: {mid}')
        donor = origin != panel or con not in {target, other}
        if donor:
            require((role, use, access) == ('base_fit', 'source_pool', 'fit'), f'donor role/access mismatch: {mid}')
            donor_counts[fold] += 1
        else:
            require((use, access) == roles[role], f'role/use/access mismatch: {mid}')
            bucket = panel_slots[fold, role, cid]
            require(con not in bucket, f'ambiguous panel replicate in {fold}: {cid}/{con}')
            bucket[con] = mid
        require(endpoint == policy['endpoint'] and decisions.get((mid, use)) == 'include',
                f'endpoint/use not eligible for {use}: {mid}')
        require((fold, obs) not in active_observations, f'reused source observation in {fold}: {obs}')
        active_observations[fold, obs] = mid
        key = (fold, components.root(('compound', cid)))
        require(role_components.setdefault(key, role) == role, f'component crosses roles in {fold}: {cid}/{role}')

    heldout = None
    for fold in sorted(folds):
        missing = pool.keys() - members[fold].keys()
        require(not missing, f'incomplete source roster in {fold}: {len(missing)} missing, first {min(missing, default="-")}')
        final = {mid for mid, role in members[fold].items() if role == 'heldout_eval'}
        require(heldout is None or final == heldout, f'final heldout changes across folds: {fold}')
        heldout = final
        require({role for f, role, _ in panel_slots if f == fold} == active_roles and donor_counts[fold] > 0,
                f'four nonempty panel roles and active sources required in {fold}')

    expected_pairs = set()
    for (fold, role, cid), slots in panel_slots.items():
        require(set(slots) == {target, other}, f'missing panel partner after assignment in {fold}: {cid}')
        expected_pairs.add((fold, role, slots[target], slots[other]))
    observed_pairs, ids = set(), set()
    for row in table(sbase, split['files']['pairs'], PAIR, 'pairs', paths):
        key = (row['fold_id'], row['role'], row['t_measurement_id'], row['o_measurement_id'])
        pid = (row['fold_id'], row['pair_id'])
        require(row['split_id'] == split['split_id'] and key in expected_pairs and key not in observed_pairs and pid not in ids,
                f'invalid/repeated within-fold T/O pair: {pid}')
        observed_pairs.add(key); ids.add(pid)
    require(observed_pairs == expected_pairs, 'pair table omits an eligible assigned panel pair')
    counts = {'input_measurements': len(pool), 'member_rows': sum(map(len, members.values())),
              'pair_rows': len(observed_pairs), 'by_fold': {}}
    for fold in sorted(folds):
        assignments = members[fold]; active = [m for m, role in assignments.items() if role != EXCLUDED]
        cids = {pool[m][1] for m in active}
        counts['by_fold'][fold] = {'roles': dict(sorted(Counter(assignments.values()).items())),
                                  'active_compounds': len(cids), 'active_components': len({components.root(('compound', c)) for c in cids}),
                                  'source_measurements': donor_counts[fold], 'pairs': sum(p[0] == fold for p in observed_pairs)}
    require(encoded(counts) == encoded(split['counts']), 'declared counts differ from independent recomputation')
    require(all(sha(p) == digest for p, digest in paths.items()), 'input changed during validation')
    return {'status': 'pass', 'scope': 'candidate_structure_only', 'formal_use_allowed': False,
            'dataset_id': data['dataset_id'], 'split_id': split['split_id'], 'counts': counts,
            'dataset_sha256': paths[dataset_path], 'policy_sha256': paths[policy_path], 'split_sha256': paths[split_path]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'policy', 'split'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true', help='always read-only; no publication mode')
    args = parser.parse_args()
    return check_candidate(args.dataset, args.policy, args.split)


if __name__ == '__main__':
    sys.exit(cli(main))
