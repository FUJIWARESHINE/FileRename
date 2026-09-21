# -*- coding: utf-8 -*-
"""打包绿色版 onedir：E:\\Python312\\python.exe app\\build.py"""
import os
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
DIST = os.path.join(ROOT, 'dist')
BUILD = os.path.join(ROOT, 'build')
NAME = 'FileRenamer'


def rmtree_native(path: str) -> None:
    """用原生 os API 递归删除。

    不直接用 shutil.rmtree：本机 Python 的 sitecustomize 会劫持 shutil.rmtree，
    打包收尾时偶发 GBK 解码异常，导致 dist 目录删不掉、整个构建失败。
    """
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            target = os.path.join(root, name)
            try:
                os.chmod(target, 0o700)
            except OSError:
                pass
            try:
                os.remove(target)
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def build():
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--onedir', '--noupx', '--windowed', '--name', NAME,
           '--distpath', DIST, '--workpath', os.path.join(BUILD, 'work'),
           '--specpath', BUILD,
           '--icon', os.path.join(APP_DIR, 'icon.ico'),
           '--add-data', os.path.join(APP_DIR, 'ui') + os.pathsep + 'ui',
           '--collect-all', 'webview',
           os.path.join(APP_DIR, 'main.py')]
    for mod in ('unittest', 'doctest', 'pydoc', 'pdb', 'setuptools', 'pip'):
        cmd += ['--exclude-module', mod]
    print(' '.join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    out = os.path.join(DIST, NAME)
    final = os.path.join(DIST, '文件批量重命名')
    rmtree_native(final)
    try:
        os.replace(out, final)
    except OSError:
        shutil.move(out, final)          # 跨卷等情况下退回 shutil

    # 必须跟着 exe 一起发布：没有它，用户从 zip 解压出来的包（文件带互联网标记）
    # 会因为 .NET Framework 拒绝 LoadFrom 而启动失败。
    cfg_src = os.path.join(APP_DIR, NAME + '.exe.config')
    if os.path.isfile(cfg_src):
        shutil.copyfile(cfg_src, os.path.join(final, NAME + '.exe.config'))
    missing = [p for p in (NAME + '.exe', NAME + '.exe.config')
               if not os.path.isfile(os.path.join(final, p))]
    if missing:
        raise RuntimeError('产物不完整，缺少：%s' % '、'.join(missing))
    return os.path.join(final, NAME + '.exe')


if __name__ == '__main__':
    exe = build()
    print('产物:', exe)
