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

from bridge import Bridge

APP_NAME = '文件批量重命名'
APP_VERSION = '2.0.0'
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
    for path in (config_path(), os.path.join(data_dir(), 'FileRenamer.json')):
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            return
        except OSError:
            continue


class App:
    def __init__(self, demo: bool = False, diag: bool = False) -> None:
        self.config = load_config()
        self.bridge = Bridge(self.config)
        self.demo = demo
        self.diag = diag
        self.window = None
        self.hotkeys = None
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
        width = int(geometry.get('width') or 1320)
        height = int(geometry.get('height') or 860)
        x = geometry.get('x')
        y = geometry.get('y')
        if not visible_on_screen(x, y):
            x = y = None
        theme = self.config.get('theme', 'dark')
        backdrop = '#0a0e18' if theme != 'light' else '#f2f4fa'

        self.window = webview.create_window(
            WINDOW_TITLE,
            url=resource_path('ui', 'index.html'),
            js_api=self.bridge,
            width=width, height=height,
            x=x, y=y,
            min_size=(820, 560),
            frameless=True,
            easy_drag=False,          # 拖动只认标题栏的 drag-region，避免抢占边缘缩放手势
            resizable=True,
            background_color=backdrop,
            text_select=False,
            confirm_close=False,
        )
        self.bridge.attach(self.window)
        self.window.events.loaded += self._on_loaded
        self.window.events.closed += self._on_closed
        webview.start(self._after_start, gui='edgechromium',
                      debug=False, private_mode=False, storage_path=data_dir())

    def _after_start(self) -> None:
        self._apply_round_corners()
        self._start_hotkeys()

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
        if getattr(self, 'hotkeys', None):
            try:
                self.hotkeys.stop()
            except Exception:
                pass
        geometry = {}
        try:
            geometry = {'x': self.window.x, 'y': self.window.y,
                        'width': self.window.width, 'height': self.window.height}
        except Exception:
            pass
        data = dict(self.config)
        data.update({
            'geometry': geometry,
            'workflow': [{'id': s['id'], 'enabled': s.get('enabled', True),
                          'config': s.get('config', {})} for s in self.bridge.workflow],
            'theme': self.bridge.config.get('theme', 'dark'),
        })
        save_config(data)


def enable_dpi_awareness() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main() -> None:
    enable_dpi_awareness()
    args = sys.argv[1:]
    if '--diag' in args:
        # 诊断模式：开启 WebView2 远程调试端口，便于外部连接排查界面问题
        import webview as _w
        _w.settings['REMOTE_DEBUGGING_PORT'] = 9223
    App(demo='--demo' in args, diag='--diag' in args).run()


if __name__ == '__main__':
    main()
