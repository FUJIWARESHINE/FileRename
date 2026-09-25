# -*- coding: utf-8 -*-
"""前后端桥接层

前端通过 window.pywebview.api.<方法>() 调用这里的方法，每个方法统一返回一份
payload：
    items    列表结构变化时的完整行数组（其它情况为 None）
    rows     需要就地刷新的行（tag / 新名 / 状态）
    workflow 规则工作流变化时的规则卡片数组（其它情况为 None）
    nav      目录导航状态（面包屑 / 是否在目录内）
    stats    统计信息
    can      按钮可用状态
    toast    需要弹出的提示 {type, text}
    dialog   需要弹出的详情 {title, lines}
    clip     剪贴板写入失败时回传的文本，交给前端兜底

列表模型对齐 ZTools 插件：
    roots        用户导入的顶层条目（文件 / 文件夹），文件夹只作为节点保留
    children     当前进入的目录的直接子项（进入时按需读取，绝不递归展开）
    crumbs       从导入层到当前目录的面包屑
    history      重命名记录，撤回时按路径回滚，跨目录也能用
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime

import clipboard
import rules
import xlsx

_MOD_LABELS = {'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift', 'meta': 'Win'}


def hk_label(cfg: dict) -> str:
    """把快捷键配置转成可读文本，如 Ctrl + Alt + R / 鼠标侧键 X1。"""
    if not isinstance(cfg, dict):
        return '未设置'
    if cfg.get('type') == 'mouse':
        return '鼠标侧键 X2（前进键）' if cfg.get('button') == 'x2' else '鼠标侧键 X1（后退键）'
    mods = [_MOD_LABELS.get(str(m).lower(), str(m)) for m in (cfg.get('mods') or [])]
    return ' + '.join(mods + [str(cfg.get('key', '')).upper() or '?'])


def window_geometry(hwnd: int) -> dict:
    """用 Win32 读窗口「还原后」的矩形与是否最大化；读不到返回 {}。

    为什么不用 pywebview 的 window.x / window.width：winforms 平台在 FormClosed
    里就把窗口实例从 instances 里删掉了，closed 事件里再读只会抛异常（原来的
    "记住窗口大小"就是这么丢掉几何信息的）。所以必须在 closing 事件（窗口还活着）
    时用原生句柄读真实矩形。

    取 rcNormalPosition 而不是当前矩形：最大化 / 最小化状态下关窗，也能记住
    「还原后」的尺寸，下次打开还是自己调好的那个大小。
    """
    if not hwnd:
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [('length', wintypes.UINT),
                        ('flags', wintypes.UINT),
                        ('showCmd', wintypes.UINT),
                        ('ptMinPosition', wintypes.POINT),
                        ('ptMaxPosition', wintypes.POINT),
                        ('rcNormalPosition', wintypes.RECT)]

        user32 = ctypes.windll.user32
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(placement)):
            return {}
        rect = placement.rcNormalPosition
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return {}
        maximized = bool(placement.showCmd == 3) or bool(user32.IsZoomed(wintypes.HWND(hwnd)))
        return {'x': int(rect.left), 'y': int(rect.top),
                'width': width, 'height': height, 'maximized': maximized}
    except Exception:
        return {}


class Bridge:
    def __init__(self, config: dict | None = None) -> None:
        self._window = None
        self.lock = threading.RLock()
        self.roots: list[rules.FileItem] = []
        self.children: list[rules.FileItem] = []
        self.workflow: list[dict] = []
        self.crumbs: list[dict] = []          # [{'name': ..., 'path': ...}]
        self.history: list[dict] = []         # [{'tag','old','new'}]
        self.config = config or {}
        self._busy = False
        self.hotkeys = None                   # HotkeyManager，由 App 启动时挂上

    # ------------------------------------------------------------------ 基础
    @property
    def items(self) -> list[rules.FileItem]:
        """当前视图：在目录里就是该目录的子项，否则是导入的顶层节点。"""
        return self.children if self.crumbs else self.roots

    @items.setter
    def items(self, value: list[rules.FileItem]) -> None:
        if self.crumbs:
            self.children = value
        else:
            self.roots = value

    def attach(self, window) -> None:
        self._window = window

    def _js(self, script: str) -> None:
        if not self._window:
            return
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass

    def _toast(self, text: str, kind: str = 'ok') -> dict:
        return {'type': kind, 'text': text}

    def _view_base(self) -> str:
        """列表里「所在目录」列的基准路径。"""
        if self.crumbs:
            return self.crumbs[-1]['path']
        if not self.roots:
            return ''
        if len(self.roots) == 1:
            only = self.roots[0]
            return only.path if only.is_dir else only.dirname
        return rules.common_base(self.roots)

    def _row(self, item: rules.FileItem, index: int) -> dict:
        return {
            'tag': item.tag,
            'i': index,
            'name': item.name,
            'dir': rules.rel_dir(item, self._view_base()),
            'new': item.new_name,
            'type': rules.type_label(item),
            'size': rules.human_size(item.size, item.is_dir),
            'mtime': rules.fmt_time(item.mtime),
            'status': item.status,
            'statusText': rules.STATUS_LABEL.get(item.status, item.status),
            'error': item.error,
            'dup': item.dup,
            'sel': item.selected,
            'manual': item.manual_name,
            'isDir': item.is_dir,
            'changed': item.new_name != item.name,
        }

    def _rows(self) -> list[dict]:
        return [self._row(item, i) for i, item in enumerate(self.items)]

    def _light_rows(self) -> list[dict]:
        out = []
        for item in self.items:
            out.append({
                'tag': item.tag,
                'new': item.new_name,
                'status': item.status,
                'statusText': rules.STATUS_LABEL.get(item.status, item.status),
                'error': item.error,
                'dup': item.dup,
                'sel': item.selected,
                'manual': item.manual_name,
                'changed': item.new_name != item.name,
            })
        return out

    def _workflow_payload(self) -> list[dict]:
        out = []
        for step in self.workflow:
            meta = rules.RULE_MAP.get(step['id'], {})
            visible = [f['key'] for f in meta.get('fields', [])
                       if rules.field_visible(f, step.get('config') or {})]
            out.append({
                'id': step['id'],
                'name': meta.get('name', step['id']),
                'icon': meta.get('icon', ''),
                'desc': meta.get('desc', ''),
                'group': meta.get('group', '其它'),
                'enabled': step.get('enabled', True),
                'config': step.get('config') or {},
                'visible': visible,
            })
        return out

    def _nav(self) -> dict:
        base = self._view_base()
        return {
            'inside': bool(self.crumbs),
            'crumbs': [dict(c) for c in self.crumbs],
            'base': os.path.basename(base.rstrip('\\/')) if base else '',
        }

    def _stats(self) -> dict:
        total = len(self.items)
        sel = sum(1 for i in self.items if i.selected)
        todo = sum(1 for i in self.items if i.new_name != i.name and i.status != 'done')
        failed = sum(1 for i in self.items if i.status == 'error')
        done = sum(1 for i in self.items if i.status == 'done')
        manual = sum(1 for i in self.items if i.manual_name)
        dirs = sum(1 for i in self.items if i.is_dir)
        return {'total': total, 'sel': sel, 'todo': todo, 'failed': failed,
                'done': done, 'manual': manual, 'dirs': dirs,
                'canUndo': bool(self.history),
                'history': len(self.history)}

    def _can(self) -> dict:
        sel = any(i.selected for i in self.items)
        return {
            'run': any(i.new_name != i.name for i in self.items),
            'revert': bool(sel or self.history),
            'remove': sel,
            'copy': bool(self.items),
            'clear': bool(self.roots or self.children),
            'clearRules': bool(self.workflow),
            'home': bool(self.crumbs),
        }

    def _payload(self, full: bool = False, rows: bool = True, workflow: bool = False,
                 toast: dict | None = None, dialog: dict | None = None) -> dict:
        return {
            'items': self._rows() if full else None,
            'rows': self._light_rows() if (rows and not full) else None,
            'workflow': self._workflow_payload() if workflow else None,
            'nav': self._nav(),
            'stats': self._stats(),
            'can': self._can(),
            'toast': toast,
            'dialog': dialog,
            'clip': None,
        }

    def _refresh(self, full: bool = False, **kw) -> dict:
        rules.build_names(self.items, self.workflow)
        return self._payload(full=full, **kw)

    # ------------------------------------------------------------------ 元数据
    def get_meta(self) -> dict:
        return {
            'rules': [
                {'id': r['id'], 'name': r['name'], 'icon': r['icon'], 'desc': r['desc'],
                 'group': r['group'],
                 'fields': [dict(f) for f in r['fields']]}
                for r in rules.RULES
            ],
            'version': self.config.get('version', ''),
            'settings': {
                'theme': self.config.get('theme', 'dark'),
                'sort': self.config.get('sort', ''),
                'hotkey': self.config.get('hotkey'),
            },
        }

    def get_state(self) -> dict:
        with self.lock:
            if getattr(self, '_demo_pending', False):
                self._demo_pending = False
                self._fill_demo()
            return self._refresh(full=True, workflow=True)

    def report_error(self, message) -> dict:
        """前端异常回传，方便在日志里定位界面脚本问题。"""
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'ui-error.log'), 'a', encoding='utf-8') as fh:
                fh.write(str(message) + '\n')
        except OSError:
            pass
        return {'ok': True}

    # ------------------------------------------------------------------ 导入
    def add_paths(self, paths) -> dict:
        with self.lock:
            if isinstance(paths, str):
                paths = [paths]
            paths = [p for p in (paths or []) if p and os.path.exists(p)]
            return self._merge(rules.scan_root(paths))

    def pick_files(self) -> dict:
        paths = self._dialog('open')
        if not paths:
            return self._payload(rows=False)
        with self.lock:
            return self._merge(rules.scan_root(list(paths)))

    def pick_folder(self) -> dict:
        paths = self._dialog('folder')
        if not paths:
            return self._payload(rows=False)
        with self.lock:
            return self._merge(rules.scan_root(list(paths)))

    def _merge(self, found: list[rules.FileItem]) -> dict:
        """把新条目并进顶层列表（文件夹只作为节点，不进目录展开）。"""
        existing = {i.tag for i in self.roots}
        added = [i for i in found if i.tag not in existing]
        if not added:
            return self._payload(rows=False, toast=self._toast('没有找到可导入的条目', 'warn'))
        self.roots.extend(added)
        self._go_home()
        payload = self._refresh(full=True)
        folders = sum(1 for i in added if i.is_dir)
        extra = '，其中 %d 个文件夹（点名称进入）' % folders if folders else ''
        payload['toast'] = self._toast('已导入 %d 项%s' % (len(added), extra))
        return payload

    def _dialog(self, kind: str, save_filename: str = '', file_types: tuple = ()):
        try:
            import webview
            dialog = getattr(webview, 'FileDialog', None)
            if dialog is not None:
                types = {'open': dialog.OPEN, 'folder': dialog.FOLDER, 'save': dialog.SAVE}
            else:
                types = {'open': getattr(webview, 'OPEN_DIALOG', 10),
                         'folder': getattr(webview, 'FOLDER_DIALOG', 20),
                         'save': getattr(webview, 'SAVE_DIALOG', 30)}
            try:
                result = self._window.create_file_dialog(
                    types[kind], allow_multiple=(kind != 'folder'),
                    save_filename=save_filename, file_types=file_types)
            except TypeError:
                # 老版本 pywebview 不认 save_filename / file_types
                result = self._window.create_file_dialog(types[kind], allow_multiple=(kind != 'folder'))
            return result or []
        except Exception:
            return []

    def export_names(self) -> dict:
        """把当前列表的文件名导出成 Excel（原始名 / 新名 / 目录 / 类型 / 大小 / 时间 / 状态）。"""
        with self.lock:
            base = self._view_base()
            rows = [[i + 1, item.name, item.new_name,
                     rules.rel_dir(item, base), rules.type_label(item),
                     rules.human_size(item.size, item.is_dir),
                     rules.fmt_time(item.mtime),
                     rules.STATUS_LABEL.get(item.status, item.status)]
                    for i, item in enumerate(self.items)]
        if not rows:
            return self._payload(rows=False,
                                 toast=self._toast('列表是空的，先导入文件再导出', 'warn'))

        default = '文件清单_%s.xlsx' % datetime.now().strftime('%Y%m%d_%H%M%S')
        picked = self._dialog('save', save_filename=default,
                              file_types=('Excel 工作簿 (*.xlsx)',))
        if isinstance(picked, str):
            path = picked
        elif isinstance(picked, (list, tuple)) and picked:
            path = str(picked[0])
        else:
            path = ''
        if not path:
            return self._payload(rows=False)          # 用户取消
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        try:
            written = xlsx.write_sheet(
                path,
                header=['序号', '原始名称', '新名称预览', '所在目录', '类型', '大小', '修改时间', '状态'],
                rows=rows, sheet_name='文件名清单',
                widths=[6, 34, 34, 24, 10, 11, 18, 10], numeric_columns=(0,))
        except OSError as exc:
            return self._payload(rows=False,
                                 toast=self._toast('导出失败：%s' % exc, 'error'))
        return self._payload(rows=False,
                             toast=self._toast('已导出 %d 条文件名：%s' % (written, path)))

    def clear_items(self) -> dict:
        with self.lock:
            self.roots = []
            self.children = []
            self.crumbs = []
            self.history = []
            return self._refresh(full=True, toast=self._toast('列表已清空'))

    # ------------------------------------------------------------------ 目录导航
    def _go_home(self) -> None:
        self.crumbs = []
        self.children = []

    def _load_dir(self, path: str) -> list[rules.FileItem]:
        return rules.list_children(path)

    def enter_dir(self, tag: str) -> dict:
        """点目录名称进入：读取它的直接子项，不递归展开。"""
        with self.lock:
            item = next((i for i in self.items if i.tag == tag), None)
            if item is None or not item.is_dir:
                return self._payload(rows=False)
            if not os.path.isdir(item.path):
                return self._payload(rows=False, toast=self._toast('文件夹已经不存在了', 'error'))
            self.crumbs.append({'name': item.name, 'path': item.path})
            self.children = self._load_dir(item.path)
            return self._refresh(full=True)

    def goto_crumb(self, index) -> dict:
        """面包屑跳转：-1 回到导入列表，其它回到第 index 层。"""
        with self.lock:
            try:
                index = int(index)
            except (TypeError, ValueError):
                return self._payload(rows=False)
            if index < 0:
                self._go_home()
            elif index < len(self.crumbs):
                self.crumbs = self.crumbs[:index + 1]
                self.children = self._load_dir(self.crumbs[-1]['path'])
            else:
                return self._payload(rows=False)
            return self._refresh(full=True)

    def go_home(self) -> dict:
        with self.lock:
            self._go_home()
            return self._refresh(full=True)

    def open_path(self, path: str) -> dict:
        """凭绝对路径直接进入某个目录（用于「进入」以外的快捷入口）。"""
        with self.lock:
            if not path or not os.path.isdir(path):
                return self._payload(rows=False, toast=self._toast('目录不存在', 'error'))
            target = os.path.abspath(path)
            self.crumbs = [{'name': os.path.basename(target.rstrip('\\/')) or target,
                            'path': target}]
            self.children = self._load_dir(target)
            return self._refresh(full=True)

    def reload(self) -> dict:
        """重新读取磁盘：顶层重新 stat，目录内重新列子项。"""
        with self.lock:
            if self.crumbs:
                self.children = self._load_dir(self.crumbs[-1]['path'])
            else:
                self.roots = rules.scan_root([i.path for i in self.roots])
            return self._refresh(full=True, toast=self._toast('已重新读取目录'))

    # ------------------------------------------------------------------ 工作流
    def add_rule(self, rule_id: str, quiet: bool = False) -> dict:
        """新增一条规则。

        quiet=True 时不弹「已添加」提示，供前端在切换模块时
        自动预置默认规则使用（那是初始状态，不是用户的操作反馈）。
        """
        with self.lock:
            if rule_id not in rules.RULE_MAP:
                return self._payload(rows=False)
            self.workflow.append({'id': rule_id, 'enabled': True,
                                  'config': rules.default_config(rule_id)})
            toast = None if quiet else self._toast('已添加「%s」' % rules.rule_name(rule_id))
            return self._refresh(workflow=True, toast=toast)

    def remove_rule(self, index: int) -> dict:
        with self.lock:
            if 0 <= index < len(self.workflow):
                name = rules.rule_name(self.workflow[index]['id'])
                del self.workflow[index]
                return self._refresh(workflow=True, toast=self._toast('已移除「%s」' % name))
            return self._payload()

    def toggle_rule(self, index: int, enabled: bool) -> dict:
        with self.lock:
            if 0 <= index < len(self.workflow):
                self.workflow[index]['enabled'] = bool(enabled)
            return self._refresh(workflow=True)

    def move_rule(self, index: int, offset: int) -> dict:
        with self.lock:
            target = index + int(offset)
            if 0 <= index < len(self.workflow) and 0 <= target < len(self.workflow):
                step = self.workflow.pop(index)
                self.workflow.insert(target, step)
            return self._refresh(workflow=True)

    def reorder_rules(self, order) -> dict:
        with self.lock:
            try:
                order = [int(i) for i in order]
                if sorted(order) == list(range(len(self.workflow))):
                    self.workflow = [self.workflow[i] for i in order]
            except (TypeError, ValueError):
                pass
            return self._refresh(workflow=True)

    def set_rule(self, index: int, key: str, value) -> dict:
        with self.lock:
            if not (0 <= index < len(self.workflow)):
                return self._payload()
            step = self.workflow[index]
            before = self._visible_of(step)
            step['config'][key] = value
            # 显隐条件变化后需要重发规则卡片
            return self._refresh(workflow=(self._visible_of(step) != before))

    @staticmethod
    def _visible_of(step: dict) -> list[str]:
        meta = rules.RULE_MAP.get(step['id'], {})
        return [f['key'] for f in meta.get('fields', [])
                if rules.field_visible(f, step.get('config') or {})]

    def clear_rules(self) -> dict:
        with self.lock:
            self.workflow = []
            return self._refresh(workflow=True, toast=self._toast('已清空已添加的规则'))

    # ------------------------------------------------------------------ 列表操作
    def toggle_select(self, tag: str, value=None) -> dict:
        with self.lock:
            for item in self.items:
                if item.tag == tag:
                    item.selected = (not item.selected) if value is None else bool(value)
                    break
            return self._payload()

    def select_all(self, value: bool = True) -> dict:
        with self.lock:
            for item in self.items:
                item.selected = bool(value)
            return self._payload()

    def invert_select(self) -> dict:
        with self.lock:
            for item in self.items:
                item.selected = not item.selected
            return self._payload()

    def remove_items(self, tags=None) -> dict:
        with self.lock:
            target = set(tags) if tags else {i.tag for i in self.items if i.selected}
            if not target:
                return self._payload(rows=False, toast=self._toast('请先勾选要移除的条目', 'warn'))
            self.items = [i for i in self.items if i.tag not in target]
            self.history = [h for h in self.history if h['tag'] not in target]
            return self._refresh(full=True,
                                 toast=self._toast('已从列表移除 %d 项（磁盘文件未改动）' % len(target)))

    def reorder_items(self, order) -> dict:
        """按前端拖动后的 tag 顺序重排（会影响序号规则）。"""
        with self.lock:
            index = {item.tag: item for item in self.items}
            new_items = [index[t] for t in order if t in index]
            for item in self.items:
                if item.tag not in order:
                    new_items.append(item)
            self.items = new_items
            return self._refresh(full=True)

    def sort_items(self, key: str, reverse: bool = False) -> dict:
        with self.lock:
            self.items = rules.sort_items(self.items, key, bool(reverse))
            label = {'name': '名称', 'size': '大小',
                     'mtime': '修改时间', 'birthtime': '创建时间'}.get(key, key)
            return self._refresh(full=True, toast=self._toast('已按%s排序' % label))

    def set_manual(self, tag: str, name: str) -> dict:
        with self.lock:
            for item in self.items:
                if item.tag == tag:
                    item.manual_name = (name or '').strip()
                    break
            return self._refresh()

    def clear_manual(self, tags=None) -> dict:
        with self.lock:
            target = set(tags) if tags else {i.tag for i in self.items}
            for item in self.items:
                if item.tag in target:
                    item.manual_name = ''
            return self._refresh(full=True, toast=self._toast('已清除手动改名'))

    def paste_column(self, text: str) -> dict:
        """从剪贴板粘贴一列，按行覆盖选中项（无选中则覆盖全部）的主名。"""
        with self.lock:
            return self._paste_column(text)

    def _paste_column(self, text: str) -> dict:
        lines = [ln.strip() for ln in (text or '').replace('\r\n', '\n').split('\n')]
        while lines and not lines[-1]:
            lines.pop()
        if not lines:
            return self._payload(rows=False, toast=self._toast('剪贴板里没有内容', 'warn'))
        targets = [i for i in self.items if i.selected] or list(self.items)
        if not targets:
            return self._payload(rows=False, toast=self._toast('列表是空的', 'warn'))
        if len(lines) < len(targets):
            fill = lines[-1] if lines else ''
            lines += [fill] * (len(targets) - len(lines))
        for item, line in zip(targets, lines):
            item.manual_name = '' if not line else line
        return self._refresh(full=True, toast=self._toast(
            '已粘贴 %d 行到 %d 项' % (len(lines), len(targets))))

    def paste_clipboard(self) -> dict:
        """Ctrl+V：优先导入资源管理器复制的文件，否则当作一列名字粘贴。"""
        with self.lock:
            paths = clipboard.clipboard_files()
            if paths:
                return self._merge(rules.scan_root(paths))
            text = clipboard.clipboard_text()
            if not (text or '').strip():
                return self._payload(rows=False, toast=self._toast(
                    '剪贴板里没有文件，也没有文字', 'warn'))
            return self._paste_column(text)

    # ------------------------------------------------------------------ 复制
    def copy_to_clipboard(self, field: str = 'path', fmt: str = 'text') -> dict:
        with self.lock:
            target = [i for i in self.items if i.selected] or list(self.items)
            if not target:
                return self._payload(rows=False, toast=self._toast('没有可复制的内容', 'warn'))
            values = [i.path if field == 'path' else i.name for i in target]
            text = json.dumps(values, ensure_ascii=False, indent=2) if fmt == 'json' \
                else '\n'.join(values)
            ok = clipboard.copy_text(text)
            payload = self._payload(rows=False)
            if not ok:
                payload['clip'] = text          # 交给前端 navigator 兜底
            payload['toast'] = self._toast('已复制 %d 项%s' % (
                len(values), '完整路径' if field == 'path' else '文件名'))
            return payload

    def clipboard_text(self, field: str = 'path', fmt: str = 'text') -> str:
        with self.lock:
            target = [i for i in self.items if i.selected] or list(self.items)
            values = [i.path if field == 'path' else i.name for i in target]
            return json.dumps(values, ensure_ascii=False, indent=2) if fmt == 'json' \
                else '\n'.join(values)

    # ------------------------------------------------------------------ 执行
    def rename_now(self, tags=None) -> dict:
        with self.lock:
            if self._busy:
                return self._payload(rows=False, toast=self._toast('正在执行，请稍候', 'warn'))
            scope = set(tags) if tags else None
            targets = [i for i in self.items
                       if (scope is None or i.tag in scope) and i.new_name != i.name]
            if not targets:
                return self._payload(rows=False, toast=self._toast('没有需要重命名的条目', 'warn'))

            blocked: list[str] = []
            # 1. 名称非法
            #    过滤一律按 tag 判断：FileItem 是 dataclass，`in` 会走带全部字段
            #    （含 info_cache 字典）的 __eq__，上万条时是 O(n²) 的隐藏开销。
            invalid_tags: set[str] = set()
            for item in targets:
                err = rules.validate(item)
                if err:
                    item.status = 'error'
                    item.error = err
                    invalid_tags.add(item.tag)
            valid = [i for i in targets if i.tag not in invalid_tags]

            # 2. 批内重名（整组跳过）
            dup = rules.find_conflicts(valid)
            dup_tags = {i.tag for i in dup}
            for item in dup:
                item.status = 'error'
                item.error = '与本批其他条目重名，建议加一条「智能去重」规则'
            valid = [i for i in valid if i.tag not in dup_tags]

            # 3. 目标已存在
            #    同批内的互换 / 链式改名（a→b、b→a）要放行，交给两阶段改名处理；
            #    只要目标被批外的东西占着就拦下，并且反复收敛（被拦下的一方不再释放名字）。
            free: list[rules.FileItem] = []
            pending: list[rules.FileItem] = []
            for item in valid:
                dst = os.path.join(item.dirname, item.new_name)
                if os.path.normcase(dst) == os.path.normcase(item.path):
                    if dst == item.path:
                        item.status = 'done'          # 名字完全没变，跳过
                        item.error = ''
                    else:
                        free.append(item)             # 只改大小写，Windows 允许直接改
                    continue
                pending.append(item)

            while pending:
                src = {os.path.normcase(i.path) for i in pending}
                keep: list[rules.FileItem] = []
                dropped: list[rules.FileItem] = []
                for item in pending:
                    dst = os.path.join(item.dirname, item.new_name)
                    if os.path.exists(dst) and os.path.normcase(dst) not in src:
                        dropped.append(item)
                    else:
                        keep.append(item)
                for item in dropped:
                    item.status = 'error'
                    item.error = '目标位置已存在同名文件'
                    blocked.append('%s → %s' % (item.name, item.new_name))
                if not dropped:
                    free.extend(keep)
                    break
                pending = keep
                if not pending:
                    break

            if not free:
                payload = self._refresh()
                payload['toast'] = self._toast('没有可执行的条目，请查看标红项', 'error')
                return payload

            self._busy = True
            try:
                ok, errors = self._apply(free)
            finally:
                self._busy = False

        payload = self._refresh(full=True)
        lines = list(blocked[:6]) + ['%s：%s' % (n, e) for n, e in errors[:6]]
        if lines:
            payload['dialog'] = {
                'title': '已跳过 %d 项' % (len(blocked) + len(errors)),
                'lines': lines,
            }
        kind = 'error' if not ok else ('warn' if (blocked or errors or dup or invalid_tags) else 'ok')
        payload['toast'] = self._toast('成功重命名 %d 项%s' % (
            ok, '' if not (blocked or errors) else '，跳过 %d 项' % (len(blocked) + len(errors))), kind)
        return payload

    def _apply(self, items: list[rules.FileItem]):
        ok = 0
        errors: list[tuple[str, str]] = []
        src = {os.path.normcase(i.path) for i in items}
        two_phase = False
        for item in items:
            dst = os.path.join(item.dirname, item.new_name)
            if os.path.normcase(dst) in src and os.path.normcase(dst) != os.path.normcase(item.path):
                two_phase = True
                break

        total = len(items)
        if two_phase:
            temps: dict[str, str] = {}
            mark = uuid.uuid4().hex[:8]
            for idx, item in enumerate(items):
                tmp = os.path.join(item.dirname, '.fr_tmp_%s_%s' % (mark, item.name))
                try:
                    os.rename(item.path, tmp)
                    temps[item.path] = tmp
                except OSError as exc:
                    item.status = 'error'
                    item.error = str(exc)
                    errors.append((item.name, str(exc)))
                self._progress(idx + 1, total)
            for item in items:
                tmp = temps.get(item.path)
                if not tmp:
                    continue
                dst = os.path.join(item.dirname, item.new_name)
                try:
                    os.rename(tmp, dst)
                except OSError as exc:
                    errors.append((item.name, str(exc)))
                    try:
                        os.rename(tmp, item.path)
                    except OSError:
                        pass
                    continue
                self._mark_done(item, dst)
                ok += 1
        else:
            for idx, item in enumerate(items):
                dst = os.path.join(item.dirname, item.new_name)
                try:
                    os.rename(item.path, dst)
                except OSError as exc:
                    item.status = 'error'
                    item.error = str(exc)
                    errors.append((item.name, str(exc)))
                else:
                    self._mark_done(item, dst)
                    ok += 1
                self._progress(idx + 1, total)
        return ok, errors

    def _progress(self, done: int, total: int) -> None:
        if total > 200 and done % 20 and done != total:
            return
        self._js('window.__onProgress && window.__onProgress(%d, %d)' % (done, total))

    def _mark_done(self, item: rules.FileItem, new_path: str) -> None:
        old_path = item.path
        self.history.append({'tag': item.tag, 'old': old_path, 'new': new_path})
        item.path = new_path
        item.name = os.path.basename(new_path)
        item.status = 'done'
        item.error = ''
        item.info_cache.pop('imageSize', None)
        item.info_cache.pop('photoTime', None)
        item.info_cache.pop('photoMeta', None)

    def revert(self, tags=None) -> dict:
        with self.lock:
            scope = set(tags) if tags else {i.tag for i in self.items if i.selected}
            if not scope and self.history:
                scope = {h['tag'] for h in self.history}
            records = [h for h in self.history if h['tag'] in scope]
            if not records:
                return self._payload(rows=False, toast=self._toast('没有可撤回的重命名记录', 'warn'))

            selected = {i.tag for i in self.items if i.selected}
            ok, errors = 0, []
            # 后进先出：先撤回子项、再撤回父目录，否则父目录改名后子项路径就对不上了
            for rec in reversed(records):
                new_path, old_path = rec['new'], rec['old']
                if not os.path.exists(new_path):
                    errors.append((os.path.basename(new_path), '文件已不存在，无法撤回'))
                    continue
                if os.path.normcase(old_path) != os.path.normcase(new_path) \
                        and os.path.exists(old_path):
                    errors.append((os.path.basename(new_path), '原名已被占用，无法撤回'))
                    continue
                try:
                    os.rename(new_path, old_path)
                except OSError as exc:
                    errors.append((os.path.basename(new_path), str(exc)))
                    continue
                self.history.remove(rec)
                ok += 1

            self.reload_view()
            for item in self.items:
                if item.tag in selected:
                    item.selected = True
            payload = self._refresh(full=True)
            if errors:
                payload['dialog'] = {
                    'title': '撤回完成 %d 项，%d 项未处理' % (ok, len(errors)),
                    'lines': ['%s：%s' % (n, e) for n, e in errors[:8]],
                }
            payload['toast'] = self._toast('已撤回 %d 项' % ok, 'ok' if ok else 'error')
            return payload

    def reload_view(self) -> None:
        """按当前视图重新读盘（不产生 payload）。"""
        if self.crumbs and not os.path.isdir(self.crumbs[-1]['path']):
            # 当前目录本身被改名 / 删掉了，退回导入层
            self._go_home()
        if self.crumbs:
            self.children = self._load_dir(self.crumbs[-1]['path'])
        else:
            self.roots = rules.scan_root([i.path for i in self.roots])

    # ------------------------------------------------------------------ 设置
    def set_setting(self, key: str, value) -> dict:
        self.config[key] = value
        return self._payload(rows=False)

    def set_hotkey(self, cfg) -> dict:
        """设置全局唤出快捷键；cfg 为 None 表示清除。立即生效并随配置保存。"""
        cfg = cfg if isinstance(cfg, dict) else None
        self.config['hotkey'] = cfg
        if cfg is None:
            if self.hotkeys:
                self.hotkeys.update(None)
            return self._payload(rows=False, toast=self._toast('已清除全局快捷键'))
        ok = True
        if self.hotkeys:
            ok = bool(self.hotkeys.update(cfg))
        if ok:
            return self._payload(rows=False, toast=self._toast('全局快捷键已生效：%s' % hk_label(cfg)))
        # 注册失败时不要把坏配置留在配置文件里
        self.config['hotkey'] = None
        return self._payload(rows=False, toast=self._toast('快捷键注册失败，可能已被其他程序占用', 'warn'))

    def toggle_visible(self) -> None:
        """全局快捷键命中：窗口可见就隐藏，否则显示并置前。"""
        if not self._window:
            return
        try:
            import ctypes
            hwnd = self._native_hwnd()
            visible = bool(hwnd) and bool(ctypes.windll.user32.IsWindowVisible(hwnd))
            if visible:
                self._window.hide()
                return
            try:
                if getattr(self._window, 'minimized', False):
                    self._window.restore()
            except Exception:
                pass
            self._window.show()
            if hwnd:
                user32 = ctypes.windll.user32
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def push_toast(self, text: str, kind: str = 'warn') -> None:
        """后台线程（快捷键钩子等）主动向前端推一条提示。"""
        payload = {'toast': {'type': kind, 'text': text}}
        self._js('window.__onPush && window.__onPush(%s)'
                 % json.dumps(payload, ensure_ascii=False))

    def minimize(self) -> None:
        self._window_call('minimize')

    def toggle_max(self) -> dict:
        if not self._window:
            return {}
        try:
            if getattr(self, '_maxed', False):
                self._window.restore()
                self._maxed = False
            else:
                self._window.maximize()
                self._maxed = True
        except Exception:
            pass
        return {'maxed': getattr(self, '_maxed', False)}

    def close(self) -> None:
        self._window_call('destroy')

    def _window_call(self, name: str) -> None:
        if not self._window:
            return
        try:
            getattr(self._window, name)()
        except Exception:
            pass

    def start_resize(self, code: int = 17) -> None:
        """拖拽窗口边缘缩放。

        code 用标准 HT* 常量：10 左 / 11 右 / 12 上 / 13 左上 / 14 右上 /
        15 下 / 16 左下 / 17 右下。

        不走 WM_NCLBUTTONDOWN 命中测试（WebView2 持有鼠标捕获时不可靠），
        改为在后台线程轮询光标位置，用 SetWindowPos 实时改窗口矩形。
        """
        try:
            import ctypes
            import time
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            hwnd = self._native_hwnd()
            if not hwnd:
                return
            if getattr(self, '_maxed', False):
                self.toggle_max()          # 最大化状态下先还原再拖
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return
            pt = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return
            l0, t0, r0, b0 = rect.left, rect.top, rect.right, rect.bottom
            x0, y0 = pt.x, pt.y
            min_w, min_h = 820, 560        # 与 main.py 的 min_size 保持一致
            SWP_NOZORDER = 0x0004
            while user32.GetAsyncKeyState(0x01) & 0x8000:   # 左键按住
                user32.GetCursorPos(ctypes.byref(pt))
                dx, dy = pt.x - x0, pt.y - y0
                l, t, r, b = l0, t0, r0, b0
                if int(code) in (10, 13, 16):              # 左边缘
                    l = min(l0 + dx, r0 - min_w)
                if int(code) in (11, 14, 17):              # 右边缘
                    r = max(r0 + dx, l0 + min_w)
                if int(code) in (12, 13, 14):              # 上边缘
                    t = min(t0 + dy, b0 - min_h)
                if int(code) in (15, 16, 17):              # 下边缘
                    b = max(b0 + dy, t0 + min_h)
                user32.SetWindowPos(wintypes.HWND(hwnd), 0, l, t,
                                    max(min_w, r - l), max(min_h, b - t), SWP_NOZORDER)
                time.sleep(0.01)

            # 松手后先记一次，免得用户拖完直接强杀进程导致尺寸丢失
            geometry = window_geometry(hwnd)
            if geometry:
                self.config['geometry'] = geometry
        except Exception:
            pass

    def native_handle(self) -> int:
        """当前窗口的原生句柄（没有窗口或取不到时返回 0）。"""
        return self._native_hwnd()

    def _native_hwnd(self) -> int:
        """优先取 pywebview 的原生窗口句柄，失败再按标题查找。"""
        try:
            handle = getattr(self._window, 'native', None)
            hwnd = getattr(handle, 'Handle', None)
            if hwnd:
                return int(hwnd)
        except Exception:
            pass
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, self._window.title)
            return int(hwnd) if hwnd else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------ 演示数据
    def add_paths_demo(self) -> dict:
        """开发 / 截图自测用：注入一批虚构条目（不依赖磁盘文件）。"""
        self._fill_demo()
        payload = self._refresh(full=True, workflow=True, toast=self._toast('演示数据已就绪'))
        if self._window:
            try:
                self._window.evaluate_js('window.__onPush && window.__onPush(%s)'
                                        % json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass
        return payload

    def _fill_demo(self) -> None:
        import datetime
        import time
        now = time.time()
        samples = [
            ('Vacation Photo 01.JPG', 'D:/photos', 240000, '2024-07-12 09:31:20'),
            ('Vacation Photo 02.JPG', 'D:/photos', 256000, '2024-07-12 09:33:05'),
            ('IMG_0007.png', 'D:/photos/raw', 4096, '2024-07-12 10:02:44'),
            ('照片 001.jpg', 'D:/photos/raw', 1258291, '2024-07-13 18:20:11'),
            ('report  final.docx', 'D:/photos/docs', 15800, '2024-07-14 08:00:00'),
            ('notes.txt', 'D:/photos/docs', 900, '2024-07-14 08:05:00'),
            ('screen shot 2024.png', 'D:/photos', 512000, '2024-07-15 21:10:00'),
            ('untitled.txt', 'D:/photos', 1200, '2024-07-15 21:12:00'),
            ('untitled.txt', 'D:/photos', 1300, '2024-07-15 21:13:00'),
        ]
        self.roots = []
        self.children = []
        self.crumbs = []
        for idx, (name, folder, size, when) in enumerate(samples):
            ts = datetime.datetime.strptime(when, '%Y-%m-%d %H:%M:%S').timestamp()
            item = rules.FileItem(path=os.path.join(folder.replace('/', os.sep), name),
                                  name=name, size=size, mtime=ts, ctime=ts - 3600)
            if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                item.info_cache['imageSize'] = '1920x1080'
                item.info_cache['photoTime'] = when if idx < 4 else ''
            self.roots.append(item)
        if not self.workflow:
            self.workflow = [
                {'id': 'clean', 'enabled': True,
                 'config': dict(rules.default_config('clean'), mode='underscore')},
                {'id': 'sequence', 'enabled': True,
                 'config': dict(rules.default_config('sequence'),
                                numType='cn_lower', digits=0, pos='prefix')},
            ]
        self.roots[0].selected = True
        self.roots[3].selected = True
