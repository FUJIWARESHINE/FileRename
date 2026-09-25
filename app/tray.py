# -*- coding: utf-8 -*-
"""系统托盘图标（纯 Win32 / ctypes，无第三方依赖）。

pywebview 没有托盘能力，这里自建一条托盘线程：
· 一个隐藏窗口接收 Shell_NotifyIcon 的回调消息（双击 / 左键 = 唤出主窗口，
  右键 = 弹菜单：显示主窗口 / 开机自启 / 退出）
· explorer 重启后收到 TaskbarCreated 广播，自动把图标补挂回去

所有回调（on_open / on_exit 等）都在托盘线程里触发，实现方自行保证线程安全
（pywebview 的 window.show / hide 内部会派发回 UI 线程，可以直接调）。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# Shell_NotifyIcon
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01

# 消息
WM_APP_TRAY = 0x8FFF            # 托盘回调消息（WM_APP 区间，避开系统占用）
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000

# 图标加载
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
IDI_APPLICATION = 32512

# 菜单
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x00, 0x800, 0x08
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_BOTTOMALIGN = 0x02, 0x100, 0x20

ID_OPEN, ID_AUTOSTART, ID_EXIT = 1001, 1002, 1003

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

# 64 位下 LRESULT / LPARAM / 各种 HANDLE 都是 64 位，
# 不声明签名的话 ctypes 默认按 32 位 int 传参，句柄会被截断甚至 OverflowError。
# 具体声明放在结构体与 _WNDCLASSW 定义之后（见下方「Win32 签名」块）。


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('hWnd', wintypes.HWND),
        ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT),
        ('uCallbackMessage', wintypes.UINT),
        ('hIcon', wintypes.HICON),
        ('szTip', wintypes.WCHAR * 128),
        ('dwState', wintypes.DWORD),
        ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256),
        ('uVersion', wintypes.UINT),
        ('szInfoTitle', wintypes.WCHAR * 64),
        ('dwInfoFlags', wintypes.DWORD),
        ('guidItem', ctypes.c_ubyte * 16),
        ('hBalloonIcon', wintypes.HICON),
    ]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', ctypes.c_void_p),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
    ]


# ------------------------------------------------------------------ Win32 签名
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.restype = ctypes.c_ushort           # ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]  # 第二参是 MAKEINTRESOURCE
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                               wintypes.LPCWSTR]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.restype = ctypes.c_ssize_t
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  ctypes.c_void_p]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """托盘图标 + 右键菜单，独立线程跑消息循环。"""

    def __init__(self, icon_path: str, tip: str, *, on_open, on_exit,
                 autostart_checked, on_toggle_autostart) -> None:
        self._icon_path = icon_path
        self._tip = tip
        self._on_open = on_open
        self._on_exit = on_exit
        self._autostart_checked = autostart_checked    # 每次弹菜单实时询问
        self._on_toggle_autostart = on_toggle_autostart
        self._thread = None
        self._ready = None
        self._hwnd = None
        self._proc = None            # 保住 WNDPROC 引用，防止被 GC 后回调崩掉
        self._hicon = None
        self._taskbar_created = 0

    # ------------------------------------------------------------------ 对外
    def start(self) -> bool:
        if self._thread:
            return True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name='fr-tray')
        self._thread.start()
        ok = self._ready.wait(2.0)
        return bool(ok and self._hwnd)

    def stop(self) -> None:
        if not self._thread:
            return
        hwnd, thread = self._hwnd, self._thread
        self._thread = None
        self._hwnd = None
        if hwnd:
            user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        if thread:
            thread.join(1.5)

    def notify(self, title: str, text: str) -> None:
        """气泡提示：窗口藏起来时告诉用户程序还在后台、怎么唤出。"""
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wintypes.HWND(self._hwnd)
        nid.uID = 1
        nid.uFlags = NIF_INFO
        nid.szInfo = (text or '')[:255]
        nid.szInfoTitle = (title or '')[:63]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # ------------------------------------------------------------------ 线程体
    def _run(self) -> None:
        try:
            self._taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
        except Exception:
            self._taskbar_created = 0
        self._proc = WNDPROC(self._wnd_proc)

        cls = _WNDCLASSW()
        cls.lpfnWndProc = self._proc
        cls.lpszClassName = 'FileRenamerTrayWnd'
        cls.hInstance = kernel32.GetModuleHandleW(None)
        if not user32.RegisterClassW(ctypes.byref(cls)):
            self._ready.set()
            return
        # 隐藏窗口：不 ShowWindow，只用来接收托盘回调消息
        hwnd = user32.CreateWindowExW(0, 'FileRenamerTrayWnd', 'FileRenamerTray',
                                      0, 0, 0, 0, 0, None, None, cls.hInstance, None)
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd
        self._hicon = self._load_icon()
        if not self._add():
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._remove()
        if self._hicon:
            try:
                user32.DestroyIcon(self._hicon)
            except Exception:
                pass

    def _load_icon(self):
        try:
            hicon = user32.LoadImageW(None, self._icon_path, IMAGE_ICON,
                                      0, 0, LR_LOADFROMFILE)
            if hicon:
                return wintypes.HICON(hicon)
        except Exception:
            pass
        return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _add(self) -> bool:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wintypes.HWND(self._hwnd)
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tip
        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove(self) -> None:
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wintypes.HWND(self._hwnd)
        nid.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    # ------------------------------------------------------------------ 窗口过程
    def _wnd_proc(self, hwnd, msg, w_param, l_param):
        try:
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_APP_TRAY:
                if l_param in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._safe(self._on_open)
                elif l_param == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self._add()               # explorer 重启后补挂图标
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

    def _popup_menu(self, hwnd) -> None:
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN, '显示主窗口')
        checked = MF_CHECKED if self._autostart_checked() else 0
        user32.AppendMenuW(hmenu, MF_STRING | checked, ID_AUTOSTART, '开机自启')
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, '退出')
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 经典坑：不先 SetForegroundWindow，点菜单外面它不会自动消失
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_BOTTOMALIGN,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(wintypes.HWND(hwnd), WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)
        if cmd == ID_OPEN:
            self._safe(self._on_open)
        elif cmd == ID_AUTOSTART:
            self._safe(self._on_toggle_autostart)
        elif cmd == ID_EXIT:
            self._safe(self._on_exit)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass
