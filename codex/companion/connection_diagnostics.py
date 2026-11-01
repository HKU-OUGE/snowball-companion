"""Read-only, redacted diagnostics and exclusive one-shot BLE authentication.

The bridge must hold device_session_lock for its complete radio session. No
diagnostic starts/stops services, reads USB credentials, or sends device commands.
"""
import asyncio
from contextlib import contextmanager
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pairing_store
import platform_support as platform

SERVICE = '9e7a0001-6ed4-4b6b-bf37-43dc42838c20'
MESSAGES = {
    'ok': '蓝牙身份验证通过，本次检查连接已断开。',
    'missing_credentials': '尚未绑定，请打开设备上的连接页面后进行绑定。',
    'invalid_credentials': '已保存的绑定资料格式不正确。',
    'credentials_not_private': '绑定文件权限不安全，需要修复。',
    'auth_mismatch': '设备身份或认证密钥与当前绑定不符，请从设备连接页面重新绑定。',
    'connection_page_required': '请先打开设备上的连接页面，再尝试绑定。',
    'no_adapter': '系统未提供可用的蓝牙适配器。',
    'adapter_off': '请打开蓝牙后重试。',
    'device_not_found': '未发现已绑定设备，请检查设备电源、距离和是否已连接其他电脑。',
    'ambiguous_device': '发现多个身份名称相同的设备，未尝试连接。',
    'background_running': '请先停止后台蓝牙服务，再运行单次连接检查。',
    'background_unverified': '无法确认后台服务归属，请先解决后台状态问题。',
    'session_busy': '另一个本机雪球连接会话正在持有蓝牙锁。',
    'occupied': '蓝牙系统明确报告已有连接操作正在进行。',
    'timeout': '蓝牙操作超时，可能与距离、蓝牙状态、配对或其他连接有关，尚不能确认是设备被占用。',
    'gatt_error': '蓝牙 GATT 读写失败。',
    'permission_denied': '操作系统拒绝了蓝牙访问。',
    'dependency_missing': '缺少所需的 Python 蓝牙依赖。',
    'disconnect_failed': '检查已结束，但无法确认蓝牙连接已断开。',
    'diagnostic_failed': '诊断未能完成。',
    'transport_error': '蓝牙传输失败。',
    'info_error': '设备身份响应不完整或格式错误。',
    'ack_timeout': '未在时限内收到设备已应用状态的确认。',
    'dpapi_decrypt_failed': 'Windows 无法解密绑定资料，请使用原 Windows 账户或重新绑定。',
    'dpapi_encrypt_failed': 'Windows 无法加密绑定资料，旧绑定已保留。',
    'credential_write_failed': '绑定资料写入失败，旧绑定已保留。',
    'credential_read_failed': '绑定资料读取失败。',
    'invalid_protected_credentials': '加密绑定资料格式不正确。',
    'dpapi_verification_failed': '加密资料校验失败，旧绑定已保留。',
    'dpapi_unavailable': '当前系统无法读取 Windows 加密绑定，请在原 Windows 账户中打开。',
}


class DiagnosticError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def result(code, **safe):
    return {'ok': code == 'ok', 'code': code,
            'message': MESSAGES.get(code, '操作未能完成。'), **safe}


def _home(profile=None):
    return Path(profile) if profile is not None else Path.home()


@contextmanager
def device_session_lock(profile=None):
    path = _home(profile) / '.config/xueqiu/ble-session.lock'
    fd = None
    locked = False
    try:
        platform.ensure_private_dir(path.parent)
        fd = platform.open_nofollow(path, os.O_CREAT | os.O_RDWR, 0o600)
        platform.protect_private(path)
        try:
            platform.flock(fd, platform.LOCK_EX | platform.LOCK_NB)
        except OSError:
            raise DiagnosticError('session_busy') from None
        locked = True
        yield
    finally:
        if fd is not None:
            try:
                if locked:
                    platform.flock(fd, platform.LOCK_UN)
            finally:
                os.close(fd)


def background_status():
    """Read-only status; output contains no PID, endpoint, token or raw errors."""
    try:
        if sys.platform == 'win32':
            import windows_runtime
            state = windows_runtime.status()
            if state.get('running'):
                return result('background_running')
            if state.get('state') not in ('stopped',):
                return result('background_unverified')
        else:
            import manage
            if manage.running():
                return result('background_running')
            if sys.platform == 'linux' and shutil.which('systemctl'):
                check = subprocess.run(['systemctl', '--user', 'is-active', 'snowball-bridge.service'],
                                       capture_output=True, timeout=3, check=False)
                if check.stdout.strip() in (b'active', b'activating', b'reloading'):
                    return result('background_running')
        return {'ok': True, 'code': 'stopped', 'message': '后台蓝牙服务未运行。'}
    except Exception:
        return result('background_unverified')


def _classify(error):
    if isinstance(error, (DiagnosticError, pairing_store.PairingStoreError)):
        return error.code
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return 'timeout'
    if isinstance(error, ImportError):
        return 'dependency_missing'
    if isinstance(error, PermissionError):
        return 'permission_denied'
    # Only explicit backend error identifiers justify an occupied diagnosis.
    name = getattr(error, 'dbus_error', None)
    if name == 'org.bluez.Error.InProgress':
        return 'occupied'
    if name in ('org.bluez.Error.NotAuthorized', 'org.bluez.Error.NotPermitted'):
        return 'permission_denied'
    if type(error).__name__ == 'BleakBluetoothNotAvailableError':
        reason = str(getattr(error, 'reason', '')).lower()
        return 'adapter_off' if 'powered_off' in reason else 'no_adapter'
    return 'gatt_error'


async def _discover(config):
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    candidates = [device for device, adv in found.values()
                  if (adv.local_name or device.name or '') == config['device']
                  and SERVICE in [str(u).lower() for u in (adv.service_uuids or [])]]
    if not candidates:
        raise DiagnosticError('device_not_found')
    if len(candidates) != 1:
        raise DiagnosticError('ambiguous_device')
    return candidates[0]


def _client(device):
    from bleak import BleakClient
    return BleakClient(device, timeout=25, pair=True)


async def _authenticated_info(client, config):
    # Keep the compact INFO parser and HMAC protocol shared with the bridge.
    from bridge import read_info
    try:
        return await read_info(client, config)
    except (ValueError, KeyError, TypeError):
        raise DiagnosticError('auth_mismatch') from None


async def check_connection(config, *, profile=None):
    """Authenticate INFO only; always attempt explicit disconnect before return."""
    try:
        config = pairing_store.validate(config)
        state = await asyncio.to_thread(background_status)
        if not state['ok']:
            return state
        with device_session_lock(profile):
            state = await asyncio.to_thread(background_status)
            if not state['ok']:
                return state
            client = None
            disconnected = True
            code = 'ok'
            try:
                device = await asyncio.wait_for(_discover(config), 12)
                client = _client(device)
                disconnected = False
                await asyncio.wait_for(client.connect(), 30)
                await asyncio.wait_for(_authenticated_info(client, config), 12)
            except Exception as error:
                code = _classify(error)
            finally:
                if client is not None:
                    try:
                        await asyncio.wait_for(client.disconnect(), 8)
                        disconnected = not bool(getattr(client, 'is_connected', False))
                    except Exception:
                        disconnected = False
            if not disconnected and code == 'ok':
                code = 'disconnect_failed'
            return result(code, disconnected=disconnected)
    except Exception as error:
        return result(_classify(error))


TRANSPORT_STATES = {'connecting', 'authenticated', 'alive', 'applied', 'disconnected', 'error'}
TRANSPORT_ERRORS = set(MESSAGES) | {'transport_error', 'info_error', 'ack_timeout'}


def record_transport(state, *, error=None, ack=None, profile=None):
    """Persist ONLY an allowlisted transport state and optional integer ACK.

    Return False on telemetry failure; never interrupt the radio worker.
    Raw exceptions/INFO/device identifiers are deliberately not accepted.
    """
    if not isinstance(state, str) or state not in TRANSPORT_STATES or \
            (error is not None and (not isinstance(error, str) or error not in TRANSPORT_ERRORS)):
        return False
    if ack is not None and (type(ack) is not int or not 0 <= ack <= 0xffffffff):
        return False
    path = _home(profile) / '.local/state/xueqiu/transport-diagnostic.json'
    temporary = None
    fd = None
    try:
        platform.ensure_private_dir(path.parent)
        platform.reject_links(path)
        fd, temporary = tempfile.mkstemp(prefix='.transport-', dir=path.parent)
        platform.protect_private(temporary)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            fd = None
            json.dump({'v': 1, 'at': time.time(), 'state': state, 'error': error, 'ack': ack}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        platform.reject_links(path)
        os.replace(temporary, path)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _bounded_json(path):
    if not platform.private_file_ok(path):
        raise OSError('private_file_required')
    with os.fdopen(platform.open_nofollow(path, os.O_RDONLY), 'rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('size')
    return json.loads(raw)


def _hub_health(profile):
    token_path = _home(profile) / '.config/xueqiu/hub-token'
    try:
        if not platform.private_file_ok(token_path):
            return {'ok': False, 'code': 'hub_token_missing', 'message': '未找到权限安全的 Hub 令牌。'}
        with os.fdopen(platform.open_nofollow(token_path, os.O_RDONLY), 'rb') as stream:
            token = stream.read(1025).decode('ascii').strip()
        if not token or len(token) > 1024 or any(c.isspace() for c in token):
            raise ValueError('token')
        request = urllib.request.Request('http://127.0.0.1:4190/v1/health',
                                         headers={'Authorization': 'Bearer ' + token})
        # No environment proxy; no redirect can forward the bearer elsewhere.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=2) as response:
            raw = response.read(4097)
        value = json.loads(raw) if len(raw) <= 4096 else {}
        ok = value.get('ok') is True and value.get('hub_alive') is True
        return {'ok': ok, 'code': 'hub_healthy' if ok else 'hub_unhealthy',
                'message': 'Hub 健康检查通过。' if ok else 'Hub 健康检查未通过。'}
    except Exception:
        return {'ok': False, 'code': 'hub_unreachable', 'message': '无法连接 Hub 或通过身份验证。'}


async def _adapter_status():
    if sys.platform != 'win32':
        return {'ok': None, 'code': 'adapter_unprobed', 'message': '蓝牙适配器可用性将在连接检查时验证。'}
    try:
        try:
            from winrt.windows.devices.bluetooth import BluetoothAdapter
        except ImportError:
            from bleak_winrt.windows.devices.bluetooth import BluetoothAdapter
        adapter = await asyncio.wait_for(BluetoothAdapter.get_default_async(), 5)
        if adapter is None:
            return result('no_adapter')
        return {'ok': True, 'code': 'adapter_present', 'message': 'Windows 已检测到蓝牙适配器。'}
    except ImportError:
        return {'ok': None, 'code': 'adapter_unprobed', 'message': '可选适配器检测接口不可用，请运行蓝牙连接检查确认。'}
    except Exception:
        return {'ok': None, 'code': 'adapter_unknown', 'message': '暂时无法确认 Windows 蓝牙适配器状态。'}


def _hooks(profile, hook_dirs=None):
    roots = hook_dirs if hook_dirs is not None else [
        _home(profile) / '.local/state/xueqiu/codex-adapter/hooks',
        _home(profile) / '.local/state/xueqiu/hook-state']
    latest = None
    now = time.time()
    scanned = 0
    for root in roots:
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            continue
        # Bounded, direct JSON records only; never return session IDs or content.
        for path in root.glob('*.json'):
            scanned += 1
            if scanned > 256:
                break
            try:
                delivery = _bounded_json(path).get('last_delivery', {})
                at = delivery.get('at')
                if delivery.get('status') == 'accepted' and type(at) in (int, float) and 0 < at <= now:
                    latest = max(latest or 0, at)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        if scanned > 256:
            break
    age = round(now - latest, 1) if latest is not None else None
    return {'ok': age is not None and age <= 300, 'code': 'hooks_recent' if age is not None and age <= 300 else 'hooks_no_recent_delivery',
            'message': '最近五分钟内有 Hook 事件被 Hub 接受。' if age is not None and age <= 300 else '最近五分钟没有已确认送达的 Hook 事件，请运行一次真实编辑器任务验证。',
            'last_accepted_age_seconds': age}


async def doctor(pairing_path=pairing_store.DEFAULT_PATH, *, profile=None, hook_dirs=None):
    """Layered diagnostics. Does not connect BLE, migrate bindings or run hooks."""
    checks = []
    dependencies = {}
    for module, distribution in [('bleak', 'bleak'), ('serial', 'pyserial')]:
        try:
            available = importlib.util.find_spec(module) is not None
            dependencies[distribution] = importlib.metadata.version(distribution) if available else 'missing'
        except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
            dependencies[distribution] = 'missing'
    checks.append({'label': 'Python 依赖', 'ok': 'missing' not in dependencies.values(),
                   'code': 'dependencies', 'message': 'Python ' + sys.version.split()[0], 'versions': dependencies})
    checks.append({'label': '蓝牙适配器', **await _adapter_status()})
    try:
        pairing_store.load(pairing_path, migrate=False)
        pairing = {'ok': True, 'code': 'credentials_valid', 'message': '绑定资料格式与文件权限检查通过。'}
    except pairing_store.PairingStoreError as error:
        pairing = result(error.code)
    checks.append({'label': '设备绑定', **pairing})
    background = await asyncio.to_thread(background_status)
    if background['code'] == 'background_running':
        background = {'ok': True, 'code': 'background_running', 'message': '后台蓝牙服务正在运行。'}
    elif background['code'] == 'stopped' and pairing['ok']:
        background['ok'] = False
    checks.append({'label': '后台蓝牙服务', **background})
    checks.append({'label': 'Hub', **await asyncio.to_thread(_hub_health, profile)})
    checks.append({'label': 'Hook 事件', **await asyncio.to_thread(_hooks, profile, hook_dirs)})
    try:
        receipt = _bounded_json(_home(profile) / '.local/state/xueqiu/windows-plugin.json')
        if not isinstance(receipt, dict) or type(receipt.get('schema')) is not int or receipt['schema'] != 1:
            raise ValueError('receipt_schema')
        registered = receipt.get('registered') is True
        version = receipt.get('version', '')
        if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
            version = None
        checks.append({'label': 'Codex 插件注册', 'ok': registered, 'code': 'plugin_receipt' if registered else 'plugin_not_registered',
                       'message': '存在最近一次插件注册记录；这不代表当前 Codex 已信任插件，请检查 Codex 插件与 Hook 授权。' if registered else '未记录成功的插件注册。',
                       'version': version, 'hooks_require_review': receipt.get('hooks_require_review') is True})
    except (OSError, ValueError, AttributeError):
        checks.append({'label': 'Codex 插件注册', 'ok': False if sys.platform == 'win32' else None,
                       'code': 'plugin_receipt_missing', 'message': '没有可验证的 Windows 插件注册记录。'})
    if sys.platform == 'win32':
        checks.append({'label': 'Codex 桌面控制', 'ok': None, 'code': 'desktop_control_unsupported',
                       'message': 'Windows 支持状态 Hook；手表回复与新建对话的桌面控制暂不支持。'})
    try:
        value = _bounded_json(_home(profile) / '.local/state/xueqiu/transport-diagnostic.json')
        state, at = value.get('state'), value.get('at')
        if state not in TRANSPORT_STATES or type(at) not in (int, float) or not 0 < at <= time.time():
            raise ValueError('invalid')
        age = round(time.time() - at, 1)
        error = value.get('error')
        error = error if isinstance(error, str) and error in TRANSPORT_ERRORS else None
        states = {'connecting': '正在连接', 'authenticated': '身份已验证', 'alive': '认证心跳正常',
                  'applied': '设备已确认应用状态', 'disconnected': '连接已断开', 'error': '传输失败'}
        checks.append({'label': '最近蓝牙传输', 'ok': state in ('authenticated', 'alive', 'applied') and age <= 60,
                       'code': error or state, 'message': MESSAGES.get(error, '最近记录：' + states[state]),
                       'age_seconds': age})
    except (OSError, ValueError, AttributeError):
        checks.append({'label': '最近蓝牙传输', 'ok': None, 'code': 'transport_unknown',
                       'message': '尚无蓝牙传输记录。'})
    return {'ok': all(check['ok'] is not False for check in checks), 'checks': checks}
