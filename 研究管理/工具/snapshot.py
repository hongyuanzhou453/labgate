"""Content snapshot of explicit protected inputs using portable workspace-relative paths."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import cli, now, read, require, sha, write
from register_sources import root_path, source_paths


def protected(root):
    paths = set(source_paths(root).values())
    paths.update(root / '研究管理' / rel for rel in (
        '来源映射.json',
        '规范/词表.json',
        '规范/阶段要求.json',
        '规范/接入映射.json',
    ))
    return sorted(p for p in paths if p.exists())


def capture(root):
    return {
        'schema_version': 'protected-inputs.v1',
        'captured_at': now(),
        'scope': '登记来源与核心规范词表的相对路径内容快照；不依赖本机绝对路径',
        'files': [
            {
                'path': p.resolve().relative_to(root.resolve()).as_posix(),
                'sha256': sha(p),
                'bytes': p.stat().st_size,
            }
            for p in protected(root)
        ],
    }


def verify(snapshot, root=None):
    base = Path(root).resolve() if root is not None else root_path()
    changes = []
    for item in snapshot['files']:
        raw_p = Path(item['path'])
        path = raw_p if raw_p.is_absolute() else (base / raw_p)
        if not path.is_file() or sha(path) != item['sha256']:
            changes.append(item['path'])
    return {'status': 'fail' if changes else 'pass', 'checked': len(snapshot['files']), 'changes': changes}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    p.add_argument('--verify', type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    root = root_path()
    if a.verify:
        return verify(read(a.verify), root)
    value = capture(root)
    if not a.dry_run:
        require(a.output is not None, '--output required')
        write(a.output, value)
    return {'status': 'pass', 'dry_run': a.dry_run, 'protected_files': len(value['files'])}


if __name__ == '__main__':
    sys.exit(cli(main))
