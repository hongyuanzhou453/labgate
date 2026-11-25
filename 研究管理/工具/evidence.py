"""Read registered authorities on demand. The ledger, comparisons and relations stay in place."""
from __future__ import annotations

import contextlib
from pathlib import Path

from common import lock, read, ref, require, under, verify_ref, write
from literature_index import LiteratureIndex
from topics import bindings, table


class Catalog:
    def __init__(self, root, registry):
        self.root = root
        require(isinstance(registry, dict) and registry.get('schema_version') == 'sources.v0.2'
                and isinstance(registry.get('entries'), list), '来源登记格式有误')
        self.entries = {e['source_id']: e for e in registry['entries']}
        require(len(self.entries) == len(registry['entries']), '重复的来源编号')
        self.mapping = read(root/'研究管理/规范/接入映射.json')

    def source(self, key):
        require(key in self.entries, f'未登记的来源：{key}')
        e = self.entries[key]
        binding = {'path': e['resolved_path'], 'sha256':e['sha256'], 'bytes':e['bytes']}
        path = verify_ref(binding, self.root)
        return path, {'source_id':key, **binding, 'source_kind':e['source_kind'],
                      'review_status': e['review_status'], 'check_scope':e['check_scope']}

    def ledger(self):
        path, source = self.source('SRC-LEDGER')
        rows = table(path, ('id','title','local_pdf','does_not_establish','establishes','evidence_note'))
        entries = {r['id']:r for r in rows}
        require(len(entries) == len(rows) and all(k.strip() for k in entries), '台账编号为空或重复')
        return entries, source

    def scoped_table(self, folder, path, kind):
        path = path.resolve()
        require(path.is_relative_to(self.root), '课题表必须在工作区内')
        parts = path.relative_to(self.root).parts
        require(not any(part.startswith('.') or part in {'_交付','__pycache__'} for part in parts),
                '交付夹具或隐藏文件不能登记为课题资料')
        topics = self.root/'研究管理'/'问题'
        require(not path.is_relative_to(topics) or path.is_relative_to(folder), '不能接入另一课题目录的表')
        require(path.is_file() and path.suffix == ('.tsv' if kind == 'comparison' else '.csv'),
                '比较表须为 TSV，关系边须为 CSV')
        return path

    def topic_source(self, folder, value, kind):
        if isinstance(value,str):
            path, rec = self.source(value)
        else:
            path = verify_ref(value,self.root)
            rec = dict(value,source_id=None,problem_id=folder.name,source_kind=kind,review_status='identity_only',
                       check_scope='课题直接引用；只核文件身份与结构。逐格核验和关系判断仍以表内记录为准。')
        return self.scoped_table(folder,path,kind), rec

    def comparison(self, folder, ledger, declaration=None):
        lit = declaration if declaration is not None else bindings(folder)['literature']
        if lit is None:
            return {'problem_id':folder.name, 'connected':False, 'comparisons':[], 'relations':[]}
        cp, cref = self.topic_source(folder,lit['comparison'],'comparison')
        ep, eref = self.topic_source(folder,lit['relations'],'relations')
        cols = self.mapping['compare_columns']
        comps = table(cp, cols, '\t',exact=True)
        require(all(all(v.strip() for v in r.values()) for r in comps), '比较表有空单元格')
        by_row = {r['row_id']: r['paper_id'] for r in comps}
        require(len(by_row) == len(comps), '同一课题比较行编号重复')
        rule = self.mapping['row_type_rule']
        project = rule['project_value']
        for r in comps:
            require(r['paper_id'] in ledger or r['paper_id'] == project, '比较行引用未知文献')
            r['row_type'] = rule['project_row_type'] if r[rule['field']] == project else rule['otherwise']
        edge_fields = ('from_id','to_id','relation','compare_rows','locator','status','basis')
        edges = table(ep, edge_fields)
        for e in edges:
            require(all(e[k].strip() for k in edge_fields), '关系边缺少类型、定位、依据或核验状态；未核请明示')
            endpoints = {e['from_id'],e['to_id']}
            require(all(i in ledger or i == project for i in endpoints), '关系边引用未知文献')
            ids = [k.strip() for k in e['compare_rows'].split(';')]
            require(all(k in by_row for k in ids) and endpoints <= {by_row[k] for k in ids},
                    '关系边无法追到本课题的对应比较行')
        return {'problem_id':folder.name,'connected':True,'comparisons':comps,'relations':edges,
                'comparison_source':cref,'relation_source':eref}

    def bind_literature(self, folder, comparison, relations, dry):
        def candidate():
            out = {}
            for kind, name in (('comparison',comparison),('relations',relations)):
                p = self.scoped_table(folder, self.root/Path(name), kind)
                rel = str(p.relative_to(self.root))
                existing = [k for k,e in self.entries.items() if e['resolved_path'] == rel]
                require(len(existing)<=1, '同一来源路径有多个登记编号')
                out[kind] = existing[0] if existing else ref(p,self.root)
            return out
        context = contextlib.nullcontext() if dry else lock(under(folder,'.bindings.lock'))
        with context:
            original = bindings(folder); pointer = candidate()
            existing = original['literature']
            require(existing is None or existing == pointer, '已有其他文献接入版本；不覆盖，需单独审阅换版')
            ledger,_ = self.ledger(); data = self.comparison(folder,ledger,pointer)
            # Check the same bytes again before publishing only the pointer, never the upstream tables.
            for k,v in pointer.items(): self.topic_source(folder,v,k)
            self.source('SRC-LEDGER')
            require(bindings(folder)==original,'接入声明在检查期间发生改变')
            if not dry and existing is None:
                write(folder/'接入.json',dict(original,literature=pointer),replace=True)
        return {'status':'pass','problem_id':folder.name,'literature':pointer,'dry_run':dry,
                'changed':not dry and existing is None,'comparison_rows':len(data['comparisons']),'edge_rows':len(data['relations'])}

    def version_groups(self, ledger):
        aliases, aref = self.source('SRC-ALIASES')
        groups = {}
        for g in read(aliases)['groups']:
            ids = [g['canonical_id'],*g['related_ids']]
            require(len(ids) == len(set(ids)) and all(i in ledger and i not in groups for i in ids),
                    '来源版本组重复或指向未知台账编号')
            groups.update(dict.fromkeys(ids, g))
        return groups, aref

    def full_text_index(self):
        ledger, source = self.ledger()
        groups, _ = self.version_groups(ledger)
        return LiteratureIndex(self.root, ledger, source['sha256'], groups)

    def literature_health(self):
        return self.full_text_index().health()

    def full_text(self, paper_id):
        return self.full_text_index().lookup(paper_id)

    def topic_literature(self, folder):
        index = self.full_text_index()
        context = self.comparison(folder, index.ledger)
        citations = {}
        for row in context['comparisons']:
            if row['row_type'] == self.mapping['row_type_rule']['otherwise']:
                citations.setdefault(row['paper_id'], []).append(row['row_id'])
        papers = []
        for paper_id, row_ids in sorted(citations.items()):
            relations = sum(paper_id in (e['from_id'],e['to_id']) for e in context['relations'])
            papers.append({'paper_id':paper_id, 'full_text':index.lookup(paper_id),
                           'comparison_row_ids':row_ids, 'relation_count':relations})
        exact = [p['full_text'] for p in papers if p['full_text']['status'] == 'exact_card']
        scopes = {s:[p['paper_id'] for p in papers if p['full_text']['status'] == s]
                  for s in ('not_indexed','related_version_only','unavailable')}
        return {'status':'pass', 'formal_use_allowed':False, 'problem_id':folder.name, 'connected':context['connected'],
                'library_status':index.health()['status'], 'papers':papers, **scopes,
                'coverage':{'cited_ledger_records':len(papers), 'exact_cards':len(exact) if index.available else None,
                            'verified_result_rows':sum(p['verified_result_count'] for p in exact) if index.available else None}}

    def trace(self, paper_id, folders):
        ledger, ledref = self.ledger()
        require(paper_id in ledger, f'台账没有此编号：{paper_id}')
        raw = ledger[paper_id]
        groups, aref = self.version_groups(ledger)
        index = LiteratureIndex(self.root, ledger, ledref['sha256'], groups)
        contexts = []
        for folder in folders:
            context = self.comparison(folder, ledger)
            context['comparisons'] = [r for r in context['comparisons'] if r['paper_id'] == paper_id]
            context['relations'] = [e for e in context['relations'] if paper_id in (e['from_id'],e['to_id'])]
            contexts.append(context)
        pdf = raw['local_pdf']
        if pdf:
            require(under(self.root, pdf).is_file(), f'台账登记的本地原件缺失：{paper_id}')
        return {'status':'pass','paper_id':paper_id,'record':raw,'ledger_source':ledref,
                'version_group':groups.get(paper_id),'version_source':aref,'problem_contexts':contexts,
                'full_text':index.lookup(paper_id),
                'scope':'按精确台账号列出引用；另列同源版本，不合计为独立证据。不重新审核文献结论或关系。'}
