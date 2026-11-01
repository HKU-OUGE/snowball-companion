"""Console and GUI entry point for the self-contained Windows distribution."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
sys.dont_write_bytecode = True

from windows_install import VERSION, market_root


def configure_stdio():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')


def run_hook(script):
    """Run the installed host's exact hook version, preserving JSON stdin/stdout."""
    from platform_support import reject_links
    path = Path(script).absolute()
    reject_links(path)
    if path.name != 'codex_hook.py' or path.parent.name != 'scripts' or not path.is_file():
        raise ValueError('Invalid Snowball hook entry')
    # The host's trusted command selects the script. Never evaluate command text.
    sys.path.insert(0, str(path.parent))
    sys.argv = [str(path), 'hook']
    runpy.run_path(str(path), run_name='__main__')
    return 0


def main(argv=None):
    configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'runtime':
        import windows_runtime
        return windows_runtime.main(argv[1:])
    if argv and argv[0] == 'pet':
        script = market_root() / 'plugins/xueqiu-status/scripts/pet_setup.py'
        sys.path.insert(0, str(script.parent))
        sys.argv = [str(script), *argv[1:]]
        runpy.run_path(str(script), run_name='__main__')
        return 0
    parser = argparse.ArgumentParser(description='Snowball Companion for Windows')
    parser.add_argument('--version', action='version', version='Snowball Companion ' + VERSION)
    sub = parser.add_subparsers(dest='command')
    gui = sub.add_parser('gui'); gui.add_argument('--smoke-test', action='store_true')
    hook = sub.add_parser('hook'); hook.add_argument('--script', required=True)
    doctor = sub.add_parser('doctor'); doctor.add_argument('--json', action='store_true')
    install = sub.add_parser('install-plugin'); install.add_argument('--codex', required=True)
    sub.add_parser('pet', help='Bundled Pet helper; use pet --help for available actions')
    args = parser.parse_args(argv)
    try:
        if args.command in (None, 'gui'):
            from windows_gui import launch
            return launch(smoke_test=getattr(args, 'smoke_test', False))
        if args.command == 'hook':
            return run_hook(args.script)
        if args.command == 'doctor':
            import asyncio
            from connection_diagnostics import doctor
            report = asyncio.run(doctor(Path.home() / '.config/xueqiu/ble-pairing.json'))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'install-plugin':
            from windows_install import install_plugin
            print(json.dumps(install_plugin(args.codex), ensure_ascii=False))
            return 0
    except (OSError, ValueError, RuntimeError) as error:
        # No exception text: serial, BLE and subprocess errors may contain secrets.
        message = {'ok': False, 'code': type(error).__name__, 'message': '操作未完成，请打开 Snowball 向导查看分层诊断。'}
        if sys.stderr is not None:
            print(json.dumps(message, ensure_ascii=False), file=sys.stderr)
        return 0 if args.command == 'hook' else 2  # Optional hooks never block a chat.


if __name__ == '__main__':
    raise SystemExit(main())
