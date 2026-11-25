"""Read a stable schema-v2 literature snapshot and project its verified metadata."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import unicodedata
from collections import Counter
from contextlib import closing
from pathlib import Path

from common import cli, read, require


DB_PATH = '论文内容索引/literature.db'
CHECKS = ('visual_verified', 'text_verified', 'pending')


def normalized_title(value):
    # Same title identity rule as the canonical card builder.
    require(isinstance(value, str) and value.strip(), '全文库题名为空')
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def database_state(path):
    require(path == path.resolve(), '全文库目录或文件不得使用链接')
    for suffix in ('-wal', '-shm', '-journal'):
        require(not os.path.lexists(str(path) + suffix), '全文库存在事务旁文件，无法只读确认稳定快照')
    if not path.exists():
        return None
    require(path.is_file(), '全文库路径不是普通文件')
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def card_metadata(root, row):
    pid = row['paper_id']
    relative = f'论文内容索引/papers/{pid}.md'
    path = root / relative
    require(row['card_path'] == relative and path == path.resolve() and path.is_file(),
            f'全文库主卡路径不符或缺失：{pid}')
    raw = path.read_bytes()
    require(len(raw) == row['card_bytes'] and hashlib.sha256(raw).hexdigest() == row['card_sha256'],
            f'全文库主卡哈希或字节数不符：{pid}')
    # Only the front matter is parsed. No body, result values or excerpts enter the projection.
    parts = raw.replace(b'\r\n', b'\n').split(b'---\n', 2)
    require(len(parts) == 3 and parts[0] == b'', f'主卡缺少元信息：{pid}')
    card = json.loads(parts[1])
    require(isinstance(card, dict) and all(isinstance(row[k], str) and row[k] and card.get(k) == row[k]
            for k in ('paper_id', 'work_id', 'review_status', 'quantitative_check_status'))
            and normalized_title(card.get('title')) == normalized_title(row['title']),
            f'全文库与主卡身份或核验状态冲突：{pid}')


class LiteratureIndex:
    def __init__(self, root, ledger, ledger_sha256, groups):
        self.ledger = ledger
        self.work_ids = {pid: group['canonical_id'] for pid, group in groups.items()}
        self.inventory = {}
        self.cards = {}
        self.by_work = {}
        root = Path(root).resolve()
        path = root / DB_PATH
        before = database_state(path)
        self.available = before is not None
        if self.available:
            try:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute('PRAGMA query_only=ON')
                    conn.execute('PRAGMA trusted_schema=OFF')
                    conn.execute('PRAGMA temp_store=MEMORY')
                    self.load(conn, root, ledger_sha256)
            except sqlite3.Error as exc:
                raise ValueError(f'全文库损坏或表结构不符：{exc}') from exc
            require(database_state(path) == before, '全文库在查询期间改变')

    def load(self, conn, root, ledger_sha256):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        require({'meta', 'papers', 'results', 'corpus_inventory', 'concepts', 'paper_concepts'} <= tables,
                '全文库缺少必需表')
        require([r[0] for r in conn.execute('PRAGMA quick_check(1)')] == ['ok'], '全文库完整性检查失败')
        metadata = conn.execute("SELECT key,value FROM meta WHERE key IN ('schema_version','inputs_detail_json')").fetchall()
        meta = dict(metadata)
        require(len(metadata) == 2 and meta.get('schema_version') == '2.0.0'
                and isinstance(meta.get('inputs_detail_json'), str), '全文库版本或构建来源缺失')
        detail = json.loads(meta['inputs_detail_json'])
        require(isinstance(detail, dict) and detail.get('ledger') == ledger_sha256, '全文库使用的 Q1 台账哈希不同')

        for row in conn.execute('SELECT paper_id,work_id,title,doi_or_url,status FROM corpus_inventory'):
            pid = row['paper_id']
            require(pid in self.ledger and pid not in self.inventory
                    and row['work_id'] == self.work_ids.get(pid, pid), '全文库清单与 Q1 编号或来源版本组冲突')
            record = self.ledger[pid]
            require(normalized_title(row['title']) == normalized_title(record['title'])
                    and (row['doi_or_url'] or '') == record.get('doi_or_url', ''), f'全文库清单与 Q1 文献身份冲突：{pid}')
            self.inventory[pid] = row['status']

        result_rows = conn.execute('SELECT id,paper_id,work_id,card_sha256,check_status FROM results')
        seen = set()
        counts = {}
        for result in result_rows:
            identifier = result['id']
            require(isinstance(identifier, str) and identifier and identifier not in seen and
                    result['check_status'] in CHECKS, '定量行身份重复或核验状态未知')
            seen.add(identifier)
            key = (result['paper_id'], result['work_id'], result['card_sha256'])
            counts.setdefault(key, Counter())[result['check_status']] += 1

        sql = ('SELECT paper_id,work_id,title,review_status,quantitative_check_status,'
               'result_count,visual_verified_count,text_verified_count,pending_row_count,'
               'card_path,card_sha256,card_bytes FROM papers ORDER BY paper_id')
        for row in conn.execute(sql):
            pid, wid = row['paper_id'], row['work_id']
            require(self.inventory.get(pid) == 'formal_verified' and pid not in self.cards
                    and wid == self.work_ids.get(pid, pid) and wid not in self.by_work,
                    '全文库主卡与 Q1、来源版本组或正式清单冲突')
            require(normalized_title(row['title']) == normalized_title(self.ledger[pid]['title']),
                    f'全文库主卡与 Q1 题名冲突：{pid}')
            card_metadata(root, row)
            counted = counts.pop((pid, wid, row['card_sha256']), Counter())
            published = [row[k] for k in ('visual_verified_count', 'text_verified_count', 'pending_row_count')]
            require([counted[k] for k in CHECKS] == published and sum(counted.values()) == row['result_count']
                    and not (row['quantitative_check_status'] == 'selected_results_verified' and counted['pending']),
                    f'全文库定量条数或核验状态冲突：{pid}')
            summary = {k: row[k] for k in ('paper_id', 'work_id', 'review_status', 'quantitative_check_status',
                                            'card_path', 'card_sha256', 'result_count')}
            summary.update(result_status_counts=dict(zip(CHECKS, published)),
                           verified_result_count=counted['visual_verified'] + counted['text_verified'], concepts=[])
            self.cards[pid] = summary
            self.by_work[wid] = summary
        require(not counts, '定量行缺少对应主卡或与主卡哈希、版本冲突')
        require(set(self.cards) == {p for p, state in self.inventory.items() if state == 'formal_verified'},
                '正式清单与主卡集合不符')
        require({p.stem for p in (root/'论文内容索引/papers').glob('L*.md')} == set(self.cards),
                '本地规范主卡目录与全文库条目集合不符')
        concepts = {}
        for code, name, en_name in conn.execute('SELECT concept_id,name,en_name FROM concepts'):
            require(all(isinstance(x, str) and x.strip() for x in (code, name, en_name))
                    and code not in concepts, '概念编号重复或名称缺失')
            concepts[code] = {'concept_id':code, 'name':name, 'en_name':en_name}
        pairs = set()
        for pid, code in conn.execute('SELECT paper_id,concept_id FROM paper_concepts ORDER BY paper_id,concept_id'):
            require(pid in self.inventory and code in concepts and (pid, code) not in pairs, '概念关联重复或悬空')
            pairs.add((pid, code))
            if pid in self.cards:
                self.cards[pid]['concepts'].append(concepts[code])

    def health(self):
        available = self.available
        return {'status':'available' if available else 'unavailable', 'path':DB_PATH,
                'paper_count':len(self.cards) if available else None,
                'result_count':sum(card['result_count'] for card in self.cards.values()) if available else None,
                'verified_result_count':sum(card['verified_result_count'] for card in self.cards.values()) if available else None}

    def lookup(self, pid):
        require(pid in self.ledger, f'台账没有此编号：{pid}')
        if pid in self.cards:
            return dict(self.cards[pid], status='exact_card', has_card=True, related_card=None)
        wid = self.work_ids.get(pid, pid)
        related = self.by_work.get(wid)
        status = 'related_version_only' if related else 'not_indexed'
        return {'paper_id':pid, 'work_id':wid, 'status':status if self.available else 'unavailable',
                'has_card':False if self.available else None, 'inventory_status':self.inventory.get(pid),
                'related_card':related, 'card_path':None, 'card_sha256':None, 'concepts':None,
                'review_status':None, 'quantitative_check_status':None, 'result_count':None,
                'verified_result_count':None, 'result_status_counts':None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='always read-only')
    parser.parse_args()
    from evidence import Catalog
    from register_sources import root_path
    root = root_path()
    health = Catalog(root, read(root/'研究管理/来源登记.json')).literature_health()
    return {'status':'pass' if health['status'] == 'available' else 'fail', 'library':health}


if __name__ == '__main__':
    sys.exit(cli(main))
