"""Read the shared controlled vocabulary when a validator first needs it."""
from __future__ import annotations

import argparse
import sys
from functools import cache
from pathlib import Path
from types import MappingProxyType

from common import cli, read, require


@cache
def _vocabulary():
    path = Path(__file__).resolve().parents[1] / '规范/词表.json'
    doc = read(path)
    require(isinstance(doc, dict) and doc.pop('schema_version', None) == 'research-vocabulary.v1',
            'unsupported vocabulary schema')
    enums = {}
    for name, values in doc.items():
        if name == 'split_role':
            require(isinstance(values, dict) and values, 'missing split role policy')
            require(all(isinstance(pair, list) and len(pair) == 2 and
                        all(isinstance(v, str) and v for v in pair) for pair in values.values()),
                    'invalid split role policy')
            enums[name] = MappingProxyType({role: tuple(pair) for role, pair in values.items()})
        else:
            require(isinstance(values, list) and values and
                    all(isinstance(v, str) and v and v == v.strip() for v in values) and
                    len(values) == len(set(values)), 'invalid controlled vocabulary')
            enums[name] = frozenset(values)
    return enums


def choices(name):
    value = _vocabulary()[name]
    return frozenset(value)


def split_role_policy():
    return _vocabulary()['split_role']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='always read-only')
    parser.parse_args()
    return {'status': 'pass', 'counts': {k: len(v) for k, v in _vocabulary().items()}}


if __name__ == '__main__':
    sys.exit(cli(main))
