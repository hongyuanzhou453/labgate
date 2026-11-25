"""Register declared workspace authorities without moving or editing them."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common import cli, now, read, ref, require, verify_ref, write


def root_path():
    return Path(__file__).resolve().parents[2]


def source_map(root):
    cfg = read(root / '研究管理/来源映射.json')
    require(cfg.get('schema_version') == 'source-map.v1', 'unsupported source-map.v1 config')
    require(isinstance(cfg.get('sources'), list) and cfg['sources'], 'source-map.v1 entries empty')
    return cfg


def source_paths(root):
    cfg = source_map(root)
    return {item['source_id']: root / item['path'] for item in cfg['sources']}


def audit_binding(root):
    cfg = source_map(root)
    review_path = root / cfg['audit_review_path']
    return {
        'status': 'checked_in_scope',
        'scope': cfg.get('audit_scope', 'Checked path, byte length, SHA-256, and column headers'),
        'review_ref': ref(review_path, root),
    }


def build(root):
    cfg = source_map(root)
    binding = audit_binding(root)
    entries = []
    for spec in cfg['sources']:
        sid = spec['source_id']
        path = root / spec['path']
        item = ref(path, root)
        header = None
        if path.suffix in {'.csv', '.tsv'}:
            with path.open(encoding='utf-8-sig', newline='') as f:
                header = next(csv.reader(f, delimiter='\t' if path.suffix == '.tsv' else ','))
            require(len(header) == len(set(header)), f'duplicate columns in {sid}')
        kind = spec.get('source_kind', 'research_source')
        audit_source = kind == 'audit'
        entries.append(dict(
            source_id=sid,
            logical_name=path.name,
            resolved_path=item.pop('path'),
            **item,
            header=header,
            source_kind=kind,
            authority_scope='上游唯一来源；本登记只保存接入版本和位置',
            review_status='checked_in_scope',
            evidence_ref=binding['review_ref'] if audit_source else ref(path, root),
            check_scope=binding['scope'] if audit_source else '仅核路径、字节哈希和表头；不代表文献内容复审',
            restrictions=['只读', '不等于正式数据准入', '保留上游核验范围'],
        ))
    return {
        'schema_version': 'sources.v0.2',
        'captured_at': now(),
        'source_map_ref': ref(root / '研究管理/来源映射.json', root),
        'entries': entries,
        'audit_binding': binding,
    }


def verify(registry, root):
    require(registry['schema_version'] == 'sources.v0.2', 'unsupported registry')
    verify_ref(registry['source_map_ref'], root)
    expected = build(root)
    require(registry['entries'] == expected['entries'], 'source registry drift or missing entries')
    require(registry['audit_binding'] == expected['audit_binding'], 'audit review changed')
    return len(registry['entries'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    root = root_path()
    destination = a.output or root / '研究管理/来源登记.json'
    candidate = build(root)
    if destination.exists():
        verify(read(destination), root)
    elif not a.dry_run:
        write(destination, candidate)
    return {
        'status': 'pass',
        'dry_run': a.dry_run,
        'source_count': len(candidate['entries']),
        'output': str(destination.relative_to(root)) if destination.is_relative_to(root) else str(destination),
        'scope': 'content identity and review-version binding',
    }


if __name__ == '__main__':
    sys.exit(cli(main))
