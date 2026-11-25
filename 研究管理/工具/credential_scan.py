"""Read-only V29 scan of portable inputs. Diagnostics never include input text or names."""
from __future__ import annotations

import argparse
import codecs
import csv
import io
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import unquote

from common import Invalid, cli, require


FIELD = (r'(?:[a-z0-9]+[_-])?(?:api[-_ ]?key|api[-_ ]?token|access[-_ ]?token|'
         r'auth[-_ ]?token|secret[-_ ]?(?:key|access[-_ ]?key)|client[-_ ]?secret|'
         r'private[-_ ]?key|password|token)')
SENSITIVE_HEADER = re.compile(FIELD, re.IGNORECASE)
RULES = (
    ('GitHub token', re.compile(r'(?:gh[pousr]_|github_pat_)[a-zA-Z0-9_]{8,}')),
    ('service API key', re.compile(r'(?<![a-zA-Z0-9])sk-[a-zA-Z0-9_-]{8,}|AIza[0-9A-Za-z_-]{35}')),
    ('AWS access key', re.compile(r'(?:AKIA|ASIA)[0-9A-Z]{16}')),
    ('bearer token', re.compile(r'\bBearer\s+[\w.~+/=-]{6,}', re.IGNORECASE)),
    ('signed URL', re.compile(r'[?&](?:amp;)?(?:X-Amz-Signature|X-Goog-Signature|Signature)\s*=', re.IGNORECASE)),
    ('credential assignment', re.compile(
        rf'(?<![\w-]){FIELD}["\']?\s*[:=]\s*["\']?[A-Za-z0-9+/._~=-]{{8,}}', re.IGNORECASE)),
    ('private key', re.compile(r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----')),
)
CHUNK = 128 * 1024
OVERLAP = 2048


def _check_text(text):
    # Recover printable ASCII from UTF-16/32 and common JSON/URL representations.
    text = text.replace('\0', '')
    if '\\' in text:
        text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), text)
        text = re.sub(r'\\(["\\/])', r'\1', text)
    if '%' in text:
        text = unquote(text)
    for label, pattern in RULES:
        if pattern.search(text):
            raise Invalid(f'V29 credential marker detected ({label})')


def _check_columns(stream, suffix):
    """Catch opaque values under sensitive CSV/TSV headers as well as token-shaped values."""
    stream.seek(0)
    prefix = stream.read(4)
    if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = 'utf-32'
    elif prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'
    stream.seek(0)
    reader = io.TextIOWrapper(stream, encoding=encoding, errors='replace', newline='')
    try:
        def lines():
            while line := reader.readline(1024 * 1024 + 1):
                require(len(line) <= 1024 * 1024, 'V29 table line exceeds scan limit')
                yield line.replace('\0', '')
        rows = csv.reader(lines(), delimiter='\t' if suffix == '.tsv' else ',')
        header = next(rows, [])
        indices = [i for i, field in enumerate(header) if SENSITIVE_HEADER.fullmatch(field.strip())]
        if indices:
            for row in rows:
                if any(i < len(row) and row[i].strip() for i in indices):
                    raise Invalid('V29 credential marker detected (credential column)')
    except (csv.Error, UnicodeError):
        raise Invalid('V29 unreadable table') from None
    finally:
        reader.detach()


def _scan_stream(stream, name):
    tail = b''
    while chunk := stream.read(CHUNK):
        window = tail + chunk
        _check_text(window.decode('latin-1'))
        tail = window[-OVERLAP:]
    suffix = Path(name).suffix.lower()
    if suffix in {'.csv', '.tsv'}:
        _check_columns(stream, suffix)


def scan_bytes(blob, name=''):
    """Check the exact bytes to be persisted; name enables header-aware table checks."""
    _check_text(str(name))
    _scan_stream(io.BytesIO(blob), name)


def scan_bundle(bundle):
    def unreadable(_error):
        raise Invalid('V29 cannot inspect bundle tree')

    try:
        root = Path(bundle)
        _check_text(root.name)
        require(stat.S_ISDIR(root.lstat().st_mode), 'V29 requires a local bundle directory')
        for _, dirs, files, fd in os.fwalk(root, onerror=unreadable):
            for name in dirs + files:
                _check_text(name)
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                require(stat.S_ISDIR(mode) or stat.S_ISREG(mode),
                        'V29 bundle contains a link or special file')
            for name in files:
                handle = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(handle, 'rb') as stream:
                    before = os.fstat(handle)
                    require(stat.S_ISREG(before.st_mode), 'V29 expected a regular file')
                    _scan_stream(stream, name)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require(os.path.samestat(before, after) and before.st_size == after.st_size and
                            before.st_mtime_ns == after.st_mtime_ns, 'V29 input changed during scan')
    except (OSError, RecursionError):
        raise Invalid('V29 cannot inspect entire bundle') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true', help='always read-only')
    args = parser.parse_args()
    scan_bundle(args.bundle)
    return {'status': 'pass', 'check': 'V29'}


if __name__ == '__main__':
    sys.exit(cli(main))
