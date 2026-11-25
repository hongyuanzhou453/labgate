"""Shared strict I/O. No writes or subprocesses at import time."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    require(isinstance(value, str) and value.endswith('Z'), 'timestamp must be UTC/Z')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def _pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, f'duplicate JSON key: {key}')
        out[key] = value
    return out


def decode_json(blob):
    def bad_constant(value):
        raise Invalid(f'non-finite JSON constant: {value}')
    return json.loads(blob.decode('utf-8'),
                      object_pairs_hook=_pairs, parse_constant=bad_constant)


def read(path):
    return decode_json(Path(path).read_bytes())


def under(base, value):
    require(isinstance(value, str) and value, 'empty path')
    p = Path(value)
    require(not p.is_absolute() and '..' not in p.parts, 'absolute/traversal path rejected')
    base = Path(base).resolve()
    dest = (base / p).resolve()
    require(dest.is_relative_to(base), 'symlink escapes root')
    return dest


def safe_id(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', value),
            'invalid identifier')
    return value


def run_id(value):
    require(isinstance(value, str), 'run_id must be a string')
    u = uuid.UUID(value)
    require(u.version == 4 and str(u) == value, 'run_id must be a canonical full UUID4')
    return value


def write(path, data, *, replace=False):
    """Callers serialize shared mutations; inputs/immutable files never overwrite."""
    path = Path(path)
    blob = data if isinstance(data, bytes) else encoded(data)
    if path.exists():
        if path.read_bytes() == blob:
            return
        require(replace, f'immutable destination differs: {path.name}')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4()}.tmp')
    with tmp.open('xb') as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def ref(path, base):
    path = Path(path).resolve()
    return {'path': str(path.relative_to(Path(base).resolve())), 'sha256': sha(path),
            'bytes': path.stat().st_size}


def verify_ref(value, base):
    require(isinstance(value, dict), 'file reference must be an object')
    digest = value.get('sha256')
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'invalid SHA-256')
    path = under(base, value.get('path'))
    require(path.is_file(), f'missing file: {value.get("path")}')
    require(sha(path) == digest, f'file hash mismatch: {value.get("path")}')
    if 'bytes' in value:
        require(type(value['bytes']) is int and value['bytes'] == path.stat().st_size,
                'file size mismatch')
    return path


def references(value):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def closure(value, base):
    """Verify each referenced file, including actual inputs behind JSON manifests."""
    result = {}
    queue = list(references(value))
    while queue:
        item = queue.pop()
        path = verify_ref(item, base)
        relative = str(path.relative_to(Path(base).resolve()))
        if relative in result:
            require(result[relative]['sha256'] == item['sha256'], 'conflicting refs to same path')
            continue
        result[relative] = ref(path, base)
        if path.suffix == '.json':
            queue.extend(references(read(path)))
    return list(sorted(result.values(), key=lambda x: x['path']))


def finite_number(value, *, optional=False):
    if value is None and optional:
        return
    require(type(value) in (int, float) and math.isfinite(value), 'finite numeric value required')


def cli(main):
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get('status') in ('pass', 'ok') else 1
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'fail', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(cli(lambda: {'status': 'pass', 'module': 'common', 'writes': False}))
