#!/usr/bin/env python3
"""Explicit user commands; never run a write from a lifecycle hook."""
import argparse
import json
from pathlib import Path
import sys
from shared import request, read_token, root_dir

CHARACTERS = {
    'snowball': '雪球', 'border-collie': '边牧', 'orange-cat': '橘猫', 'ragdoll': '布偶猫',
    'chiikawa': '吉伊', 'hachiware': '小八', 'usagi': '乌萨奇', 'my-melody': '美乐蒂',
    'kuromi': '酷洛米', 'homelander': '祖国人', 'joseph-joestar': '老年乔瑟夫',
    'stellalou': '星黛露', 'frank-underwood': '弗兰克', 'heisenberg': '海森堡',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('characters')
    select = sub.add_parser('pet')
    select.add_argument('character_id', choices=CHARACTERS)
    args = p.parse_args()
    if args.command == 'characters':
        print(json.dumps(CHARACTERS, ensure_ascii=False, indent=2))
        return 0
    report = {'plugin': 'snowball-companion', 'version': '0.2.0', 'python': sys.version.split()[0],
              'platform': sys.platform, 'hub_url': 'http://127.0.0.1:4190',
              'provider': 'Cursor', 'automatic_permission_approval': False}
    try:
        token = read_token(root_dir())
        health = request('/v1/health', token, timeout=1)
        if health.get('ok') is not True or health.get('version') != 1 or health.get('hub_alive') is not True:
            raise ValueError('unexpected_hub')
    except Exception:
        report.update(hub_healthy=False, next_step='启动 Snowball Companion Hub，或检查本机 hub-token 的私有文件权限。')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    if args.command == 'pet':
        response = request('/v1/pet', token, {'character_id': args.character_id}, timeout=1)
        print(json.dumps({'hub_receipt': response, 'note': 'Hub 已接收选择；设备实际加载及资源可用性需在雪球上核对。'}, ensure_ascii=False, indent=2))
        return 0
    report.update(hub_healthy=True, note='Hub 可达不等于设备在线。请在雪球状态页核对连接，在设置里选择 Cursor。')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
