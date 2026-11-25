"""Discover file-based research dossiers and check references within their topic."""
from __future__ import annotations

import csv
import os
import re
import uuid
from datetime import date

from common import encoded, lock, read, require, safe_id, under, verify_ref, write


SECTIONS = ('当前判断', '支持证据', '反对证据', '资源缺口', '实验草案', '决策记录', '需领域确认', '待决定', '下一步')


def topic_dirs(home):
    root = home / '问题'
    require(root.is_dir() and not root.is_symlink(), '缺少真实的问题目录')
    result = {}
    for p in sorted(root.iterdir()):
        if p.name.startswith('.'):  # unpublished creation staging; never a registered topic
            continue
        require(p.is_dir() and not p.is_symlink(), f'问题目录包含非课题项或链接：{p.name}')
        key = safe_id(p.name).casefold()
        require(key not in result, f'课题编号大小写冲突：{p.name}')
        result[key] = p
    return {p.name: p for p in result.values()}


def select_topic(home, requested):
    dirs = topic_dirs(home)
    if requested is None:
        require(len(dirs) == 1, '请用 --problem 指定一个课题；当前登记：' + (', '.join(dirs) or '无'))
        return next(iter(dirs.values()))
    safe_id(requested)
    require(requested in dirs, f'未登记的课题：{requested}')
    return dirs[requested]


def local_file(folder, name):
    p = under(folder, name)
    require(p == folder / name and p.is_file(), f'文件缺失或使用路径别名：{folder.name}/{name}')
    return p


def table(path, required, delimiter=',', *, exact=False):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=delimiter, strict=True)
        result = []
        try:
            cols = reader.fieldnames or []
            require(set(required) <= set(cols) and len(cols) == len(set(cols)), f'列名缺失或重复：{path.name}')
            require(not exact or list(required) == cols, f'表头或顺序与规范不符：{path.name}')
            for row in reader:
                require(None not in row and all(v is not None for v in row.values()), f'列数有误：{path.name}:{reader.line_num}')
                result.append(row)
        except csv.Error as exc:
            raise ValueError(f'表格格式错误：{path.name}:{reader.line_num}: {exc}') from exc
    return result


def parse_state(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    require(lines and lines[0].startswith('# ') and lines[0][2:].strip(), f'缺少课题标题：{path}')
    starts = [(i, s[3:]) for i, s in enumerate(lines) if s.startswith('## ')]
    require(tuple(s for _, s in starts) == SECTIONS, f'状态九节缺失、重复或顺序不符：{path}')
    sections = {name: '\n'.join(lines[i+1:end]).strip()
                for (i, name), end in zip(starts, [i for i, _ in starts[1:]] + [len(lines)])}
    require(all(sections.values()), f'状态小节不能空白，未核应明确说明：{path}')
    return lines[0][2:].strip().removesuffix('：当前状态'), sections


def bindings(folder):
    item = read(local_file(folder, '接入.json'))
    require(isinstance(item, dict) and item.get('schema_version') == 'topic-bindings.v1'
            and item.get('problem_id') == folder.name and {'literature', 'g3'} <= item.keys(),
            f'课题接入声明归属或格式有误：{folder.name}')
    lit = item['literature']
    def is_source(v):
        return ((isinstance(v, str) and v.startswith('SRC-')) or
                (isinstance(v, dict) and set(v) == {'path','sha256','bytes'} and type(v['bytes']) is int))
    require(lit is None or (isinstance(lit, dict) and set(lit) == {'comparison', 'relations'} and
            all(is_source(v) for v in lit.values()) and lit['comparison'] != lit['relations']),
            '文献表需要两份不同的来源编号或文件引用')
    g3 = item['g3']
    require(g3 is None or (isinstance(g3, dict) and set(g3) == {'profile', 'design_ref', 'eligibility_ref'}
            and isinstance(g3['profile'], str)), 'G3 接入字段不完整')
    return item


def draft_identity(path, folder):
    draft = read(path)
    require(isinstance(draft, dict) and draft.get('schema_version') == 'design-draft.v0.2'
            and draft.get('problem_id') == folder.name and draft.get('status') == 'draft'
            and draft.get('formal_use_allowed') is False, f'草稿归属或状态有误：{path}')
    proto, version = safe_id(draft['protocol_id']), safe_id(draft['version'])
    require(path == local_file(folder, f'协议/{proto}/{version}/design.json'), '协议身份与目录不一致')
    return draft


def decision_register(root, folder):
    register = read(local_file(folder, '决策.json'))
    pid = folder.name
    require(isinstance(register, dict) and register.get('schema_version') == 'decisions.v1'
            and register.get('problem_id') == pid and isinstance(register.get('decisions'), list), f'决策归属有误：{pid}')
    seen = set()
    for decision in register['decisions']:
        require(isinstance(decision, dict) and decision.get('problem_id', pid) == pid
                and isinstance(decision.get('confirmed_by'), str) and decision['confirmed_by'].strip(),
                f'决策缺少确认人或跨课题：{pid}')
        source = decision.get('confirmation_ref')
        path = verify_ref(source, root)
        require(str(path.relative_to(root)) == source['path']
                and (not path.is_relative_to(folder.parent) or path.is_relative_to(folder)),
                '不能借用其他课题的确认原件或路径别名')
        reserved = {'决策.json', '假设.csv', '状态.md', '预算.csv', '接入.json', 'AGENTS.md', '测试暴露.head.json'}
        require(not (path.parent == folder and path.name in reserved)
                and not any(path.is_relative_to(folder / part) for part in ('协议', '测试暴露'))
                and not path.is_relative_to(root/'研究管理'/'_交付'), '需要独立的外置人工确认原件')
        if path.suffix.lower() == '.json':
            evidence = read(path)
            if isinstance(evidence, dict):
                require(evidence.get('problem_id', pid) == pid, '外置确认原件属于其他课题')
                require(evidence.get('confirmed_by', decision['confirmed_by']) == decision['confirmed_by'],
                        '确认人与原件不符')
        if not path.is_relative_to(folder):
            for other in folder.parent.glob('*/决策.json'):
                if other.parent != folder:
                    for row in read(other)['decisions']:
                        require(under(root, row['confirmation_ref']['path']) != path, '跨题重复登记同一外置确认')
        if 'decision_id' not in decision:
            require(not {'decision_key','hypothesis_id','hypothesis_version','protocol_id','protocol_version'}
                    .intersection(decision), '逐项决定缺少 decision_id')
            continue  # Original W10b confirmation-only row.
        key = safe_id(decision['decision_id'])
        require(key not in seen, f'决策编号重复：{pid}/{key}')
        seen.add(key)
        for field in ('statement', 'basis', 'date'):
            require(isinstance(decision.get(field), str) and decision[field].strip(), f'决策缺少 {field}：{key}')
        require(date.fromisoformat(decision['date']).isoformat() == decision['date'], '决策日期须为 YYYY-MM-DD')
        for first, second in (('protocol_id', 'protocol_version'), ('hypothesis_id', 'hypothesis_version')):
            if first in decision or second in decision:
                safe_id(decision.get(first))
                safe_id(decision.get(second))
        if 'decision_key' in decision or 'value' in decision:
            require('protocol_id' in decision and isinstance(decision.get('decision_key'), str)
                    and decision['decision_key'].strip() and decision.get('value') is not None,
                    '草稿字段决定缺少协议、字段或已确认的值')
    return register['decisions']


STATE_DECISION = re.compile(r'- \[决策 ([A-Za-z0-9.-]+)\] (\d{4}-\d{2}-\d{2}) \| (.+?) \| 依据=(.+?) \| 确认人=(.+)')
CARD_LINE = re.compile(r'- \[主卡 (L[0-9]+)\] (已核验|待审)')
AFFIRMATION = re.compile(r'已(?:经)?(?:确认|决定|确定|批准|选定|敲定|拍板)|'
                         r'(?:导师|用户|研究者|课题组)(?:正式)?(?:决定|批准|拍板|敲定)|'
                         r'(?:决定|确认)(?:采用|选用|使用|选定)|最终(?:采用|确定|选定)')


def affirmative_claim(text):
    # Ignore mentions of headings, negations, and questions about future decisions.
    text = re.sub(r'(?:旧?标题|栏目|字样)[“「"]已确认决定[”」"]', '', text)
    for clause in re.split(r'[。；;!?！？\n]|但是|但', text):
        for match in AFFIRMATION.finditer(clause):
            before = clause[:match.start()]
            if re.search(r'(?:尚无|并无|暂无|没有|并非|不是|不等于|不能|无法|未|是否|尚待|等候|需要|待|请)'
                         r'(?:任何|已|被|作为|由|的|相关|一项|科研|研究|正式|再次|重新|导师|用户|人工|\s)*$', before):
                continue
            return True
    return False


def state_references(sections, by_id):
    declared, cards = set(), []
    for section, text in sections.items():
        for raw in text.splitlines():
            line = raw.strip()
            if '[决策 ' in line:
                m = STATE_DECISION.fullmatch(line)
                require(section == '决策记录' and m, '决策行须在决策记录节列日期、陈述、依据、确认人')
                key = m[1]
                decision = by_id.get(key)
                require(decision is not None and key not in declared and m.groups()[1:] ==
                        tuple(decision[k] for k in ('date','statement','basis','confirmed_by')),
                        '状态决策未登记、重复或与本题登记不一致')
                declared.add(key)
            elif '[主卡 ' in line or re.search(r'论文内容索引/papers/L\d+\.md', line):
                m = CARD_LINE.fullmatch(line)
                require(section in ('支持证据', '反对证据') and m, '主卡引用须逐行标注已核验或待审')
                cards.append({'paper_id': m[1], 'status': m[2], 'section': section})
            elif affirmative_claim(line):
                raise ValueError('状态中的肯定决定须在决策记录节逐条引用本题决策编号')
    return {'confirmed_state_lines':len(declared), 'card_refs':cards}


def decision_scope(decision, protocol, hypothesis=None):
    if 'protocol_id' in decision:
        require((decision['protocol_id'], decision['protocol_version']) == protocol, '决策引用属于其他协议版本')
    if hypothesis is not None and 'hypothesis_id' in decision:
        require((decision['hypothesis_id'], decision['hypothesis_version']) == hypothesis, '决策引用属于其他假设版本')


def check_draft_decisions(root, draft, decisions):
    protocol = (draft['protocol_id'], draft['version'])
    values = draft.get('decisions', {})
    refs = draft.get('decision_refs', [])
    require(isinstance(values, dict) and isinstance(refs, list), '草稿决定或引用格式错误')
    available = []
    for item in refs:
        verify_ref(item, root)
        matched = [d for d in decisions if all(d['confirmation_ref'][k] == item[k] for k in ('path', 'sha256'))]
        require(matched, '草稿外置确认未在本题登记')
        available.extend(d for d in matched if 'protocol_id' not in d
                         or (d['protocol_id'], d['protocol_version']) == protocol)
        require(any(d in available for d in matched), '草稿确认仅属于其他协议版本')
    for field, value in values.items():
        if value is not None:
            require(any(d.get('decision_key') == field and encoded(d['value']) == encoded(value) for d in available),
                    f'草稿决定缺少本题同版本、同字段及同值的登记：{field}')
    for note in ('notes', 'note'):
        if isinstance(draft.get(note), str):
            require(not affirmative_claim(draft[note]), '草稿备注中的肯定决定请逐项登记在 decisions')


def dossier(root, folder):
    pid = folder.name
    title, sections = parse_state(local_file(folder, '状态.md'))
    decisions = decision_register(root, folder)
    decisions_by_id = {d['decision_id']: d for d in decisions if 'decision_id' in d}
    state_audit = state_references(sections, decisions_by_id)
    connected = bindings(folder)
    hypotheses, budgets, designs = [], [], {}
    drafts = {}
    if (folder/'协议').exists():
        require((folder/'协议').is_dir() and not (folder/'协议').is_symlink(), '协议目录不合法')
        for p in sorted((folder/'协议').glob('*/*/design.json')):
            d = draft_identity(p, folder)
            key = (d['protocol_id'],d['version'])
            drafts[key] = d
            check_draft_decisions(root, d, decisions)
            hs = d.get('hypotheses')
            require(isinstance(hs, list), '草稿缺少假设列表')
            ids = [(safe_id(h['hypothesis_id']),safe_id(h['hypothesis_version'])) for h in hs]
            require(len(set(ids)) == len(ids), f'草稿重复引用同一假设版本：{pid}/{key}')
            designs[key] = {'protocol_id':key[0], 'version':key[1], 'path':str(p.relative_to(root)), 'hypotheses':ids}
    if (folder/'假设.csv').exists():
        hypotheses = table(local_file(folder, '假设.csv'),
                           ('problem_id','hypothesis_id','hypothesis_version','protocol_id','protocol_version','statement'))
    by_id = {}
    for h in hypotheses:
        key = safe_id(h['hypothesis_id']), safe_id(h['hypothesis_version'])
        require(h['problem_id'] == pid and h['statement'].strip() and key not in by_id, f'假设重复、缺内容或跨课题：{pid}/{key}')
        proto = (h['protocol_id'], h['protocol_version'])
        require(proto == ('', '') or (proto in designs and key in designs[proto]['hypotheses']),
                f'假设无法连回本题对应的设计版本：{pid}/{key}')
        by_id[key] = h
    require(all(h in by_id for d in designs.values() for h in d['hypotheses']), f'草稿引用未登记的本题假设：{pid}')
    for key, row in by_id.items():
        proto = (row['protocol_id'], row['protocol_version'])
        refs = [row.get('decision_ref', '')]
        status = [row.get('status')]
        if proto in drafts:
            item = next(h for h in drafts[proto]['hypotheses']
                        if (h['hypothesis_id'], h['hypothesis_version']) == key)
            refs.append(item.get('decision_ref', ''))
            status.append(item.get('status'))
        ids = {v for v in refs if v}
        require(len(ids) <= 1 and ids <= decisions_by_id.keys(), '假设决定未登记或与草稿引用不一致')
        if any(s in ('confirmed', 'approved', '已确认', '已批准') for s in status):
            require(ids, '已确认假设缺少本题决定编号')
        for v in ids:
            decision_scope(decisions_by_id[v], proto, key)
    for decision in decisions_by_id.values():
        if 'protocol_id' in decision:
            proto = (decision['protocol_id'], decision['protocol_version'])
            require(proto in drafts, '登记决定引用未知协议版本')
            if 'decision_key' in decision:
                draft = drafts[proto]
                require(encoded(draft.get('decisions', {}).get(decision['decision_key'])) == encoded(decision['value'])
                        and any(all(r[k] == decision['confirmation_ref'][k] for k in ('path','sha256'))
                                for r in draft.get('decision_refs', [])), '登记决定未与对应协议字段及原件双向关联')
        if 'hypothesis_id' in decision:
            key = (decision['hypothesis_id'], decision['hypothesis_version'])
            require(key in by_id and by_id[key].get('decision_ref') == decision['decision_id'],
                    '登记决定未与指定假设行双向关联')
    if (folder/'预算.csv').exists():
        budgets = table(local_file(folder, '预算.csv'), ('problem_id','budget_id','planned_limit','actual_value','missing_reason'))
        require(all(b['problem_id'] == pid and b['budget_id'].strip() for b in budgets)
                and len({b['budget_id'] for b in budgets}) == len(budgets), f'预算身份重复或跨课题：{pid}')
    return {'problem_id':pid, 'title':title, 'sections':sections, 'hypotheses':hypotheses,
            'designs':list(designs.values()), 'budgets':budgets, 'decisions':decisions, 'state_audit':state_audit,
            'binding':connected, 'state_path':str((folder/'状态.md').relative_to(root))}


def g3_inputs(root, folder):
    g = bindings(folder)['g3']
    require(g is not None, f'课题 {folder.name} 尚未接入 G3 的设计和用途视图')
    require(g['profile'] == 'fixed-panel-preview.v1', f'尚未实现该 G3 检查方案：{g["profile"]}')
    design_path, manifest_path = (verify_ref(g[k], root) for k in ('design_ref','eligibility_ref'))
    draft = draft_identity(design_path, folder)
    base = root/'研究管理'/'数据'/'准入'
    require(manifest_path.parent.parent == base and manifest_path.name == 'manifest.json', '非准入目录中的用途视图')
    manifest = read(manifest_path)
    require(isinstance(manifest, dict) and manifest.get('schema_version') == 'eligibility-preview-manifest.v1'
            and manifest.get('formal_use_allowed') is False, '用途视图必须是已知类型的非正式预览')
    rules_path = verify_ref(manifest['rules_ref'], root)
    require(rules_path.parent == manifest_path.parent, '用途规则与视图不属于同一版本目录')
    rules = read(rules_path)
    require(rules.get('schema_version') == 'eligibility-preview.v1' and rules.get('formal_use_allowed') is False
            and rules['problem_id'] == folder.name and rules['eligibility_id'] == manifest_path.parent.name
            and verify_ref(rules['design_ref'], root) == design_path, '用途视图绑定到其他课题或其他草稿')
    return draft, manifest_path


def create_topic(home, pid, title, dry):
    safe_id(pid)
    require(len(pid) <= 96 and isinstance(title, str) and 0 < len(title) <= 120 and title == title.strip()
            and all(c.isprintable() for c in title), '课题编号过长或题名不是单行可见文本')
    local_file(home, 'AGENTS.md')
    root = home/'问题'
    def available():
        require(pid.casefold() not in {k.casefold() for k in topic_dirs(home)}, f'编号已登记，不覆盖：{pid}')
    # Placeholders express absence, not research choices or authorization.
    defaults = ('候选问题，尚未批准立项。','尚未登记。','尚未登记。','待评估。','尚未拟定。',
                '尚无已确认研究决定。','待梳理。','是否立项及其依据待决定。','登记支持与反对该问题的证据，并说明下一项可区分方案的检查。')
    contents = {
        'AGENTS.md': ('# 课题卷宗规则\n\n继承 [研究管理规则](../../AGENTS.md)。本目录只记录本题的状态、依据与真实确认；'
                      '假设和协议编号在本题内解释，未接入的资料保持空缺。\n').encode(),
        '状态.md': (f'# {title}：当前状态\n\nproblem_id: {pid}\n\n' +
                  '\n\n'.join(f'## {s}\n\n{v}' for s,v in zip(SECTIONS, defaults)) + '\n').encode(),
        '决策.json': encoded({'schema_version':'decisions.v1','problem_id':pid,'decisions':[]}),
        '接入.json': encoded({'schema_version':'topic-bindings.v1','problem_id':pid,'literature':None,'g3':None})}
    available()
    if not dry:
        with lock(under(root, '.create.lock')):
            available()
            staging = root/f'.new-{uuid.uuid4()}'
            staging.mkdir()
            for name, value in contents.items():
                write(staging/name, value)
            require(all((staging/name).read_bytes() == value for name,value in contents.items()), '新建内容回读不符')
            os.rename(staging, root/pid)
    return {'status':'pass','dry_run':dry,'problem_id':pid,'files':list(contents),'formal_use_allowed':False}
