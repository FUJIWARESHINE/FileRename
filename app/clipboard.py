# -*- coding: utf-8 -*-
"""Windows 剪贴板读写（纯 ctypes，标准库）。

· clipboard_files() 读取资源管理器中「复制」的文件 / 文件夹（CF_HDROP）
· clipboard_text()  读取剪贴板纯文本（CF_UNICODETEXT）
· copy_text()       把纯文本写进剪贴板

非 Windows 或任何失败都安静降级：读返回空、写返回 False。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CF_TEXT = 1
CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


def _bind() -> tuple | None:
    """绑定 Win32 函数签名，避免 64 位下句柄被截断。"""
    if os.name != 'nt':
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL

        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           wintypes.LPWSTR, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
    except (AttributeError, OSError):
        return None
    return user32, kernel32, shell32


def clipboard_files() -> list[str]:
    """返回剪贴板里的文件 / 文件夹绝对路径；没有资源管理器内容则返回 []。"""
    bound = _bind()
    if not bound:
        return []
    user32, _kernel32, shell32 = bound
    if not user32.OpenClipboard(None):
        return []
    try:
        if not user32.IsClipboardFormatAvailable(CF_HDROP):
            return []
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        total = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        if not total or total > 20000:
            return []
        paths: list[str] = []
        for index in range(total):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            if not length:
                continue
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buffer, length + 1)
            value = (buffer.value or '').strip().strip('\x00')
            if value:
                paths.append(os.path.abspath(value))
        return paths
    except Exception:
        return []
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass


def clipboard_text() -> str:
    """读取剪贴板纯文本；没有则返回空串。"""
    bound = _bind()
    if not bound:
        return ''
    user32, kernel32, _shell32 = bound

    if not user32.OpenClipboard(None):
        return ''
    try:
        handle = None
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            wide = True
        elif user32.IsClipboardFormatAvailable(CF_TEXT):
            handle = user32.GetClipboardData(CF_TEXT)
            wide = False
        if not handle:
            return ''
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ''
        try:
            if wide:
                return ctypes.wstring_at(pointer) or ''
            return (ctypes.string_at(pointer) or b'').decode('gbk', 'ignore')
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return ''
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass


def copy_text(text: str) -> bool:
    """把文本写进剪贴板，成功返回 True。"""
    bound = _bind()
    if not bound:
        return False
    user32, kernel32, _shell32 = bound
    data = str(text if text is not None else '')
    size = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(pointer, ctypes.create_unicode_buffer(data), size)
    finally:
        kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True                   # 成功后所有权归系统，不能自己释放
    except Exception:
        kernel32.GlobalFree(handle)
        return False
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass
