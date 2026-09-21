# -*- coding: utf-8 -*-
"""文件批量重命名 · 规则引擎

纯标准库实现，不依赖任何第三方包。

13 条规则（按界面显示顺序）：
    查找替换 / 批量替换 / 提取子串 / 添加前后缀 / 大小写转换 / 清理文件名 / 删除字符
    智能序列号 / 插入内容 / 模板替换 / 时间命名 / 扩展名处理 / 智能去重

能力对齐 rename-lite：序号类型（中文 / 繁体 / 人民币 / 英文字母 / 罗马 / 带圈 / 全角）、
取子串的 7 种方式、按文件信息命名（时间 / 大小 / 图片尺寸 / EXIF 相机、镜头、光圈、
快门、ISO、焦距、拍摄时间）。
"""
from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import imaging
import numerals

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
INVALID_HINT = '包含非法字符 < > : " / \\ | ? *'

STATUS_LABEL = {
    'pending': '未变更',
    'changed': '待处理',
    'done': '已完成',
    'error': '失败',
}

STATUS_TONE = {
    'pending': 'idle',
    'changed': 'pending',
    'done': 'done',
    'error': 'error',
}

TYPE_GROUPS = [
    ('图片', {'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'heic', 'tif', 'tiff'}),
    ('视频', {'mp4', 'mov', 'avi', 'mkv', 'wmv', 'flv', 'webm', 'm4v'}),
    ('音频', {'mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'wma'}),
    ('压缩包', {'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'}),
    ('代码', {'js', 'ts', 'vue', 'html', 'htm', 'css', 'json', 'py', 'go', 'rb', 'java',
              'c', 'cpp', 'h', 'rs', 'sh', 'yml', 'yaml', 'xml', 'sql'}),
    ('文档', {'txt', 'md', 'doc', 'docx', 'pdf', 'ppt', 'pptx', 'xls', 'xlsx', 'csv'}),
]

FILE_FIELDS = [
    ('name', '原文件名'),
    ('basename', '主名（去扩展名）'),
    ('ext', '扩展名'),
    ('folder', '所在文件夹'),
    ('mtime', '修改时间'),
    ('birthtime', '创建时间'),
    ('size', '文件大小'),
    ('imageSize', '图片尺寸'),
    ('photoTime', '照片拍摄时间'),
    ('cameraMake', '相机品牌'),
    ('cameraModel', '相机型号'),
    ('lens', '镜头'),
    ('aperture', '光圈'),
    ('shutter', '快门'),
    ('iso', 'ISO'),
    ('focal', '焦距'),
]

# EXIF 里取出来的字段（走 photo_meta，一次读取缓存复用）
CAMERA_FIELDS = frozenset({'cameraMake', 'cameraModel', 'lens', 'aperture',
                           'shutter', 'iso', 'focal'})

NUMERAL_OPTIONS = numerals.options()
TIME_TOKENS = {'YYYY': '%Y', 'MM': '%m', 'DD': '%d', 'HH': '%H', 'mm': '%M', 'ss': '%S'}
TIME_PATTERN = re.compile('YYYY|MM|DD|HH|mm|ss')


# --------------------------------------------------------------------------- #
# 批量替换预设
# --------------------------------------------------------------------------- #

CN_PUNCT_TABLE = str.maketrans({
    '，': ',', '。': '.', '、': '_', '：': '_', '；': ';', '！': '!', '？': '?',
    '（': '(', '）': ')', '【': '[', '】': ']', '《': '_', '》': '', '「': '', '」': '',
    '“': '', '”': '', '‘': '', '’': '', '～': '~', '－': '-', '　': ' ', '·': '_',
})

EMOJI_PATTERN = re.compile(
    '[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]')


def _preset_fullwidth(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(' ')
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return ''.join(out)


RULE_PRESETS: dict[str, tuple] = {
    'cnpunct': ('中文标点 → 英文', lambda s: s.translate(CN_PUNCT_TABLE)),
    'fullwidth': ('全角字符 → 半角', _preset_fullwidth),
    'brackets': ('删除括号及其中内容', lambda s: re.sub(
        r'\s*[（(\[【〈《][^）)\]】〉》]*[）)\]】〉》]', '', s)),
    'emoji': ('删除表情符号', lambda s: EMOJI_PATTERN.sub('', s)),
    'space': ('压缩多余空格', lambda s: re.sub(r'\s+', ' ', s).strip()),
    'symbols': ('删除符号，只留文字数字', lambda s: re.sub(r'[^\w.\-\s\u4e00-\u9fff]', '', s)),
}

PRESET_OPTIONS = [('', '自定义（在下方逐行填写）')] + \
    [(key, label) for key, (label, _fn) in RULE_PRESETS.items()]


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

@dataclass
class FileItem:
    """列表中的一个待处理项（文件或文件夹）。"""

    path: str
    name: str
    is_dir: bool = False
    size: int = 0
    mtime: float = 0.0
    ctime: float = 0.0
    orig: str = ''                 # 导入时的名字，工作流始终以它为基础计算
    basename: str = ''             # 导入时的主名（不含扩展名）
    ext: str = ''                  # 导入时的扩展名（含点）
    new_name: str = ''
    manual_name: str = ''          # 手动改名（主名，不含扩展名）；空 = 未手动改
    status: str = 'pending'        # pending / changed / done / error
    error: str = ''
    dup: bool = False
    selected: bool = False
    tag: str = ''                  # 稳定的行标识（Treeview / 前端行 id）
    info_cache: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.orig:
            self.orig = self.name
        if not self.basename and not self.ext:
            self.basename, self.ext = split_name(self.name, self.is_dir)
        if not self.new_name:
            self.new_name = self.name
        if not self.tag:
            self.tag = self.path.lower()

    @property
    def dirname(self) -> str:
        return os.path.dirname(self.path)

    @property
    def size_text(self) -> str:
        return '--' if self.is_dir else human_size(self.size)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    """拆出主名与扩展名（扩展名带点）。文件夹不拆扩展名。"""
    if is_dir:
        return name, ''
    i = name.rfind('.')
    if i <= 0:
        return name, ''
    return name[:i], name[i:]


def sanitize(name: str) -> str:
    """去掉 Windows 不允许的字符与结尾的点/空格。"""
    out = INVALID_CHARS.sub('_', name)
    out = out.rstrip(' .')
    return out or '_'


def ext_of(name: str) -> str:
    return split_name(name)[1].lstrip('.').lower()


def human_size(size: int | None, is_dir: bool = False) -> str:
    if is_dir or size is None:
        return '--'
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return ('%d %s' % (value, unit)) if unit == 'B' else ('%.1f %s' % (value, unit))
        value /= 1024
    return '--'


def format_datetime(ts: float, fmt: str = 'YYYY-MM-DD_HH-mm-ss') -> str:
    if not ts:
        return ''
    return format_pattern(datetime.fromtimestamp(ts), fmt)


def format_pattern(moment: datetime, fmt: str) -> str:
    return TIME_PATTERN.sub(lambda m: moment.strftime(TIME_TOKENS[m.group(0)]), fmt or '')


def fmt_time(ts: float) -> str:
    if not ts:
        return ''
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def type_label(item: FileItem) -> str:
    if item.is_dir:
        return '文件夹'
    e = ext_of(item.name)
    for label, exts in TYPE_GROUPS:
        if e in exts:
            return label
    return e.upper() if e else '文件'


# --------------------------------------------------------------------------- #
# 规则定义（fields 里的 show_if 由界面决定字段的显隐）
# --------------------------------------------------------------------------- #

def _f(key, label, kind='text', default='', **kw):
    return dict(key=key, label=label, type=kind, default=default, **kw)


RULES: list[dict[str, Any]] = [
    {
        'id': 'replace', 'name': '查找替换', 'icon': '🔍', 'group': '常规',
        'desc': '查找文本或截取一段再替换，支持正则',
        'fields': [
            _f('mode', '查找方式', 'select', 'text', options=[
                ('text', '直接查找文本'), ('head', '前 N 位'), ('tail', '后 N 位'),
                ('slice', '第 X 位起取 N 位'), ('before', '某字符之前'),
                ('after', '某字符之后'), ('beforeN', '某字符之前的 N 位'),
                ('afterN', '某字符之后的 N 位')]),
            _f('find', '查找', default='', show_if={'mode': ['text']}),
            _f('n', '位数 N', 'number', 3, show_if={'mode': ['head', 'tail', 'slice', 'beforeN', 'afterN']}),
            _f('from', '起始位 X', 'number', 1, show_if={'mode': ['slice']}),
            _f('marker', '分隔字符', default='_',
               show_if={'mode': ['before', 'after', 'beforeN', 'afterN']}),
            _f('replace', '替换为', default=''),
            _f('isRegex', '使用正则', 'bool', False, show_if={'mode': ['text']}),
            _f('caseSensitive', '区分大小写', 'bool', False, show_if={'mode': ['text']}),
            _f('replaceExt', '包含扩展名', 'bool', True),
        ],
    },
    {
        'id': 'replace_multi', 'name': '批量替换', 'icon': '🧾', 'group': '常规',
        'desc': '一次替换多组内容，或直接套用常见预设',
        'fields': [
            _f('preset', '预设方案', 'select', '', options=PRESET_OPTIONS),
            _f('pairs', '替换列表', 'textarea', '',
               placeholder='每行一组：旧=新 或 旧=>新，# 开头为注释',
               show_if={'preset': ['']}),
            _f('isRegex', '列表左侧按正则解释', 'bool', False, show_if={'preset': ['']}),
            _f('caseSensitive', '区分大小写', 'bool', False, show_if={'preset': ['']}),
            _f('replaceExt', '包含扩展名', 'bool', True),
        ],
    },
    {
        'id': 'extract', 'name': '提取子串', 'icon': '⛏', 'group': '常规',
        'desc': '截取文件名的一段作为新主名',
        'fields': [
            _f('mode', '提取方式', 'select', 'head', options=[
                ('head', '前 N 位'), ('tail', '后 N 位'), ('slice', '第 X 位起取 N 位'),
                ('before', '某字符之前'), ('after', '某字符之后'),
                ('beforeN', '某字符之前的 N 位'), ('afterN', '某字符之后的 N 位')]),
            _f('n', '位数 N', 'number', 3, show_if={'mode': ['head', 'tail', 'slice', 'beforeN', 'afterN']}),
            _f('from', '起始位 X', 'number', 1, show_if={'mode': ['slice']}),
            _f('marker', '分隔字符', default='_',
               show_if={'mode': ['before', 'after', 'beforeN', 'afterN']}),
            _f('keepExt', '保留原扩展名', 'bool', True),
        ],
    },
    {
        'id': 'affix', 'name': '添加前后缀', 'icon': '➕', 'group': '常规',
        'desc': '在主名前后添加文本，扩展名保持不动',
        'fields': [
            _f('prefix', '前缀', default=''),
            _f('suffix', '后缀', default=''),
        ],
    },
    {
        'id': 'case', 'name': '大小写转换', 'icon': '🔠', 'group': '常规',
        'desc': '转换文件名大小写',
        'fields': [
            _f('mode', '模式', 'select', 'lower', options=[
                ('lower', '全小写'), ('upper', '全大写'),
                ('title', '首字母大写'), ('words', '每词首字母大写')]),
            _f('onlyBase', '只改主名，扩展名不变', 'bool', True),
        ],
    },
    {
        'id': 'clean', 'name': '清理文件名', 'icon': '🧹', 'group': '常规',
        'desc': '处理空格、特殊字符与多余点号',
        'fields': [
            _f('mode', '模式', 'select', 'underscore', options=[
                ('underscore', '空格 → 下划线'), ('hyphen', '空格 → 连字符'),
                ('remove', '删除空格'), ('collapse', '压缩多余空格')]),
            _f('keepSpecial', '保留特殊字符', 'bool', False),
            _f('normalizeDots', '规范连续点号', 'bool', True),
        ],
    },
    {
        'id': 'delete', 'name': '删除字符', 'icon': '✂️', 'group': '常规',
        'desc': '按位置区间或指定内容删除字符',
        'fields': [
            _f('mode', '模式', 'select', 'range', options=[
                ('range', '按位置区间'), ('head', '删除开头 N 个'),
                ('tail', '删除末尾 N 个'), ('chars', '删除指定字符')]),
            _f('start', '起始位置', 'number', 0, show_if={'mode': ['range']}),
            _f('count', '数量', 'number', 1, show_if={'mode': ['range', 'head', 'tail']}),
            _f('target', '要删除的字符', default='', show_if={'mode': ['chars']}),
        ],
    },
    {
        'id': 'sequence', 'name': '智能序列号', 'icon': '🔢', 'group': '编号',
        'desc': '按规则插入递增序号，支持中文、字母、人民币',
        'fields': [
            _f('start', '起始值', 'number', 1),
            _f('step', '步长', 'number', 1),
            _f('numType', '序号类型', 'select', 'arabic', options=NUMERAL_OPTIONS),
            _f('digits', '补零位数', 'number', 3),
            _f('pos', '位置', 'select', 'prefix', options=[
                ('prefix', '前缀'), ('suffix', '后缀（扩展名前）'),
                ('replace', '替换整个主名')]),
            _f('sep', '分隔符', default='', placeholder='留空 = 与原名直接相连',
               show_if={'pos': ['prefix', 'suffix']}),
            _f('template', '替换模板', default='IMG_{n}',
               show_if={'pos': ['replace']}),
        ],
    },
    {
        'id': 'insert', 'name': '插入内容', 'icon': '🧩', 'group': '编号',
        'desc': '插入固定文本 / 序号 / 文件信息（时间、大小、图片尺寸…）',
        'fields': [
            _f('kind', '内容类型', 'select', 'text', options=[
                ('text', '固定文本'), ('number', '序号'), ('file', '文件信息')]),
            _f('text', '固定文本', default='', show_if={'kind': ['text']}),
            _f('start', '序号起始', 'number', 1, show_if={'kind': ['number']}),
            _f('step', '序号步长', 'number', 1, show_if={'kind': ['number']}),
            _f('numType', '序号类型', 'select', 'arabic', options=NUMERAL_OPTIONS,
               show_if={'kind': ['number']}),
            _f('width', '补零位数', 'number', 0, show_if={'kind': ['number']}),
            _f('field', '文件信息', 'select', 'mtime', options=FILE_FIELDS,
               show_if={'kind': ['file']}),
            _f('format', '时间格式', default='YYYY-MM-DD_HH-mm-ss',
               show_if={'kind': ['file'], 'field': ['mtime', 'birthtime', 'photoTime']}),
            _f('substring', '再取一部分', 'select', '', options=[
                ('', '不处理'), ('head', '前 N 位'), ('tail', '后 N 位'),
                ('before', '某字符之前'), ('after', '某字符之后')],
               show_if={'kind': ['file'], 'field': ['name', 'basename', 'ext', 'folder']}),
            _f('subN', '取部分位数', 'number', 3,
               show_if={'kind': ['file'], 'substring': ['head', 'tail']}),
            _f('subMarker', '取部分分隔字符', default='_',
               show_if={'kind': ['file'], 'substring': ['before', 'after']}),
            _f('prefix', '内容前加', default=''),
            _f('suffix', '内容后加', default=''),
            _f('position', '插入位置', 'select', 'end', options=[
                ('all', '整个文件名（覆盖主名）'), ('start', '开头'),
                ('at', '指定位置'), ('end', '末尾')], toggle=True),
            _f('atIndex', '第 N 位', 'number', 1, show_if={'position': ['at']}),
        ],
    },
    {
        'id': 'template', 'name': '模板替换', 'icon': '📐', 'group': '编号',
        'desc': '用占位符拼新名：{name} {ext} {n} {YYYY} {MM} {DD}',
        'fields': [
            _f('template', '模板', default='{name}_{n}'),
            _f('start', '序号起始', 'number', 1),
            _f('step', '步长', 'number', 1),
            _f('numType', '序号类型', 'select', 'arabic', options=NUMERAL_OPTIONS),
            _f('digits', '补零位数', 'number', 2),
        ],
    },
    {
        'id': 'timestamp', 'name': '时间命名', 'icon': '🕒', 'group': '编号',
        'desc': '按当前时间或文件时间命名',
        'fields': [
            _f('src', '时间来源', 'select', 'now', options=[
                ('now', '当前时间'), ('mtime', '修改时间'), ('ctime', '创建时间')]),
            _f('format', '格式', default='YYYY-MM-DD_HH-mm-ss'),
            _f('pos', '位置', 'select', 'replace', options=[
                ('replace', '替换整个主名'), ('prefix', '作为前缀'),
                ('suffix', '作为后缀')]),
            _f('sep', '分隔符', default='', placeholder='留空 = 与原名直接相连',
               show_if={'pos': ['prefix', 'suffix']}),
        ],
    },
    {
        'id': 'extension', 'name': '扩展名处理', 'icon': '📎', 'group': '其它',
        'desc': '统一扩展名大小写或改成指定扩展名',
        'fields': [
            _f('mode', '模式', 'select', 'lowercase', options=[
                ('lowercase', '转小写'), ('uppercase', '转大写'),
                ('replace', '改为指定扩展名'), ('remove', '删除扩展名')]),
            _f('value', '新扩展名', default='.txt', show_if={'mode': ['replace']}),
        ],
    },
    {
        'id': 'uniqueify', 'name': '智能去重', 'icon': '🔀', 'group': '其它',
        'desc': '重名时自动追加序号（添加并启用后生效）',
        'fields': [
            _f('sep', '分隔符', default='_'),
            _f('start', '起始序号', 'number', 2),
            _f('digits', '补零位数', 'number', 0),
        ],
    },
]

RULE_MAP = {r['id']: r for r in RULES}
RULE_ORDER = {r['id']: i for i, r in enumerate(RULES)}


def default_config(rule_id: str) -> dict[str, Any]:
    return {f['key']: f['default'] for f in RULE_MAP[rule_id]['fields']}


def rule_name(rule_id: str) -> str:
    r = RULE_MAP.get(rule_id)
    return r['name'] if r else rule_id


def field_visible(field_def: dict, config: dict) -> bool:
    """按 show_if 判断字段是否显示（多个条件同时满足才显示）。"""
    cond = field_def.get('show_if')
    if not cond:
        return True
    for key, allowed in cond.items():
        value = config.get(key)
        if isinstance(allowed, list):
            if value not in allowed:
                return False
        elif value != allowed:
            return False
    return True


# --------------------------------------------------------------------------- #
# 取子串
# --------------------------------------------------------------------------- #

def extract_span(text: str, mode: str, cfg: dict) -> tuple[str, int]:
    """按 7 种方式截取子串，返回 (子串, 起始位置)；取不到返回 ('', -1)。

    返回起始位置是为了让「查找替换」能精确替换该子串，而不是去替换
    字符串里第一处相同的文本（rename-lite 在这里有已知缺陷）。
    """
    if not text:
        return '', -1
    n = _to_int(cfg.get('n'), 0)
    marker = cfg.get('marker') or ''
    if mode == 'head':
        return (text[:n], 0) if n > 0 else ('', -1)
    if mode == 'tail':
        if n <= 0:
            return '', -1
        start = max(0, len(text) - n)
        return text[start:], start
    if mode == 'slice':
        if n <= 0:
            return '', -1
        start = max(1, _to_int(cfg.get('from'), 1)) - 1
        return text[start:start + n], start
    if not marker:
        return '', -2
    pos = text.find(marker)
    if pos < 0:
        return '', -2
    if mode == 'after':
        return text[pos + len(marker):], pos + len(marker)
    if mode == 'before':
        return text[:pos], 0
    if mode == 'afterN':
        return text[pos + len(marker):pos + len(marker) + n], pos + len(marker)
    if mode == 'beforeN':
        start = max(0, pos - n)
        return text[start:pos], start
    return '', -1


def extract_substring(text: str, mode: str, cfg: dict, empty_on_miss: bool = True) -> str:
    """按 7 种方式截取子串。

    empty_on_miss=True：取不到时返回空串（查找替换用，跳过该条）；
    False：分隔字符没命中时返回整串（文件信息取值用，避免整段消失）。
    """
    piece, pos = extract_span(text, mode, cfg)
    if not piece and not empty_on_miss and pos == -2:
        return text
    return piece


# --------------------------------------------------------------------------- #
# 规则实现
# --------------------------------------------------------------------------- #

def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == '':
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _num_text(cfg: dict, ctx: dict, start_key: str = 'start') -> str:
    start = _to_int(cfg.get(start_key), 1)
    step = _to_int(cfg.get('step'), 1)
    value = start + ctx.get('index', 0) * step
    return numerals.pad_number(
        numerals.format_sequence(value, cfg.get('numType') or 'arabic'),
        _to_int(cfg.get('digits'), 0) or _to_int(cfg.get('width'), 0))


def _file_field(item: FileItem, cfg: dict) -> str:
    key = cfg.get('field') or 'mtime'
    if key == 'imageSize':
        value = _cached(item, 'imageSize', lambda: imaging.image_size(item.path))
    elif key == 'photoTime':
        raw = _cached_meta(item).get('photoTime', '')
        fmt = cfg.get('format') or ''
        if raw and fmt:
            try:
                value = format_pattern(datetime.strptime(raw, '%Y-%m-%d %H:%M:%S'), fmt)
            except ValueError:
                value = raw
        else:
            value = raw
    elif key in CAMERA_FIELDS:
        value = _cached_meta(item).get(key, '')
    elif key == 'name':
        value = item.name
    elif key == 'basename':
        value = split_name(item.name, item.is_dir)[0]
    elif key == 'ext':
        value = split_name(item.name, item.is_dir)[1].lstrip('.')
    elif key == 'folder':
        value = os.path.basename(item.dirname)
    elif key in ('mtime', 'birthtime'):
        ts = item.mtime if key == 'mtime' else item.ctime
        value = format_datetime(ts, cfg.get('format') or 'YYYY-MM-DD_HH-mm-ss')
    elif key == 'size':
        value = '' if item.is_dir else human_size(item.size)
    else:
        value = ''

    sub = cfg.get('substring') or ''
    if sub and value:
        value = extract_substring(value, sub, {'n': cfg.get('subN'), 'marker': cfg.get('subMarker')},
                                  empty_on_miss=False)
    return value


def _cached(item: FileItem, key: str, loader) -> str:
    if key not in item.info_cache:
        item.info_cache[key] = loader() or ''
    return item.info_cache[key]


def _cached_meta(item: FileItem) -> dict:
    """EXIF 拍摄信息（相机 / 镜头 / 光圈…），一个文件只解析一次。"""
    meta = _cached(item, 'photoMeta', lambda: imaging.photo_meta(item.path) or {})
    return meta if isinstance(meta, dict) else {}


def _r_replace(name: str, c: dict, ctx: dict) -> str:
    mode = c.get('mode') or 'text'
    use_full = c.get('replaceExt', True)
    base, ext = split_name(name, ctx.get('is_dir', False))
    subject = (base + ext) if use_full else base

    if mode == 'text':
        find = c.get('find') or ''
        if not find:
            return name
        rep = c.get('replace') or ''
        if c.get('isRegex'):
            try:
                flags = 0 if c.get('caseSensitive') else re.IGNORECASE
                result = re.sub(find, rep, subject, flags=flags)
            except re.error:
                return name
        elif c.get('caseSensitive'):
            result = subject.replace(find, rep)
        else:
            try:
                result = re.sub(re.escape(find), lambda m: rep, subject, flags=re.IGNORECASE)
            except re.error:
                return name
    else:
        target, pos = extract_span(subject, mode, c)
        if not target or pos < 0:
            return name
        rep = c.get('replace') or ''
        result = subject[:pos] + rep + subject[pos + len(target):]

    if use_full:
        return result
    return result + ext


def _apply_pair_list(text: str, blob: str, is_regex: bool, case_sensitive: bool) -> str:
    """逐行套用「旧=新」，支持 => / 制表符 / = 三种分隔，按顺序执行。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    for raw in (blob or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        left = right = None
        for sep in ('=>', '\t', '='):
            if sep in line:
                left, _, right = line.partition(sep)
                break
        if left is None:
            left, right = line, ''
        left = left.strip()
        right = (right or '').strip()
        if not left:
            continue
        if is_regex:
            try:
                text = re.sub(left, right, text, flags=flags)
            except re.error:
                continue
        elif case_sensitive:
            text = text.replace(left, right)
        else:
            text = re.sub(re.escape(left), lambda _m, rep=right: rep, text, flags=flags)
    return text


def _r_replace_multi(name: str, c: dict, ctx: dict) -> str:
    base, ext = split_name(name, ctx.get('is_dir', False))
    use_full = c.get('replaceExt', True)
    subject = (base + ext) if use_full else base
    preset = (c.get('preset') or '').strip()
    if preset:
        entry = RULE_PRESETS.get(preset)
        if not entry:
            return name
        try:
            subject = entry[1](subject)
        except Exception:
            return name
    else:
        subject = _apply_pair_list(subject, c.get('pairs') or '',
                                   bool(c.get('isRegex')), bool(c.get('caseSensitive')))
    return subject if use_full else subject + ext


def _r_extract(name: str, c: dict, ctx: dict) -> str:
    base, ext = split_name(name, ctx.get('is_dir', False))
    piece = extract_substring(base, c.get('mode') or 'head', c, empty_on_miss=False)
    return piece + (ext if c.get('keepExt', True) else '')


def _r_affix(name: str, c: dict, ctx: dict) -> str:
    base, ext = split_name(name, ctx.get('is_dir', False))
    return '%s%s%s%s' % (c.get('prefix') or '', base, c.get('suffix') or '', ext)


def _r_case(name: str, c: dict, ctx: dict) -> str:
    base, ext = split_name(name, ctx.get('is_dir', False))
    only_base = c.get('onlyBase', True)
    target = base if only_base else (base + ext)
    mode = c.get('mode') or 'lower'
    if mode == 'upper':
        out = target.upper()
    elif mode == 'lower':
        out = target.lower()
    elif mode == 'title':
        out = target[:1].upper() + target[1:].lower()
    elif mode == 'words':
        out = re.sub(r'\b\w', lambda m: m.group(0).upper(), target.lower())
    else:
        return name
    return (out + ext) if only_base else out


def _r_clean(name: str, c: dict, ctx: dict) -> str:
    mode = c.get('mode') or 'underscore'
    base, ext = split_name(name, ctx.get('is_dir', False))
    if mode == 'underscore':
        base = re.sub(r'\s+', '_', base)
    elif mode == 'hyphen':
        base = re.sub(r'\s+', '-', base)
    elif mode == 'remove':
        base = re.sub(r'\s+', '', base)
    else:
        base = re.sub(r'\s+', ' ', base).strip()
    if not c.get('keepSpecial'):
        base = re.sub(r'[^\w\s.\-\u4e00-\u9fff]', '', base)
    if c.get('normalizeDots', True):
        base = re.sub(r'\.{2,}', '.', base).strip('.')
    return base + ext


def _r_delete(name: str, c: dict, ctx: dict) -> str:
    mode = c.get('mode') or 'range'
    base, ext = split_name(name, ctx.get('is_dir', False))
    if mode == 'range':
        start = max(0, _to_int(c.get('start'), 0))
        count = max(0, _to_int(c.get('count'), 0))
        base = base[:start] + base[start + count:]
    elif mode == 'head':
        base = base[max(0, _to_int(c.get('count'), 0)):]
    elif mode == 'tail':
        count = max(0, _to_int(c.get('count'), 0))
        base = base[:max(0, len(base) - count)]
    elif mode == 'chars':
        target = c.get('target') or ''
        if target:
            base = base.replace(target, '')
    return base + ext


def _r_sequence(name: str, c: dict, ctx: dict) -> str:
    num = _num_text(c, ctx)
    base, ext = split_name(name, ctx.get('is_dir', False))
    pos = c.get('pos') or 'prefix'
    # 分隔符留空就是直接相连（默认如此），要下划线自己填
    sep = c.get('sep') or ''
    if pos == 'prefix':
        return num + sep + base + ext
    if pos == 'suffix':
        return base + sep + num + ext
    tpl = c.get('template') or 'IMG_{n}'
    out = (tpl.replace('{n}', num)
              .replace('{name}', base)
              .replace('{ext_name}', ext.lstrip('.'))
              .replace('{ext}', ext))
    if ext and not split_name(out, False)[1]:
        out += ext
    return out


def _r_insert(name: str, c: dict, ctx: dict) -> str:
    kind = c.get('kind') or 'text'
    if kind == 'text':
        piece = c.get('text') or ''
    elif kind == 'number':
        piece = _num_text(c, ctx)
    else:
        piece = _file_field(ctx.get('item'), c)
    if not piece:
        return name
    piece = (c.get('prefix') or '') + piece + (c.get('suffix') or '')

    base, ext = split_name(name, ctx.get('is_dir', False))
    position = c.get('position') or ''
    if position == 'all':
        return piece + ext
    if not position:
        return name
    if position == 'start':
        return piece + base + ext
    if position == 'end':
        return base + piece + ext
    idx = max(1, _to_int(c.get('atIndex'), 1))
    idx = min(idx, len(base) + 1)
    return base[:idx - 1] + piece + base[idx - 1:] + ext


def _r_template(name: str, c: dict, ctx: dict) -> str:
    tpl = c.get('template') or '{name}_{n}'
    base, ext = split_name(name, ctx.get('is_dir', False))
    num = _num_text(c, ctx)
    moment = datetime.now()
    out = (tpl.replace('{name}', base)
              .replace('{ext_name}', ext.lstrip('.'))
              .replace('{ext}', ext)
              .replace('{n}', num)
              .replace('{YYYY}', moment.strftime('%Y'))
              .replace('{MM}', moment.strftime('%m'))
              .replace('{DD}', moment.strftime('%d')))
    if ext and not split_name(out, False)[1]:
        out += ext
    return out


def _r_timestamp(name: str, c: dict, ctx: dict) -> str:
    src = c.get('src') or 'now'
    if src == 'mtime':
        moment = datetime.fromtimestamp(ctx.get('mtime') or 0)
    elif src == 'ctime':
        moment = datetime.fromtimestamp(ctx.get('ctime') or 0)
    else:
        moment = datetime.now()
    text = format_pattern(moment, c.get('format') or 'YYYY-MM-DD_HH-mm-ss')
    base, ext = split_name(name, ctx.get('is_dir', False))
    pos = c.get('pos') or 'replace'
    sep = c.get('sep') or ''
    if pos == 'prefix':
        return text + sep + base + ext
    if pos == 'suffix':
        return base + sep + text + ext
    return text + ext


def _r_extension(name: str, c: dict, ctx: dict) -> str:
    if ctx.get('is_dir', False):
        return name
    base, ext = split_name(name, False)
    mode = c.get('mode') or 'lowercase'
    if mode == 'lowercase':
        return base + ext.lower()
    if mode == 'uppercase':
        return base + ext.upper()
    if mode == 'remove':
        return base
    if mode == 'replace':
        value = c.get('value') or ''
        if value and not value.startswith('.'):
            value = '.' + value
        return base + value
    return name


def _r_uniqueify(name: str, c: dict, ctx: dict) -> str:
    idx = ctx.get('dup_index')
    if not idx or idx <= 1:
        return name
    base, ext = split_name(name, ctx.get('is_dir', False))
    start = _to_int(c.get('start'), 2)
    number = numerals.pad_number(str(start + idx - 2), _to_int(c.get('digits'), 0))
    return '%s%s%s%s' % (base, c.get('sep') or '_', number, ext)


_APPLY = {
    'replace': _r_replace,
    'replace_multi': _r_replace_multi,
    'extract': _r_extract,
    'affix': _r_affix,
    'case': _r_case,
    'clean': _r_clean,
    'delete': _r_delete,
    'sequence': _r_sequence,
    'insert': _r_insert,
    'template': _r_template,
    'timestamp': _r_timestamp,
    'extension': _r_extension,
    'uniqueify': _r_uniqueify,
}


def apply_rule(rule_id: str, name: str, config: dict | None, ctx: dict) -> str:
    fn = _APPLY.get(rule_id)
    if fn is None:
        return name
    try:
        return fn(name, config or {}, ctx)
    except Exception:
        return name


# --------------------------------------------------------------------------- #
# 工作流
# --------------------------------------------------------------------------- #

def build_names(files: list[FileItem], workflow: list[dict]) -> None:
    """按工作流刷新每个条目的 new_name / status / dup。"""
    active = [a for a in workflow if a.get('enabled', True)]
    dup_rule = next((a for a in active if a.get('id') == 'uniqueify'), None)
    chain = [a for a in active if a.get('id') != 'uniqueify']
    total = len(files)

    def ctx_of(index: int, item: FileItem, dup_index: int | None) -> dict:
        return {'index': index, 'total': total, 'is_dir': item.is_dir,
                'mtime': item.mtime, 'ctime': item.ctime,
                'dup_index': dup_index, 'item': item}

    def run_chain(item: FileItem, index: int, dup_index: int | None, rules_chain: list) -> str:
        # 手动改名拥有最高优先级，直接覆盖主名
        if item.manual_name:
            _, ext = split_name(item.name, item.is_dir)
            name = sanitize(item.manual_name) + ext
            if item.is_dir:
                name = sanitize(item.manual_name)
        else:
            name = item.orig or item.name
        c = ctx_of(index, item, dup_index)
        for act in rules_chain:
            name = apply_rule(act.get('id'), name, act.get('config'), c)
        return sanitize(name)

    base = [run_chain(item, i, None, chain) for i, item in enumerate(files)]

    counter: dict[str, int] = {}
    for n in base:
        counter[n] = counter.get(n, 0) + 1
    order: dict[str, int] = {}

    for i, item in enumerate(files):
        n = base[i]
        dup_index = None
        if counter[n] > 1:
            order[n] = order.get(n, 0) + 1
            dup_index = order[n]
        item.new_name = run_chain(item, i, dup_index, active)
        item.dup = counter[n] > 1
        if item.status not in ('done', 'error'):
            item.status = 'changed' if item.new_name != item.name else 'pending'


def find_conflicts(files: list[FileItem]) -> list[FileItem]:
    """批内同名冲突：同目录下新名重复时，整组返回。"""
    groups: dict[str, list[FileItem]] = {}
    for item in files:
        key = os.path.join(item.dirname, item.new_name).lower()
        groups.setdefault(key, []).append(item)
    conflicts: list[FileItem] = []
    for group in groups.values():
        if len(group) > 1:
            conflicts.extend(group)
    return conflicts


def validate(item: FileItem) -> str:
    """校验新名字是否合法，返回错误信息（空串 = 通过）。"""
    name = item.new_name
    if not name or not name.strip():
        return '文件名不能为空'
    if INVALID_CHARS.search(name):
        return INVALID_HINT
    base, ext = split_name(name, item.is_dir)
    if base != base.strip():
        return '首尾不能有空格'
    if re.search(r'[. ]$', base) and base != ext:
        return '结尾不能是点或空格'
    if len(name) > 200:
        return '文件名过长'
    return ''


# --------------------------------------------------------------------------- #
# 扫描导入
# --------------------------------------------------------------------------- #

SKIP_NAMES = {'desktop.ini', 'thumbs.db'}


def make_item(path: str) -> FileItem | None:
    """把一个真实路径包装成条目；路径不存在 / 不可读时返回 None。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    full = os.path.abspath(path)
    name = os.path.basename(full.rstrip('\\/')) or full
    return FileItem(path=full, name=name, is_dir=os.path.isdir(path),
                    size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime)


def scan_root(paths: list[str]) -> list[FileItem]:
    """只导入用户给到的那一层：文件加文件，文件夹加一个目录节点，不展开子项。"""
    items: list[FileItem] = []
    seen: set[str] = set()
    for raw in paths or []:
        path = os.path.abspath(raw)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        item = make_item(path)
        if item is not None:
            items.append(item)
    return items


def list_children(folder: str) -> list[FileItem]:
    """列出目录的直接子项：文件夹在前、文件在后，各自按资源管理器顺序。"""
    try:
        entries = [e for e in os.scandir(folder) if e.name.lower() not in SKIP_NAMES]
    except OSError:
        return []
    compare = _load_natural_compare()

    def name_key(entry):
        return entry.name.lower()

    def order(group):
        if not compare:
            return sorted(group, key=name_key)
        return sorted(group, key=functools.cmp_to_key(lambda a, b: compare(a.name, b.name)))

    dirs = order([e for e in entries if e.is_dir(follow_symlinks=False)])
    files = order([e for e in entries if not e.is_dir(follow_symlinks=False)])
    out: list[FileItem] = []
    for entry in dirs + files:
        item = make_item(entry.path)
        if item is not None:
            out.append(item)
    return out


def scan(paths: list[str], recursive: bool = True) -> list[FileItem]:
    """把路径列表展开成待处理条目（包含所选文件夹本身）。"""
    items: list[FileItem] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        try:
            st = os.stat(path)
        except OSError:
            return
        name = os.path.basename(path.rstrip('\\/')) or path
        items.append(FileItem(path=os.path.abspath(path), name=name,
                              is_dir=os.path.isdir(path), size=st.st_size,
                              mtime=st.st_mtime, ctime=st.st_ctime))

    def walk(folder: str) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.lower() in SKIP_NAMES:
                continue
            add(entry.path)
            if entry.is_dir(follow_symlinks=False) and recursive:
                walk(entry.path)

    for raw in paths:
        raw = os.path.abspath(raw)
        if os.path.isdir(raw):
            add(raw)
            walk(raw)
        else:
            add(raw)
    return items


def common_base(items: list[FileItem]) -> str:
    if not items:
        return ''
    dirs = [os.path.dirname(i.path) for i in items]
    try:
        return os.path.commonpath(dirs)
    except ValueError:
        return ''


def rel_dir(item: FileItem, base: str) -> str:
    if not base:
        return item.dirname
    try:
        rel = os.path.relpath(item.dirname, base)
    except ValueError:
        return item.dirname
    return '' if rel == '.' else rel


# --------------------------------------------------------------------------- #
# 排序
# --------------------------------------------------------------------------- #

_natural_compare = None


def _load_natural_compare():
    """借用 Windows 资源管理器的排序规则：数字按数值、中文按拼音。"""
    global _natural_compare
    if _natural_compare is not None:
        return _natural_compare
    try:
        import ctypes
        from ctypes import wintypes
        fn = ctypes.windll.shlwapi.StrCmpLogicalW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        fn.restype = ctypes.c_int
        fn('a', 'b')
        _natural_compare = lambda a, b: fn(a, b)
    except Exception:
        _natural_compare = False
    return _natural_compare


def sort_items(items: list[FileItem], key: str, reverse: bool = False) -> list[FileItem]:
    if key in ('size', 'mtime', 'birthtime'):
        field_name = {'size': 'size', 'mtime': 'mtime', 'birthtime': 'ctime'}[key]
        return sorted(items, key=lambda i: getattr(i, field_name), reverse=reverse)

    def base_of(item: FileItem) -> str:
        return split_name(item.name, item.is_dir)[0]

    compare = _load_natural_compare()
    if not compare:
        return sorted(items, key=lambda i: (base_of(i).lower(), i.dirname.lower()),
                      reverse=reverse)

    def norm(text: str) -> str:
        return (text or '').replace('/', '\\')

    def cmp(a: FileItem, b: FileItem) -> int:
        result = compare(norm(a.dirname), norm(b.dirname))
        if result == 0:
            result = compare(base_of(a), base_of(b))
        return -result if reverse else result

    return sorted(items, key=functools.cmp_to_key(cmp))
