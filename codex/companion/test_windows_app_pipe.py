"""Native isolated pipes: no live app messages, Hub or device changes."""
import json
import os
import struct
import threading
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, Mock

from app_tools import AppTools, AppToolError


class ConfirmedMessageTests(unittest.TestCase):
    def test_persisted_unknown_result_never_replays_or_saves_text(self):
        from pathlib import Path
        from status_hub import StatusHub, Invalid
        from codex_desktop import DeliveryError
        with tempfile.TemporaryDirectory() as folder:
            desktop=Mock(); desktop.summaries={}; desktop.available.return_value=True
            desktop.instruction_route.return_value={'thread':'exact-selected-thread'}
            desktop.deliver_instruction.side_effect=DeliveryError('delivery_unconfirmed',True)
            hub=StatusHub(Path(folder),desktop=desktop)
            hub.accept({'version':1,'provider':'Codex','source':'codex-hooks','session_id':'a'*32,
                'seq':1,'event_id':'event','timestamp':time.time(),'kind':'task','phase':'idle'})
            request={'session':'codex-hooks/'+'a'*32,'text':'只发到选中的会话','request_id':'b'*32}
            self.assertEqual(hub.message(request)['delivery'],'unconfirmed')
            restored=StatusHub(Path(folder),desktop=desktop)
            self.assertEqual(restored.message(request)['delivery'],'unconfirmed')
            desktop.deliver_instruction.assert_called_once_with(request['session'],request['text'],request['request_id'])
            self.assertNotIn(request['text'],hub.message_path.read_text())
            with self.assertRaises(Invalid): restored.message({**request,'text':'different'})


@unittest.skipUnless(os.name == 'nt', 'Windows answer routing')
class ConfirmedAnswerTests(unittest.TestCase):
    def adapter(self):
        from codex_desktop import CodexDesktop
        desktop=CodexDesktop()
        desktop.app_tools=Mock()
        desktop.app_tools.available.return_value=True
        desktop.app_tools.call.return_value={'status':'queued'}
        desktop.ipc_factory=Mock(side_effect=AssertionError('Never fall back to Unix IPC'))
        self.route={'thread':'selected-thread','call':'exact-call','index':2,
            'question':'下一步？','options':['继续','暂停'],'kind':'async','activity':'waiting'}
        desktop.route=Mock(return_value=dict(self.route))
        return desktop

    def test_exact_question_envelope_uses_selected_thread_without_overrides(self):
        desktop=self.adapter()
        self.assertEqual(desktop.deliver('session','question','语音回答','request'),{'status':'accepted'})
        tool,args,caller,rid=desktop.app_tools.call.call_args.args
        self.assertEqual((tool,caller,rid),('send_message_to_thread','selected-thread','request'))
        self.assertEqual(set(args),{'threadId','hostId','prompt'})
        body=json.loads(args['prompt'].split('\n')[1])[0]
        self.assertEqual(json.loads(body['questionItemId']),['request_user_input_async','exact-call',2])
        self.assertEqual(body['answer'],'语音回答')
        desktop.ipc_factory.assert_not_called()

    def test_changed_or_expired_question_is_not_sent(self):
        from codex_desktop import DeliveryError
        for current in (None,{**self.adapter().route.return_value,'call':'different-call'}):
            desktop=self.adapter();desktop.route.side_effect=[dict(self.route),current]
            with self.assertRaises(DeliveryError): desktop.deliver('s','q','a','r')
            desktop.app_tools.call.assert_not_called()

    def test_unknown_result_does_not_fall_back_or_retry(self):
        from codex_desktop import DeliveryError
        desktop=self.adapter(); desktop.app_tools.call.side_effect=AppToolError('delivery_unconfirmed',True)
        with self.assertRaises(DeliveryError) as failure: desktop.deliver('s','q','a','r')
        self.assertTrue(failure.exception.uncertain)
        self.assertEqual(desktop.app_tools.call.call_count,1)
        desktop.ipc_factory.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows named pipes')
class NativePipeTests(unittest.TestCase):
    def test_concurrent_health_probe_is_false_until_verified(self):
        started,release=threading.Event(),threading.Event()
        app=AppTools();results=[]
        def probe(*args):
            started.set();release.wait(2)
            return {'tools':[{'name':'send_message_to_thread','namespace':'codex_app'}]}
        with patch.object(app,'rpc',side_effect=probe):
            thread=threading.Thread(target=lambda:results.append(app.available()))
            thread.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertFalse(app.available())
            finally:
                release.set();thread.join(3)
            self.assertEqual(results,[True])

    def server(self, respond):
        import _winapi
        name = '\\\\.\\pipe\\codex-app-tools-' + str(uuid.uuid4())
        handle = _winapi.CreateNamedPipe(name, 3, 0, 1, 65536, 65536, 0, 0)
        errors = []
        def run():
            try:
                try: _winapi.ConnectNamedPipe(handle, overlapped=False)
                except OSError as exc:
                    if exc.winerror != 535: raise
                respond(handle)
            except BaseException as exc: errors.append(exc)
            finally: _winapi.CloseHandle(handle)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        return name, errors

    def test_real_fragmented_framing_and_unicode(self):
        import _winapi
        seen = []
        def respond(handle):
            head, _ = _winapi.ReadFile(handle, 4)
            size = struct.unpack('<I', head)[0]
            data = bytearray()
            while len(data) < size:
                chunk, _ = _winapi.ReadFile(handle, size-len(data)); data.extend(chunk)
            seen.append(json.loads(data))
            raw = json.dumps({'jsonrpc':'2.0','id':1,'result':{'text':'确认文字'}}, ensure_ascii=False).encode()
            frame = struct.pack('<I',len(raw))+raw
            for part in (frame[:2],frame[2:7],frame[7:]): _winapi.WriteFile(handle,part)
        name, errors = self.server(respond)
        with patch.dict(os.environ,CODEX_APP_TOOLS_PIPE_PATH=name), patch('windows_app_pipe.verify_server'):
            result = AppTools().rpc('tools/list', {'threadStartKind':'all'})
        self.assertEqual(result, {'text':'确认文字'})
        self.assertEqual(seen[0]['method'],'tools/list'); self.assertFalse(errors)

    def test_untrusted_python_server_rejected_before_request(self):
        import _winapi
        received = []
        def respond(handle):
            try: received.append(_winapi.ReadFile(handle,4)[0])
            except OSError: pass
        name, _ = self.server(respond)
        with patch.dict(os.environ,CODEX_APP_TOOLS_PIPE_PATH=name):
            with self.assertRaises(AppToolError) as failure:
                AppTools().rpc('tools/list',{})
        self.assertFalse(failure.exception.uncertain)
        self.assertFalse(any(received))

    def test_timeout_cancels_read_and_closes_pipe(self):
        import _winapi
        from windows_app_pipe import WindowsAppPipe
        release = threading.Event()
        self.addCleanup(release.set)
        name, _ = self.server(lambda handle: release.wait(1))
        with patch.dict(os.environ,CODEX_APP_TOOLS_PIPE_PATH=name), patch('windows_app_pipe.verify_server'):
            with WindowsAppPipe(name, timeout=.05) as connection:
                with self.assertRaises((TimeoutError,OSError)): connection.recv(4)
            self.assertIsNone(connection.handle)

    def test_write_disconnect_is_uncertain_and_never_retried(self):
        import _winapi
        received = []
        def respond(handle):
            head, _ = _winapi.ReadFile(handle,4)
            body, _ = _winapi.ReadFile(handle,struct.unpack('<I',head)[0])
            received.append(json.loads(body))
        name, _ = self.server(respond)
        with patch.dict(os.environ,CODEX_APP_TOOLS_PIPE_PATH=name), patch('windows_app_pipe.verify_server'):
            with self.assertRaises(AppToolError) as failure:
                AppTools().rpc('tools/call',{},write=True)
        self.assertTrue(failure.exception.uncertain)
        self.assertEqual(len(received),1)

    def test_foreign_endpoint_and_isolated_home_fail_closed(self):
        from pathlib import Path
        from windows_app_pipe import endpoint
        for value in ('tcp://localhost:1', r'\\remote\pipe\codex-app-tools-test', r'\\.\pipe\other'):
            with patch.dict(os.environ,CODEX_APP_TOOLS_PIPE_PATH=value):
                self.assertIsNone(endpoint())
        self.assertIsNone(AppTools(Path.home()/'isolated-test-home').pipe())


if __name__ == '__main__': unittest.main()
