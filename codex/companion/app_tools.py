"""Narrow, same-user Codex app-tools transport for explicitly confirmed device actions.

Reads the installed app's own pipe configuration; does not patch Codex, change
permissions/model settings, or expose a generic tool endpoint to the browser/BLE.
"""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import uuid

READ_ONLY={'list_projects','list_threads','read_thread'}
ALLOWED=READ_ONLY|{'send_message_to_thread','create_thread'}
class AppToolError(ValueError):
    def __init__(self,code,uncertain=False):self.code=code;self.uncertain=uncertain;super().__init__(code)

class AppTools:
    def __init__(self,home=None):self.home=Path(home or Path.home())
    def pipe(self):
        # Windows named-pipe Desktop control has not been implemented. Hooks
        # and read-only catalogues remain available; never guess an IPC path.
        if os.name == 'nt':
            return None
        candidates=[]
        if self.home==Path.home():
            if os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'):candidates.append(os.environ['CODEX_APP_TOOLS_PIPE_PATH'])
            # The app injects this one non-secret endpoint into its bundled MCP
            # process, rather than persisting it in .mcp.json. Read no other
            # environment value; never scan arbitrary sockets or credentials.
            for process in Path('/proc').iterdir():
                if not process.name.isdigit():continue
                try:
                    if process.stat().st_uid!=os.getuid():continue
                    argv=(process/'cmdline').read_bytes().split(b'\0')
                    app_server=b'app-server' in argv and argv and Path(os.fsdecode(argv[0]))==Path('/usr/lib/chatgpt/resources/codex')
                    module=Path('/usr/lib/chatgpt/resources/codex') if app_server else next((Path(os.fsdecode(a)) for a in argv if b'codex-app-tools/' in a and a.endswith(b'/server.mjs')),None)
                    if module is None or module.is_symlink():continue
                    trusted=app_server or module.is_relative_to(self.home/'.codex/plugins/cache/openai-bundled/codex-app-tools') or module==Path('/usr/lib/chatgpt/resources/plugins/openai-bundled/plugins/codex-app-tools/server.mjs')
                    if not trusted or module.stat().st_mode&0o022:continue
                    value=next((x.split(b'=',1)[1] for x in (process/'environ').read_bytes().split(b'\0') if x.startswith(b'CODEX_APP_TOOLS_PIPE_PATH=')),None)
                    if value:candidates.append(os.fsdecode(value))
                except (OSError,ValueError):continue
        base=self.home/'.codex/plugins/cache/openai-bundled/codex-app-tools'
        for config in sorted(base.glob('*/.mcp.json'),reverse=True):
            try:
                st=config.lstat()
                if config.is_symlink() or st.st_uid!=os.getuid() or st.st_mode&0o022 or st.st_size>65536:continue
                candidates.append(json.loads(config.read_text())['mcpServers']['codex_app']['env']['CODEX_APP_TOOLS_PIPE_PATH'])
            except (OSError,ValueError,KeyError,TypeError):continue
        for value in candidates:
            try:
                path=Path(value);st=path.lstat()
                if path.is_absolute() and stat.S_ISSOCK(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o022:return path
            except (OSError,ValueError):continue
        return None
    def available(self):return self.pipe() is not None
    def rpc(self,method,params,write=False):
        path=self.pipe()
        if path is None:raise AppToolError('desktop_app_unavailable')
        sent=False
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(20);sock.connect(str(path))
                body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params},ensure_ascii=False).encode()
                if len(body)>16384:raise AppToolError('invalid_app_request')
                sent=True;sock.sendall(struct.pack('<I',len(body))+body)
                def read(count):
                    data=bytearray()
                    while len(data)<count:
                        part=sock.recv(count-len(data))
                        if not part:raise ConnectionError('closed')
                        data.extend(part)
                    return bytes(data)
                length=struct.unpack('<I',read(4))[0]
                if not 0<length<=8*1024*1024:raise ValueError('invalid_app_frame')
                response=json.loads(read(length))
                if not isinstance(response,dict) or response.get('id')!=1 or response.get('jsonrpc')!='2.0' or 'result' not in response:raise ValueError('invalid_app_response')
                return response['result']
        except AppToolError:raise
        except (OSError,ValueError,KeyError,TypeError,struct.error) as exc:
            raise AppToolError('delivery_unconfirmed' if write and sent else 'desktop_app_unavailable',write and sent) from exc
    def call(self,tool,args,caller,request_id):
        if tool not in ALLOWED or not isinstance(caller,str) or not caller:raise AppToolError('invalid_app_action')
        catalog=self.rpc('tools/list',{'threadStartKind':'all'})
        if not isinstance(catalog,dict) or not isinstance(catalog.get('tools'),list):raise AppToolError('desktop_protocol_changed')
        spec=next((x for x in catalog.get('tools',[]) if x.get('name')==tool and x.get('namespace')=='codex_app'),None)
        if not spec:raise AppToolError('desktop_action_unavailable')
        value=self.rpc('tools/call',{'arguments':args,'callerSource':'codex','hostId':'local',
            'namespace':'codex_app','threadId':caller,'tool':tool,
            'turnId':'snowball-device-'+request_id,'callId':'snowball-device-'+request_id},write=tool not in READ_ONLY)
        if not isinstance(value,dict) or value.get('success') is not True:raise AppToolError('delivery_unconfirmed',tool not in READ_ONLY)
        for item in value.get('contentItems',[]):
            if item.get('type')=='inputText':
                try:result=json.loads(item['text'])
                except (ValueError,KeyError,TypeError):continue
                if isinstance(result,dict):return result
        raise AppToolError('delivery_unconfirmed',tool not in READ_ONLY)
