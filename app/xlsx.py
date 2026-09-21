# -*- coding: utf-8 -*-
"""极简 xlsx 写出（纯标准库，不依赖 openpyxl）。

只覆盖本项目要用的能力：一个工作表、加粗表头、列宽、冻结首行、自动筛选。
xlsx 本质是一组 XML 打的 zip 包，这里用内联字符串（inlineStr）写单元格，
省掉 sharedStrings 那一层，Excel / WPS 都能直接打开。
"""
from __future__ import annotations

import re
import zipfile
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

# XML 1.0 不允许的字符（控制字符），文件名里偶发时要先剔掉，否则文件打不开
_ILLEGAL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')

_CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

_ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''

_WORKBOOK_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

# 两个样式：0 = 普通（默认），1 = 表头（加粗 + 灰底 + 居中）
_STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="微软雅黑"/></font>
<font><b/><sz val="11"/><color rgb="FF1F2937"/><name val="微软雅黑"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFEDF0F7"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center"/></xf>
</cellXfs>
</styleSheet>'''


def _col_letter(index: int) -> str:
    """0 → A，25 → Z，26 → AA。"""
    letters = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _text_cell(ref: str, value: str, style: int) -> str:
    text = escape(_ILLEGAL.sub('', str(value)), {'\n': '&#10;'})
    return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style, text)


def _number_cell(ref: str, value: float, style: int = 0) -> str:
    return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, value)


def write_sheet(path: str, header: Sequence[str], rows: Iterable[Sequence],
                sheet_name: str = 'Sheet1',
                widths: Sequence[int] | None = None,
                numeric_columns: Sequence[int] = ()) -> int:
    """把二维数据写成 xlsx，返回写出的数据行数（不含表头）。

    numeric_columns 里的列按下标（从 0 开始）写成数字单元格，其余按文本写。
    """
    rows = list(rows)
    numeric = set(numeric_columns)
    widths = list(widths or [])

    body = []
    # 表头
    head_cells = ''.join(_text_cell(_col_letter(i) + '1', str(title), 1)
                         for i, title in enumerate(header))
    body.append('<row r="1">%s</row>' % head_cells)

    for r, row in enumerate(rows, start=2):
        cells = []
        for i, value in enumerate(row):
            ref = '%s%d' % (_col_letter(i), r)
            if i in numeric:
                cells.append(_number_cell(ref, value, 0))
            else:
                cells.append(_text_cell(ref, '' if value is None else value, 0))
        body.append('<row r="%d">%s</row>' % (r, ''.join(cells)))

    last_col = _col_letter(max(len(header), 1) - 1)
    last_row = len(rows) + 1
    cols = ''
    if widths:
        cols = '<cols>%s</cols>' % ''.join(
            '<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w)
            for i, w in enumerate(widths))
    # 冻结首行 + 自动筛选，几千行也方便看
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '</sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="18"/>'
             '%s<sheetData>%s</sheetData>'
             '<autoFilter ref="A1:%s%d"/>'
             '</worksheet>') % (cols, ''.join(body), last_col, last_row)

    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'
                ) % escape(_ILLEGAL.sub('', sheet_name)[:31] or 'Sheet1')

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr('[Content_Types].xml', _CONTENT_TYPES)
        zf.writestr('_rels/.rels', _ROOT_RELS)
        zf.writestr('xl/workbook.xml', workbook)
        zf.writestr('xl/_rels/workbook.xml.rels', _WORKBOOK_RELS)
        zf.writestr('xl/styles.xml', _STYLES)
        zf.writestr('xl/worksheets/sheet1.xml', sheet)
    return len(rows)
