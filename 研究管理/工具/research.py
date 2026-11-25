"""Daily file-based research desk: overview, show, trace, sources and new."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter

from common import cli, lock, read, require, under, write
from evidence import Catalog
from freeze_protocol import add_commands as add_freeze_commands, dispatch as dispatch_freeze
from make_report import init_week, render_week, verify_all_reports
from register_sources import root_path
from sync_runs import committed_records, transactions
from topics import create_topic, dossier, g3_inputs, local_file, select_topic, topic_dirs


MARKER = '<!-- generated: research.py overview; edit the linked sources -->'


def run_totals(home, problems):
    store = home/'实验登记'
    result = {'by_problem':{p:dict(exploratory=0,confirmatory=0) for p in problems},
              'registered_research_runs':0, 'smoke_or_fixture':0, 'prepared_transactions':0, 'index':'store_absent'}
    if not store.exists() and not store.is_symlink():
        return result
    require(store.is_dir() and not store.is_symlink(), '实验登记位置有误')
    journal = under(store, 'transactions'); index = under(store, 'runs.jsonl')
    require(not (store/'transactions').is_symlink() and not (store/'runs.jsonl').is_symlink(), '登记入口不得使用链接')
    require(not journal.exists() or journal.is_dir(), '同步事务位置不是目录')
    require(not any(p.is_symlink() for p in journal.glob('*.json')), '事务记录不得使用链接')
    before = transactions(store)
    rows, _ = committed_records(store)  # verifies revisions, hashes, parents and retained artifacts
    for r in rows.values():
        if r['run_kind'] == 'smoke' or r['platform'] == 'local_fixture':
            result['smoke_or_fixture'] += 1
            continue
        require(r['problem_id'] in result['by_problem'], '已提交研究运行指向未登记的课题：' + r['problem_id'])
        result['by_problem'][r['problem_id']][r['run_kind']] += 1
        result['registered_research_runs'] += 1
    result['prepared_transactions'] = sum(t['state']=='prepared' for _,t in before)
    if index.is_file():
        expected = ''.join(json.dumps(rows[k], ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n' for k in sorted(rows))
        result['index'] = 'matches_journal' if index.read_bytes() == expected.encode() else 'stale_or_unbacked'
    else:
        result['index'] = 'missing_after_commit' if rows else 'no_index_yet'
    require(before == transactions(store), '读取期间同步事务改变，请重试')
    return result


def compact(item):
    return {'problem_id':item['problem_id'],'title':item['title'],'state':item['state_path'],
            'hypothesis_versions':len(item['hypotheses']), 'draft_versions':len(item['designs']),
            'registered_decisions':len(item['decisions']), 'budget_items':len(item['budgets']),
            'budget_limits_pending':sum(b['planned_limit']=='' for b in item['budgets']),
            'next':item['sections']['下一步'], 'literature_connected':item['binding']['literature'] is not None,
            'g3_profile':(item['binding']['g3'] or {}).get('profile')}


def build_overview(root):
    home = root/'研究管理'; dirs = topic_dirs(home)
    catalog = Catalog(root, read(local_file(home,'来源登记.json')))
    kinds = Counter(catalog.source(k)[1]['source_kind'] for k in catalog.entries)
    projects = []; topic_paths = set()
    for folder in dirs.values():
        r = dossier(root,folder)
        if r['binding']['g3'] is not None:
            g3_inputs(root, folder)  # identity only; this does not approve or run a gate
        if r['binding']['literature'] is not None:
            for kind,v in r['binding']['literature'].items():
                _, locator = catalog.topic_source(folder,v,kind)
                topic_paths.add(locator['path'])
        projects.append(compact(r))
    return {'status':'pass','schema_version':'research-desk.v1','scope':'workspace_inventory',
            'topics':projects, 'registered_sources':len(catalog.entries),'sources_by_kind':dict(kinds),
            'additional_topic_tables':len(topic_paths - {e['resolved_path'] for e in catalog.entries.values()}),
            'runs':run_totals(home,dirs), 'reports':verify_all_reports(root, check_views=False),
            'formal_use_allowed':False}


def cell(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', ' '.join(text.split()))
    return re.sub(r'([\\|\x60*_\[\]~])', r'\\\1', html.escape(text, quote=False))


def render(data):
    runs = data['runs']; topics = data['topics']
    lines = ['# 科研管理工作台', '', MARKER, '',
             f"已登记 **{len(topics)} 个课题**、**{data['registered_sources']} 项全局来源文件**；"
             f"另有 {data['additional_topic_tables']} 份仅按课题接入的表。"
             f"从本地提交事务核到 **{runs['registered_research_runs']} 个研究运行**；试跑/夹具另有 {runs['smoke_or_fixture']} 个。"
             '这里的数字是登记情况，不能据此认定实验已获准或结果有效。','',
             '| 我要做什么 | 从哪里开始 |','| --- | --- |',
             '| 看整个系统建到哪了 | [建设路线、工作包依赖与下一步](建设路线.md) |',
             '| 复核协议或追查测试访问 | [冻结与测试访问契约](规范/W10b_冻结与测试访问_v1.md)，按课题查询跨协议版本的暴露记录。 |',
             '| 从课题查文献，或从文献追到课题 | [全文库联查命令](规范/全文库联查与状态核对_v1.md)按台账号返回主卡、核验状态、定量条目数及引用该文献的课题。 |',
             '| 一次检查阶段、工作台、周报与全文库 | [统一只读校验入口](工具/run_checks.py)，逐项返回结果及退出码。 |',
             '| 生成或核查承接式周报与下周计划 | [周报交付文件夹](../周报/)（默认形态，含相关材料）、[派生单文件镜像](派生/周报/)与[周报契约](规范/周报契约_v1.md)；命令见 `research.py report`。 |',
             '| 判断能否开跑、怎么登记运行 | [阶段要求](规范/阶段要求.json)与[运行包说明](README.md#运行包与同步)。 |',
             '', '## 课题卷宗', '',
             '| 课题 | 假设版本 / 设计草稿 | 带依据的决定 | 已提交研究运行 | 最近登记的下一步 |',
             '| --- | ---: | ---: | ---: | --- |']
    for t in topics:
        pid = t['problem_id']; count = sum(runs['by_problem'][pid].values())
        next_step = cell(t['next'])
        if len(next_step)>85: next_step = next_step[:84]+'…'
        lines.append(f"| [{cell(t['title'])}](问题/{pid}/状态.md) | {t['hypothesis_versions']} / {t['draft_versions']} | "
                     f"[{t['registered_decisions']}](问题/{pid}/决策.json) | {count} | {next_step} |")
    if not topics:
        lines.append('| 尚无已登记课题 | — | — | — | 尚未录入。 |')
    explanations = {'store_absent':'尚未建立运行登记目录','no_index_yet':'暂无派生索引',
                    'matches_journal':'派生索引与提交事务相符','missing_after_commit':'有已提交运行，派生索引缺失',
                    'stale_or_unbacked':'派生索引与提交事务不符，已按事务实算；请检查同步恢复'}
    lines += ['',f"运行登记：{explanations[runs['index']]}；{runs['prepared_transactions']} 笔尚未提交的事务不计入运行数。",
              '登记口径只覆盖本系统；旧实验、历史报告和交付目录的合成测试不自动转换成研究运行。','',
              '此页可重新生成。更新课题状态后，从工作区根目录执行：','', '~~~bash',
              '/opt/anaconda3/bin/python3 -B 研究管理/工具/research.py overview --refresh', '~~~','']
    return '\n'.join(lines).encode()


def overview(root, *, refresh=False, check=False, dry=False):
    home = root/'研究管理'; dest = home/'工作台.md'
    def inspect():
        require(not dest.is_symlink(), '工作台文件不得使用链接')
        data = build_overview(root); content = render(data)
        previous = dest.read_bytes() if dest.exists() else None
        state = 'absent' if previous is None else ('current' if previous == content else 'stale')
        return data,content,previous,state
    if refresh and not dry:
        with lock(under(home,'.workbench.lock')):
            data,content,previous,state = inspect()
            require(previous is None or MARKER.encode() in previous.splitlines()[:5], '目标不是已标识的派生工作台，不覆盖')
            require(render(build_overview(root)) == content, '生成期间输入改变，请重试')
            write(dest,content,replace=True)
            require(dest.read_bytes()==content,'工作台回读不一致')
        return dict(data,view='current',wrote=state!='current')
    data,_,_,state = inspect()
    return dict(data,view=state,wrote=False,**({'status':'fail','reason':'工作台未发布或已过期，请刷新'}
                                                if check and state!='current' else {}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op',required=True)
    add_freeze_commands(sub)
    board=sub.add_parser('overview',help='查全局现状；可刷新工作台'); mode=board.add_mutually_exclusive_group()
    mode.add_argument('--refresh',action='store_true'); mode.add_argument('--check-view',action='store_true')
    show=sub.add_parser('show',help='查一份课题卷宗'); show.add_argument('problem_id')
    trace=sub.add_parser('trace',help='顺着一个台账或来源编号追查'); trace.add_argument('identifier'); trace.add_argument('--problem')
    literature=sub.add_parser('topic-literature',help='按课题查看引用的台账号及本地全文卡覆盖')
    literature.add_argument('problem_id')
    health=sub.add_parser('literature-health',help='只读校验本地全文库及 Q1 身份')
    sources=sub.add_parser('sources',help='列出登记文件和核验范围')
    bind=sub.add_parser('bind-literature',help='原位接入本题比较表和关系边')
    bind.add_argument('problem_id'); bind.add_argument('--comparison',required=True); bind.add_argument('--relations',required=True)
    new=sub.add_parser('new',help='建立候选课题空卷宗'); new.add_argument('problem_id'); new.add_argument('--title',required=True)
    rep=sub.add_parser('report',help='列出、初始化、生成或校验承接式周报')
    rep.add_argument('report_op',choices=['list','check','render','init'])
    rep.add_argument('--week'); rep.add_argument('--from-week'); rep.add_argument('--from-markdown')
    rep.add_argument('--refresh',action='store_true'); rep.add_argument('--check-view',action='store_true')
    rep.add_argument('--advisor-en',action='store_true')
    for p in (board,show,trace,sources,new,bind,rep,literature,health): p.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(); root=root_path(); home=root/'研究管理'
    if args.op in ('freeze', 'exposure'):
        return dispatch_freeze(args, root)
    if args.op=='report':
        if args.report_op=='list': return verify_all_reports(root,check_views=False)
        if args.report_op=='check':
            return (render_week(root,args.week,refresh=False,check=True,dry=True)
                    if args.week else verify_all_reports(root,check_views=True))
        require(args.week is not None,'render/init 须指定 --week YYYY-Www')
        if args.report_op=='init':
            return init_week(root,args.week,from_week=args.from_week,from_markdown=args.from_markdown,dry=args.dry_run)
        return render_week(root,args.week,refresh=args.refresh,check=args.check_view,advisor_en=args.advisor_en,dry=args.dry_run)
    if args.op=='new': return create_topic(home,args.problem_id,args.title,args.dry_run)
    if args.op=='overview': return overview(root,refresh=args.refresh,check=args.check_view,dry=args.dry_run)
    if args.op=='show':
        folder=select_topic(home,args.problem_id); item=dossier(root,folder)
        if item['binding']['g3'] is not None: g3_inputs(root,folder)
        if item['binding']['literature'] is not None:
            catalog=Catalog(root,read(local_file(home,'来源登记.json')))
            for kind,v in item['binding']['literature'].items(): catalog.topic_source(folder,v,kind)
        return {'status':'pass','summary':compact(item),'dossier':item,'formal_use_allowed':False}
    catalog=Catalog(root,read(local_file(home,'来源登记.json')))
    if args.op=='topic-literature':
        return catalog.topic_literature(select_topic(home,args.problem_id))
    if args.op=='literature-health':
        library = catalog.literature_health()
        return {'status':'pass' if library['status']=='available' else 'fail',
                'library':library,'formal_use_allowed':False}
    if args.op=='sources': return {'status':'pass','sources':[catalog.source(k)[1] for k in catalog.entries]}
    if args.op=='bind-literature':
        return catalog.bind_literature(select_topic(home,args.problem_id),args.comparison,args.relations,args.dry_run)
    if args.identifier.startswith('SRC-'):
        require(args.problem is None,'SRC 为全局文件身份，不接受 --problem；L 编号可查询逐题使用情况')
        return {'status':'pass','source':catalog.source(args.identifier)[1]}
    folders = [select_topic(home,args.problem)] if args.problem is not None else topic_dirs(home).values()
    return catalog.trace(args.identifier,folders)


if __name__=='__main__':
    sys.exit(cli(main))
