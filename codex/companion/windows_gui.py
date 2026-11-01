"""Native first-run and repair wizard. BLE work never runs on Tk's UI thread."""
import asyncio
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import windows_install as installer

PAIRING = Path.home() / '.config/xueqiu/ble-pairing.json'
STEPS = ('安装到电脑', '连接 Codex', '绑定雪球', '后台运行', '连接诊断')


def pause_for_probe(operation):
    """Avoid simultaneous GATT clients; restore an already running supervisor."""
    import windows_runtime as runtime
    before = runtime.status()
    if before.get('state') in ('unreachable', 'unverified'):
        raise RuntimeError('后台归属或状态无法核实，请先查看诊断。')
    was_running = before.get('running', False)
    if was_running:
        runtime.stop()
    try:
        return asyncio.run(operation())
    finally:
        if was_running:
            runtime.start()


def runtime_start(autostart):
    import windows_runtime as runtime
    installer.require_installed()
    result = runtime.start()
    if autostart:
        result = {**result, 'autostart': runtime.install_autostart()}
    return result


class Wizard:
    def __init__(self, window, smoke_test=False):
        self.window = window
        self.busy = False
        self.queue = queue.Queue()
        self.report = None
        self.step = 0
        self.ports = []
        window.title('Snowball Companion · 设置与诊断')
        window.geometry('1000x740')
        window.minsize(900, 680)
        window.configure(bg='#101c1b')
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 11), background='#172623', foreground='#edf8f1')
        style.configure('TFrame', background='#172623')
        style.configure('TLabel', padding=(0, 4))
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'))
        style.configure('Muted.TLabel', foreground='#b4c9be')
        style.configure('TButton', padding=(16, 10), background='#2b443a')
        style.map('TButton', background=[('active', '#3e6050')])
        style.configure('Accent.TButton', background='#a3ebc0', foreground='#112219', font=('Segoe UI', 11, 'bold'))
        style.map('Accent.TButton', background=[('active', '#c8f7d8')])
        style.configure('TEntry', fieldbackground='#eff8f3', foreground='#152a20')
        style.configure('TCombobox', fieldbackground='#eff8f3', foreground='#152a20')
        self.frame = ttk.Frame(window, padding=24)
        self.frame.pack(fill='both', expand=True, padx=12, pady=12)
        ttk.Label(self.frame, text='雪球陪你连接电脑', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self.frame, text='Snowball Companion  ' + installer.VERSION + '  ·  Windows 原生版', style='Muted.TLabel').pack(anchor='w')
        nav = ttk.Frame(self.frame)
        nav.pack(fill='x', pady=(20, 16))
        self.nav_buttons = []
        for i, text in enumerate(STEPS):
            button = ttk.Button(nav, text=f'{i+1}  {text}', command=lambda step=i: self.show(step))
            button.pack(side='left', expand=True, fill='x', padx=(0, 4))
            self.nav_buttons.append(button)
        content = ttk.Frame(self.frame)
        content.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(content, background='#172623', highlightthickness=0)
        scroll = ttk.Scrollbar(content, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        body_window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(body_window, width=event.width))
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta / 120), 'units'))
        self.status = tk.StringVar(value='首次绑定需要 USB 数据线；绑定成功后，日常同步只需蓝牙与后台程序。')
        self.progress = ttk.Progressbar(self.frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=(12, 4))
        ttk.Label(self.frame, textvariable=self.status, wraplength=910, style='Muted.TLabel').pack(anchor='w')
        footer = ttk.Frame(self.frame)
        footer.pack(fill='x', pady=(12, 0))
        ttk.Label(footer, text='关闭这个窗口后，已启动的后台程序会继续同步。', style='Muted.TLabel').pack(side='left')
        self.next_button = ttk.Button(footer, text='下一步 →', command=lambda: self.show(min(4, self.step+1)))
        self.next_button.pack(side='right')
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.show(0)
        window.after(80, self.poll)
        if smoke_test:
            # Render every real page without pairing, registration or startup changes.
            for step in range(5):
                self.show(step)
                window.update_idletasks()
            window.after(250, window.destroy)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请等当前步骤完成后再关闭，避免中断安装或首次绑定。', parent=self.window)
            return
        self.window.destroy()

    def paragraph(self, text, muted=False):
        label = ttk.Label(self.body, text=text, wraplength=890, justify='left', style='Muted.TLabel' if muted else 'TLabel')
        label.pack(anchor='w', fill='x', pady=(0, 10))
        label.bind('<Configure>', lambda event: label.configure(wraplength=max(240, event.width-8)))

    def button(self, text, callback, accent=False):
        button = ttk.Button(self.body, text=text, command=callback, style='Accent.TButton' if accent else 'TButton')
        button.pack(anchor='w', pady=5)
        return button

    def submit(self, label, action, done=None):
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        self.progress.start(12)
        for button in [*self.nav_buttons, self.next_button]:
            button.state(['disabled'])
        def worker():
            try:
                self.queue.put((True, action(), done))
            except Exception as error:
                # Only our curated installer errors are user text. Never show raw BLE/USB errors.
                if isinstance(error, installer.InstallError):
                    safe = str(error)
                else:
                    from windows_runtime import error_report
                    report = error_report(error)
                    safe = report['message'] + ' [' + report['code'] + ']'
                self.queue.put((False, safe, None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            ok, value, done = self.queue.get_nowait()
            self.busy = False
            self.progress.stop()
            for button in [*self.nav_buttons, self.next_button]:
                button.state(['!disabled'])
            if not ok:
                self.status.set(value)
            elif done:
                done(value)
            else:
                self.status.set('操作完成。')
        except queue.Empty:
            pass
        self.window.after(80, self.poll)

    def show(self, step):
        if self.busy:
            return
        self.step = step
        self.canvas.yview_moveto(0)
        for item in self.body.winfo_children():
            item.destroy()
        for i, button in enumerate(self.nav_buttons):
            button.configure(style='Accent.TButton' if i == step else 'TButton')
        self.next_button.configure(text='完成' if step == 4 else '下一步 →', command=self.close if step == 4 else lambda: self.show(step+1))
        getattr(self, 'page_' + str(step))()

    def page_0(self):
        self.paragraph('安装一次，之后从开始菜单打开。')
        self.paragraph('安装器自带 Python 和蓝牙依赖，不需要 WSL、管理员密码或手动运行终端。文件放在当前用户的应用目录，配对数据独立保留。', True)
        self.paragraph('升级会短暂停止本机雪球后台，再由下一步恢复。不会修改设备固件、重置手表或删除账号里的 Pet。', True)
        self.button('安装到这台电脑', self.install_native, True)
        self.button('打开已安装版本', self.open_installed)
        self.button('修复损坏的安装（后台需已停止）', self.repair)
        self.paragraph('如果已经在安装目录中运行，直接进入下一步。', True)

    def install_native(self):
        if not getattr(sys, 'frozen', False):
            self.status.set('源码模式：请使用 Windows CI 生成的完整 Snowball-Windows-x64.zip 进行原生安装。')
            return
        def install():
            return installer.install_and_switch(Path(sys.executable).parent)
        def done(result):
            self.status.set('已安装。正在打开固定目录中的设置向导，请继续连接 Codex。')
            if Path(sys.executable).absolute() != result['gui'].absolute():
                subprocess.Popen([str(result['gui'])], close_fds=True)
                self.window.destroy()
            else:
                self.show(1)
        self.submit('正在校验安装包并安装…', install, done)

    def open_installed(self):
        if self.busy:
            return
        try:
            current = installer.read_current()
            if not current:
                self.status.set('这台电脑尚未安装。请先点“安装到这台电脑”。')
                return
            subprocess.Popen([str(current['gui'])], close_fds=True)
            self.window.destroy()
        except (OSError, ValueError):
            self.status.set('无法打开已安装版本，请检查安装目录。')

    def repair(self):
        if not getattr(sys, 'frozen', False):
            self.status.set('请从相同版本的完整 Windows 安装包中执行修复。')
            return
        self.submit('正在确认后台已停止并修复；旧目录和配置会保留…',
            lambda: installer.repair_installation(Path(sys.executable).parent),
            lambda _: self.status.set('修复完成，旧目录已隔离保留。请打开已安装版本并启动后台。'))

    def page_1(self):
        self.paragraph('让 Codex 将状态交给雪球。')
        self.paragraph('选择 Codex 自带的 codex.exe。向导会通过官方插件命令注册 Snowball；若不支持插件管理，会在修改前停止。', True)
        self.codex = tk.StringVar(value=installer.discover_codex())
        ttk.Entry(self.body, textvariable=self.codex, width=90).pack(fill='x', pady=5)
        self.button('自动查找已安装的 Codex', self.find_codex)
        self.button('浏览 codex.exe…', self.browse_codex)
        self.button('安装 / 更新 Codex 插件', self.install_codex, True)
        self.paragraph('安装后请在 Codex 的 /hooks 中审阅并启用 Snowball Hooks，再新开一个聊天。向导不会替你批准 Hooks，也不会自动发送测试任务。')
        self.paragraph('Windows 本版支持会话状态与待答提醒。通过手表直接回复、新建对话等 Desktop 控制暂未接入 Windows，诊断页会单独标明。', True)

    def browse_codex(self):
        if self.busy:
            return
        selected = filedialog.askopenfilename(title='选择 Codex CLI', filetypes=[('Codex CLI', '*.exe')], parent=self.window)
        if selected:
            self.codex.set(selected)

    def find_codex(self):
        def done(candidates):
            if candidates:
                self.codex.set(candidates[0])
                self.status.set('已找到 Codex CLI。安装前还会检查是否支持插件管理。')
            else:
                self.status.set('未找到可用 CLI。请更新 Codex 后重试，或通过“浏览”选择其 resources 目录中的 codex.exe。')
        self.submit('正在查找当前用户安装的 Codex…', installer.discover_codex_candidates, done)

    def install_codex(self):
        codex = self.codex.get().strip()
        def action():
            result = subprocess.run([*installer.cli_prefix(), 'install-plugin', '--codex', codex],
                capture_output=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise installer.InstallError('Codex 插件注册失败。请核对 codex.exe 是否支持 plugin 命令，并检查 Codex 插件页；旧 Hooks 已保留。')
            return True
        self.submit('正在注册插件并保护旧聊天的 Hooks…', action,
            lambda _: self.status.set('插件已注册。请在 Codex 审阅 Hooks；然后进入“绑定雪球”。'))

    def page_2(self):
        self.paragraph('用 USB 确认这只雪球，再验证蓝牙。')
        self.paragraph('1. 用数据线连接雪球并开机。\n2. 在雪球上打开「设置 → 连接电脑 / 开始配对」。\n3. 刷新串口并选择这台设备，点击绑定。\n若 Windows 弹出蓝牙配对提示，请核对设备名称后允许。')
        self.port = tk.StringVar()
        self.port_box = ttk.Combobox(self.body, textvariable=self.port, state='readonly', width=86)
        self.port_box.pack(fill='x', pady=6)
        self.button('刷新 USB 串口', self.refresh_ports)
        self.button('绑定并验证蓝牙', self.bind, True)
        self.paragraph('只有设备身份和蓝牙认证都通过后才保存新绑定。密钥由 Windows 当前用户加密保存，不显示、不复制到日志。首次完成后可拔掉 USB。', True)

    def refresh_ports(self):
        from device_setup import enumerate_ports
        def done(ports):
            self.ports = ports
            values = [p['port'] + '  ·  ' + p.get('description', '') for p in ports]
            self.port_box.configure(values=values)
            if len(values) == 1:
                self.port_box.current(0)
            self.status.set(f'发现 {len(values)} 个串口。请选择雪球对应的端口。' if values else '未发现串口，请检查数据线、USB 端口和设备电源。')
        self.submit('正在读取 USB 串口…', enumerate_ports, done)

    def bind(self):
        index = self.port_box.current()
        if index < 0 or index >= len(self.ports):
            self.status.set('请先刷新串口并选择雪球。')
            return
        port = self.ports[index]['port']
        def action():
            from device_setup import bind_device
            return pause_for_probe(lambda: bind_device(port, PAIRING))
        def done(result):
            self.status.set(result.get('message', '绑定完成。') if result.get('ok') else result.get('message', '绑定未完成，请查看连接诊断。'))
            if result.get('ok'):
                self.show(3)
        self.submit('正在读取 USB 身份并验证蓝牙；请保持连接电脑页面开启…', action, done)

    def page_3(self):
        self.paragraph('关掉向导，雪球也能继续同步。')
        self.paragraph('后台运行在你登录的 Windows 用户会话中。关闭向导不影响连接；电脑退出登录或睡眠时不能继续同步，重新登录后可自动恢复。', True)
        self.autostart = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.body, text='登录 Windows 后自动启动', variable=self.autostart).pack(anchor='w', pady=12)
        self.button('启动后台并应用设置', self.start_background, True)
        self.button('暂时停止后台', self.stop_background)
        self.button('关闭登录自启动', self.remove_autostart)
        self.paragraph('停止后台或取消自启动会保留插件、设备绑定和 Wi-Fi 凭据。', True)

    def start_background(self):
        enabled = self.autostart.get()
        def action():
            result = runtime_start(enabled)
            if not enabled:
                import windows_runtime
                windows_runtime.remove_autostart()
            return result
        self.submit('正在启动 Hub 与蓝牙后台…', action, lambda _: self.status.set('后台已启动。请进入连接诊断，分别检查 Hook、Hub 与设备。'))

    def stop_background(self):
        import windows_runtime
        self.submit('正在停止本安装管理的后台…', windows_runtime.stop,
            lambda _: self.status.set('后台已停止，配置保留。下次登录仍按自启动设置执行。'))

    def remove_autostart(self):
        import windows_runtime
        self.submit('正在关闭本安装的登录自启动…', windows_runtime.remove_autostart,
            lambda _: self.status.set('登录自启动已关闭，正在运行的后台保持原状。'))

    def page_4(self):
        self.paragraph('每一层单独检查，才能找出“一连就断”的原因。')
        self.report_box = tk.Text(self.body, height=12, wrap='word', background='#101c1b', foreground='#e4f5e9',
            insertbackground='#e4f5e9', relief='flat', font=('Segoe UI', 11), padx=12, pady=10)
        self.report_box.pack(fill='both', expand=True)
        self.button('读取连接诊断', self.diagnose, True)
        self.button('主动验证蓝牙（会短暂暂停后台）', self.probe)
        self.button('导出脱敏诊断报告…', self.export_report)
        self.paragraph('插件注册、收到真实 Hook、Hub 健康、蓝牙身份认证和设备回执会分别显示；不会把“已配对”直接当成同步成功。', True)
        if self.report:
            self.render_report(self.report)

    def render_report(self, report):
        self.report = report
        self.report_box.configure(state='normal')
        self.report_box.delete('1.0', 'end')
        checks = report.get('checks', [])
        if checks:
            for check in checks:
                self.report_box.insert('end', f"{check.get('label', check.get('name', check.get('layer', '检查')))}\n{check.get('message', check.get('code', ''))}\n\n")
        else:
            self.report_box.insert('end', json.dumps(report, ensure_ascii=False, indent=2))
        self.report_box.configure(state='disabled')
        self.status.set('诊断完成。尚未验证的层级会保留为未知，不会伪造已同步。')

    def diagnose(self):
        from connection_diagnostics import doctor
        self.submit('正在读取分层状态…', lambda: asyncio.run(doctor(PAIRING)), self.render_report)

    def probe(self):
        def action():
            from connection_diagnostics import check_connection
            from pairing_store import load
            return pause_for_probe(lambda: check_connection(load(PAIRING)))
        self.submit('正在验证蓝牙服务与应用认证…', action, self.render_report)

    def export_report(self):
        if self.busy or not self.report:
            self.status.set('请先运行一次连接诊断。')
            return
        selected = filedialog.asksaveasfilename(title='保存脱敏报告', initialfile='snowball-diagnostics.json',
            defaultextension='.json', filetypes=[('JSON', '*.json')], parent=self.window)
        if selected:
            Path(selected).write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self.status.set('已导出脱敏报告，不包含配对密钥、用户路径或会话内容。')


def launch(smoke_test=False):
    if sys.platform != 'win32' and not smoke_test:
        raise RuntimeError('Use the Windows wizard on Windows; Linux keeps its existing lifecycle tools.')
    window = tk.Tk()
    Wizard(window, smoke_test=smoke_test)
    window.mainloop()
    return 0
