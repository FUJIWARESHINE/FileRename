# -*- coding: utf-8 -*-
"""图片尺寸与 EXIF 拍摄信息（只读文件头，纯标准库）。

尺寸：PNG / GIF / BMP / JPEG / WEBP（VP8X、VP8L、VP8 ）。
EXIF：解析 JPEG 与 TIFF 的 IFD0 + Exif 子 IFD，取相机品牌 / 型号 / 镜头 /
      光圈 / 快门 / ISO / 焦距 / 拍摄时间（DateTimeOriginal 优先，回退
      DateTimeDigitized 与 IFD0 DateTime）。
所有函数出错一律返回空串，绝不抛异常。
"""
from __future__ import annotations

import struct

HEAD_LIMIT = 512 * 1024          # 尺寸解析最多读 512KB
EXIF_LIMIT = 2 * 1024 * 1024     # EXIF 最多读 2MB
EXIF_EXTS = {'.jpg', '.jpeg', '.jpe', '.tif', '.tiff'}


def _read_head(path: str, limit: int) -> bytes:
    try:
        with open(path, 'rb') as fh:
            return fh.read(limit)
    except OSError:
        return b''


def _fmt(w: int, h: int) -> str:
    return '%dx%d' % (w, h) if w > 0 and h > 0 else ''


def _png(data: bytes) -> str:
    if len(data) >= 24 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
        return _fmt(struct.unpack('>I', data[16:20])[0], struct.unpack('>I', data[20:24])[0])
    return ''


def _gif(data: bytes) -> str:
    if len(data) >= 10 and data[:3] == b'GIF':
        w, h = struct.unpack('<HH', data[6:10])
        return _fmt(w, h)
    return ''


def _bmp(data: bytes) -> str:
    if len(data) >= 26 and data[:2] == b'BM':
        w, h = struct.unpack('<ii', data[18:26])
        return _fmt(w, abs(h))
    return ''


def _webp(data: bytes) -> str:
    if len(data) < 30 or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        return ''
    kind = data[12:16]
    if kind == b'VP8X':
        w = data[24] | (data[25] << 8) | (data[26] << 16)
        h = data[27] | (data[28] << 8) | (data[29] << 16)
        return _fmt(w + 1, h + 1)
    if kind == b'VP8L':
        bits = struct.unpack('<I', data[21:25])[0]
        return _fmt((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if kind == b'VP8 ':
        if data[23:26] != b'\x9d\x01\x2a':
            return ''
        w, h = struct.unpack('<HH', data[26:30])
        return _fmt(w & 0x3FFF, h & 0x3FFF)
    return ''


def _jpeg_segments(data: bytes):
    """遍历 JPEG 段，产出 (marker, 段数据起始, 段长度)。"""
    i = 2
    size = len(data)
    while i + 4 <= size:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
        yield marker, i + 4, seg_len
        i += 2 + seg_len


def _jpeg(data: bytes) -> str:
    if len(data) < 4 or data[:2] != b'\xff\xd8':
        return ''
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    for marker, start, seg_len in _jpeg_segments(data):
        if marker in sof and start + 5 <= len(data):
            h, w = struct.unpack('>HH', data[start + 1:start + 5])
            return _fmt(w, h)
        if marker == 0xDA:                      # 进入压缩数据，停止
            break
    return ''


def image_size(path: str) -> str:
    """返回 '宽x高'，拿不到返回空串。"""
    lower = path.lower()
    data = _read_head(path, HEAD_LIMIT)
    if not data:
        return ''
    try:
        size = _png(data) or _gif(data) or _bmp(data) or _webp(data)
        if size:
            return size
        if lower.endswith(('.jpg', '.jpeg', '.jpe')):
            return _jpeg(data)
    except (struct.error, IndexError, ValueError):
        return ''
    return ''


# --------------------------------------------------------------------------- #
# EXIF
# --------------------------------------------------------------------------- #

def _exif_payload(data: bytes) -> bytes:
    if not data[:2] == b'\xff\xd8':
        return b''
    try:
        for marker, start, seg_len in _jpeg_segments(data):
            if marker == 0xE1 and data[start:start + 6] == b'Exif\x00\x00':
                return data[start + 6:start + seg_len - 2]
            if marker == 0xDA:
                break
    except (struct.error, IndexError, ValueError):
        return b''
    return b''


def _normalize(raw: str) -> str:
    """'2024:03:05 08:09:10' → '2024-03-05 08:09:10'（全零日期视为没有）"""
    text = raw.strip().strip('\x00')
    if len(text) >= 19 and text[4] == ':' and text[7] == ':':
        date = text[:10].replace(':', '-')
        if date == '0000-00-00':
            return ''
        return date + text[10:19]
    return text


def _read_ifd(data: bytes, offset: int, endian: str) -> dict:
    tags: dict[int, tuple] = {}
    if offset <= 0 or offset + 2 > len(data):
        return tags
    count = struct.unpack(endian + 'H', data[offset:offset + 2])[0]
    if count > 512:
        return tags
    pos = offset + 2
    for _ in range(count):
        if pos + 12 > len(data):
            break
        tag, ftype = struct.unpack(endian + 'HH', data[pos:pos + 4])
        count_v = struct.unpack(endian + 'I', data[pos + 4:pos + 8])[0]
        value_off = data[pos + 8:pos + 12]
        length = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}.get(ftype, 1) * count_v
        if length <= 4:
            payload = value_off[:length]
        else:
            v_off = struct.unpack(endian + 'I', value_off)[0]
            payload = data[v_off:v_off + length] if v_off < len(data) else b''
        tags[tag] = (ftype, payload)
        pos += 12
    return tags


def _exif_ifds(data: bytes):
    """解析出 (字节序, IFD0 标签, Exif 子 IFD 标签)，失败返回 None。"""
    payload = _exif_payload(data)
    if len(payload) < 8 or payload[:2] not in (b'II', b'MM'):
        return None
    endian = '<' if payload[:2] == b'II' else '>'
    try:
        ifd0_off = struct.unpack(endian + 'I', payload[4:8])[0]
        ifd0 = _read_ifd(payload, ifd0_off, endian)
        exif: dict = {}
        entry = ifd0.get(0x8769)
        payload_bytes = entry[1] if entry else b''
        if isinstance(payload_bytes, bytes) and len(payload_bytes) >= 4:
            exif = _read_ifd(payload, struct.unpack(endian + 'I', payload_bytes[:4])[0], endian)
        return endian, ifd0, exif
    except (struct.error, IndexError, ValueError):
        return None


def _entry_bytes(entry) -> bytes:
    if not entry:
        return b''
    value = entry[1]
    return value if isinstance(value, bytes) else b''


def _ascii(entry) -> str:
    raw = _entry_bytes(entry)
    if not raw:
        return ''
    return raw.split(b'\x00')[0].decode('ascii', 'ignore').strip()


def _rational(entry, endian: str) -> float:
    raw = _entry_bytes(entry)
    if len(raw) < 8:
        return 0.0
    try:
        numerator, denominator = struct.unpack(endian + 'II', raw[:8])
    except struct.error:
        return 0.0
    return (numerator / denominator) if denominator else 0.0


def _short(entry, endian: str) -> int:
    raw = _entry_bytes(entry)
    if len(raw) < 2:
        return 0
    try:
        return struct.unpack(endian + 'H', raw[:2])[0]
    except struct.error:
        return 0


def _fmt_aperture(value: float) -> str:
    return '' if value <= 0 else 'f%g' % value


def _fmt_shutter(value: float) -> str:
    """快门：慢门写 '1.3s'，高速写 '1-250s'（'/' 不能出现在文件名里）。"""
    if value <= 0:
        return ''
    if value >= 1:
        return '%gs' % value
    return '1-%ds' % max(1, round(1 / value))


def _fmt_lens_spec(entry, endian: str) -> str:
    raw = _entry_bytes(entry)
    if len(raw) < 16:
        return ''
    try:
        values = struct.unpack(endian + 'IIII', raw[:16])
    except struct.error:
        return ''
    pairs = [(values[0], values[1]), (values[2], values[3])]
    numbers = [n / d for n, d in pairs if d]
    if not numbers:
        return ''
    if len(numbers) >= 2 and numbers[0] != numbers[1]:
        return '%.4g-%.4gmm' % (numbers[0], numbers[1])
    return '%.4gmm' % numbers[0]


def photo_meta(path: str) -> dict:
    """读取 EXIF 拍摄信息，返回相机/镜头/光圈/快门/ISO/焦距/拍摄时间。

    任何字段拿不到就是空串；整张图没有 EXIF 时返回空 dict。
    绝不抛异常。
    """
    result: dict = {}
    if not str(path).lower().endswith(tuple(EXIF_EXTS)):
        return result
    ifds = _exif_ifds(_read_head(path, EXIF_LIMIT))
    if not ifds:
        return result
    endian, ifd0, exif = ifds
    result['cameraMake'] = _ascii(ifd0.get(0x010F))
    result['cameraModel'] = _ascii(ifd0.get(0x0110))
    result['lens'] = _ascii(exif.get(0xA434)) or _fmt_lens_spec(exif.get(0xA432), endian)
    result['aperture'] = _fmt_aperture(_rational(exif.get(0x829D), endian))
    result['shutter'] = _fmt_shutter(_rational(exif.get(0x829A), endian))
    iso = _short(exif.get(0x8827), endian)
    result['iso'] = ('ISO%d' % iso) if iso else ''
    focal = _rational(exif.get(0x920A), endian)
    result['focal'] = ('%.4gmm' % focal) if focal > 0 else ''

    taken = ''
    for tags in (exif, ifd0):
        for tag in (0x9003, 0x9004, 0x0132):   # Original / Digitized / IFD0 DateTime
            value = _ascii(tags.get(tag))
            if value:
                taken = _normalize(value)
                if taken:
                    break
        if taken:
            break
    result['photoTime'] = taken
    return result


def photo_taken_time(path: str) -> str:
    """返回 'YYYY-MM-DD HH:MM:SS'，拿不到返回空串。"""
    return photo_meta(path).get('photoTime', '')
