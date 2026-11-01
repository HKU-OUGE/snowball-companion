"""Installer, entry point and wizard contracts; no real device or user installation."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock, AsyncMock

import pytest

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import windows_install as install


def make_bundle(path, version='0.6.0'):
    path.mkdir(parents=True)
    files = {'Snowball.exe': b'fake-gui', 'SnowballCLI.exe': b'fake-cli', '_internal/data/example.txt': b'data'}
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {'schema': 1, 'product': install.PRODUCT, 'version': version,
        'files': {name: {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()} for name, content in files.items()}}
    (path / install.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    return path


def test_atomic_install_repeat_keeps_private_data_and_rejects_extra(tmp_path):
    source = make_bundle(tmp_path / '下载 文件')
    root = tmp_path / 'Local AppData/SnowballCompanion'
    private = tmp_path / 'ble-pairing.json'
    private.write_bytes(b'existing-private-data')
    installed = install.install_bundle(source, root)
    assert installed['cli'].is_file()
    assert install.install_bundle(source, root) == installed
    assert private.read_bytes() == b'existing-private-data'
    (source / 'unexpected.txt').write_text('not allowed')
    with pytest.raises(install.InstallError):
        install.install_bundle(source, root)
    assert install.read_current(root) == installed


def test_corrupted_previous_executable_is_never_launched(tmp_path):
    root = tmp_path / 'installed'
    install.install_bundle(make_bundle(tmp_path / 'source'), root)
    (root / 'app-0.6.0/SnowballCLI.exe').write_bytes(b'changed')
    with pytest.raises(install.InstallError):
        install.read_current(root)


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'C:/drive', 'folder\\file', 'foo//bar', 'foo/./bar'])
def test_manifest_paths_cannot_escape(name):
    with pytest.raises(install.InstallError):
        install.checked_relative(name)


def test_source_validation_precedes_stopping_previous_runtime(tmp_path):
    source = make_bundle(tmp_path / 'source')
    (source / 'Snowball.exe').write_bytes(b'broken')
    with patch.object(install, '_runtime_command') as command:
        with pytest.raises(install.InstallError):
            install._install_and_switch_locked(source)
    command.assert_not_called()


def test_previous_version_is_verified_and_downgrade_refused(tmp_path):
    source = make_bundle(tmp_path / 'source')
    root = tmp_path / 'installed'; root.mkdir()
    make_bundle(root / 'app-0.7.0', '0.7.0')
    install._publish_current(root, '0.7.0')
    assert install.read_current(root)['version'] == '0.7.0'
    with pytest.raises(install.InstallError):
        install.install_bundle(source, root)
    assert install.read_current(root)['version'] == '0.7.0'


def test_upgrade_rollback_stops_new_before_restoring_old(tmp_path):
    source = make_bundle(tmp_path / 'source')
    old = {'version': '0.5.9', 'cli': Path('old.exe')}
    new = {'version': '0.6.0', 'cli': Path('new.exe'), 'gui': Path('newgui.exe')}
    calls = []
    def command(current, operation):
        calls.append((current['version'], operation))
        if operation == 'status': return {'state': 'running', 'running': True}
        if operation == 'autostart-status': return {'state': 'owned'}
        return {}
    with patch.object(install, 'read_current', return_value=old), \
         patch.object(install, 'install_bundle', return_value=new), \
         patch.object(install, 'app_root', return_value=tmp_path), \
         patch.object(install, '_runtime_command', side_effect=command), \
         patch.object(install, 'create_shortcut', side_effect=OSError('test')), \
         patch.object(install, '_publish_current') as publish:
        with pytest.raises(OSError):
            install._install_and_switch_locked(source)
    assert calls == [('0.5.9','status'),('0.5.9','autostart-status'),('0.5.9','stop'),
        ('0.5.9','remove-autostart'),('0.6.0','start'),('0.6.0','install-autostart'),('0.6.0','status'),('0.6.0','autostart-status'),
        ('0.6.0','stop'),('0.6.0','remove-autostart'),('0.5.9','install-autostart'),('0.5.9','start')]
    publish.assert_called_once_with(tmp_path, '0.5.9')


def test_runtime_cannot_start_from_download_folder(tmp_path):
    current = {'gui': tmp_path/'installed/Snowball.exe', 'cli': tmp_path/'installed/SnowballCLI.exe'}
    with patch.object(install, 'read_current', return_value=current), patch.object(sys, 'executable', str(tmp_path/'download/Snowball.exe')):
        with pytest.raises(install.InstallError):
            install.require_installed()


def test_packaged_hook_override_decodes_to_reviewed_source():
    plugin = BASE.parent / 'plugins/xueqiu-status'
    expected = (plugin/'scripts/windows_hook.ps1').read_text(encoding='utf-8')
    config = json.loads((plugin/'hooks/hooks.json').read_text(encoding='utf-8'))
    for groups in config['hooks'].values():
        command = groups[0]['hooks'][0]['commandWindows']
        assert command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand ')
        assert base64.b64decode(command.split()[-1]).decode('utf-16-le') == expected
        assert 'ExecutionPolicy' not in command
    assert '$OutputEncoding' in expected and 'ReadToEnd()' in expected


def test_console_version_is_headless():
    result = subprocess.run([sys.executable, str(BASE/'snowball_windows.py'), '--version'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert '0.6.0' in result.stdout


def test_private_write_and_readonly_desktop_fail_closed_on_windows(tmp_path):
    from network_security import private_write
    private_write(tmp_path/'private/test.json', b'{"v":1}')
    assert (tmp_path/'private/test.json').read_bytes() == b'{"v":1}'
    import app_tools
    with patch.object(app_tools.os, 'name', 'nt'):
        # Build instance first: pathlib's class selection follows os.name.
        obj = object.__new__(app_tools.AppTools)
        assert obj.pipe() is None


def test_probe_restores_background_on_failure():
    import windows_gui
    import windows_runtime
    async def fail():
        raise RuntimeError('probe failed')
    with patch.object(windows_runtime, 'status', return_value={'state':'running','running':True}), \
         patch.object(windows_runtime, 'stop') as stop, patch.object(windows_runtime, 'start') as start:
        with pytest.raises(RuntimeError):
            windows_gui.pause_for_probe(fail)
    stop.assert_called_once()
    start.assert_called_once()


def test_busy_wizard_does_not_exit_into_previous_installation():
    from windows_gui import Wizard
    wizard = object.__new__(Wizard)
    wizard.busy = True
    wizard.window = Mock()
    with patch.object(install, 'read_current') as read, patch('windows_gui.subprocess.Popen') as launch:
        wizard.open_installed()
    read.assert_not_called()
    launch.assert_not_called()
    wizard.window.destroy.assert_not_called()


def test_windows_bridge_loads_protected_store_without_plain_json(tmp_path):
    import asyncio
    import bridge
    import pairing_store
    args = SimpleNamespace(command='watch', config=tmp_path/'encrypted.json')
    # Deliberately not a JSON pairing file: the Windows store is authoritative.
    args.config.write_bytes(b'ciphertext')
    config = {'v': 1, 'device': 'Xueqiu-00000000', 'key': '00'*32}
    with patch.object(bridge.sys, 'platform', 'win32'), \
         patch.object(pairing_store, 'load', return_value=config) as load, \
         patch.object(bridge, 'connected_session', new=AsyncMock()) as session:
        asyncio.run(bridge.run(args))
    load.assert_called_once_with(args.config)
    session.assert_awaited_once_with(args, config)
