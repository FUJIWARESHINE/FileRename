# -*- coding: utf-8 -*-
"""规则引擎自测：python -m unittest test_rules -v"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import imaging  # noqa: E402
import numerals  # noqa: E402
import rules  # noqa: E402
from rules import FileItem, build_names, default_config, apply_rule, sanitize, scan, split_name  # noqa: E402


def ctx(index: int = 0, **kw) -> dict:
    base = {'index': index, 'total': 10, 'is_dir': False,
            'mtime': datetime(2024, 3, 5, 8, 9, 10).timestamp(),
            'ctime': datetime(2023, 12, 31, 23, 59, 58).timestamp(),
            'dup_index': None}
    base.update(kw)
    return base


def run(rule_id: str, name: str, config: dict | None = None, **kw) -> str:
    return apply_rule(rule_id, name, config or {}, ctx(**kw))


class TestReplace(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(run('replace', 'photo_001.jpg', {'find': 'photo', 'replace': 'image'}), 'image_001.jpg')

    def test_case_insensitive(self):
        self.assertEqual(run('replace', 'Photo.JPG', {'find': 'photo', 'replace': 'img'}), 'img.JPG')

    def test_case_sensitive(self):
        c = {'find': 'photo', 'replace': 'img', 'caseSensitive': True}
        self.assertEqual(run('replace', 'Photo_photo.jpg', c), 'Photo_img.jpg')
        self.assertEqual(run('replace', 'photo_photo.jpg', c), 'img_img.jpg')

    def test_regex_group(self):
        c = {'find': r'(\d+)', 'replace': r'[\1]', 'isRegex': True}
        self.assertEqual(run('replace', 'a12b.png', c), 'a[12]b.png')

    def test_bad_regex_keeps_name(self):
        self.assertEqual(run('replace', 'a.txt', {'find': '([', 'replace': 'x', 'isRegex': True}), 'a.txt')

    def test_empty_find(self):
        self.assertEqual(run('replace', 'a.txt', {'find': '', 'replace': 'x'}), 'a.txt')


class TestAffix(unittest.TestCase):
    def test_both(self):
        self.assertEqual(run('affix', 'file.txt', {'prefix': 'new_', 'suffix': '_final'}), 'new_file_final.txt')

    def test_no_ext(self):
        self.assertEqual(run('affix', 'README', {'prefix': 'a-', 'suffix': '-b'}), 'a-README-b')

    def test_dir_untouched_ext(self):
        self.assertEqual(run('affix', 'my.folder', {'prefix': 'x_'}, is_dir=True), 'x_my.folder')


class TestCaseRule(unittest.TestCase):
    def test_modes(self):
        # 默认只改主名，扩展名保持不动
        self.assertEqual(run('case', 'My File.TXT', {'mode': 'lower'}), 'my file.TXT')
        self.assertEqual(run('case', 'My File.txt', {'mode': 'upper'}), 'MY FILE.txt')
        self.assertEqual(run('case', 'my File.txt', {'mode': 'title'}), 'My file.txt')
        self.assertEqual(run('case', 'my File name.txt', {'mode': 'words'}), 'My File Name.txt')

    def test_full_name(self):
        c = {'mode': 'lower', 'onlyBase': False}
        self.assertEqual(run('case', 'My File.TXT', c), 'my file.txt')


class TestSequence(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(run('sequence', 'photo.jpg', {'start': 1, 'step': 1, 'digits': 3, 'pos': 'prefix'}, index=0),
                         '001_photo.jpg')
        self.assertEqual(run('sequence', 'photo.jpg', {'start': 1, 'step': 2, 'digits': 2, 'pos': 'prefix'}, index=3),
                         '07_photo.jpg')

    def test_suffix_and_replace(self):
        self.assertEqual(run('sequence', 'photo.jpg', {'digits': 0, 'pos': 'suffix'}, index=4), 'photo_5.jpg')
        self.assertEqual(run('sequence', 'photo.jpg',
                             {'digits': 3, 'pos': 'replace', 'template': 'IMG_{n}'}, index=6), 'IMG_007.jpg')
        self.assertEqual(run('sequence', 'photo.jpg',
                             {'digits': 3, 'pos': 'replace', 'template': 'IMG_{n}{ext}'}, index=6), 'IMG_007.jpg')
        self.assertEqual(run('sequence', 'photo.jpg',
                             {'digits': 3, 'pos': 'replace', 'template': '{n}.png'}, index=6), '007.png')


class TestTemplate(unittest.TestCase):
    def test_tokens(self):
        c = {'template': '{name}-{n}{ext}', 'start': 1, 'step': 1, 'digits': 2}
        self.assertEqual(run('template', 'photo.jpg', c, index=0), 'photo-01.jpg')

    def test_ext_name(self):
        c = {'template': '{n}.{ext_name}', 'digits': 1}
        self.assertEqual(run('template', 'photo.JPG', c, index=0), '1.JPG')


class TestTimestamp(unittest.TestCase):
    def test_now_format_len(self):
        out = run('timestamp', 'doc.pdf', {'src': 'now', 'format': 'YYYY-MM-DD_HH-mm-ss'})
        self.assertEqual(len(out), len('2024-01-01_00-00-00') + len('.pdf'))
        self.assertTrue(out.endswith('.pdf'))

    def test_mtime(self):
        out = run('timestamp', 'doc.pdf', {'src': 'mtime', 'format': 'YYYYMMDD-HHmm'})
        self.assertEqual(out, '20240305-0809.pdf')

    def test_ctime(self):
        out = run('timestamp', 'doc.pdf', {'src': 'ctime', 'format': 'YYYYMMDD'})
        self.assertEqual(out, '20231231.pdf')


class TestClean(unittest.TestCase):
    def test_underscore(self):
        self.assertEqual(run('clean', 'my file name.txt', {'mode': 'underscore'}), 'my_file_name.txt')

    def test_hyphen_collapse_remove(self):
        self.assertEqual(run('clean', 'a b.txt', {'mode': 'hyphen'}), 'a-b.txt')
        self.assertEqual(run('clean', 'a  b.txt', {'mode': 'collapse'}), 'a b.txt')
        self.assertEqual(run('clean', 'a b.txt', {'mode': 'remove'}), 'ab.txt')

    def test_special_chars(self):
        self.assertEqual(run('clean', 'my file#1.txt', {'mode': 'collapse'}), 'my file1.txt')
        self.assertEqual(run('clean', 'my file#1.txt', {'mode': 'collapse', 'keepSpecial': True}), 'my file#1.txt')

    def test_chinese_kept(self):
        self.assertEqual(run('clean', '项目 报告#1.docx', {'mode': 'underscore'}), '项目_报告1.docx')


class TestDelete(unittest.TestCase):
    def test_range(self):
        self.assertEqual(run('delete', 'abcdef.txt', {'mode': 'range', 'start': 1, 'count': 3}), 'aef.txt')

    def test_head_tail(self):
        self.assertEqual(run('delete', 'abcdef.txt', {'mode': 'head', 'count': 2}), 'cdef.txt')
        self.assertEqual(run('delete', 'abcdef.txt', {'mode': 'tail', 'count': 2}), 'abcd.txt')

    def test_chars(self):
        self.assertEqual(run('delete', 'a-b-c.txt', {'mode': 'chars', 'target': '-'}), 'abc.txt')

    def test_out_of_range(self):
        self.assertEqual(run('delete', 'ab.txt', {'mode': 'range', 'start': 5, 'count': 3}), 'ab.txt')


class TestExtension(unittest.TestCase):
    def test_lower_upper(self):
        self.assertEqual(run('extension', 'photo.JPEG', {'mode': 'lowercase'}), 'photo.jpeg')
        self.assertEqual(run('extension', 'photo.jpeg', {'mode': 'uppercase'}), 'photo.JPEG')

    def test_replace_and_remove(self):
        self.assertEqual(run('extension', 'photo.jpeg', {'mode': 'replace', 'value': 'jpg'}), 'photo.jpg')
        self.assertEqual(run('extension', 'photo.jpeg', {'mode': 'replace', 'value': '.png'}), 'photo.png')
        self.assertEqual(run('extension', 'photo.jpeg', {'mode': 'remove'}), 'photo')

    def test_dir_ignored(self):
        self.assertEqual(run('extension', 'my.folder', {'mode': 'uppercase'}, is_dir=True), 'my.folder')


class TestWorkflow(unittest.TestCase):
    def make(self, names):
        return [FileItem(path='C:/tmp/' + n, name=n) for n in names]

    def test_duplicate_gets_index(self):
        files = self.make(['a.txt', 'a.txt', 'b.txt'])
        wf = [{'id': 'uniqueify', 'enabled': True, 'config': {'sep': '_', 'start': 2}}]
        build_names(files, wf)
        self.assertEqual([f.new_name for f in files], ['a.txt', 'a_2.txt', 'b.txt'])
        self.assertTrue(files[0].dup and files[1].dup)
        self.assertFalse(files[2].dup)

    def test_duplicate_produced_by_workflow(self):
        files = self.make(['x 1.txt', 'x 2.txt'])
        wf = [{'id': 'clean', 'enabled': True,
               'config': dict(default_config('clean'), mode='remove')},
              {'id': 'uniqueify', 'enabled': True, 'config': {'sep': '_', 'start': 2}}]
        build_names(files, wf)
        self.assertEqual([f.new_name for f in files], ['x1.txt', 'x2.txt'])

    def test_chain_and_status(self):
        files = self.make(['Vacation Photo 01.JPG'])
        wf = [
            {'id': 'clean', 'enabled': True, 'config': dict(default_config('clean'), mode='underscore')},
            {'id': 'case', 'enabled': True, 'config': {'mode': 'lower'}},
            {'id': 'sequence', 'enabled': True,
             'config': {'start': 1, 'step': 1, 'digits': 3, 'pos': 'prefix'}},
        ]
        build_names(files, wf)
        # 大小写规则默认只改主名，扩展名交给「扩展名处理」规则
        self.assertEqual(files[0].new_name, '001_vacation_photo_01.JPG')
        self.assertEqual(files[0].status, 'changed')

    def test_disabled_rule_skipped(self):
        files = self.make(['a.txt'])
        wf = [{'id': 'affix', 'enabled': False, 'config': {'prefix': 'x_', 'suffix': ''}}]
        build_names(files, wf)
        self.assertEqual(files[0].new_name, 'a.txt')
        self.assertEqual(files[0].status, 'pending')

    def test_done_status_not_reset(self):
        files = self.make(['a.txt'])
        files[0].status = 'done'
        files[0].name = 'b.txt'
        build_names(files, [])
        self.assertEqual(files[0].status, 'done')

    def test_illegal_chars_sanitized(self):
        files = self.make(['a:b?c.txt'])
        files[0].orig = 'a:b?c.txt'
        build_names(files, [])
        self.assertEqual(files[0].new_name, 'a_b_c.txt')

    def test_bad_rule_does_not_break(self):
        files = self.make(['a.txt'])
        wf = [{'id': 'sequence', 'enabled': True, 'config': {'start': 'abc', 'digits': 'x', 'pos': 'prefix'}}]
        build_names(files, wf)
        self.assertTrue(files[0].new_name.endswith('a.txt'))


class TestHelpers(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_name('a.txt'), ('a', '.txt'))
        self.assertEqual(split_name('a.b.c'), ('a.b', '.c'))
        self.assertEqual(split_name('.gitignore'), ('.gitignore', ''))
        self.assertEqual(split_name('noext'), ('noext', ''))
        self.assertEqual(split_name('my.folder', True), ('my.folder', ''))

    def test_sanitize(self):
        self.assertEqual(sanitize('a<b>c.txt'), 'a_b_c.txt')
        self.assertEqual(sanitize('end.'), 'end')
        self.assertEqual(sanitize('   '), '_')

    def test_human_size(self):
        self.assertEqual(rules.human_size(0), '0 B')
        self.assertEqual(rules.human_size(1536), '1.5 KB')
        self.assertEqual(rules.human_size(100, True), '--')

    def test_type_label(self):
        self.assertEqual(rules.type_label(FileItem(path='C:/a.png', name='a.png')), '图片')
        self.assertEqual(rules.type_label(FileItem(path='C:/a', name='a', is_dir=True)), '文件夹')
        self.assertEqual(rules.type_label(FileItem(path='C:/a.xyz', name='a.xyz')), 'XYZ')


class TestScanAndConflict(unittest.TestCase):
    def test_scan_recursive(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, 'sub')
            os.makedirs(sub)
            for p in [os.path.join(tmp, 'a.txt'), os.path.join(sub, 'b.txt')]:
                with open(p, 'w', encoding='utf-8') as fh:
                    fh.write('x')
            items = scan([tmp], recursive=True)
            expected = sorted(['a.txt', 'b.txt', os.path.basename(sub), os.path.basename(tmp)])
            self.assertEqual(sorted(i.name for i in items), expected)
            shallow = scan([tmp], recursive=False)
            self.assertEqual(sorted(i.name for i in shallow),
                             sorted(['a.txt', os.path.basename(sub), os.path.basename(tmp)]))

    def test_conflicts(self):
        a = FileItem(path='C:/t/a.txt', name='a.txt')
        b = FileItem(path='C:/t/b.txt', name='b.txt')
        c = FileItem(path='C:/t2/c.txt', name='c.txt')
        a.new_name = 'same.txt'
        b.new_name = 'same.txt'
        c.new_name = 'same.txt'
        # 同目录下的冲突整组返回；c 在另一个目录，不参与冲突
        self.assertEqual(rules.find_conflicts([a, b, c]), [a, b])

    def test_rel_dir(self):
        item = FileItem(path=os.path.join('C:', 'root', 'docs', 'a.txt'), name='a.txt')
        self.assertEqual(rules.rel_dir(item, os.path.join('C:', 'root')), 'docs')
        self.assertEqual(rules.rel_dir(item, os.path.join('C:', 'root', 'docs')), '')


class TestNumerals(unittest.TestCase):
    def test_chinese(self):
        cases = {0: '零', 1: '一', 10: '一十', 11: '一十一', 20: '二十', 100: '一百',
                 101: '一百零一', 1001: '一千零一', 1010: '一千零一十', 1234: '一千二百三十四',
                 10000: '一万', 100000: '一十万', 1000001: '一百万零一', 100000000: '一亿'}
        for value, expect in cases.items():
            self.assertEqual(numerals.format_sequence(value, 'cn_lower'), expect, value)

    def test_upper_rmb_negative(self):
        self.assertEqual(numerals.format_sequence(100, 'cn_upper'), '壹佰')
        self.assertEqual(numerals.format_sequence(100, 'rmb_cn'), '壹佰元')
        self.assertEqual(numerals.format_sequence(100, 'rmb_trad'), '壹佰圓')
        self.assertEqual(numerals.format_sequence(-5, 'cn_lower'), '负五')

    def test_letters(self):
        cases = {1: 'A', 26: 'Z', 27: 'AA', 28: 'AB', 52: 'AZ', 53: 'BA', 702: 'ZZ', 703: 'AAA'}
        for value, expect in cases.items():
            self.assertEqual(numerals.format_sequence(value, 'en_upper'), expect, value)
        self.assertEqual(numerals.format_sequence(3, 'en_lower'), 'c')
        self.assertEqual(numerals.format_sequence(0, 'en_upper'), '')

    def test_pad(self):
        self.assertEqual(numerals.pad_number('7', 3), '007')
        self.assertEqual(numerals.pad_number('一', 3), '00一')
        self.assertEqual(numerals.pad_number('abc', 0), 'abc')

    def test_sequence_rule_numeral(self):
        c = {'start': 1, 'step': 1, 'digits': 0, 'pos': 'replace',
             'numType': 'cn_lower', 'template': '{n}'}
        self.assertEqual(run('sequence', 'a.jpg', c, index=0), '一.jpg')
        self.assertEqual(run('sequence', 'a.jpg', c, index=9), '一十.jpg')
        c2 = {'start': 1, 'digits': 3, 'numType': 'en_upper', 'pos': 'prefix'}
        self.assertEqual(run('sequence', 'a.jpg', c2, index=0), '00A_a.jpg')


class TestExtractRule(unittest.TestCase):
    name = 'IMG_20240101_1234.jpg'

    def test_modes(self):
        self.assertEqual(run('extract', self.name, {'mode': 'head', 'n': 3}), 'IMG.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'tail', 'n': 4}), '1234.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'slice', 'from': 5, 'n': 8}), '20240101.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'after', 'marker': '_'}), '20240101_1234.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'before', 'marker': '_'}), 'IMG.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'afterN', 'marker': '_', 'n': 4}), '2024.jpg')
        self.assertEqual(run('extract', self.name, {'mode': 'beforeN', 'marker': '_', 'n': 0}), '.jpg')

    def test_keep_ext_off(self):
        self.assertEqual(run('extract', 'a-b-c.txt',
                             {'mode': 'after', 'marker': '-', 'keepExt': False}), 'b-c')

    def test_miss_marker_keeps_name(self):
        # 分隔字符没命中时保持主名不变，避免整段名字凭空消失
        self.assertEqual(run('extract', 'abc.txt', {'mode': 'after', 'marker': '_'}), 'abc.txt')


class TestReplaceModes(unittest.TestCase):
    def test_position_exact(self):
        # 按位置精确替换，而不是替换第一处相同的文本
        self.assertEqual(run('replace', 'abab.txt',
                             {'mode': 'slice', 'from': 3, 'n': 2, 'replace': 'X'}), 'abX.txt')

    def test_head_delete(self):
        self.assertEqual(run('replace', 'abcdef.txt', {'mode': 'head', 'n': 3}), 'def.txt')

    def test_miss_marker_skip(self):
        self.assertEqual(run('replace', 'abc.txt',
                             {'mode': 'after', 'marker': '_', 'replace': 'X'}), 'abc.txt')

    def test_scope(self):
        base = {'mode': 'head', 'n': 8, 'replace': 'doc'}
        self.assertEqual(run('replace', 'file.txt', dict(base, replaceExt=False)), 'doc.txt')
        self.assertEqual(run('replace', 'file.txt', dict(base, replaceExt=True)), 'doc')


class TestInsertRule(unittest.TestCase):
    def test_text_positions(self):
        self.assertEqual(run('insert', 'photo.jpg', {'kind': 'text', 'text': '2024', 'position': 'start'}),
                         '2024photo.jpg')
        self.assertEqual(run('insert', 'photo.jpg', {'kind': 'text', 'text': 'x', 'position': 'end'}),
                         'photox.jpg')
        self.assertEqual(run('insert', 'photo.jpg',
                             {'kind': 'text', 'text': '-', 'position': 'at', 'atIndex': 3}), 'ph-oto.jpg')
        self.assertEqual(run('insert', 'photo.jpg', {'kind': 'text', 'text': 'SNAP', 'position': 'all'}),
                         'SNAP.jpg')
        self.assertEqual(run('insert', 'photo.jpg', {'kind': 'text', 'text': 'x', 'position': ''}),
                         'photo.jpg')

    def test_at_index_clamped(self):
        self.assertEqual(run('insert', 'ab.txt', {'kind': 'text', 'text': '-', 'position': 'at', 'atIndex': 99}),
                         'ab-.txt')
        self.assertEqual(run('insert', 'ab.txt', {'kind': 'text', 'text': '-', 'position': 'at', 'atIndex': 0}),
                         '-ab.txt')

    def test_number_with_prefix_suffix(self):
        c = {'kind': 'number', 'start': 1, 'step': 1, 'numType': 'cn_lower', 'width': 0,
             'position': 'start', 'prefix': '第', 'suffix': '张_'}
        self.assertEqual(run('insert', 'photo.jpg', c, index=0), '第一张_photo.jpg')

    def test_file_field(self):
        item = FileItem(path=os.path.join('C:', 't', 'photo.jpg'), name='photo.jpg', size=1536)
        c = {'kind': 'file', 'field': 'basename', 'position': 'start'}
        self.assertEqual(apply_rule('insert', 'photo.jpg', c, ctx(item=item)), 'photophoto.jpg')
        c2 = {'kind': 'file', 'field': 'ext', 'position': 'all'}
        self.assertEqual(apply_rule('insert', 'photo.jpg', c2, ctx(item=item)), 'jpg.jpg')

    def test_file_time_field(self):
        item = FileItem(path=os.path.join('C:', 't', 'a.jpg'), name='a.jpg',
                        mtime=datetime(2024, 3, 5, 8, 9, 10).timestamp())
        c = {'kind': 'file', 'field': 'mtime', 'format': 'YYYYMMDD', 'position': 'start'}
        self.assertEqual(apply_rule('insert', 'a.jpg', c, ctx(item=item)), '20240305a.jpg')


class TestImaging(unittest.TestCase):
    @staticmethod
    def write(tmp, name, data):
        path = os.path.join(tmp, name)
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def test_sizes(self):
        import struct
        with tempfile.TemporaryDirectory() as tmp:
            png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + \
                  struct.pack('>II', 640, 480) + b'\x08\x06\x00\x00\x00' + b'\x00' * 8
            gif = b'GIF89a' + struct.pack('<HH', 12, 34) + b'\x00' * 16
            bmp = b'BM' + b'\x00' * 16 + struct.pack('<ii', 7, 9) + b'\x00' * 8
            webp = b'RIFF' + struct.pack('<I', 14) + b'WEBP' + b'VP8X' + struct.pack('<I', 10) + \
                   b'\x00\x00\x00\x00' + (99).to_bytes(3, 'little') + (77).to_bytes(3, 'little')
            jpeg = (b'\xff\xd8' + b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
                    + b'\xff\xc0\x00\x11\x08' + struct.pack('>HH', 200, 300)
                    + b'\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01')
            self.assertEqual(imaging.image_size(self.write(tmp, 'a.png', png)), '640x480')
            self.assertEqual(imaging.image_size(self.write(tmp, 'a.gif', gif)), '12x34')
            self.assertEqual(imaging.image_size(self.write(tmp, 'a.bmp', bmp)), '7x9')
            self.assertEqual(imaging.image_size(self.write(tmp, 'a.webp', webp)), '100x78')
            self.assertEqual(imaging.image_size(self.write(tmp, 'a.jpg', jpeg)), '300x200')

    def test_broken_file_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'bad.jpg', b'\xff\xd8not-an-image')
            self.assertEqual(imaging.image_size(path), '')
            self.assertEqual(imaging.photo_taken_time(path), '')
            missing = os.path.join(tmp, 'nope.jpg')
            self.assertEqual(imaging.image_size(missing), '')
            self.assertEqual(imaging.photo_taken_time(missing), '')

    def test_non_exif_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'a.txt', b'hello')
            self.assertEqual(imaging.photo_taken_time(path), '')


class TestManualAndSort(unittest.TestCase):
    def make(self, names, sizes=None):
        sizes = sizes or [0] * len(names)
        return [FileItem(path=os.path.join('C:', 't', n), name=n, size=s) for n, s in zip(names, sizes)]

    def test_manual_name(self):
        files = self.make(['a.txt'])
        files[0].manual_name = '我的名字'
        build_names(files, [])
        self.assertEqual(files[0].new_name, '我的名字.txt')

    def test_manual_then_rule(self):
        files = self.make(['a.txt'])
        files[0].manual_name = 'My File'
        build_names(files, [{'id': 'clean', 'enabled': True,
                             'config': dict(default_config('clean'), mode='underscore')}])
        self.assertEqual(files[0].new_name, 'My_File.txt')

    def test_manual_keeps_folder_ext(self):
        files = [FileItem(path=os.path.join('C:', 't', 'my.folder'), name='my.folder', is_dir=True)]
        files[0].manual_name = 'new.folder'
        build_names(files, [])
        self.assertEqual(files[0].new_name, 'new.folder')

    def test_sort_by_size(self):
        items = self.make(['a.txt', 'b.txt', 'c.txt'], [3, 1, 2])
        self.assertEqual([i.name for i in rules.sort_items(items, 'size')], ['b.txt', 'c.txt', 'a.txt'])
        self.assertEqual([i.name for i in rules.sort_items(items, 'size', True)], ['a.txt', 'c.txt', 'b.txt'])

    def test_natural_sort(self):
        items = self.make(['file10.txt', 'file2.txt', 'file1.txt'])
        self.assertEqual([i.name for i in rules.sort_items(items, 'name')],
                         ['file1.txt', 'file2.txt', 'file10.txt'])

    def test_validate(self):
        item = FileItem(path=os.path.join('C:', 't', 'a.txt'), name='a.txt')
        item.new_name = 'a/b.txt'
        self.assertTrue(rules.validate(item))
        item.new_name = '  abc.txt'
        self.assertTrue(rules.validate(item))
        item.new_name = 'ok.txt'
        self.assertEqual(rules.validate(item), '')
        item.new_name = '   '
        self.assertTrue(rules.validate(item))


class TestWorkflowExtra(unittest.TestCase):
    def test_uniqueify_with_digits(self):
        files = [FileItem(path=os.path.join('C:', 't', n), name=n) for n in ('a.txt', 'a.txt')]
        wf = [{'id': 'uniqueify', 'enabled': True, 'config': {'sep': '_', 'start': 1, 'digits': 3}}]
        build_names(files, wf)
        self.assertEqual([f.new_name for f in files], ['a.txt', 'a_001.txt'])

    def test_status_tone_mapping(self):
        self.assertEqual(rules.STATUS_TONE['done'], 'done')
        self.assertEqual(rules.STATUS_TONE['error'], 'error')

    def test_field_visible(self):
        field_def = {'key': 'n', 'show_if': {'mode': ['head', 'tail']}}
        self.assertTrue(rules.field_visible(field_def, {'mode': 'head'}))
        self.assertFalse(rules.field_visible(field_def, {'mode': 'before'}))
        self.assertTrue(rules.field_visible({'key': 'x'}, {}))


class TestReplaceMulti(unittest.TestCase):
    def test_pairs_are_applied_in_order(self):
        c = {'pairs': 'a=1\nb=2', 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'a-b.txt', c), '1-2.txt')

    def test_arrow_and_tab_separators(self):
        c = {'pairs': 'IMG_=>DSC_\nXXX\tYYY', 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'IMG_XXX.txt', c), 'DSC_YYY.txt')

    def test_comment_and_blank_lines_ignored(self):
        c = {'pairs': '# 注释\n\n  \nfoo=bar', 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'foo.txt', c), 'bar.txt')

    def test_case_insensitive_by_default(self):
        c = {'pairs': 'abc=X', 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'ABC.txt', c), 'X.txt')

    def test_case_sensitive(self):
        c = {'pairs': 'abc=X', 'caseSensitive': True, 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'ABC.txt', c), 'ABC.txt')

    def test_regex_pairs(self):
        c = {'pairs': r'(\d+)=>[\1]', 'isRegex': True, 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'a12b.txt', c), 'a[12]b.txt')

    def test_replace_ext_toggle(self):
        self.assertEqual(run('replace_multi', 'note.TXT', {'pairs': 'txt=md'}),
                         'note.md')
        self.assertEqual(run('replace_multi', 'note.TXT',
                             {'pairs': 'txt=md', 'replaceExt': False}), 'note.TXT')

    def test_bad_regex_line_is_skipped(self):
        c = {'pairs': '([=oops\nnote=doc', 'isRegex': True, 'replaceExt': False}
        self.assertEqual(run('replace_multi', 'note.txt', c), 'doc.txt')

    def test_presets(self):
        self.assertEqual(run('replace_multi', 'a（b）c.txt', {'preset': 'brackets'}),
                         'ac.txt')
        self.assertEqual(run('replace_multi', 'a１２３.txt', {'preset': 'fullwidth'}),
                         'a123.txt')
        self.assertEqual(run('replace_multi', '文件，名。txt', {'preset': 'cnpunct'}),
                         '文件,名.txt')
        self.assertEqual(run('replace_multi', 'a   b.txt', {'preset': 'space'}),
                         'a b.txt')

    def test_unknown_preset_keeps_name(self):
        self.assertEqual(run('replace_multi', 'a.txt', {'preset': 'nope'}), 'a.txt')

    def test_rule_registered_with_options(self):
        self.assertIn('replace_multi', rules.RULE_MAP)
        self.assertGreater(len(rules.PRESET_OPTIONS), 2)


class TestNumeralsExtra(unittest.TestCase):
    def test_roman(self):
        self.assertEqual(numerals.format_sequence(4, 'roman_upper'), 'IV')
        self.assertEqual(numerals.format_sequence(1990, 'roman_upper'), 'MCMXC')
        self.assertEqual(numerals.format_sequence(9, 'roman_lower'), 'ix')
        self.assertEqual(numerals.format_sequence(4000, 'roman_upper'), '4000')

    def test_circled(self):
        self.assertEqual(numerals.format_sequence(1, 'circled'), '①')
        self.assertEqual(numerals.format_sequence(20, 'circled'), '⑳')
        self.assertEqual(numerals.format_sequence(21, 'circled'), '(21)')

    def test_fullwidth(self):
        self.assertEqual(numerals.format_sequence(12, 'fullwidth'), '１２')
        self.assertEqual(numerals.format_sequence(-3, 'fullwidth'), '－３')

    def test_sequence_rule_uses_new_systems(self):
        cfg = {'start': 4, 'step': 1, 'numType': 'roman_upper', 'digits': 0, 'pos': 'prefix'}
        self.assertEqual(run('sequence', 'photo.jpg', cfg, index=0), 'IV_photo.jpg')


def _pack_ifd(entries, ifd_offset):
    """把 [(tag, ftype, payload)] 打成 IFD 字节，返回 (ifd, 值区, 值区起始偏移)。"""
    import struct
    count = len(entries)
    value_start = ifd_offset + 2 + count * 12 + 4
    cursor = value_start
    out = struct.pack('<H', count)
    values = b''
    unit = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
    for tag, ftype, payload in entries:
        size = len(payload)
        total = size // unit.get(ftype, 1)
        if size <= 4:
            field = payload.ljust(4, b'\x00')
        else:
            field = struct.pack('<I', cursor)
            values += payload
            cursor += size
        out += struct.pack('<HHI', tag, ftype, total) + field
    out += struct.pack('<I', 0)
    return out, values, value_start


def build_exif_jpeg():
    """拼一张带 EXIF 的最小 JPEG：Canon EOS R5 / 1-250s / f2.8 / ISO400 / 50mm。"""
    import struct
    make = b'Canon\x00'
    model = b'EOS R5\x00'
    lens = b'RF50mm F1.2 L USM\x00'
    taken = b'2024:03:05 08:09:10\x00'
    exif_raw = sorted([
        (0x829A, 5, struct.pack('<II', 1, 250)),        # 曝光时间
        (0x829D, 5, struct.pack('<II', 28, 10)),        # 光圈
        (0x8827, 3, struct.pack('<H', 400)),            # ISO
        (0x9003, 2, taken),                             # 拍摄时间
        (0x920A, 5, struct.pack('<II', 50, 1)),         # 焦距
        (0xA434, 2, lens),                              # 镜头
    ], key=lambda e: e[0])

    # 第一遍只为算出 IFD0 的尺寸，从而知道 Exif 子 IFD 该落在哪
    probe = [(0x010F, 2, make), (0x0110, 2, model), (0x8769, 4, b'\x00' * 4)]
    _ifd0, probe_values, probe_start = _pack_ifd(probe, 8)
    exif_offset = probe_start + len(probe_values)

    ifd0_entries = [(0x010F, 2, make), (0x0110, 2, model),
                    (0x8769, 4, struct.pack('<I', exif_offset))]
    ifd0, ifd0_values, _ = _pack_ifd(ifd0_entries, 8)
    exif, exif_values, _ = _pack_ifd(exif_raw, exif_offset)

    tiff = (b'II' + struct.pack('<H', 42) + struct.pack('<I', 8)
            + ifd0 + ifd0_values + exif + exif_values)
    body = b'Exif\x00\x00' + tiff
    app1 = b'\xff\xe1' + struct.pack('>H', len(body) + 2) + body
    return b'\xff\xd8' + app1 + b'\xff\xd9'


class TestExifMeta(unittest.TestCase):
    def write(self, tmp, name, data):
        path = os.path.join(tmp, name)
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def test_photo_meta_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'shot.jpg', build_exif_jpeg())
            meta = imaging.photo_meta(path)
            self.assertEqual(meta['cameraMake'], 'Canon')
            self.assertEqual(meta['cameraModel'], 'EOS R5')
            self.assertEqual(meta['lens'], 'RF50mm F1.2 L USM')
            self.assertEqual(meta['shutter'], '1-250s')
            self.assertEqual(meta['aperture'], 'f2.8')
            self.assertEqual(meta['iso'], 'ISO400')
            self.assertEqual(meta['focal'], '50mm')
            self.assertEqual(meta['photoTime'], '2024-03-05 08:09:10')
            self.assertEqual(imaging.photo_taken_time(path), '2024-03-05 08:09:10')

    def test_photo_meta_without_exif(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'plain.jpg', b'\xff\xd8\xff\xd9')
            self.assertEqual(imaging.photo_meta(path), {})

    def test_insert_rule_uses_camera_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'shot.jpg', build_exif_jpeg())
            item = rules.make_item(path)
            cfg = dict(default_config('insert'), kind='file', field='cameraModel',
                       position='all')
            out = rules.apply_rule('insert', item.name, cfg,
                                   {'index': 0, 'is_dir': False, 'item': item,
                                    'mtime': item.mtime, 'ctime': item.ctime})
            self.assertEqual(out, 'EOS R5.jpg')

    def test_camera_field_name_hidden_for_other_kinds(self):
        fields = rules.RULE_MAP['insert']['fields']
        camera = next(f for f in fields if f['key'] == 'field')
        keys = [k for k, _label in camera['options']]
        self.assertIn('cameraModel', keys)
        self.assertIn('iso', keys)


class TestDirectoryModel(unittest.TestCase):
    def make_tree(self, tmp):
        sub = os.path.join(tmp, 'Photos')
        inner = os.path.join(sub, 'raw')
        os.makedirs(inner)
        for path in (os.path.join(tmp, 'a.txt'), os.path.join(sub, 'b.jpg'),
                     os.path.join(inner, 'c.png')):
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('x')
        return sub, inner

    def test_scan_root_only_keeps_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub, _inner = self.make_tree(tmp)
            roots = rules.scan_root([tmp])
            self.assertEqual([i.name for i in roots], [os.path.basename(tmp)])
            self.assertTrue(roots[0].is_dir)
            picked = rules.scan_root([os.path.join(tmp, 'a.txt'), sub])
            self.assertEqual(sorted(i.name for i in picked), ['Photos', 'a.txt'])

    def test_list_children_lists_direct_children_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub, inner = self.make_tree(tmp)
            names = [i.name for i in rules.list_children(sub)]
            self.assertEqual(names, ['raw', 'b.jpg'])          # 文件夹在前
            self.assertEqual([i.name for i in rules.list_children(inner)], ['c.png'])

    def test_list_children_missing_dir(self):
        self.assertEqual(rules.list_children(os.path.join('C:', 'definitely', 'nope')), [])

    def test_scan_root_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'a.txt')
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write('x')
            self.assertEqual(len(rules.scan_root([target, target])), 1)

    def test_make_item_missing(self):
        self.assertIsNone(rules.make_item(os.path.join('C:', 'no', 'such', 'file')))


class TestClipboardModule(unittest.TestCase):
    def test_module_imports_and_degrades_safely(self):
        import clipboard
        self.assertIsInstance(clipboard.clipboard_files(), list)
        self.assertIsInstance(clipboard.clipboard_text(), str)
        self.assertIsInstance(clipboard.copy_text('hello'), bool)


class TestBridgeNavigation(unittest.TestCase):
    def setUp(self):
        import bridge
        self.bridge = bridge.Bridge({})

    def test_import_keeps_folder_as_node_then_enter(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, 'Photos')
            os.makedirs(sub)
            with open(os.path.join(sub, 'b.jpg'), 'w', encoding='utf-8') as fh:
                fh.write('x')
            payload = self.bridge.add_paths([tmp])
            self.assertEqual([i.name for i in self.bridge.items], [os.path.basename(tmp)])
            self.assertFalse(payload['nav']['inside'])

            tag = self.bridge.items[0].tag
            payload = self.bridge.enter_dir(tag)
            self.assertTrue(payload['nav']['inside'])
            self.assertEqual([i.name for i in self.bridge.items], ['Photos'])
            self.assertEqual(len(payload['nav']['crumbs']), 1)

            self.bridge.enter_dir(self.bridge.items[0].tag)
            self.assertEqual([i.name for i in self.bridge.items], ['b.jpg'])

            payload = self.bridge.goto_crumb(-1)
            self.assertFalse(payload['nav']['inside'])
            self.assertEqual([i.name for i in self.bridge.items], [os.path.basename(tmp)])

    def test_rename_and_revert_inside_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'My File.TXT'), 'w', encoding='utf-8') as fh:
                fh.write('x')
            self.bridge.workflow = [{'id': 'case', 'enabled': True,
                                     'config': {'mode': 'lower', 'onlyBase': False}}]
            self.bridge.open_path(tmp)
            self.assertEqual([i.new_name for i in self.bridge.items], ['my file.txt'])
            self.bridge.rename_now()
            self.assertTrue(os.path.exists(os.path.join(tmp, 'my file.txt')))
            self.assertEqual(len(self.bridge.history), 1)

            payload = self.bridge.revert()
            self.assertTrue(os.path.exists(os.path.join(tmp, 'My File.TXT')))
            self.assertEqual(self.bridge.history, [])
            self.assertEqual(payload['toast']['type'], 'ok')

    def test_remove_is_view_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, 'Photos')
            os.makedirs(sub)
            with open(os.path.join(sub, 'b.jpg'), 'w', encoding='utf-8') as fh:
                fh.write('x')
            self.bridge.add_paths([tmp])
            self.bridge.enter_dir(self.bridge.items[0].tag)
            payload = self.bridge.enter_dir(self.bridge.items[0].tag)
            self.assertEqual(len(self.bridge.items), 1)
            self.bridge.remove_items([self.bridge.items[0].tag])
            self.assertEqual(self.bridge.items, [])
            self.bridge.goto_crumb(-1)
            self.assertEqual([i.name for i in self.bridge.items], [os.path.basename(tmp)])
            self.assertIn('nav', payload)

    def test_go_home_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.bridge.add_paths([tmp])
            self.assertTrue(self.bridge.items)
            self.bridge.go_home()
            self.bridge.clear_items()
            self.assertEqual(self.bridge.items, [])
            self.assertEqual(self.bridge.crumbs, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
