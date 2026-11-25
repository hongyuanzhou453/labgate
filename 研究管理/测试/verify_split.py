"""Exercise W7 through its public CLI using only retained, synthetic input bundles."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path


SPLIT_ID = 'S-00000000-0000-4000-8000-000000000002'
DATA_ID = 'D-00000000-0000-4000-8000-000000000001'
HOME = Path(__file__).resolve().parents[1]
CHECKER = HOME / '工具/check_split.py'
REASONS = {
    'legal_rotation_shared_panel': None,
    'donor_hidden': 'incomplete source roster',
    'origin_hidden': 'policy origin scopes',
    'other_protein_same_ligand': 'component crosses roles',
    'bridged_donor': 'component crosses roles',
    'within_fold_role_alias': 'component crosses roles',
    'final_rotation': 'final heldout changes across folds',
    'wrong_access': 'role/use/access mismatch',
    'pending_not_include': 'not eligible',
    'exclude_not_include': 'not eligible',
    'wrong_endpoint': 'endpoint/use not eligible',
    'reused_observation': 'reused source observation',
    'duplicate_assignment': 'duplicate assignment',
    'silent_exclusion': 'exclusion lacks reason',
    'wrong_member_group': 'source identity/group mismatch',
    'grouping_omits_source': 'grouping map misses',
    'ambiguous_replicate': 'ambiguous panel replicate',
    'pair_one_side_excluded': 'missing panel partner',
    'cross_role_pair': 'invalid/repeated within-fold T/O pair',
    'reversed_pair': 'invalid/repeated within-fold T/O pair',
    'wrong_fold_pair': 'invalid/repeated within-fold T/O pair',
    'missing_pair': 'omits an eligible assigned panel pair',
    'duplicate_pair': 'invalid/repeated within-fold T/O pair',
    'stale_pair_parent': 'pair table not bound',
    'false_count': 'declared counts differ',
    'false_table_count': 'file record_count mismatch',
    'stale_label_bytes': 'file hash mismatch',
    'stale_dataset': 'does not bind the supplied dataset',
    'different_policy': 'does not bind the supplied policy',
    'unsupported_scope': 'unsupported role, overlap or support policy',
}


def dump(p, data):
    blob = data if isinstance(data, bytes) else (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()
    tmp = p.with_name(f'.{p.name}.{uuid.uuid4()}.tmp')
    with tmp.open('xb') as f:
        f.write(blob)
    os.replace(tmp, p)


def reference(path):
    data = path.read_bytes()
    return {'path': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def table(folder, name, fields, rows):
    rows = list(rows); buf = io.StringIO(newline=''); w = csv.writer(buf, lineterminator='\n')
    w.writerow(fields.split()); w.writerows(rows); p = folder / (name + '.csv'); dump(p, buf.getvalue().encode())
    return dict(reference(p), record_count=len(rows))


def prepare(folder, case):
    folder.mkdir()
    origins = ['P', 'E']; ligands = ['A', 'B', 'C', 'H']; folds = ['F1', 'F2']
    # A/B/C/H: four shared-protein panel roles. X is an unrelated donor; Z connects to H
    # only via a third, unmeasured ligand Y. HX is H on a different protein.
    pool = {'P': [(c + k, c, k, 'obs-' + c + k, 'strict_kd_point') for c in ligands for k in ('T','O')],
            'E': [('XD','X','D','obs-XD','strict_kd_point'), ('ZD','Z','D','obs-ZD','strict_kd_point')]}
    pool['P'].append(('HX','H','X','obs-HX','strict_kd_point'))
    if case == 'reused_observation': pool['E'][0] = ('XD','X','D','obs-AT','strict_kd_point')
    if case == 'wrong_endpoint': pool['E'][0] = ('XD','X','D','obs-XD','other_endpoint')
    if case == 'ambiguous_replicate': pool['P'].append(('AT2','A','T','obs-AT2','strict_kd_point'))
    source_data = {}; pool_refs = {}
    for origin, rows in pool.items():
        # Synthetic numerical source bytes, never real labels.
        source_data[origin] = table(folder, 'labels-' + origin, 'measurement_id value', ((r[0], i) for i,r in enumerate(rows)))
        source_data[origin].pop('record_count')
        pool_refs[origin] = table(folder, 'pool-' + origin, 'measurement_id compound_id construct_id observation_id endpoint', rows)
    group_rows = [('P',c,'LP'+c,'J'+c,'K'+c) for c in ligands]
    group_rows += [('E','X','LEX','JX','KX'), ('E','Y','LEYZ','JY','KH'), ('E','Z','LEYZ','JZ','KZ')]
    if case == 'within_fold_role_alias': group_rows[2] = ('P','C','LPB','JC','KC')
    if case == 'grouping_omits_source': group_rows = [g for g in group_rows if g[1]!='X']
    gr = table(folder, 'grouping', 'origin_scope compound_id local_group_id joint_group_id overlap_key', group_rows)
    elig = []
    for origin, rows in pool.items():
        for mid, cid, con, *_ in rows:
            for use in (['point_training','point_evaluation'] if origin=='P' and con in ('T','O') else ['source_pool']):
                decision = 'include'
                if mid=='HT' and use=='point_evaluation' and case in ('pending_not_include','exclude_not_include'):
                    decision = case.split('_')[0]
                elig.append((mid,use,decision))
    er = table(folder, 'eligibility', 'measurement_id intended_use decision', elig)
    if case == 'origin_hidden': pool_refs.pop('E')
    dataset = {'schema_version':'candidate-universe.v1','formal_use_allowed':False,'dataset_id':DATA_ID,
               'grouping_scheme':'fixture-S1','pools':pool_refs,'source_data_refs':source_data,'grouping_ref':gr,'eligibility_ref':er}
    dump(folder/'dataset.json',dataset); dh = reference(folder/'dataset.json')['sha256']
    policy={'schema_version':'candidate-policy.v1','formal_use_allowed':False,'dataset_manifest_sha256':dh,'dataset_id':DATA_ID,
            'profile':'fixed-panel-strict-paired.v1','allowed_shared_entities':['construct_id'],'support_policy':'none',
            'fold_ids':folds,'origin_scopes':origins,'panel_origin':'P','target_construct_id':'T','offtarget_construct_id':'O',
            'endpoint':'strict_kd_point','grouping_scheme':'fixture-S1'}
    if case == 'unsupported_scope': policy['support_policy'] = 'heldout_labels'
    dump(folder/'policy.json',policy)
    permissions = {'base_fit':('fit','point_training'), 'rule_dev':('develop','point_training'),
                   'rule_eval':('evaluate','point_evaluation'),'heldout_eval':('sealed','point_evaluation')}
    members=[]; pairs=[]
    for f in folds:
        roles = dict(zip(ligands, ['base_fit','rule_dev','rule_eval','heldout_eval'] if f=='F1' else
                          ['rule_dev','base_fit','rule_eval','heldout_eval']))
        if case=='final_rotation' and f=='F2': roles['C'],roles['H'] = roles['H'],roles['C']
        for origin, rows in pool.items():
            for mid,cid,con,_,_ in rows:
                if case=='donor_hidden' and mid=='ZD' and f=='F2': continue
                if origin=='P' and con in ('T','O'):
                    role=roles[cid]; access,use=permissions[role]; reason=''
                elif mid=='XD' or (mid=='ZD' and case=='bridged_donor') or (mid=='HX' and case=='other_protein_same_ligand'):
                    role,access,use,reason='base_fit','fit','source_pool',''
                else: role,access,use,reason='excluded','none','none','heldout_component'
                if mid=='HT' and case=='wrong_access': access='fit'
                if mid=='HX' and case=='silent_exclusion': reason=''
                if mid=='HO' and case=='pair_one_side_excluded': role,access,use,reason='excluded','none','none','fixture_drop'
                group='bad-group' if case=='wrong_member_group' and mid=='XD' else 'J'+cid
                members.append((SPLIT_ID,f,mid,cid,con,group,role,access,use,origin,reason))
        for c in ligands: pairs.append([SPLIT_ID,f,'pair-'+c,roles[c],c+'T',c+'O'])
    if case=='duplicate_assignment': members.append(members[0])
    if case=='cross_role_pair': pairs[0][5]='HO'
    if case=='reversed_pair': pairs[0][4:6]=['AO','AT']
    if case=='wrong_fold_pair': pairs[0][1]='F2'
    if case=='missing_pair': pairs.pop()
    if case=='duplicate_pair': pairs.append(pairs[0][:])
    mr = table(folder,'members','split_id fold_id measurement_id compound_id construct_id group_id role label_access intended_use origin_scope exclusion_reason',members)
    pr = table(folder,'pairs','split_id fold_id pair_id role t_measurement_id o_measurement_id',pairs)
    if case=='false_table_count': pr['record_count']+=1
    # Expected fixture facts are specified independently of the checker.
    per_fold={'roles':{'base_fit':3,'rule_dev':2,'rule_eval':2,'heldout_eval':2,'excluded':2},
              'active_compounds':5,'active_components':5,'source_measurements':1,'pairs':4}
    counts={'input_measurements':11,'member_rows':22,'pair_rows':8,'by_fold':dict.fromkeys(folds,per_fold)}
    if case=='false_count': counts['pair_rows']=True
    split={'schema_version':'candidate-split.v1','formal_use_allowed':False,'split_id':SPLIT_ID,'dataset_id':DATA_ID,
           'policy_sha256':reference(folder/'policy.json')['sha256'],'dataset_manifest_sha256':dh,
           'pairs_from_members_sha256':mr['sha256'] if case!='stale_pair_parent' else '0'*64,'files':{'members':mr,'pairs':pr},
           'counts':counts}
    dump(folder/'split.json',split)
    if case=='stale_dataset': dump(folder/'dataset.json',dict(dataset,revision_note='changed'))
    if case=='different_policy': dump(folder/'policy.json',dict(policy,notes='changed'))
    if case=='stale_label_bytes':
        p = folder/'labels-P.csv'; dump(p,p.read_bytes().replace(b'AT,0',b'AT,9'))


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--dry-run',action='store_true'); args=parser.parse_args()
    output=args.output.resolve(); delivery=(HOME/'_交付').resolve()
    if output.exists() or not output.parent.is_relative_to(delivery) or not output.parent.is_dir():
        parser.error('use a new child of an existing delivery directory')
    if args.dry_run:
        print(json.dumps({'status':'pass','writes':False,'cases':list(REASONS)})); return 0
    output.mkdir(); cases=[]
    for name,expected in REASONS.items():
        folder=output/name; prepare(folder,name)
        cmd=[sys.executable,'-B',str(CHECKER),'--dataset','dataset.json','--policy','policy.json','--split','split.json','--dry-run']
        before={p.name: reference(p) for p in folder.iterdir()}
        proc=subprocess.run(cmd,cwd=folder,text=True,capture_output=True,timeout=20)
        result=json.loads(proc.stdout); error=result.get('error','')
        unchanged=before=={p.name:reference(p) for p in folder.iterdir()}
        ok=((expected is None and proc.returncode==0 and result.get('formal_use_allowed') is False and result.get('status')=='pass') or
            (expected is not None and proc.returncode==1 and result.get('status')=='fail' and expected in error)) and unchanged and not proc.stderr
        receipt={'args':cmd,'cwd':name,'returncode':proc.returncode,'result':result,'stderr':proc.stderr,
                 'expected_error_contains':expected,'inputs_unchanged':unchanged,'inputs':list(before.values())}
        dump(folder/'receipt.json',receipt)
        cases.append({'case':name,'status':'pass' if ok else 'fail','actual':error or result.get('scope'),
                      'receipt':dict(reference(folder/'receipt.json'),path=f'{name}/receipt.json')})
    report={'schema_version':'split-tests.v1','scope':'synthetic only','formal_use_allowed':False,
            'status':'pass' if all(c['status']=='pass' for c in cases) else 'fail','cases':cases,
            'code_refs':[dict(reference(p),path=str(p.relative_to(HOME))) for p in
                         [Path(__file__).resolve(),*sorted((HOME/'工具').glob('*.py')),HOME/'规范/词表.json']]}
    dump(output/'report.json',report)
    failed=[c for c in cases if c['status']!='pass']; print(json.dumps({'status':report['status'],'passed':len(cases)-len(failed),'total':len(cases),'failed':failed},ensure_ascii=False))
    return 1 if failed else 0


if __name__=='__main__':
    sys.exit(main())
