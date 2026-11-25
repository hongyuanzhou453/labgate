"""Exercise the public desk and scoped gate in an isolated two-topic workspace."""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from pathlib import Path


def csv_bytes(fields, rows, sep=','):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter=sep, lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode()


def main():
    actual = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(actual/'工具'))
    from common import cli, now, read, ref, require, write
    from sync_runs import ingest
    from verify_runtime import Suite

    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output',required=True,type=Path)
    p.add_argument('--dry-run',action='store_true'); args = p.parse_args()
    out = args.output.resolve()
    if out.exists() or not out.parent.is_relative_to(actual/'_交付') or not out.parent.is_dir():
        p.error('选择现有交付轮下未使用的测试目录')
    if args.dry_run:
        print(json.dumps({'status':'pass','writes':False,'scope':'topic creation, selection, evidence, committed runs, desk refresh'})); return 0
    out.mkdir(); root = out/'workspace'; home = root/'研究管理'; folder = home/'问题'; cases=[]; commands=[]

    def command(tool, *argv, code=0):
        cmd=[sys.executable,'-B',str(home/'工具'/tool),*map(str,argv)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30,cwd=root)
        commands.append({'tool':tool,'args':list(map(str,argv)),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        require(r.returncode==code and not r.stderr, f'{tool} {argv}: unexpected exit {r.returncode}: {r.stdout} {r.stderr}')
        value=json.loads(r.stdout); require(isinstance(value,dict),'non-JSON command result')
        return value

    def desk(*argv,code=0): return command('research.py',*argv,code=code)
    def gate(*argv):
        r=command('check_all.py','--stage','G3','--phase','prepare',*argv,'--dry-run',code=1)
        require(not r['formal_use_allowed'],'fixture cannot authorize execution')
        return r['scope'],{c['check']:c for c in r['checks']}
    def expect(name, condition):
        require(condition, f'failed desk assertion: {name}')
        cases.append({'case':name,'status':'pass','through_command':len(commands)})
    def put(path,value): write(path,value,replace=True)
    def hashes(folder):
        return {str(p.relative_to(folder)):ref(p,folder)['sha256'] if p.is_file() else 'dir' for p in folder.rglob('*')}
    def source(name, data, fields=None):
        path=root/'evidence'/name
        put(path,data)
        r=ref(path,root)
        return {'source_id':'SRC-'+name.split('.')[0],'resolved_path':r['path'],'sha256':r['sha256'],'bytes':r['bytes'],
                'source_kind':'fixture','review_status':'fixture-only','check_scope':'structural fixture','header':fields}

    def exercise():
        folder.mkdir(parents=True)
        for t in (actual/'工具').glob('*.py'): write(home/'工具'/t.name,t.read_bytes())
        for f in ('规范/接入映射.json','规范/阶段要求.json','规范/词表.json'): write(home/f,(actual/f).read_bytes())
        write(home/'AGENTS.md',b'Fixture workspace only.\n')
        mapping=read(home/'规范/接入映射.json'); cols=mapping['compare_columns']
        fields=['id','title','local_pdf','establishes','does_not_establish','evidence_note']
        entries=[source('LEDGER.csv',csv_bytes(fields,[dict(zip(fields,[k,'record '+k,'','fixture fact','no generalization','unassessed'])) for k in ('L01','L09')]),fields),
                 source('ALIASES.json',{'groups':[{'canonical_id':'L01','related_ids':['L09'],'relation':'fixture_versions'}]})]
        for name in ('alpha','beta'):
            def record(row_id, paper_id): return dict.fromkeys(cols,'fixture') | {'row_id':row_id,'paper_id':paper_id}
            comps=[record(name+'-L','L01'),record(name+'-P','项目参照')]
            ef=['from_id','to_id','relation','compare_rows','locator','status','basis']
            edge=dict(zip(ef,['L01','项目参照','parallel',name+'-L;'+name+'-P','fixture page 1','待审','fixture inference']))
            material=[source(name+'-table.tsv',csv_bytes(cols,comps,'\t'),cols),
                      source(name+'-edge.csv',csv_bytes(ef,[edge]),ef)]
            if name=='alpha': entries.extend(material)
        put(home/'来源登记.json',{'schema_version':'sources.v0.2','entries':entries})

        prior=hashes(home)
        d=desk('new','alpha','--title','第一个候选','--dry-run')
        expect('dry-run creates neither folders nor locks',d['dry_run'] and not d['formal_use_allowed'] and prior==hashes(home))
        desk('new','alpha','--title','第一个候选')
        opened=desk('show','alpha'); s=opened['summary']
        expect('fresh candidate contains no hypotheses, budget or approval', s['registered_decisions']==s['hypothesis_versions']==s['draft_versions']==s['budget_items']==0
               and s['g3_profile'] is None and not opened['formal_use_allowed'])
        before=hashes(folder/'alpha')
        expect('duplicate and case variants do not overwrite', '不覆盖' in desk('new','ALPHA','--title','不同题名',code=1)['error'] and before==hashes(folder/'alpha'))
        expect('invalid topic path rejected without writing outside', 'invalid identifier' in desk('new','../escape','--title','bad',code=1)['error'] and not (home/'escape').exists())
        one,checks=gate()
        expect('single candidate implicitly selects only itself; no borrowed G3 profile',one['problem_id']=='alpha' and one['selection']=='sole_registered'
               and '尚未接入' in checks['G3.topic_inputs']['reason'] and checks['V09/V10.preview']['status']=='blocked')
        desk('new','beta','--title','第二个候选')
        scope,checks=gate()
        expect('multiple candidates require an explicit G3 selection',scope['problem_id'] is None and '--problem' in checks['G3.topic_inputs']['reason'])
        scope,checks=gate('--problem','missing')
        expect('unknown topic cannot fall back to a different dossier','未登记' in checks['G3.topic_inputs']['reason'])

        for name in ('alpha','beta'):
            top=folder/name
            d={'schema_version':'design-draft.v0.2','problem_id':name,'protocol_id':'P','version':'v1','status':'draft','formal_use_allowed':False,
               'hypotheses':[{'hypothesis_id':'H1','hypothesis_version':'v1'}],'decisions':{},'decision_refs':[]}
            put(top/'协议/P/v1/design.json',d)
            hf=['problem_id','hypothesis_id','hypothesis_version','protocol_id','protocol_version','statement']
            put(top/'假设.csv',csv_bytes(hf,[dict(zip(hf,[name,'H1','v1','P','v1',name+' statement']))]))
            argv=('bind-literature',name,'--comparison',f'evidence/{name}-table.tsv','--relations',f'evidence/{name}-edge.csv')
            untouched=hashes(top); upstream=hashes(root/'evidence')
            if name=='beta':
                edge_path=root/'evidence/beta-edge.csv'; saved=edge_path.read_bytes()
                put(edge_path,saved.replace(b'fixture page 1',b''))
                expect('empty relation locator cannot be silently registered',
                       '定位' in desk(*argv,'--dry-run',code=1)['error'] and untouched==hashes(top))
                put(edge_path,saved)
            trial=desk(*argv,'--dry-run')
            expect(name+' binding preview has no side effects',trial['changed'] is False and untouched==hashes(top))
            live=desk(*argv); again=desk(*argv)
            expect(name+' binds valid tables once, never changes the original sources',
                   live['changed'] and not again['changed'] and live['edge_rows']==1 and upstream==hashes(root/'evidence'))
        expect('global references stay global and topic-only tables need no new global records',
               isinstance(read(folder/'alpha/接入.json')['literature']['comparison'],str)
               and isinstance(read(folder/'beta/接入.json')['literature']['comparison'],dict)
               and desk('overview')['additional_topic_tables']==2 and len(desk('sources')['sources'])==4)
        expect('does not silently replace an existing binding','不覆盖' in desk('bind-literature','beta','--comparison','evidence/alpha-table.tsv','--relations','evidence/alpha-edge.csv',code=1)['error'])
        sibling=folder/'alpha/private.tsv'; put(sibling,(root/'evidence/alpha-table.tsv').read_bytes())
        expect('cannot bind another dossier private table','另一课题' in desk('bind-literature','beta','--comparison',sibling,'--relations','evidence/beta-edge.csv','--dry-run',code=1)['error'])
        a,b=(desk('show',k)['dossier'] for k in ('alpha','beta'))
        expect('two local H1s and same protocol/version resolve to different files',
               a['hypotheses'][0]['statement']=='alpha statement' and b['hypotheses'][0]['statement']=='beta statement'
               and a['designs'][0]['path'] != b['designs'][0]['path'])
        bf=['problem_id','budget_id','planned_limit','actual_value','missing_reason']
        put(folder/'beta/预算.csv',csv_bytes(bf,[dict(zip(bf,['beta','B1','0','','not_checked'])),
                                                dict(zip(bf,['beta','B2','','','not_checked']))]))
        budget=desk('show','beta')
        expect('a recorded zero budget is not an unknown limit',
               budget['summary']['budget_items']==2 and budget['summary']['budget_limits_pending']==1
               and budget['dossier']['budgets'][0]['planned_limit']=='0')
        scoped=desk('trace','L01','--problem','beta')['problem_contexts']
        expect('paper trace respects topic scope and preserves relation review state',
               len(scoped)==1 and scoped[0]['comparisons'][0]['row_id']=='beta-L' and scoped[0]['relations'][0]['status']=='待审')
        ext=root/'evidence/beta-table.tsv'; ext_bytes=ext.read_bytes(); put(ext,ext_bytes+b'\n')
        expect('drifting topic-only source blocks only that scoped lookup',
               'hash mismatch' in desk('trace','L01','--problem','beta',code=1)['error'] and desk('trace','L01','--problem','alpha')['status']=='pass')
        put(ext,ext_bytes)
        alias=desk('trace','L09')
        expect('aliases stay linked but are not counted as evidence for another record',
               alias['version_group']['canonical_id']=='L01' and all(not c['comparisons'] for c in alias['problem_contexts']))
        expect('unknown paper is not a successful empty trace','没有此编号' in desk('trace','L99',code=1)['error'])
        a=folder/'alpha'; b=folder/'beta'; original=read(b/'接入.json')
        design=ref(a/'协议/P/v1/design.json',root); ebase=home/'数据/准入/E-fixture'
        put(ebase/'rules.json',{'schema_version':'eligibility-preview.v1','formal_use_allowed':False,
                               'problem_id':'alpha','eligibility_id':'E-fixture','design_ref':design})
        put(ebase/'manifest.json',{'schema_version':'eligibility-preview-manifest.v1','formal_use_allowed':False,'rules_ref':ref(ebase/'rules.json',root)})
        profile={'profile':'fixed-panel-preview.v1','design_ref':design,'eligibility_ref':ref(ebase/'manifest.json',root)}
        put(b/'接入.json',dict(original,g3=profile))
        _,checks=gate('--problem','beta')
        expect('real hashes on another topic draft cannot satisfy the selected gate','草稿归属' in checks['G3.topic_inputs']['reason'] and checks['G3.scientific_decisions']['status']=='blocked')
        put(b/'接入.json',dict(original,g3=dict(profile,design_ref=ref(b/'协议/P/v1/design.json',root))))
        expect('a different topic view cannot be attached to a valid local draft','其他课题' in desk('show','beta',code=1)['error'])
        put(b/'接入.json',original)
        put(a/'接入.json',dict(read(a/'接入.json'),g3=profile))
        scope,checks=gate('--problem','alpha')
        expect('properly bound candidate selects its own inputs; missing science still fails',
               scope['problem_id']=='alpha' and checks['G3.topic_inputs']['status']=='pass' and checks['G3.scientific_decisions']['status']=='fail')
        dec=b/'决策.json'; previous=read(dec); put(dec,dict(previous,problem_id='alpha'))
        expect('cross-topic decision register is rejected','决策归属' in desk('show','beta',code=1)['error']); put(dec,previous)
        paper_bytes=(root/'evidence/LEDGER.csv').read_bytes(); put(root/'evidence/LEDGER.csv',paper_bytes+b'\n')
        expect('registered evidence drift fails rather than displaying stale records','hash mismatch' in desk('trace','L01',code=1)['error'])
        put(root/'evidence/LEDGER.csv',paper_bytes)
        require(desk('overview','--check-view',code=1)['view']=='absent','expected unpublished workbench')
        desk('overview','--refresh','--dry-run')
        expect('refresh dry-run does not publish a view or create a lock',not (home/'工作台.md').exists() and not (home/'.workbench.lock').exists())
        desk('overview','--refresh')
        expect('refresh is repeatable and read-only freshness check agrees',desk('overview','--check-view')['view']=='current' and desk('overview','--refresh')['wrote'] is False)
        old=(b/'状态.md').read_text(); put(b/'状态.md',(old+'新增合成下一步。\n').encode())
        expect('source edit invalidates the displayed view',desk('overview','--check-view',code=1)['view']=='stale')
        desk('overview','--refresh')
        saved=(home/'工作台.md').read_bytes(); put(home/'工作台.md',b'# human source\n')
        expect('refuses to replace unrelated human-authored file','不覆盖' in desk('overview','--refresh',code=1)['error'] and (home/'工作台.md').read_bytes()==b'# human source\n')
        put(home/'工作台.md',saved)

        # Generate local synthetic transactions using the established logger/sync pipeline; no model or network.
        adapter=Suite(out); adapter.case='bundles'; store=home/'实验登记'
        smoke=adapter.bundle('smoke',problem='alpha'); adapter.start(smoke); adapter.sync(smoke,store)
        research=adapter.bundle('explore',problem='beta'); exp=read(research/'experiment.json')
        put(research/'experiment.json',dict(exp,run_kind='exploratory',platform='server'))
        rev0=adapter.start(research); adapter.sync(research,store)
        adapter.update(research,rev0,{'revision_kind':'progress','gpu_hours':0.0,'resource_accounting_status':'measured'})
        adapter.sync(research,store)
        view=desk('overview')['runs']
        expect('two revisions count as one run of the correct topic; smoke counted separately',
               view['registered_research_runs']==1 and view['smoke_or_fixture']==1 and view['by_problem']['beta']['exploratory']==1
               and view['by_problem']['alpha']['exploratory']==0 and view['index']=='matches_journal')
        index=store/'runs.jsonl'; put(index,b'{"fixture_uncommitted":true}\n')
        expect('an unbacked index cannot manufacture a research run',desk('overview')['runs']==dict(view,index='stale_or_unbacked'))
        pending=adapter.bundle('pending',problem='alpha'); adapter.start(pending)
        try:
            ingest(pending,store,fault='prepared')
        except ValueError as exc:
            require('injected crash after prepared' in str(exc),'wrong fault window')
        else:
            raise ValueError('fault window not reached')
        v=desk('overview')['runs']
        expect('prepared transaction is visible but never counted as a committed run',v['prepared_transactions']==1 and v['registered_research_runs']==1)
        write(out/'fixture_run_commands.json',adapter.commands)
        return 'pass'

    error=None
    try: status=exercise()
    except Exception as exc: status='fail'; error=f'{type(exc).__name__}: {exc}'
    write(out/'commands.json',commands)
    report={'schema_version':'research-desk-tests.v1','status':status,'checked_at':now(),'scope':'isolated synthetic workspace; no scientific approval',
            'cases':cases,'error':error,'commands':ref(out/'commands.json',out),
            'code_refs':[ref(p,actual) for p in [Path(__file__).resolve(),*(actual/'工具').glob('*.py'),actual/'规范/词表.json']]}
    write(out/'report.json',report)
    return cli(lambda:{'status':status,'passed':len(cases),'error':error,'report':str(out/'report.json')})


if __name__=='__main__':
    sys.exit(main())
