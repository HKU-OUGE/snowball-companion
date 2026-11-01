"""Per-user installation of the audited Windows bundle; never deletes user data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from platform_support import ensure_private_dir, open_nofollow, protect_private, reject_links

VERSION = '0.6.0'
PRODUCT = 'SnowballCompanion'
MANIFEST = 'WINDOWS-BUNDLE.json'


class InstallError(ValueError):
    pass


def app_root():
    value = os.environ.get('LOCALAPPDATA')
    if not value:
        raise InstallError('请在 Windows 的当前用户会话中运行安装器。')
    return Path(value) / PRODUCT


def bundle_root():
    return Path(sys._MEIPASS) / 'bundled' if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


def market_root():
    return bundle_root() / 'codex'


def checked_relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or
            any(p in ('', '.', '..') for p in name.split('/')) or name.startswith('/')):
        raise InstallError('安装包包含无效文件路径。')
    return Path(*name.split('/'))


def validate_bundle(source, version=VERSION):
    source = Path(source).absolute()
    reject_links(source)
    manifest_path = source / MANIFEST
    reject_links(manifest_path)
    if manifest_path.stat().st_size > 2_000_000:
        raise InstallError('安装清单过大。')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('product') != PRODUCT or manifest.get('version') != version or manifest.get('schema') != 1:
        raise InstallError('安装包版本或产品标识不正确。')
    listing = manifest.get('files')
    if not isinstance(listing, dict) or not {'Snowball.exe', 'SnowballCLI.exe'} <= listing.keys():
        raise InstallError('安装包不完整。')
    actual = set()
    for item in source.rglob('*'):
        reject_links(item)
        if item.is_file():
            actual.add(item.relative_to(source).as_posix())
    if actual != set(listing) | {MANIFEST}:
        raise InstallError('安装包文件与清单不一致，请重新下载完整 ZIP。')
    for name, expected in listing.items():
        path = source / checked_relative(name)
        reject_links(path)
        if (not isinstance(expected, dict) or path.stat().st_size != expected.get('bytes') or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected.get('sha256')):
            raise InstallError('安装包校验失败，请重新下载。')
    return manifest


def read_current(root=None):
    root = Path(root) if root else app_root()
    marker = root / 'current.json'
    reject_links(marker)
    if not marker.exists():
        return None
    if marker.stat().st_size > 4096:
        raise InstallError('安装记录无效。')
    data = json.loads(marker.read_text(encoding='utf-8'))
    version = data.get('version', '')
    if data.get('product') != PRODUCT or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise InstallError('安装记录不属于 Snowball。')
    target = root / ('app-' + version)
    reject_links(target)
    if not target.is_dir() or not (target / MANIFEST).is_file():
        raise InstallError('安装目录已移动或不完整，请重新安装。')
    validate_bundle(target, version=version)
    return {'version': version, 'directory': target, 'cli': target / 'SnowballCLI.exe', 'gui': target / 'Snowball.exe'}


def _publish_current(root, version=VERSION):
    fd, temporary = tempfile.mkstemp(prefix='.current-', dir=root)
    try:
        protect_private(temporary)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'product': PRODUCT, 'version': version}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        reject_links(root / 'current.json')
        os.replace(temporary, root / 'current.json')
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def install_bundle(source, root=None):
    """Versioned atomic copy. Does not start services, install plugins or pair hardware."""
    source = Path(source).absolute()
    manifest = validate_bundle(source)
    root = ensure_private_dir(Path(root) if root else app_root())
    from platform_support import flock, LOCK_EX, LOCK_NB
    with os.fdopen(open_nofollow(root / 'install.lock', os.O_CREAT | os.O_RDWR), 'a') as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        previous = read_current(root)
        if previous and tuple(map(int, previous['version'].split('.'))) > tuple(map(int, VERSION.split('.'))):
            raise InstallError('已安装版本更新，请使用对应的新版本安装包。')
        target = root / ('app-' + VERSION)
        reject_links(target)
        if target.exists():
            if validate_bundle(target) != manifest:
                raise InstallError('同版本目录内容不同，已保留现有安装；请使用新版本号。')
        else:
            staging = Path(tempfile.mkdtemp(prefix='.install-', dir=root))
            try:
                for name in [*manifest['files'], MANIFEST]:
                    relative = checked_relative(name)
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / relative, destination)
                validate_bundle(staging)
                staging.rename(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        _publish_current(root)
    return read_current(root)


def create_shortcut(gui):
    """Current user's Start Menu only. Arguments travel through environment, not code."""
    gui = Path(gui).absolute()
    script = "$s=New-Object -ComObject WScript.Shell;$p=Join-Path ([Environment]::GetFolderPath('Programs')) 'Snowball Companion.lnk';$l=$s.CreateShortcut($p);$l.TargetPath=$env:SNOWBALL_GUI;$l.WorkingDirectory=[IO.Path]::GetDirectoryName($env:SNOWBALL_GUI);$l.Save()"
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        env={**os.environ, 'SNOWBALL_GUI': str(gui)}, check=True, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def cli_prefix():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('SnowballCLI.exe'))]
    return [sys.executable, str(Path(__file__).with_name('snowball_windows.py'))]


def require_installed():
    current = read_current()
    if not current or Path(sys.executable).absolute() not in (current['gui'].absolute(), current['cli'].absolute()):
        raise InstallError('请先安装，再从开始菜单打开 Snowball Companion。后台和插件必须使用固定安装目录。')
    return current


def _runtime_command(current, command):
    result = subprocess.run([str(current['cli']), 'runtime', command], capture_output=True,
        timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise InstallError('后台迁移未完成，已保留原安装。请在原版中检查后台状态。')
    try:
        return json.loads(result.stdout.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise InstallError('后台返回无效状态，已停止迁移。') from None


def install_and_switch(source):
    """Validate before stopping. Upgrade only the exact previous owned runtime."""
    from platform_support import flock, LOCK_EX, LOCK_NB
    root = ensure_private_dir(app_root())
    with os.fdopen(open_nofollow(root / 'migration.lock', os.O_CREAT | os.O_RDWR), 'a') as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        return _install_and_switch_locked(source)


def _install_and_switch_locked(source):
    validate_bundle(source)
    previous = read_current()
    was_running = had_autostart = stopped = removed = False
    installed = None
    new_started = new_autostart = False
    if previous:
        state = _runtime_command(previous, 'status')
        if state.get('state') not in ('running', 'stopped'):
            raise InstallError('旧后台状态无法确认，未进行升级。')
        was_running = bool(state.get('running'))
        task = _runtime_command(previous, 'autostart-status')
        if task.get('state') not in ('owned', 'absent'):
            raise InstallError('登录启动项归属无法确认，请先在旧版中查看诊断。')
        had_autostart = task.get('state') == 'owned'
    try:
        if was_running:
            _runtime_command(previous, 'stop'); stopped = True
        if had_autostart:
            _runtime_command(previous, 'remove-autostart'); removed = True
        installed = install_bundle(source)
        if was_running:
            _runtime_command(installed, 'start')
            new_started = True
        if had_autostart:
            _runtime_command(installed, 'install-autostart')
            new_autostart = True
        create_shortcut(installed['gui'])
        return installed
    except Exception:
        # Never delete a previous installation or credentials. Restore its pointer
        # and preferences only through that version's own verified executable.
        if previous:
            if installed:
                # A failing command may already have created a process/task.
                # Query ownership again before restoring the previous pointer.
                new_state = _runtime_command(installed, 'status')
                new_task = _runtime_command(installed, 'autostart-status')
                if new_state.get('state') not in ('running', 'stopped') or new_task.get('state') not in ('owned', 'absent'):
                    raise InstallError('升级未完成，后台状态暂时无法核实；旧版本与配置均保留，请查看诊断后恢复。') from None
                if new_state.get('running'):
                    _runtime_command(installed, 'stop')
                if new_task.get('state') == 'owned':
                    _runtime_command(installed, 'remove-autostart')
            _publish_current(app_root(), previous['version'])
            if removed:
                _runtime_command(previous, 'install-autostart')
            if stopped:
                _runtime_command(previous, 'start')
        raise


def discover_codex():
    """Only return an existing CLI; never assume a developer-machine path."""
    from shutil import which
    for name in ('codex.exe', 'codex'):
        found = which(name)
        if found and Path(found).suffix.lower() == '.exe':
            return found
    return ''


def discover_codex_candidates():
    """Read installed per-user MSIX locations; never execute a discovered file."""
    found = discover_codex()
    candidates = [found] if found else []
    if sys.platform != 'win32':
        return candidates
    roots = []
    for variable in ('LOCALAPPDATA', 'ProgramFiles'):
        if os.environ.get(variable):
            base = Path(os.environ[variable])
            roots += [base / 'Programs/Codex', base / 'Codex']
    script = "@(Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object {$_.Name -like '*Codex*'} | Select-Object -ExpandProperty InstallLocation) | ConvertTo-Json -Compress"
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
            capture_output=True, timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        locations = json.loads(result.stdout.decode('utf-8-sig')) if result.returncode == 0 and len(result.stdout) < 32768 else []
        if isinstance(locations, str): locations = [locations]
        if isinstance(locations, list):
            roots += [Path(p) for p in locations if isinstance(p, str) and Path(p).is_absolute()]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    for root in roots:
        for relative in ('resources/codex.exe', 'app/resources/codex.exe', 'bin/codex.exe'):
            candidate = root / relative
            if candidate.is_file() and str(candidate) not in candidates:
                candidates.append(str(candidate))
    return candidates


def install_plugin(codex):
    if getattr(sys, 'frozen', False):
        require_installed()
    path = Path(codex).expanduser().absolute()
    if not path.is_file() or (sys.platform == 'win32' and path.suffix.lower() != '.exe'):
        raise InstallError('请选择 Codex 自带的 codex.exe；不使用 shell 包装脚本。')
    # Feature detection occurs before touching plugin registration.
    result = subprocess.run([str(path), 'plugin', '--help'], capture_output=True, timeout=20)
    if result.returncode or b'marketplace' not in result.stdout:
        raise InstallError('此 Codex CLI 未提供插件管理，请升级 Codex 或选择其自带的 CLI。')
    import importlib.util
    spec = importlib.util.spec_from_file_location('snowball_plugin_installer', market_root() / 'tools/install_plugin.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install(str(path), market_root())
    result = subprocess.run([str(path), 'plugin', 'list', '--json'], capture_output=True, timeout=20)
    if result.returncode or b'xueqiu-status' not in result.stdout:
        raise InstallError('安装命令已执行，但未核实插件注册，请在 Codex 插件页检查。')
    from network_security import private_write
    private_write(Path.home() / '.local/state/xueqiu/windows-plugin.json', json.dumps({
        'schema': 1, 'version': VERSION, 'registered': True,
        'hooks_require_review': True}).encode())
    return {'ok': True, 'registered': True, 'hooks_require_review': True}
