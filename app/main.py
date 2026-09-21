# -*- coding: utf-8 -*-
"""文件批量重命名 · 便携版

界面用 WebView2 渲染（pywebview），重命名逻辑在 Python 侧（rules.py）。
打包方式：PyInstaller onedir —— exe 与依赖放在同一目录，整个目录拷走即可用。
"""
from __future__ import annotations

import json
import os
import sys

import webview

from bridge import Bridge, window_geometry

APP_NAME = '文件批量重命名'
APP_VERSION = '2.0.3'
WINDOW_TITLE = '%s v%s' % (APP_NAME, APP_VERSION)


def app_dir() -> str:
    """程序目录（打包后是 exe 所在目录）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts) -> str:
    base = getattr(sys, '_MEIPASS', None) or app_dir()
    return os.path.join(base, *parts)


def data_dir() -> str:
    """便携数据目录：优先程序目录下 data\\，不可写时退回 %LOCALAPPDATA%。"""
    target = os.path.join(app_dir(), 'data')
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, '.write-test')
        with open(probe, 'w', encoding='utf-8') as fh:
            fh.write('ok')
        os.remove(probe)
        return target
    except OSError:
        fallback = os.path.join(os.environ.get('LOCALAPPDATA', app_dir()), 'FileRenamerPro')
        os.makedirs(fallback, exist_ok=True)
        return fallback


def config_path() -> str:
    return os.path.join(app_dir(), 'FileRenamer.json')


def visible_on_screen(x, y) -> bool:
    """保存的窗口坐标是否还落在当前虚拟桌面里。

    便携版被拷到另一台机器、或外接屏被拔掉以后，旧坐标可能落在屏幕外，
    窗口会「启动了但看不见」，这里直接把越界坐标丢掉改用默认位置。
    """
    try:
        x = float(x)
        y = float(y)
    except (TypeError, ValueError):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)           # SM_YVIRTUALSCREEN
        right = left + user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        bottom = top + user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    except Exception:
        return True                                 # 取不到屏幕信息就别拦着
    if right - left <= 0 or bottom - top <= 0:
        return True
    # 至少要让标题栏附近 60x40 的一块留在可见区域内
    return (left - 4 <= x <= right - 60) and (top - 4 <= y <= bottom - 40)


MIN_WIDTH, MIN_HEIGHT = 820, 560          # 与 create_window 的 min_size 保持一致
MAX_WIDTH, MAX_HEIGHT = 8000, 8000        # 配置被改坏时不至于开出一个巨大的窗口


def screen_bounds() -> tuple[int, int]:
    """当前虚拟桌面的宽高（物理像素），取不到时给个保守值。

    上次在大屏上调好的窗口拿到小屏上要收敛，不然会开出一个比屏幕还大的窗口。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return (int(user32.GetSystemMetrics(78)) or 1920,
                int(user32.GetSystemMetrics(79)) or 1080)
    except Exception:
        return (1920, 1080)


def clamp_size(value, default: int, low: int, high: int) -> int:
    """把配置里的尺寸收敛到合理区间（配置手改坏了也只是回到默认值）。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(size, high))


def load_config() -> dict:
    try:
        with open(config_path(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            with open(os.path.join(data_dir(), 'FileRenamer.json'), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save_config(data: dict) -> None:
    """原子写配置：先落临时文件再替换。

    关窗时进程可能立刻就退了，直接写目标文件有概率留下半个 JSON，
    替换写入能保证读到的要么是旧的完整内容、要么是新的完整内容。
    """
    try:
        blob = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return
    for path in (config_path(), os.path.join(data_dir(), 'FileRenamer.json')):
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(blob)
            os.replace(tmp, path)
            return
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            continue


class App:
    def __init__(self, demo: bool = False, diag: bool = False) -> None:
        self.config = load_config()
        self.bridge = Bridge(self.config)
        self.demo = demo
        self.diag = diag
        self.window = None
        self.hotkeys = None
        self.remember_maximized = False
        self._restore_rect = None
        # 前端可能在窗口线程启动前后任意时刻来取状态，这里先挂上标记
        self.bridge._demo_pending = demo
        self._restore_workflow()

    def _restore_workflow(self) -> None:
        saved = self.config.get('workflow')
        if not isinstance(saved, list):
            return
        import rules
        for step in saved:
            rid = step.get('id')
            if rid not in rules.RULE_MAP:
                continue
            config = rules.default_config(rid)
            config.update(step.get('config') or {})
            self.bridge.workflow.append({'id': rid, 'enabled': step.get('enabled', True),
                                         'config': config})

    # ------------------------------------------------------------------ 窗口
    def run(self) -> None:
        geometry = self.config.get('geometry') or {}
        # 复用上次关闭时记下的窗口大小与位置
        screen_w, screen_h = screen_bounds()
        width = clamp_size(geometry.get('width'), 1320, MIN_WIDTH,
                           max(MIN_WIDTH, min(MAX_WIDTH, screen_w)))
        height = clamp_size(geometry.get('height'), 860, MIN_HEIGHT,
                            max(MIN_HEIGHT, min(MAX_HEIGHT, screen_h)))
        x = geometry.get('x')
        y = geometry.get('y')
        if not visible_on_screen(x, y):
            x = y = None
        # create_window 的 width/height 是客户区尺寸，和记下来的外框矩形差一圈边框，
        # 窗口起来之后再按真实矩形校正一次，否则每次开合都会缩掉十几个像素
        self._restore_rect = None
        if geometry.get('width') or geometry.get('height'):
            self._restore_rect = {'x': x, 'y': y, 'width': width, 'height': height}
        self.remember_maximized = bool(geometry.get('maximized'))
        theme = self.config.get('theme', 'dark')
        backdrop = '#0a0e18' if theme != 'light' else '#f2f4fa'

        self.window = webview.create_window(
            WINDOW_TITLE,
            url=resource_path('ui', 'index.html'),
            js_api=self.bridge,
            width=width, height=height,
            x=x, y=y,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            frameless=True,
            easy_drag=False,          # 拖动只认标题栏的 drag-region，避免抢占边缘缩放手势
            resizable=True,
            background_color=backdrop,
            text_select=False,
            confirm_close=False,
        )
        self.bridge.attach(self.window)
        self.window.events.loaded += self._on_loaded
        self.window.events.shown += self._on_shown
        self.window.events.closing += self._on_closing
        self.window.events.closed += self._on_closed
        webview.start(self._after_start, gui='edgechromium',
                      debug=False, private_mode=False, storage_path=data_dir())

    def _snapshot_config(self, geometry: dict | None = None) -> dict:
        """要落盘的配置：窗口几何 + 规则工作流 + 主题。"""
        data = dict(self.config)
        if geometry:
            data['geometry'] = geometry
        data.update({
            'workflow': [{'id': s['id'], 'enabled': s.get('enabled', True),
                          'config': s.get('config', {})} for s in self.bridge.workflow],
            'theme': self.bridge.config.get('theme', 'dark'),
        })
        return data

    def _on_closing(self) -> None:
        """关闭前把窗口大小 / 位置落盘。

        必须在这里同步写文件（pywebview 的 closed 事件跑在另一个线程里，进程
        往往等不到它执行完就退出了，原来的实现就是这么把尺寸丢掉的）；
        此时原生窗口还没销毁，用句柄读到的矩形才是用户真正调好的那个大小。
        返回 None = 不取消关闭，不能返回 True。
        """
        geometry = window_geometry(self.bridge.native_handle())
        if geometry:
            self.config['geometry'] = geometry
        save_config(self._snapshot_config())

    def _after_start(self) -> None:
        self._apply_round_corners()
        self._apply_saved_geometry()
        self._start_hotkeys()

    def _on_shown(self) -> None:
        """窗口真正显示出来之后才有原生句柄，几何校正和最大化都要在这里做。"""
        self._apply_round_corners()
        self._apply_saved_geometry()
        if self.remember_maximized:
            try:
                self.window.maximize()
                self.bridge._maxed = True      # 与前端「最大化」按钮的状态对齐
            except Exception:
                pass

    def _apply_saved_geometry(self) -> None:
        """把上次关闭时的窗口矩形原样贴回去。

        create_window 的 width/height 是客户区尺寸，而无边框窗口会砍掉
        16x39 那圈非客户区，只靠回填参数每次开合都会缩一点，所以窗口起来后
        按记录的外框矩形用 SetWindowPos 校正一次。
        """
        rect = self._restore_rect
        if not rect:
            return
        hwnd = self.bridge.native_handle()
        if not hwnd:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            SWP_NOZORDER, SWP_NOMOVE = 0x0004, 0x0002
            flags = SWP_NOZORDER
            x, y = rect.get('x'), rect.get('y')
            if x is None or y is None:
                flags |= SWP_NOMOVE            # 位置不可信（比如换了显示器），只校正尺寸
            user32.SetWindowPos(hwnd, 0, int(x or 0), int(y or 0),
                                int(rect['width']), int(rect['height']), flags)
        except Exception:
            pass

    def _start_hotkeys(self) -> None:
        """全局快捷键（唤出 / 隐藏窗口）：键盘组合 + 鼠标侧键。"""
        try:
            from hotkeys import HotkeyManager
            self.hotkeys = HotkeyManager(
                self.bridge.toggle_visible,
                on_error=lambda msg: self.bridge.push_toast(msg, 'warn'))
            self.bridge.hotkeys = self.hotkeys
            self.hotkeys.update(self.config.get('hotkey'))
        except Exception as exc:
            try:
                print('hotkey init failed: %s' % exc)
            except Exception:
                pass

    def _apply_round_corners(self) -> None:
        """Windows 11 原生圆角，无边框窗口不至于像一块贴死的方块。"""
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.FindWindowW(None, self.window.title)
            if not hwnd:
                return
            value = ctypes.c_int(2)          # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), 33, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _on_loaded(self) -> None:
        from webview.dom import DOMEventHandler
        try:
            self.window.dom.document.on(
                'drop', DOMEventHandler(self._on_drop, prevent_default=True))
        except Exception:
            pass

    def _on_drop(self, event) -> None:
        """系统拖拽：pywebview 会把真实路径附在 pywebviewFullPath 上。"""
        payload = None
        try:
            data = (event or {}).get('dataTransfer') or {}
            files = data.get('files') or []
            paths = []
            for entry in files:
                if isinstance(entry, dict):
                    path = entry.get('pywebviewFullPath')
                    if path:
                        paths.append(path)
            if paths:
                payload = self.bridge.add_paths(paths)
            else:
                payload = {'toast': {'type': 'warn',
                                     'text': '没能读取拖入文件的路径，请改用「选择文件」按钮'}}
        except Exception as exc:
            payload = {'toast': {'type': 'error', 'text': '拖拽导入出错：%s' % exc}}
        if payload:
            try:
                self.window.evaluate_js(
                    'window.__onPush && window.__onPush(%s)' % json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass

    def _on_closed(self) -> None:
        """窗口已销毁，只做收尾。

        配置在 closing 事件里就已经落盘了，这里不再读窗口、也不再写文件：
        这个回调跑在独立线程里，进程退出时可能把它拦腰截断，
        再写一次反而有把好配置写坏的风险。
        """
        if getattr(self, 'hotkeys', None):
            try:
                self.hotkeys.stop()
            except Exception:
                pass


def enable_dpi_awareness() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def report_fatal(exc: BaseException) -> None:
    """启动失败的兜底提示：写日志 + 中文对话框。

    界面后端（WebView2 / pythonnet / .NET Framework）的问题都会在
    webview.start() 这一步炸出来，直接抛出去的话用户只能看到一堆英文堆栈，
    这里换成能照着做的中文说明，同时把完整堆栈留在日志里。
    """
    import traceback
    detail = traceback.format_exc()
    log_path = ''
    try:
        import datetime
        log_path = os.path.join(app_dir(), '启动错误.log')
        with open(log_path, 'w', encoding='utf-8') as fh:
            fh.write('%s\n%s\n' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), detail))
    except OSError:
        log_path = ''

    text = (
        '程序启动失败：\n%s\n\n'
        '常见原因与处理办法：\n'
        '1. 需要 Windows 10 / 11 64 位，且系统带 .NET Framework 4.6.2 以上（一般自带）；\n'
        '2. 需要 WebView2 运行时，Win10 可安装微软官方「Microsoft Edge WebView2 Runtime」；\n'
        '3. 如果是从压缩包解压出来的：右键压缩包 → 属性 → 勾选「解除锁定」后再解压；\n'
        '   也可以解压后在文件夹里执行：Get-ChildItem -Recurse | Unblock-File\n'
        '4. 目录请放在本地硬盘（不要放网络盘 / 映射盘）。\n'
        '%s' % (exc, ('\n详细堆栈已写入：%s' % log_path) if log_path else '')
    )
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, '%s 启动失败' % APP_NAME, 0x10 | 0x40000)
    except Exception:
        pass


def main() -> None:
    enable_dpi_awareness()
    args = sys.argv[1:]
    if '--diag' in args:
        # 诊断模式：开启 WebView2 远程调试端口，便于外部连接排查界面问题
        import webview as _w
        _w.settings['REMOTE_DEBUGGING_PORT'] = 9223
    try:
        App(demo='--demo' in args, diag='--diag' in args).run()
    except Exception as exc:          # noqa: BLE001 —— 兜底提示，堆栈写日志
        report_fatal(exc)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
