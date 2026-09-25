# -*- coding: utf-8 -*-
"""开机自启：写当前用户的注册表 Run 项（HKCU，不需要管理员权限）。

便携版没有安装器，平时确实不碰注册表；只有用户在设置里主动开启自启时
才写 HKCU\\...\\Run 这一个值，关闭时删掉，程序目录本身依旧是绿色的。
"""
from __future__ import annotations

import os
import sys

try:
    import winreg
except ImportError:                       # 非 Windows 环境（跑单测等）
    winreg = None

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'FileRenamerPro'


def _command() -> str:
    """启动命令：打包后指向 exe；开发态指向 python 解释器 + main.py。"""
    if getattr(sys, 'frozen', False):
        return '"%s"' % sys.executable
    script = os.path.abspath(sys.argv[0] or 'main.py')
    return '"%s" "%s"' % (sys.executable, script)


def enabled() -> bool:
    """当前是否已开启自启（以注册表为准，不信任配置缓存）。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, _type = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value).strip())
    except OSError:
        return False


def set_enabled(on: bool) -> tuple[bool, str]:
    """开启 / 关闭自启；返回 (是否成功, 给用户的提示文本)。"""
    if winreg is None:
        return False, '当前系统不支持开机自启'
    try:
        if on:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            return True, '已开启开机自启，下次登录 Windows 时自动运行'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass                          # 本来就没开，删个寂寞也算成功
        return True, '已关闭开机自启'
    except OSError as exc:
        return False, '开机自启设置失败：%s' % exc
