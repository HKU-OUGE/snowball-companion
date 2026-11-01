"""Explicit USB selection and authenticated initial binding; never auto-claim."""
import asyncio
import time

import pairing_store
import connection_diagnostics as diagnostics


class SetupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def enumerate_ports():
    from serial.tools import list_ports
    entries = []
    for item in list_ports.comports():
        entries.append({'port': item.device, 'description': item.description or '',
                        'vid': item.vid, 'pid': item.pid,
                        'serial_number': item.serial_number,
                        'manufacturer': item.manufacturer, 'product': item.product,
                        'location': item.location})
    return sorted(entries, key=lambda item: item['port'])


def read_pairing(port, timeout=12):
    """Returns sensitive config ONLY to the caller. Never print this return value.

    The port must be selected explicitly. DTR/RTS are deasserted before open;
    no bootloader reset, touchscreen command, Wi-Fi setup or other command runs.
    """
    if not isinstance(port, str) or not port.strip() or '\x00' in port:
        raise SetupError('serial_selection_required')
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise SetupError('invalid_timeout')
    stream = None
    try:
        import serial
        stream = serial.Serial()
        stream.port, stream.baudrate = port, 115200
        stream.timeout, stream.write_timeout = min(.2, timeout), 3
        stream.dtr = False
        stream.rts = False
        stream.open()
        stream.reset_input_buffer()
        stream.write(b':PAIR\n')
        stream.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = stream.readline(4096)
            if b'XQPAIR_ERROR ' in raw:
                error = raw.split(b'XQPAIR_ERROR ', 1)[1].strip()
                if error == b'open_connection_page':
                    raise SetupError('connection_page_required')
                raise SetupError('device_not_ready' if error == b'not_ready' else 'pairing_rejected')
            if b'XQPAIR ' in raw:
                try:
                    return pairing_store.validate(pairing_store._json(raw.split(b'XQPAIR ', 1)[1].strip()))
                except pairing_store.PairingStoreError:
                    raise SetupError('invalid_credentials') from None
        raise SetupError('serial_timeout')
    except (SetupError, ImportError):
        raise
    except Exception:
        raise SetupError('serial_unavailable') from None
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


async def bind_device(port, path=pairing_store.DEFAULT_PATH):
    """Save only after BLE HMAC proves the serial candidate's identity."""
    state = await asyncio.to_thread(diagnostics.background_status)
    if not state['ok']:
        return state
    try:
        config = await asyncio.to_thread(read_pairing, port)
        checked = await diagnostics.check_connection(config)
        if not checked['ok']:
            return checked
        await asyncio.to_thread(pairing_store.save, config, path)
        return {'ok': True, 'code': 'bound', 'message': '设备身份验证通过，绑定资料已安全保存。', 'saved': True}
    except ImportError:
        return diagnostics.result('dependency_missing')
    except (SetupError, pairing_store.PairingStoreError) as error:
        messages = {'serial_selection_required': '请明确选择设备的 COM 串口。',
                    'serial_unavailable': '无法打开或读取所选串口，请检查线缆、驱动和是否有其他串口程序占用。',
                    'serial_timeout': '所选串口未及时返回绑定资料，请打开设备上的连接页面后重试。',
                    'device_not_ready': '设备尚未准备好接受绑定。',
                    'pairing_rejected': '设备拒绝了本次绑定请求。'}
        answer = diagnostics.result(error.code)
        answer['message'] = messages.get(error.code, answer['message'])
        return answer
