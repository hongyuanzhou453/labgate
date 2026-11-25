"""通过公开 CLI 在两个合成卷宗验收文献联查、决定对账和门禁汇总。"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def table_bytes(columns, rows, delimiter=','):
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=columns, delimiter=delimiter, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def library_bytes(ledger_hash, cards, *, conflict=False):
    """Use the columns read from the v2 database. Rows and Markdown are synthetic."""
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE corpus_inventory (paper_id TEXT PRIMARY KEY, work_id TEXT,
              title TEXT, doi_or_url TEXT, status TEXT);
            CREATE TABLE papers (paper_id TEXT PRIMARY KEY, work_id TEXT, title TEXT,
              review_status TEXT, quantitative_check_status TEXT, result_count INTEGER,
              visual_verified_count INTEGER, text_verified_count INTEGER, pending_row_count INTEGER,
              card_path TEXT, card_sha256 TEXT, card_bytes INTEGER);
            CREATE TABLE results (id TEXT PRIMARY KEY, paper_id TEXT, work_id TEXT,
              check_status TEXT, card_sha256 TEXT);
            CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, name TEXT, en_name TEXT);
            CREATE TABLE paper_concepts (paper_id TEXT, concept_id TEXT);
        """)
        conn.executemany('INSERT INTO meta VALUES (?,?)',
                         [('schema_version', '2.0.0'), ('inputs_detail_json', json.dumps({'ledger': ledger_hash}))])
        conn.execute('INSERT INTO concepts VALUES (?,?,?)', ('C1', '合成概念', 'fixture concept'))
        for paper_id, (r, counts) in cards.items():
            title = 'Synthetic paper ' + paper_id
            if conflict and paper_id == 'L01':
                title = 'A different synthetic paper'
            conn.execute('INSERT INTO corpus_inventory VALUES (?,?,?,?,?)',
                         (paper_id, paper_id, title, '', 'formal_verified'))
            visual, text, pending = counts
            quantitative = 'pending' if pending else 'selected_results_verified'
            conn.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (paper_id, paper_id, title, 'full_text_reviewed', quantitative,
                          sum(counts), visual, text, pending, r['path'], r['sha256'], r['bytes']))
            conn.execute('INSERT INTO paper_concepts VALUES (?,?)', (paper_id, 'C1'))
            for i, status in enumerate(['visual_verified'] * visual + ['text_verified'] * text + ['pending'] * pending):
                conn.execute('INSERT INTO results VALUES (?,?,?,?,?)',
                             (f'{paper_id}-R{i + 1}', paper_id, paper_id, status, r['sha256']))
        conn.commit()
        return conn.serialize()


def main():
    actual = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() or args.output.is_symlink() or not out.parent.is_dir() or not out.parent.is_relative_to(actual/'_交付'):
        parser.error('请在已有交付轮目录下指定全新的测试子目录')
    if args.dry_run:
        print(json.dumps({'status': 'pass', 'writes': False, 'scope': 'two synthetic topics; read-only queries'}))
        return 0

    sys.path.insert(0, str(actual/'工具'))
    from common import cli, now, read, ref, require, sha, write

    out.mkdir()
    root = out/'workspace'
    home = root/'研究管理'
    problems = home/'问题'
    index = root/'论文内容索引'
    db = index/'literature.db'
    commands, cases = [], []
    code = sorted((actual/'工具').glob('*.py'))

    def put(path, value):
        write(path, value, replace=True)

    def inventory(base):
        return {str(p.relative_to(base)): (p.stat().st_mtime_ns, sha(p) if p.is_file() else 'dir')
                for p in base.rglob('*')}

    def command(tool, *argv, ok=True):
        before = inventory(root) if '--dry-run' in argv else None
        proc = subprocess.run([sys.executable, '-B', str(tool), *map(str, argv)], cwd=root,
                              capture_output=True, text=True, timeout=90)
        require(not proc.stderr, f'{tool.name} emitted stderr: {proc.stderr}')
        result = json.loads(proc.stdout)
        commands.append({'tool': str(tool), 'args': list(map(str, argv)), 'exit': proc.returncode, 'result': result})
        require(proc.returncode == (0 if ok else 1) and result['status'] == ('pass' if ok else 'fail'),
                f'command {len(commands)} unexpected status {proc.returncode}: {result.get("error")}')
        require(before is None or before == inventory(root), 'dry-run changed fixture bytes, metadata or directory entries')
        return result

    def desk(*argv, ok=True):
        return command(home/'工具/research.py', *argv, '--dry-run', ok=ok)

    def expect(name, condition):
        require(condition, 'failed check: ' + name)
        cases.append({'case': name, 'status': 'pass', 'through_command': len(commands)})

    def sources():
        for tool in code:
            write(home/'工具'/tool.name, tool.read_bytes())
        for name in ('接入映射.json', '阶段要求.json', '词表.json'):
            write(home/'规范'/name, (actual/'规范'/name).read_bytes())
        write(home/'AGENTS.md', b'# Synthetic workspace for build-06 CLI checks\n')
        problems.mkdir()
        columns = ['id', 'title', 'doi_or_url', 'local_pdf', 'establishes', 'does_not_establish', 'evidence_note']
        ledger = root/'evidence/Q1.csv'
        aliases = root/'evidence/aliases.json'
        write(ledger, table_bytes(columns, [dict(zip(columns, [k, 'Synthetic paper ' + k, '', '',
                                                               'synthetic observation', 'no scientific result', 'fixture']))
                                           for k in ('L01', 'L02', 'L03', 'L04')]))
        write(aliases, {'groups': [{'canonical_id': 'L01', 'related_ids': ['L03'], 'relation': 'fixture_versions'}]})
        entries = []
        for key, path in [('SRC-LEDGER', ledger), ('SRC-ALIASES', aliases)]:
            item = ref(path, root)
            entries.append({'source_id': key, 'resolved_path': item['path'], 'sha256': item['sha256'], 'bytes': item['bytes'],
                            'review_status': 'fixture', 'source_kind': 'fixture', 'check_scope': 'synthetic identity'})
        write(home/'来源登记.json', {'schema_version': 'sources.v0.2', 'entries': entries})
        cols = read(home/'规范/接入映射.json')['compare_columns']
        edge_cols = ['from_id','to_id','relation','compare_rows','locator','status','basis']
        for pid, ids in [('alpha', ['L01','L02','L03']), ('beta', ['L04','L01'])]:
            command(home/'工具/research.py', 'new', pid, '--title', f'合成课题 {pid}')
            folder = problems/pid
            comp, edge = folder/'comparison.tsv', folder/'relations.csv'
            rows = [dict.fromkeys(cols, 'fixture') | {'paper_id': k, 'row_id': f'{pid}-{i}'}
                    for i, k in enumerate([*ids, ids[0], '项目参照'])]
            write(comp, table_bytes(cols, rows, '\t'))
            write(edge, table_bytes(edge_cols, [dict(zip(edge_cols, [ids[0], '项目参照', 'comparison',
                                     rows[0]['row_id'] + ';' + rows[-1]['row_id'], 'p. 1', '待审', 'fixture']))]))
            command(home/'工具/research.py', 'bind-literature', pid, '--comparison', comp, '--relations', edge)
        return ledger

    def check_links(ledger):
        missing = desk('trace', 'L01')['full_text']
        expect('absent library keeps Q1 identity with unknown card and count',
               missing['status'] == 'unavailable' and missing['has_card'] is None and missing['result_count'] is None)
        topic = desk('topic-literature', 'alpha')
        expect('absent library cannot establish zero coverage',
               set(topic['unavailable']) == {'L01','L02','L03'} and topic['coverage']['exact_cards'] is None
               and topic['coverage']['verified_result_rows'] is None and not index.exists())
        unavailable = desk('literature-health',ok=False)['library']
        expect('optional health explicitly rejects a missing database without creating it',
               unavailable['status'] == 'unavailable' and unavailable['paper_count'] is None and not index.exists())
        cards = {}
        for pid, counts in [('L01',(1,1,0)), ('L04',(0,1,1))]:
            path = index/'papers'/f'{pid}.md'
            meta = {'paper_id':pid, 'work_id':pid, 'title':'Synthetic paper '+pid,
                    'review_status':'full_text_reviewed',
                    'quantitative_check_status':'pending' if counts[2] else 'selected_results_verified'}
            write(path, ('---\n' + json.dumps(meta, indent=2) + '\n---\n# Synthetic card\n').encode())
            cards[pid] = ref(path, root), counts
        original = library_bytes(sha(ledger), cards)
        put(db, original)
        db.chmod(0o444)
        health = desk('literature-health')['library']
        expect('read-only SQLite reports real paper and verified result counts',
               health['status'] == 'available' and (health['paper_count'], health['result_count'],
               health['verified_result_count']) == (2,4,3))
        traced = desk('trace', 'L01')
        item = traced['full_text']
        expect('Q1 identity links to the exact card, review state, verified rows and concept',
               traced['record']['title'] == 'Synthetic paper L01' and item['status'] == 'exact_card'
               and item['has_card'] is True and item['card_path'] == cards['L01'][0]['path']
               and item['card_sha256'] == cards['L01'][0]['sha256'] and item['review_status'] == 'full_text_reviewed'
               and item['result_count'] == item['verified_result_count'] == 2 and
               item['result_status_counts']['text_verified'] == item['result_status_counts']['visual_verified'] == 1
               and [c['concept_id'] for c in item['concepts']] == ['C1'])
        scoped = desk('trace', 'L01', '--problem', 'beta')['problem_contexts']
        expect('reverse query reports both actual uses globally and only selected topic rows when scoped',
               {c['problem_id'] for c in traced['problem_contexts'] if c['comparisons']} == {'alpha','beta'}
               and len(scoped) == 1 and scoped[0]['problem_id'] == 'beta'
               and [r['row_id'] for r in scoped[0]['comparisons']] == ['beta-1'])
        a, b = (desk('topic-literature', p) for p in ('alpha','beta'))
        ap = {r['paper_id']:r for r in a['papers']}
        expect('forward query deduplicates comparison rows and excludes project and sibling-only records',
               set(ap) == {'L01','L02','L03'} and len(a['papers']) == 3 and
               set(ap['L01']['comparison_row_ids']) == {'alpha-0','alpha-3'} and ap['L01']['relation_count'] == 1
               and {r['paper_id'] for r in b['papers']} == {'L01','L04'} and len(b['papers']) == 2)
        expect('version group and absent Q1 records do not inherit exact-card counts',
               set(a['not_indexed']) == {'L02'} and set(a['related_version_only']) == {'L03'}
               and a['coverage']['exact_cards'] == 1 and a['coverage']['verified_result_rows'] == 2
               and b['coverage']['verified_result_rows'] == 3)
        related = desk('trace', 'L03')['full_text']
        absent = desk('trace', 'L02')['full_text']
        expect('unindexed paper stays traceable and same-work card is explicitly a different version',
               related['status'] == 'related_version_only' and related['card_path'] is None
               and related['related_card']['paper_id'] == 'L01' and absent['status'] == 'not_indexed'
               and absent['has_card'] is False and absent['card_path'] is None)
        expect('unknown ledger record fails', bool(desk('trace','L90',ok=False)['error']))
        pointer = problems/'beta/接入.json'
        saved = read(pointer)
        put(pointer, dict(saved, literature=read(problems/'alpha/接入.json')['literature']))
        expect('another topic private comparison and relation files cannot be attached',
               '另一课题' in desk('topic-literature','beta',ok=False)['error'])
        put(pointer, saved)
        put(db, library_bytes(sha(ledger), cards, conflict=True))
        expect('Q1 title and database title conflict rejects both query directions',
               bool(desk('trace','L01',ok=False)['error']) and bool(desk('topic-literature','alpha',ok=False)['error']))
        put(db, b'Synthetic invalid database bytes\n')
        expect('present but malformed SQLite fails rather than claiming no entries',
               bool(desk('trace','L01',ok=False)['error']) and bool(desk('literature-health',ok=False)['error']))
        put(db, original)
        db.chmod(0o444)
        expect('queries preserve database bytes and leave no SQLite sidecars',
               db.read_bytes() == original and not any(index.glob('literature.db-*')))

    def state_gate():
        result = command(home/'工具/check_all.py','--stage','G1','--dry-run',ok=False)
        return next(c for c in result['checks'] if c['check'] == 'V19.state')

    def check_card_status():
        state = problems/'beta/状态.md'
        original = state.read_text()
        heading = '## 支持证据\n'
        require(original.count(heading) == 1, 'missing synthetic state section')
        def cite(citations):
            put(state,original.replace(heading,heading+'\n'+citations+'\n').encode())
            return state_gate()
        good = cite('- [主卡 L01] 已核验\n- [主卡 L04] 待审')
        expect('state permits an exact verified card and explicitly pending quantitative evidence', good['status'] == 'pass')
        expect('state rejects pending card promoted to verified', cite('- [主卡 L04] 已核验')['status'] == 'fail')
        expect('state rejects a related-version card promoted to exact evidence', cite('- [主卡 L03] 已核验')['status'] == 'fail')
        put(state,original.encode())

    def check_decisions():
        a, b = problems/'alpha', problems/'beta'
        expect('legacy empty register has zero confirmed choices', desk('show','alpha')['summary']['registered_decisions'] == 0)
        state = a/'状态.md'
        empty = state.read_text()
        default = '尚无已确认研究决定。'
        require(empty.count(default) == 1, 'unexpected synthetic state template')
        put(state, empty.replace(default, '导师已决定采用预登记阈值。').encode())
        expect('affirmative decision in state without register is rejected', bool(desk('show','alpha',ok=False)['error']))
        put(state, empty.encode())
        ack = a/'确认/D1.json'
        write(ack, {'problem_id':'alpha','fixture_only':True,'decision_id':'D1','confirmed_by':'合成导师'})
        ack_ref = ref(ack,root)
        record = {'decision_id':'D1','problem_id':'alpha','confirmed_by':'合成导师','date':'2026-11-24',
                  'basis':'合成依据','statement':'采用预登记阈值','confirmation_ref':ack_ref,
                  'protocol_id':'P','protocol_version':'v1','decision_key':'effect_threshold','value':0}
        decision_path = a/'决策.json'
        reg = read(decision_path)
        draft_path = a/'协议/P/v1/design.json'
        draft = {'schema_version':'design-draft.v0.2','protocol_id':'P','version':'v1','problem_id':'alpha',
                 'status':'draft','formal_use_allowed':False,'hypotheses':[{'hypothesis_id':'H1','hypothesis_version':'v1'}],
                 'decisions':{'effect_threshold':0},'decision_refs':[ack_ref]}
        hcols = ['problem_id','hypothesis_id','hypothesis_version','protocol_id','protocol_version','statement','decision_ref']
        h = dict(zip(hcols,['alpha','H1','v1','P','v1','合成假设','D1']))
        write(a/'假设.csv', table_bytes(hcols,[dict(h,decision_ref='')]))
        write(draft_path,draft)
        expect('draft with real bytes and no registered decision fails', bool(desk('show','alpha',ok=False)['error']))
        put(decision_path,dict(reg,decisions=[record]))
        state_line = '- [决策 D1] 2026-11-24 | 采用预登记阈值 | 依据=合成依据 | 确认人=合成导师'
        confirmed = empty.replace(default,state_line)
        put(state,confirmed.encode())
        put(a/'假设.csv',table_bytes(hcols,[h]))
        expect('state, hypothesis and zero-valued threshold link to this topic external evidence',
               desk('show','alpha')['summary']['registered_decisions'] == 1 and
               desk('show','beta')['summary']['registered_decisions'] == 0)
        put(state,confirmed.replace('依据=合成依据','依据=另一份依据').encode())
        expect('state basis drift is rejected', bool(desk('show','alpha',ok=False)['error']))
        put(state,confirmed.encode())
        put(draft_path,dict(draft,decisions={'effect_threshold':False}))
        expect('changing numerical zero to boolean is detected', bool(desk('show','alpha',ok=False)['error']))
        put(draft_path,draft)
        put(a/'假设.csv',table_bytes(hcols,[dict(h,decision_ref='D-unknown')]))
        expect('unknown hypothesis decision reference fails', bool(desk('show','alpha',ok=False)['error']))
        put(a/'假设.csv',table_bytes(hcols,[h]))
        saved = ack.read_bytes()
        put(ack,saved+b'\n')
        expect('confirmation byte drift is rejected', bool(desk('show','alpha',ok=False)['error']))
        put(ack,saved)
        other = b/'确认/D1.json'
        write(other, {'problem_id':'beta','fixture_only':True})
        other_ref = ref(other, root)
        put(decision_path,dict(reg,decisions=[dict(record,confirmation_ref=other_ref)]))
        put(draft_path,dict(draft,decision_refs=[other_ref]))
        expect('sibling confirmation with correct local hash fails V19 even when draft repeats it',
               state_gate()['status'] == 'fail' and bool(desk('show','alpha',ok=False)['error']))
        put(draft_path,draft)
        put(decision_path,dict(reg,decisions=[record]))
        state_check = state_gate()
        expect('state gate verifies restored local register although global research inputs are absent',
               state_check['status'] == 'pass' and
               {t['problem_id']:t['recorded_decisions'] for t in state_check['detail']['topics']} == {'alpha':1,'beta':0})

    def check_runner():
        command(home/'工具/research.py','overview','--refresh')
        runtime = root/'missing-runtime.json'
        args = ['--runtime-report',runtime,'--problem','alpha','--literature','--dry-run']
        real = command(home/'工具/run_checks.py',*args,ok=False)
        checks = {c['check']:c for c in real['checks']}
        expect('real aggregate returns every requested check and propagates stage failures',
               len(checks) == len(real['checks']) == 6 and set(checks) == {'G1','G2','G3','overview','report','literature'}
               and real['formal_use_allowed'] is False and checks['G2']['exit_code'] == checks['G3']['exit_code'] == 1
               and all(checks[k]['exit_code'] == 0 for k in ('overview','report','literature'))
               and checks['G3']['result']['scope']['problem_id'] == 'alpha')
        stubs = root/'runner-dispatch-fixture/研究管理/工具'
        write(stubs/'run_checks.py',(home/'工具/run_checks.py').read_bytes())
        # Imports for the real dispatcher. Only its downstream CLI processes are substitutes.
        for path in code:
            if path.name not in {'run_checks.py','research.py','check_all.py','make_report.py'}:
                write(stubs/path.name,path.read_bytes())
        child = b"import json, sys\nprint(json.dumps({'status':'pass','formal_use_allowed':False,'args':sys.argv[1:]}))\n"
        for name in ('research.py','check_all.py','make_report.py'):
            write(stubs/name,child)
        passed = command(stubs/'run_checks.py',*args)
        pass_checks = {c['check']:c for c in passed['checks']}
        expect('dispatcher only: all children pass, stages selected and dry-run forwarded',
               set(pass_checks) == set(checks) and all(c['exit_code'] == 0 and c['status'] == 'pass'
               and '--dry-run' in c['result']['args'] for c in pass_checks.values())
               and all(pass_checks[k]['result']['args'][pass_checks[k]['result']['args'].index('--stage')+1] == k
                       for k in ('G1','G2','G3')) and passed['formal_use_allowed'] is False)
        failed_child = child + b"sys.exit(7 if 'G2' in sys.argv else 0)\n"
        put(stubs/'check_all.py',failed_child)
        rejected = command(stubs/'run_checks.py',*args,ok=False)
        failed = {c['check']:c for c in rejected['checks'] if c['status'] == 'fail'}
        expect('dispatcher preserves a nonzero child exit even when stdout declares pass',
               set(failed) == {'G2'} and failed['G2']['exit_code'] == 7 and len(rejected['checks']) == 6)
        projection = command(stubs/'run_checks.py','--stage','G1','--dry-run')
        expect('G1 projection omits unrequested scientific and literature stages',
               {c['check'] for c in projection['checks']} == {'G1','overview','report'})

    error = None
    try:
        ledger = sources()
        probe = out.with_name(out.name+'-dry-probe')
        require(not probe.exists(), 'choose a fresh output and dry-run probe')
        command(Path(__file__).resolve(),'--output',probe,'--dry-run')
        expect('verification dry-run leaves its requested path and locks absent', not probe.exists())
        check_links(ledger)
        check_decisions()
        check_card_status()
        check_runner()
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    status = 'fail' if error else 'pass'
    write(out/'commands.json',commands)
    result = {'schema_version':'links-and-checks-tests.v1','checked_at':now(),'status':status,
              'scope':'two synthetic topics; v2 SQLite projections; public CLI and isolated dispatcher transport',
              'formal_use_allowed':False,'cases':cases,'error':error,'commands':ref(out/'commands.json',out),
              'code_refs':[ref(p,actual) for p in [Path(__file__).resolve(),*code,actual/'规范/词表.json']]}
    write(out/'report.json',result)
    return cli(lambda:{'status':status,'passed':len(cases),'error':error,'report':str(out/'report.json')})


if __name__ == '__main__':
    sys.exit(main())
