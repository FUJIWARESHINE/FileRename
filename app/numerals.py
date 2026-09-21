# -*- coding: utf-8 -*-
"""序号类型转换：阿拉伯数字 / 中文 / 人民币 / 英文字母 / 罗马 / 带圈 / 全角。

算法对齐 rename-lite：
· 中文数字按「个十百千万亿兆」四级分段，连续零合并为一个「零」，段首零去掉
· 字母序号是 Excel 列号式双射进制：1→A … 26→Z，27→AA，28→AB，53→BA
· 人民币只在整数末尾加「元 / 圓」，没有角分与「整」
额外补充（超出范围时自动回退成阿拉伯数字，保证一定有位号可写）：
· 罗马数字 1→Ⅰ，4→Ⅳ，1990→MCMXC，上限 3999
· 带圈数字 1→①，20→⑳，超过 20 用 (21) 形式
· 全角数字 1→１
"""
from __future__ import annotations

# digits 按 0-9 顺序，units 是个十百千，sections 是段后缀
SYSTEMS: dict[str, dict] = {
    'arabic': {'label': '阿拉伯数字  1 2 3', 'kind': 'arabic'},
    'cn_lower': {'label': '中文小写  一二三', 'kind': 'cn',
                 'digits': '零一二三四五六七八九', 'units': ('', '十', '百', '千'),
                 'sections': ('', '万', '亿', '兆'), 'suffix': ''},
    'cn_upper': {'label': '中文大写  壹贰叁', 'kind': 'cn',
                 'digits': '零壹贰叁肆伍陆柒捌玖', 'units': ('', '拾', '佰', '仟'),
                 'sections': ('', '万', '亿', '兆'), 'suffix': ''},
    'cn_trad': {'label': '繁体小写  壹貳參', 'kind': 'cn',
                'digits': '零壹貳參肆伍陸柒捌玖', 'units': ('', '十', '百', '千'),
                'sections': ('', '萬', '億', '兆'), 'suffix': ''},
    'cn_trad_upper': {'label': '繁体大写  壹貳參', 'kind': 'cn',
                      'digits': '零壹貳參肆伍陸柒捌玖', 'units': ('', '拾', '佰', '仟'),
                      'sections': ('', '萬', '億', '兆'), 'suffix': ''},
    'rmb_cn': {'label': '人民币大写  壹佰元', 'kind': 'cn',
               'digits': '零壹贰叁肆伍陆柒捌玖', 'units': ('', '拾', '佰', '仟'),
               'sections': ('', '万', '亿', '兆'), 'suffix': '元'},
    'rmb_trad': {'label': '人民币繁体  壹佰圓', 'kind': 'cn',
                 'digits': '零壹貳參肆伍陸柒捌玖', 'units': ('', '拾', '佰', '仟'),
                 'sections': ('', '萬', '億', '兆'), 'suffix': '圓'},
    'en_lower': {'label': '字母小写  a b c', 'kind': 'letters', 'alphabet': 'abcdefghijklmnopqrstuvwxyz'},
    'en_upper': {'label': '字母大写  A B C', 'kind': 'letters', 'alphabet': 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'},
    'roman_upper': {'label': '罗马数字 Ⅰ Ⅱ Ⅲ', 'kind': 'roman', 'upper': True},
    'roman_lower': {'label': '罗马小写 ⅰ ⅱ ⅲ', 'kind': 'roman', 'upper': False},
    'circled': {'label': '带圈数字 ① ② ③', 'kind': 'circled'},
    'fullwidth': {'label': '全角数字 １ ２ ３', 'kind': 'fullwidth'},
}

ROMAN_TABLE = ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
               (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'))
ROMAN_LIMIT = 3999
CIRCLED_DIGITS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
FULLWIDTH_TABLE = str.maketrans('0123456789-', '０１２３４５６７８９－')

DEFAULT_SYSTEM = 'arabic'


def options() -> list[tuple[str, str]]:
    """给界面用的下拉选项：(key, 标签)。"""
    return [(key, spec['label']) for key, spec in SYSTEMS.items()]


def _section_to_chinese(section: int, digits: str, units: tuple) -> str:
    """把一个 4 位以内的小节转成中文（1234 → 一千二百三十四）。"""
    out = ''
    unit_pos = 0
    zero_flag = True
    value = section
    while value > 0:
        digit = value % 10
        if digit == 0:
            if not zero_flag:                     # 连续零只记一个
                zero_flag = True
                out = digits[0] + out
        else:
            zero_flag = False
            out = digits[digit] + units[unit_pos] + out
        unit_pos += 1
        value //= 10
    return out


def to_chinese_number(number: int, spec: dict) -> str:
    if number == 0:
        return spec['digits'][0]
    digits = spec['digits']
    units = spec['units']
    sections = spec['sections']
    text = ''
    section_pos = 0
    value = number
    need_zero = False
    while value > 0:
        if need_zero:
            text = digits[0] + text
        section = value % 10000
        piece = _section_to_chinese(section, digits, units)
        text = piece + (sections[section_pos] if section > 0 and section_pos < len(sections) else '') + text
        need_zero = 0 < section < 1000
        value //= 10000
        section_pos += 1
    # 合并多余零、去掉开头零
    while digits[0] * 2 in text:
        text = text.replace(digits[0] * 2, digits[0])
    if text.startswith(digits[0]):
        text = text[1:]
    return text


def to_letters_number(number: int, alphabet: str) -> str:
    """Excel 列号式双射进制。"""
    if number <= 0:
        return ''
    base = len(alphabet)
    out = ''
    value = number
    while value > 0:
        value, rem = divmod(value - 1, base)
        out = alphabet[rem] + out
    return out


def to_roman(number: int, upper: bool = True) -> str:
    """罗马数字，超出 1..3999 返回空串（由调用方回退）。"""
    if number <= 0 or number > ROMAN_LIMIT:
        return ''
    out: list[str] = []
    value = number
    for amount, symbol in ROMAN_TABLE:
        while value >= amount:
            out.append(symbol)
            value -= amount
    text = ''.join(out)
    return text if upper else text.lower()


def to_circled(number: int) -> str:
    if 1 <= number <= len(CIRCLED_DIGITS):
        return CIRCLED_DIGITS[number - 1]
    return '(%d)' % number


def to_fullwidth(number: int) -> str:
    return str(number).translate(FULLWIDTH_TABLE)


def format_sequence(value: int, system: str = DEFAULT_SYSTEM) -> str:
    spec = SYSTEMS.get(system) or SYSTEMS[DEFAULT_SYSTEM]
    kind = spec['kind']
    if kind == 'arabic':
        return str(value)
    if kind == 'letters':
        return to_letters_number(value, spec['alphabet'])
    if kind == 'roman':
        return to_roman(value, spec.get('upper', True)) or str(value)
    if kind == 'circled':
        return to_circled(value)
    if kind == 'fullwidth':
        return to_fullwidth(value)
    negative = value < 0
    text = to_chinese_number(abs(value), spec) + spec.get('suffix', '')
    return ('负' + text) if negative else text


def pad_number(text: str, width: int) -> str:
    """按位数补零（对齐 rename-lite：统一用 ASCII 0 左补）。"""
    if not text or width <= 0 or len(text) >= width:
        return text
    return '0' * (width - len(text)) + text
