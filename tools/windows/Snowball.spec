# Native Windows x64 only. One Analysis/PYZ and one shared runtime, two entry EXEs.
import ast
import os
from pathlib import Path
import struct
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

if sys.platform != 'win32' or struct.calcsize('P') != 8:
    raise RuntimeError('Build on native 64-bit Windows with 64-bit Python')
repo = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(repo/'tools/windows'))
from stage_payload import verify
payload = Path(os.environ['SNOWBALL_STAGED_PAYLOAD']).resolve()
listing = verify(payload)
companion = payload/'codex/companion'
scripts = payload/'codex/plugins/xueqiu-status/scripts'
tools = payload/'codex/tools'
# Dynamic runpy/importlib dispatches must be visible to Analysis, including the
# hook's stdlib imports. Only reviewed Python source contributes hidden imports.
hidden = {'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.filedialog', '_tkinter', '_cffi_backend',
          'bleak', 'bleak.backends.winrt.client', 'bleak.backends.winrt.scanner',
          'serial', 'serial.tools.list_ports', 'serial.tools.list_ports_windows'}
unsupported = {'fcntl', 'pwd', 'grp', 'termios', 'dbus_fast', 'Foundation', 'CoreBluetooth'}
for name in listing:
    path = payload/name
    if path.suffix != '.py':
        continue
    if path.parent in (companion, scripts, tools) and path.stem.isidentifier():
        hidden.add(path.stem)
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            hidden.update(alias.name for alias in node.names if alias.name.split('.')[0] not in unsupported)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module.split('.')[0] not in unsupported:
                hidden.add(node.module)
hidden.update(collect_submodules('winrt'))
hidden.update(collect_submodules('bleak.backends.winrt'))
crypto_data, crypto_binaries, crypto_hidden = collect_all('cryptography')
hidden.update(crypto_hidden)
datas = [(str(payload/name), 'bundled/' + str(Path(name).parent).replace('\\', '/')) for name in listing]
datas.append((str(payload/'FILES.sha256.json'), 'bundled'))
datas.extend(crypto_data)
for distribution in ('bleak', 'pyserial', 'winrt-runtime'):
    datas.extend(copy_metadata(distribution))
a = Analysis([str(companion/'snowball_windows.py')],
    pathex=[str(companion), str(tools), str(scripts)], binaries=crypto_binaries,
    datas=datas, hiddenimports=sorted(hidden), hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['bleak.backends.bluezdbus', 'bleak.backends.corebluetooth'], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Snowball',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, contents_directory='_internal')
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SnowballCLI',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, contents_directory='_internal')
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='Snowball')
