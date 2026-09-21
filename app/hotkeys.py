# -*- coding: utf-8 -*-
"""全局快捷键：唤出 / 隐藏主窗口。

支持两种绑定：
· 键盘组合  Ctrl / Alt / Shift / Win + 任意键（含 F1~F12、方向键等）
· 鼠标侧键  X1（后退键）/ X2（前进键），用 WH_MOUSE_LL 低级钩子全局捕获

键盘走 RegisterHotKey（系统级，稳定不吃键）；鼠标侧键必须走低级钩子，
因为 RegisterHotKey 不支持鼠标。钩子命中后吞掉事件，避免侧键同时触发
浏览器后退之类的默认行为。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_MAP = {'ctrl': MOD_CONTROL, 'alt': MOD_ALT, 'shift': MOD_SHIFT, 'meta': MOD_WIN}

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_XBUTTONDOWN = 0x020B
WH_MOUSE_LL = 0x000E

LRESULT = ctypes.c_ssize_t

VK_SPECIAL = {
    'UP': 0x26, 'DOWN': 0x28, 'LEFT': 0x25, 'RIGHT': 0x27,
    'SPACE': 0x20, 'ENTER': 0x0D, 'HOME': 0x24, 'END': 0x23,
    'PGUP': 0x21, 'PGDN': 0x22, 'INS': 0x2D, 'DEL': 0x2E, 'TAB': 0x09,
}


def vk_of(key: str) -> int:
    """把前端传来的键名转成虚拟键码，不支持返回 0。"""
    key = str(key or '').strip().upper()
    if not key:
        return 0
    if len(key) == 1 and key.isalnum():
        return ord(key)
    if key.startswith('F') and key[1:].isdigit():
        num = int(key[1:])
        if 1 <= num <= 24:
            return 0x70 + num - 1
    return VK_SPECIAL.get(key, 0)


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt', wintypes.POINT),
        ('mouseData', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


MouseProc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                               ctypes.POINTER(_MSLLHOOKSTRUCT))


class HotkeyManager:
    """注册 / 注销全局快捷键，命中时回调 callback（在钩子线程里调用）。"""

    def __init__(self, callback, on_error=None) -> None:
        self._callback = callback
        self._on_error = on_error or (lambda msg: None)
        self._lock = threading.Lock()
        self._kb_thread = None
        self._kb_ready = None
        self._kb_ok = False
        self._ms_thread = None
        self._ms_ready = None
        self._ms_ok = False
        self._ms_proc = None       # 保住回调引用，防止被 GC
        self._ms_target = 0

    # ---------------------------------------------------------------- 对外
    def update(self, cfg) -> bool:
        """按配置重建快捷键；返回是否注册成功（cfg 为空即注销成功）。"""
        with self._lock:
            self.stop()
            cfg = cfg if isinstance(cfg, dict) else None
            if not cfg:
                return True
            if cfg.get('type') == 'key':
                return self._start_key(cfg)
            if cfg.get('type') == 'mouse':
                return self._start_mouse(cfg)
            return True

    def stop(self) -> None:
        if self._kb_ready is not None:
            if self._kb_ready.wait(1.0) and self._kb_thread is not None:
                user32.PostThreadMessageW(self._kb_thread.ident, WM_QUIT, 0, 0)
                self._kb_thread.join(1.0)
            self._kb_thread = None
            self._kb_ready = None
            self._kb_ok = False
        if self._ms_ready is not None:
            if self._ms_ready.wait(1.0) and self._ms_thread is not None:
                user32.PostThreadMessageW(self._ms_thread.ident, WM_QUIT, 0, 0)
                self._ms_thread.join(1.0)
            self._ms_thread = None
            self._ms_ready = None
            self._ms_ok = False
            self._ms_proc = None
            self._ms_target = 0

    # ---------------------------------------------------------------- 键盘
    def _start_key(self, cfg: dict) -> bool:
        mods = 0
        for m in (cfg.get('mods') or []):
            mods |= MOD_MAP.get(str(m).lower(), 0)
        vk = vk_of(cfg.get('key'))
        if not vk:
            self._on_error('快捷键无效，请重新录制')
            return False
        ready = threading.Event()
        result = {'ok': False}

        def run():
            if not user32.RegisterHotKey(None, 1, mods, vk):
                ready.set()          # 注册失败，多半是被别的程序占用了
                return
            result['ok'] = True
            ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == 1:
                    try:
                        self._callback()
                    except Exception:
                        pass
            user32.UnregisterHotKey(None, 1)

        thread = threading.Thread(target=run, daemon=True, name='fr-hotkey-kb')
        self._kb_thread = thread
        self._kb_ready = ready
        thread.start()
        ready.wait(1.5)
        self._kb_ok = result['ok']
        if not self._kb_ok:
            # 线程已退出，清掉挂起的句柄，方便下次重试
            self._kb_thread = None
            self._kb_ready = None
            self._on_error('快捷键注册失败，可能已被其他程序占用')
        return self._kb_ok

    # ---------------------------------------------------------------- 鼠标
    def _start_mouse(self, cfg: dict) -> bool:
        self._ms_target = {'x1': 1, 'x2': 2}.get(str(cfg.get('button', 'x1')).lower(), 1)
        proc = MouseProc(self._mouse_proc)
        self._ms_proc = proc
        ready = threading.Event()
        result = {'ok': False}

        def run():
            hhook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
            if not hhook:
                ready.set()
                return
            result['ok'] = True
            ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
            user32.UnhookWindowsHookEx(hhook)

        thread = threading.Thread(target=run, daemon=True, name='fr-hotkey-mouse')
        self._ms_thread = thread
        self._ms_ready = ready
        thread.start()
        ready.wait(1.5)
        self._ms_ok = result['ok']
        if not self._ms_ok:
            self._ms_thread = None
            self._ms_ready = None
            self._ms_proc = None
            self._on_error('鼠标侧键钩子安装失败')
        return self._ms_ok

    def _mouse_proc(self, n_code, w_param, l_param):
        try:
            if n_code >= 0 and w_param == WM_XBUTTONDOWN:
                xbtn = (l_param.contents.mouseData >> 16) & 0xFFFF
                if xbtn == self._ms_target:
                    try:
                        self._callback()
                    except Exception:
                        pass
                    return 1               # 吞掉侧键，不触发系统默认行为
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)
