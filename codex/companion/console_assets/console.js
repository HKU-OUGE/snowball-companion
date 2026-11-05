'use strict';
let browserSession='';try{browserSession=sessionStorage.getItem('snowball_console_session')||'';}catch{}
const $=id=>document.getElementById(id);
let latest=null,step=0,polling=false,actionPending=false,lastJob='',networkRequest=false,initialized=false;
const messages={usb_permission_required:'需要 USB 串口访问权限，请从安装向导点击授权 USB 访问。',firewall_enabled:'Windows 专用网络的局域网访问已设置。',firewall_not_enabled:'局域网访问未启用，可能取消了 Windows 管理员确认。原规则已保留。',remote_endpoint_invalid:'远程入口需填写带端口的 HTTPS 地址，例如 https://api.snowball.pet:443。',prepared:'连接环境准备好了。',bound:'身份绑定完成。连接进度见上方状态。',pairing_reused:'已复用原有配对。',services_started:'后台连接服务已启动，正在等待设备。',network_started:'局域网入口准备好了，请打开雪球的 Wi-Fi 配网页。',autostart_enabled:'已设置 Windows 登录自启。',dependencies_missing:'请先在安装向导或“自动配置”中完成连接环境准备。',usb_port_missing:'没有找到选中的雪球串口，请检查数据线并刷新。',usb_binding:'通过 USB 自动绑定雪球…',usb_port_ambiguous:'发现多个串口，请先选择要配置的雪球。',usb_binding_failed:'串口绑定未完成，请检查数据线、串口占用或权限后重试。',usb_pairing_confirmation_required:'设备已明确要求本机确认：请在雪球的“连接电脑”页开启配对，再回这里重试。已有配置保留。',usb_device_not_ready:'雪球还在启动，请保持 USB 连接，启动完成后重试。',usb_no_response:'没有收到串口回复，请检查数据线和串口，并先退出 USB 存储模式。',usb_binding_rejected:'设备拒绝了这次绑定，请通过 Setup 检查具体设备状态。',pairing_invalid:'已有配对文件不可用，请先核对已有设备绑定，不要重复覆盖。',lan_address_missing:'没有找到这个局域网地址，请让电脑连接局域网后刷新。',gateway_identity_conflict:'已有网关身份或地址与本次配置不同，已保留原配置。请通过 Setup 检查迁移。',autostart_failed:'登录自启未设置成功，请通过插件 Setup 检查任务归属。',job_busy:'当前操作还在进行，请稍等。',operation_failed:'操作没有完成，请检查网络或本机依赖后重试。',open_device_network_page:'请在雪球打开 Wi-Fi 配网页，并保持蓝牙连接。',provision_busy:'上一条配网还在处理中，请稍等。',provision_schema:'请检查 Wi-Fi 名称、密码长度和远程地址。',browser_session_required:'本地设置会话已过期，请重新双击桌面入口或从插件 Setup 打开。',local_service_unavailable:'本机服务暂时不可用，请先启动连接服务。',local_service_rejected:'本机服务拒绝了请求，请打开“选择功能”检查当前安装；详细错误保存在本机私有安装日志。',local_only:'请使用“打开雪球控制台”入口访问本机页面。'};
const jobs={firewall:'请在 Windows 弹出的窗口确认局域网访问权限…',prepare:'正在准备连接环境，首次下载可能需要几分钟…',bind:'正在通过 USB 核对设备身份…',services:'正在启动后台连接服务…',network:'正在准备局域网同步入口…',autostart:'正在设置 Windows 登录自启…'};
const phases={off:'Wi-Fi 已关闭',not_configured:'等待配置',ble_priority:'蓝牙优先，Wi-Fi 休眠',joining:'正在加入 Wi-Fi',gateway:'正在连接电脑网关',online:'Wi-Fi 同步正常',retrying:'连接重试中',wifi_auth_failed:'Wi-Fi 密码未通过验证',no_2ghz_ap:'未找到对应的 2.4 GHz 网络',no_memory:'设备暂时没有足够内存'};
const provision={queued:'正在通过加密蓝牙传输…',stored:'配置已加密保存，断电重启后仍可使用。',temporary:'配置仅在本次开机有效；设备尚未启用硬件加密保存。',failed:'配网未成功，请重新打开设备配网页后重试。',expired:'本次配网请求已过期，请重试。',open_device_network_page:messages.open_device_network_page,device_mismatch:'当前设备与配网目标不同，已停止传送。'};
function notice(message,error=false,busy=false){$('notice').textContent=message;$('notice').hidden=!message;$('notice').className=(error?'error ':'')+(busy?'busy':'');}
async function api(path,data){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const r=await fetch(path,{method:data?'POST':'GET',headers:{'X-Snowball-UI':'1',...(browserSession?{'X-Snowball-Session':browserSession}:{}),...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined,cache:'no-store',signal:controller.signal});const value=await r.json();if(!r.ok)throw new Error(value.error||'operation_failed');return value;}finally{clearTimeout(timer);}}
function choose(next){if(next===0&&latest?.installer?.started){location.assign('/install');return;}step=next;document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=Number(p.dataset.panel)!==next);document.querySelectorAll('[data-step]').forEach(b=>{if(Number(b.dataset.step)===next)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});$('mentor').textContent=['环境、语音和桌宠，我会一步步准备。','保持接线，在这里等我连接。','确认同步后，就回我这里练习吧。','基础连接已经够用，其他慢慢加。'][next];if(latest)guide(latest);if(next===1&&!latest?.paired)ports();}
document.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>choose(Number(b.dataset.step))));document.querySelectorAll('[data-next]').forEach(b=>b.addEventListener('click',()=>choose(Number(b.dataset.next))));
function checkList(target,rows){target.replaceChildren();for(const [title,description,label,tone] of rows){const row=document.createElement('div');row.className='check';const copy=document.createElement('div'),b=document.createElement('b'),small=document.createElement('small'),state=document.createElement('span');b.textContent=title;small.textContent=description;state.textContent=label;state.className=tone||'';copy.append(b,small);row.append(copy,state);target.append(row);}}
function updateSelect(select,values){const before=select.value;if(JSON.stringify([...select.options].map(o=>o.value))===JSON.stringify(values))return;select.replaceChildren();for(const value of values){const option=document.createElement('option');option.value=value;option.textContent=value;select.append(option);}if(values.includes(before))select.value=before;}
function guide(v){
 const ready=['bleak','serial','cryptography'].every(k=>v.dependencies?.[k]);
 let label='现在看电脑控制台',action='自动准备环境、登录自启动、语音和桌宠。完成状态会在下面逐项更新。';
 if(step===1){
  if(v.paired)action='已绑定的雪球可以直接连接；无需重复进入系统蓝牙设置或重新绑定。';
  else if(!ready)action='先完成连接环境准备，保持 USB 连接即可。';
  else if(!$('ports').value)action='先接上 USB 数据线并刷新串口，确认检测到雪球。';
  
  else action='保持 USB 连接。首次配置自动绑定；多台设备时核对下方串口再点绑定。';
 }else if(step===2)action='先看下方连接结果。连接确认后，雪球会自动继续顺序练习；做对后会开心回应并进入下一步。';
 else if(step===3)action='需要 Wi-Fi 时，按下方提示在雪球打开配网页，再在这里填写。其他功能的准备进度在“自动配置”中。';
 $('where-label').textContent=label;$('where-action').textContent=action;
}
let setupPending=false,setupDrawn="";
const featureNames={environment:'连接环境',autostart:'登录自启动',connection:'电脑连接服务',speech:'本地语音',pet:'账号桌宠'};
const setupStates={pending:'等待中',running:'正在配置',ready:'已就绪',failed:'需要重试',needs_user:'等待操作',skipped:'已跳过',unsupported:'不适用'};
const setupCodes={usb_pairing_confirmation_required:messages.usb_pairing_confirmation_required,usb_device_not_ready:messages.usb_device_not_ready,usb_no_response:messages.usb_no_response,usb_binding_rejected:messages.usb_binding_rejected,usb_binding_failed:messages.usb_binding_failed,usb_binding:messages.usb_binding,usb_port_missing:messages.usb_port_missing,usb_port_ambiguous:messages.usb_port_ambiguous,resources_cache:'正在复用已校验的电脑缓存…',resources_network:'正在从互联网下载，持续有数据时会继续下载…',resources_sd:'正在从 SD 卡导入并校验…',resources_sd_fallback:'网络不可用，正在尝试从 SD 卡导入并校验…',speech_sd_imported:'已从 SD 卡导入并校验模型，正在启动识别服务。',pending:'依次准备，已有配置会复用。',checking:'正在检查已有配置…',ready:'检查通过，配置已就绪。',interrupted:'上次配置被中断，可从这里继续。',operation_failed:'本项配置未完成，其他项目不受影响。',dependencies_missing:'请先完成连接环境准备。',autostart_failed:'登录自启动未通过检查，请重试。',autostart_conflict:'发现其他安装的后台配置，已保留原设置，等待 agent 核对归属。',autostart_unsupported:'当前系统暂不支持自动注册登录任务，可继续使用其他功能。',local_service_unavailable:'连接服务尚未就绪，可重试。',speech_dependencies:'正在安装本地识别环境…',speech_model:'正在检查本机模型缓存，大文件优先联网获取…',speech_starting:'正在加载模型，确认录音服务就绪…',speech_failed:'语音准备失败，可重试；已安装模型会复用。',speech_not_ready:'模型已准备，语音服务尚未通过健康检查。',speech_worker_upgrade_required:'现有语音进程版本较旧，等待 agent 更新服务后继续切换；原模型已保留。',agent_required:'等待配置 agent 连接。其他项目会继续准备。',pet_working:'agent 正在检查当前账号，并安装或复用宠物。',pet_verified:'agent 已通过当前账号的 Pets 接口确认。',pet_auth_required:'请完成宿主的 Pets 授权，完成后在此重试，agent 会继续。',pet_failed:'账号桌宠尚未确认，可重试；不会盲目重复创建。',pet_unsupported:'此适配器不提供 ChatGPT 账号桌宠，设备宠物仍可使用。',user_skipped:'已按你的选择跳过，可随时重试。'};
async function setupAction(operation,extra={}){
 if(setupPending)return;setupPending=true;if(latest)renderSetup(latest);
 try{await api('/api/setup',{operation,...extra});}
 catch(e){notice(({setup_agent_expired:'配置 agent 已断开，正在等待重新连接。',setup_adapter_conflict:'本次配置属于另一软件，请继续当前流程。',job_busy:'当前步骤正在处理，请稍后重试。',not_found:'设置服务版本较旧，请通过新版插件 Setup 重新打开控制台。'})[e.message]||'配置暂未完成，请稍后重试。',true);}
 finally{setupPending=false;await refresh();}
}
function renderSetup(v){
 const p=v.setup||{started:false,steps:{}},box=$('setup-checks');
 const drawKey=JSON.stringify([p.steps,p.local_running,setupPending,p.speech_model]);
 const needsHelp=Object.values(p.steps).some(x=>['failed','needs_user'].includes(x.state));
 $('setup-heading').textContent=p.complete?'电脑配置已完成':needsHelp?'有几项需要处理':p.started?'雪球正在为你准备':'准备开始自动配置';
 $('setup-detail').textContent=p.complete?'在本页连接设备并确认同步，随后即可使用。语音首次录音仍需要实际验证。':p.started?'你可以留在网页查看进度、重试或跳过。连接服务就绪后，可以先绑定设备。':'通过插件 Setup 打开会自动开始；普通打开控制台时，可点下方按钮启动。';
 $('setup-adapter').disabled=p.started||setupPending;if(p.started)$('setup-adapter').value=p.adapter;
 $('setup-start').disabled=setupPending||p.local_running;$('setup-start').textContent=p.started?'重新检查电脑配置':'开始自动配置';
 if(setupDrawn!==drawKey){box.replaceChildren();setupDrawn=drawKey;
 for(const [name,title] of Object.entries(featureNames)){
  const state=p.steps[name];if(!state)continue;
  const row=document.createElement('div');row.className='check';const copy=document.createElement('div'),b=document.createElement('b'),small=document.createElement('small'),badge=document.createElement('span');
  b.textContent=title;small.textContent=setupCodes[state.code]||'等待状态更新';badge.textContent=setupStates[state.state]||'待检查';badge.className=state.state==='ready'?'good':state.state==='failed'||state.state==='needs_user'?'warn':'';
  if(state.state==='running')b.className='setup-running';
  if(name==='speech'&&state.code==='speech_model')small.textContent+=` 预计模型约 ${{tiny:78,base:148,small:486}[p.speech_model]||148} MB，不含依赖。`;
  copy.append(b,small);row.append(copy,badge);
  const controls=document.createElement('div');controls.className='setup-row-actions';
  if(['failed','needs_user','skipped'].includes(state.state)){const retry=document.createElement('button');retry.type='button';retry.textContent='重试';retry.setAttribute('aria-label','重试'+title);retry.disabled=setupPending||(name!=='pet'&&p.local_running);retry.onclick=()=>setupAction('retry',{feature:name});controls.append(retry);}
  if(['autostart','speech','pet'].includes(name)&&['pending','failed','needs_user'].includes(state.state)){const skip=document.createElement('button');skip.type='button';skip.textContent='暂不配置';skip.setAttribute('aria-label','暂不配置'+title);skip.disabled=setupPending||(name!=='pet'&&p.local_running);skip.onclick=()=>setupAction('skip',{feature:name});controls.append(skip);}
  if(controls.children.length)row.append(controls);box.append(row);
 }
 }
 const picker=$('setup-character'),characters=v.pet_catalog||[],ids=['auto',...characters.map(x=>x.id)];
 if(JSON.stringify([...picker.options].map(o=>o.value))!==JSON.stringify(ids)){picker.replaceChildren();for(const id of ids){const o=document.createElement('option');o.value=id;o.textContent=id==='auto'?'沿用当前宠物 · 首次使用雪球':characters.find(x=>x.id===id).name;picker.append(o);}}
 if(p.started){picker.value=p.character||'auto';$('setup-model').value=p.speech_model||'auto';}
 picker.disabled=!p.started||p.adapter!=='codex'||p.steps.pet?.state==='running'||setupPending;
 $('pet-choice-field').hidden=p.started&&p.adapter!=='codex';
 $('setup-model').disabled=!p.started||p.steps.speech?.state==='running'||setupPending;
}
$('setup-start').onclick=()=>setupAction('start',{adapter:$('setup-adapter').value});
$('setup-character').onchange=()=>setupAction('character',{value:$('setup-character').value});
$('setup-model').onchange=()=>setupAction('speech_model',{value:$('setup-model').value});
function render(v){latest=v;const n=v.network,nw=n?.network||{},ble=!!(n?.ble||v.telemetry?.connected&&v.telemetry.transport==='ble'),deps=v.dependencies||{},ready=['bleak','serial','cryptography'].every(k=>deps[k]);const busy=actionPending||v.job?.state==='running';renderSetup(v);if(!initialized){initialized=true;choose(v.installer?.started?(v.paired?2:1):v.setup?.started&&!v.setup.complete?0:v.paired?2:0);}document.querySelector('[data-step="0"] b').textContent=v.installer?.started?'选择功能':'自动配置';$('version').textContent=`随插件提供 · v${v.version} · 本地运行`;$('hub-chip').textContent=v.hub?'电脑服务 · 已就绪':'电脑服务 · 未启动';$('hub-chip').className=v.hub?'good':'';$('device-chip').textContent=ble?'雪球 · 蓝牙已连接':v.paired?'雪球 · 已绑定':'雪球 · 未绑定';$('device-chip').className=ble?'good':'';const synced=!!v.telemetry?.status_current; $('route-chip').textContent=synced?'状态 · 已收到设备回执':'状态 · 等待同步';$('route-chip').className=synced?'good':'';guide(v);
checkList($('dependency-checks'),[['Python 环境',v.platform==='windows'?'Windows 原生运行':'本机 Python 运行时','已就绪','good'],['蓝牙与 USB 支持','用于设备发现、首次绑定和状态同步',deps.bleak&&deps.serial?'已准备':'待准备',deps.bleak&&deps.serial?'good':''],['加密连接支持','用于安全配网和局域网证书',deps.cryptography?'已准备':'待准备',deps.cryptography?'good':'']]);
$('prepare').disabled=busy;$('prepare').textContent=ready?'检查并更新依赖':'准备连接环境';$('start-services').disabled=busy||(!ready&&!v.hub);$('connect-existing').disabled=busy||(!ready&&!v.hub);$('bound-card').hidden=!v.paired;$('bind-card').hidden=v.paired;$('device-name').textContent=v.device||'';$('bind').disabled=busy||!ready||!$('ports').value;
$('gateway-card').hidden=!!n;updateSelect($('lan-address'),v.addresses||[]);$('init-network').disabled=busy||!ready||!v.paired||!$('lan-address').value;$('window-hint').classList.toggle('open',!!(n?.ble&&nw.window));$('window-hint').querySelector('p').textContent=n?.ble&&nw.window?'安全配网窗口已打开，可以传送。':'保持页面打开，电脑就能安全传送配置。';$('provision').disabled=busy||networkRequest||!n?.ble||!nw.window;$('provision-hint').textContent=networkRequest?'正在提交…':n?.ble&&nw.window?'安全配网窗口已打开':'等待蓝牙和配网窗口';
if(n?.provision&&provision[n.provision]&&(!v.job||v.job.state!=='running')){notice(provision[n.provision],['failed','expired','device_mismatch','open_device_network_page'].includes(n.provision),n.provision==='queued');}
checkList($('diagnostics'),[
 ['电脑后台',v.hub?'本机 Hub 正常响应':'先在“自动配置”启动连接服务',v.hub?'已就绪':'待启动',v.hub?'good':''],
 ['设备身份',v.device||'第一次通过 USB 确认设备身份',v.paired?'已绑定':'待绑定',v.paired?'good':''],
 ['蓝牙连接',ble?'已收到当前设备认证后的新鲜状态':'系统已配对不等于应用已连接',ble?'已确认':'等待连接',ble?'good':''],
 ['设备状态同步',synced?'设备已回执收到状态；请再核对当前会话内容':'连接后仍需等待真实会话状态与设备回执',synced?'已收到回执':'待验证',synced?'good':''],
 ['Wi-Fi（可选）',nw.configured?(nw.sealed?'配置已加密保存；'+(phases[nw.state]||'等待网络状态'):'配置仅在本次开机有效'):'无需配置 Wi-Fi，也可通过蓝牙同步',nw.configured?'已配置':'可跳过',nw.configured?'good':'']]);
 const capability=v.capabilities||{}, fresh=capability.fresh===true;
 checkList($('capability-checks'),[
 ['Codex 指令通道',fresh&&(capability.can_instruct||capability.can_create)?'运行中 Hub 已发现投递能力，实际发送需另行确认':'在当前 Codex 会话运行 Setup 检查，不需要重新配对',fresh&&(capability.can_instruct||capability.can_create)?'通道可用':'待检查',fresh&&(capability.can_instruct||capability.can_create)?'good':''],
 ['当前问题回答',fresh&&capability.pending?`${capability.pending} 条待答中，${capability.answerable} 条允许设备回答`:'没有可用于验证的待答问题，教程练习不算真实回答',fresh&&capability.pending?(capability.answerable?'有可答问题':'请在电脑处理'):'尚未实测',fresh&&capability.answerable?'good':''],
 ['本地语音识别',v.speech?.ready?`电脑识别服务已就绪 · ${v.speech.profile||'本地模型'}；首次录音仍需实际验证`:'在“自动配置”查看安装进度，失败可以在网页重试',v.speech?.ready?'服务就绪':'准备中',v.speech?.ready?'good':'']]);
 $('check-title').textContent=!v.hub?'先启动电脑服务':!v.paired?'先绑定雪球':!ble&&!n?.online?'等待设备连接':!synced?'连接好了，继续确认会话同步':'下一站：看雪球，继续练习';
 $('check-action').textContent=!v.hub?'回“自动配置”点启动连接服务。':!v.paired?'回“连接雪球”，完成 USB 身份绑定。':!ble&&!n?.online?'保持雪球开机，已绑定设备由后台自动重连，无需反复开启公开配对。':!synced?'回到已安装插件的 AI 会话正常使用一次；若宿主提示，审阅 Hooks 后回这里查看。仅旧会话未加载插件时再用新会话验证。':'先在雪球上滑查看会话，核对正在使用的聊天。没有任务时显示“等待新任务”是正常的；完成后继续设备手势练习。';
$('firewall-card').hidden=v.platform!=='windows';$('firewall').disabled=busy||!v.gateway_configured;$('autostart-card').hidden=true;$('autostart').disabled=busy||!ready||!v.paired;[v.installer?.started?!!v.installer.complete:!!v.setup?.complete,ble||!!n?.online,synced,!!nw.configured].forEach((done,i)=>$('mark-'+i).textContent=done?'✓':'');
if(v.job){if(v.job.state==='running')notice(jobs[v.job.action]||'正在处理…',false,true);else if(lastJob!==v.job.id+v.job.state){lastJob=v.job.id+v.job.state;notice(messages[v.job.result]||'操作已结束，请查看连接状态。',v.job.state==='failed');}}
}
async function refresh(){if(polling||document.hidden)return;polling=true;try{render(await api('/api/status'));}catch(e){notice(messages[e.message]||'控制台暂时不可用，请重新双击“打开雪球控制台”。',true);}finally{polling=false;}}
async function act(action,extra={}){if(actionPending||latest?.job?.state==='running')return;actionPending=true;if(latest)render(latest);notice(jobs[action]||'正在处理…',false,true);try{await api('/api/action',{action,...extra});}catch(e){notice(messages[e.message]||'操作未完成，请重试。',true);}finally{actionPending=false;await refresh();}}
async function ports(){try{const v=await api('/api/ports'),select=$('ports'),old=select.value;select.replaceChildren();for(const p of v.ports){const option=document.createElement('option');option.value=p.port;option.textContent=`${p.port} · ${p.name}`;select.append(option);}if(!v.ports.length){const option=document.createElement('option');option.value='';option.textContent='未检测到雪球，请检查数据线';select.append(option);}else if(v.ports.some(p=>p.port===old))select.value=old;if(latest)render(latest);}catch(e){notice('串口检测未完成，请先准备连接环境。',true);}}
$('prepare').onclick=()=>act('prepare');$('start-services').onclick=$('connect-existing').onclick=()=>act('services');$('refresh-ports').onclick=ports;$('ports').onchange=()=>{if(latest)render(latest);};$('bind').onclick=()=>act('bind',{port:$('ports').value});$('init-network').onclick=()=>act('network',{bind:$('lan-address').value});$('autostart').onclick=()=>act('autostart');$('firewall').onclick=()=>act('firewall');
$('wifi-form').addEventListener('submit',async e=>{e.preventDefault();if(networkRequest||$('provision').disabled)return;networkRequest=true;const data={ssid:$('ssid').value,password:$('password').value,mode:Number($('mode').value),remote:$('remote').value.trim()};if(latest)render(latest);try{await api('/api/provision',data);notice('配置已提交，正在等待雪球确认。',false,true);}catch(error){notice(messages[error.message]||'配网请求未成功，请检查网络和配网窗口。',true);}finally{$('password').value='';data.password='';networkRequest=false;await refresh();}});
async function start(){let ticket=new URLSearchParams(location.hash.slice(1)).get('setup');if(ticket){history.replaceState(null,'',location.pathname);try{const auth=await api('/api/bootstrap',{ticket});browserSession=auth.session;try{sessionStorage.setItem('snowball_console_session',browserSession);}catch{}}catch(e){notice('控制台入口已过期，请重新双击桌面入口或从插件 Setup 打开。',true);return;}finally{ticket='';}}await refresh();setInterval(refresh,2000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});}start();
