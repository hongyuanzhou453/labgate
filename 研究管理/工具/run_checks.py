"""以只读模式运行各独立校验入口，保留逐项结果与退出码。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from common import cli, decode_json, now, require


def run_child(name, command):
    item = {'check':name, 'command':command, 'status':'fail', 'exit_code':None, 'result':None}
    try:
        proc = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return dict(item, error='子命令在 120 秒内没有完成')
    except OSError as exc:
        return dict(item, error=f'无法启动子命令: {exc}')
    item['exit_code'] = proc.returncode
    try:
        result = decode_json(proc.stdout)
        require(isinstance(result, dict) and result.get('status') in ('pass','fail'),
                '预期 JSON 对象及 pass/fail 状态')
    except (ValueError, TypeError, RecursionError) as exc:
        return dict(item, error=f'子命令输出无效: {exc}')
    item['result'] = result
    if proc.returncode == 0 and result['status'] == 'pass':
        item['status'] = 'pass'
    elif (proc.returncode == 0) != (result['status'] == 'pass'):
        item['error'] = '子命令退出码与 JSON 状态不一致'
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('G1','G2','G3','all'), default='all')
    parser.add_argument('--runtime-report', type=Path, help='G2/G3 所需 runtime 验收报告')
    parser.add_argument('--problem', help='G3 对应课题；仅有一个课题时可省略')
    parser.add_argument('--phase', choices=('prepare','result'), default='prepare')
    parser.add_argument('--baseline', type=Path, help='沿用 check_all 的可选保护基线参数')
    parser.add_argument('--literature', action='store_true', help='同时核对只读全文库')
    parser.add_argument('--dry-run', action='store_true', help='默认只读；此标记明确请求同一行为')
    args = parser.parse_args()
    if args.problem and args.stage not in ('G3','all'):
        parser.error('--problem 只用于包含 G3 的检查')
    tools = Path(__file__).resolve().parent
    checks = []

    def execute(name, script, *params):
        checks.append(run_child(name, [sys.executable,'-B',str(tools/script),*params,'--dry-run']))

    for stage in (('G1','G2','G3') if args.stage == 'all' else (args.stage,)):
        params = ['--stage',stage]
        if args.baseline:
            params += ['--baseline',str(args.baseline.resolve())]
        if stage in ('G2','G3') and args.runtime_report:
            params += ['--runtime-report',str(args.runtime_report.resolve())]
        if stage == 'G3':
            params += ['--phase',args.phase]
            if args.problem:
                params += ['--problem',args.problem]
        execute(stage, 'check_all.py', *params)
    execute('overview', 'research.py', 'overview', '--check-view')
    execute('report', 'research.py', 'report', 'check')
    if args.literature:
        execute('literature', 'research.py', 'literature-health')
    return {'schema_version':'research-checks.v1', 'checked_at':now(),
            'status':'pass' if all(c['status']=='pass' for c in checks) else 'fail',
            'stage':args.stage, 'read_only':True, 'formal_use_allowed':False, 'checks':checks}


if __name__ == '__main__':
    sys.exit(cli(main))
