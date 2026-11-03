"""长回复排版成长图（纯 PIL 本机渲染，不依赖任何远端渲染服务）。

设计宽度 400 逻辑像素，内部按 SS 倍超采样排版，最后 LANCZOS 缩回输出。
400 是手机屏幕的可用宽度：图宽与屏宽对齐，字才按原大小落地，不会被整体缩小。
标点做挤压，开引号左移吃掉左侧空白；字体不随插件分发，首次转图前按需
下载到 fonts/，拿不到就整条不转图，绝不用系统字体顶上；GB2312 子集外的
字符交给系统 Noto Serif CJK，连回退字体都没有字形时跳过绘制。

超过单页高度上限时按块边界分页，每页各带页眉页脚。libwebp 单边上限
16383 像素，分页同时是这条硬限制的唯一出路。
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 400
SS = 3
MAX_PIXELS = 120_000_000   # 单页内部画布像素预算，超了就整体降采样
PAGE_MAX_H = 8000          # 单页输出高度上限，超过就分页

PAD_X = 22
PAD_TOP = 26
PAD_BOTTOM = 22

# 字号按手机端知乎/微博的密度定：逻辑宽 400 减去左右边距，正文一行约 17 字，
# 与参考截图一致。字号偏小会把字挤在一行里，缩到手机上就显得又小又虚。
BODY_SIZE = 21
H1_SIZE = 28
H2_SIZE = 24
H3_SIZE = 21
H_SIZE = H3_SIZE          # 兼容旧引用；标题层级一律按 h 块的 level 取字号
CODE_SIZE = 19
TABLE_SIZE = 19
KICKER_SIZE = 14
HEADER_SIZE = 19
FOOT_SIZE = 13

BODY_LH = 1.55
CODE_LH = 1.6
PARA_GAP = 18

# 加粗幅度：横向错位宽度（逻辑像素）。中文横画密集，纵向加厚会把上下笔画之间的
# 空隙填死、字黏成一团，所以只加水平方向，与浏览器合成粗体的做法一致。
BOLD_SHIFT = 0.35
H_BOLD_SHIFT = 0.7

INK = (27, 26, 23)             # #1B1A17 墨
INK2 = (58, 56, 51)            # #3A3833
MUTED = (110, 106, 99)         # #6E6A63 次要文字
WATERMARK_INK = (214, 210, 200)  # #D6D2C8 水印
LINE = (227, 224, 216)         # #E3E0D8 细分割线
PAPER_TOP = (250, 248, 243)    # #FAF8F3 纸面顶
PAPER_BOTTOM = (242, 239, 232) # #F2EFE8 纸面底
ACCENT = (219, 74, 36)         # #DB4A24 橙红强调
ACCENT2 = (232, 154, 130)      # #E89A82 浅橙红
CODE_BG = (237, 233, 224)      # #EDE9E0 代码块底
QUOTE_BG = (246, 242, 233)     # #F6F2E9 引用块底

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
# 思源黑体 / Noto Sans SC（SIL OFL 1.1）：字形最接近手机端主流排版，且是可变字体，
# Regular 与 Bold 都是字体自带的，标题不必靠描边假装粗体。
FONT_BODY = str(FONT_DIR / "NotoSansSC-VF.ttf")
FONT_BOLD = FONT_BODY
FONT_MONO = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_MONO_ASCII = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_FALLBACK = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"
FONT_FALLBACK_BOLD = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"

NO_LINE_START = set("，。、；：？！）」』】〉》…—·,.;:?!)]}\"'")
NO_LINE_END = set("（「『【〈《([{‘“\"")

PUNCT_TRAIL = set("，。、；：？！）」』】〉》”’…—·,.;:?!)]}")
PUNCT_SQUEEZE = 0.65
PUNCT_LEAD = set("（「『【〈《“‘([{")

_ADV_CACHE = {}
_FONT_CACHE = {}
_FONT_META = {}
_CMAP_CACHE = {}
_BOLD_INSTANCES = set()   # 真取到 Bold 字重的字体对象 id

try:
    from fontTools.ttLib import TTFont as _TTFont
except ImportError:  # pragma: no cover - 缺 fonttools 时退化为 mask 判字形
    _TTFont = None


# ---------- 字体 ----------

def find_sc_index(path: str) -> int:
    """在 ttc 里找简体中文那一份，避免落到日文字形上。"""
    for i in range(8):
        try:
            name = ImageFont.truetype(path, 20, index=i).getname()
        except Exception:
            break
        if name and "SC" in name[0]:
            return i
    return 0


def clear_font_cache() -> None:
    """字体文件被替换、删除后必须调用一次。

    这里缓存的都是字形与字宽的内存副本，``os.replace`` 换了磁盘上的文件，
    内存里的旧字形不会自己失效：不清理的话，刚上传的新字体在出图上完全不生效。
    """
    _FONT_CACHE.clear()
    _FONT_META.clear()
    _ADV_CACHE.clear()
    _CMAP_CACHE.clear()
    _GLYPH_CACHE.clear()
    _BOLD_INSTANCES.clear()


def load(path: str, size: int, tag: str = "") -> ImageFont.FreeTypeFont:
    """按 (路径, 字号, 标记) 取字体。

    tag="bold" 时若这份字体可变（如 Noto Sans SC），直接取 Bold 实例，粗体就是
    字体自带的那一套；单字重字体切不过去，交给绘制时的横向错位兜底。
    同路径同字号的两个实例字重互不干扰，已实测（Regular 974 墨迹 / Bold 1479）。
    """
    key = (path, size, tag)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    if path.lower().endswith(".ttc"):
        index = find_sc_index(path)
        font = ImageFont.truetype(path, size, index=index)
    else:
        index = 0
        font = ImageFont.truetype(path, size)
    wanted = {"bold": "Bold", "": "Regular"}.get(tag)
    if wanted:
        try:
            font.set_variation_by_name(wanted)
            if tag == "bold":
                _BOLD_INSTANCES.add(id(font))
        except Exception:
            # 单字重字体没有可变轴，按原样返回即可
            pass
    _FONT_CACHE[key] = font
    _FONT_META[id(font)] = (path, index)
    return font


def is_true_bold(font) -> bool:
    """这份字体是真取到了 Bold 字重，还是只能靠描/错位假装。"""
    return id(font) in _BOLD_INSTANCES


def advance(font, ch: str) -> float:
    """单字推进宽度。尾部标点挤压、开标点收半宽，避免整段发松。"""
    key = (id(font), ch)
    hit = _ADV_CACHE.get(key)
    if hit is not None:
        return hit
    w = float(font.getlength(ch))
    if ch in PUNCT_TRAIL:
        w *= PUNCT_SQUEEZE
    elif ch in PUNCT_LEAD:
        w *= 0.5
    _ADV_CACHE[key] = w
    return w


def is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"


def text_width(font, text: str) -> float:
    return sum(advance(font, ch) for ch in text)


@lru_cache(maxsize=16384)
def in_subset(ch: str) -> bool:
    """随插件分发的字体只收 GB2312 全字符集，之外的字符交给系统字体。"""
    try:
        ch.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


_GLYPH_CACHE = {}


def _font_cmap(path, index):
    key = (path, index)
    if key in _CMAP_CACHE:
        return _CMAP_CACHE[key]
    chars = None
    if _TTFont is not None:
        try:
            chars = set()
            tt = _TTFont(path, fontNumber=index, lazy=True)
            for table in tt["cmap"].tables:
                chars.update(table.cmap.keys())
            tt.close()
        except Exception:
            chars = None
    _CMAP_CACHE[key] = chars
    return chars


def has_glyph(font, ch: str) -> bool:
    """字体里没有这个字形时不要画成方块，直接跳过绘制。"""
    key = (id(font), ch)
    hit = _GLYPH_CACHE.get(key)
    if hit is not None:
        return hit
    meta = _FONT_META.get(id(font))
    if meta is not None:
        cmap = _font_cmap(*meta)
        if cmap is not None:
            hit = ord(ch) in cmap
            _GLYPH_CACHE[key] = hit
            return hit
    try:
        hit = font.getmask(ch).getbbox() is not None
    except Exception:
        hit = True
    _GLYPH_CACHE[key] = hit
    return hit


def apply_fallback(segs, pairs):
    """把落在子集外的字符切出来，改用回退字体。"""
    out = []
    for text, font in segs:
        fb = None
        for f0, f1 in pairs:
            if font is f0:
                fb = f1
                break
        if fb is None:
            out.append((text, font))
            continue
        buf = ""
        cur = font
        for ch in text:
            f = font if in_subset(ch) else fb
            if f is not cur and buf:
                out.append((buf, cur))
                buf = ""
            cur = f
            buf += ch
        if buf:
            out.append((buf, cur))
    return out


# ---------- 行内解析 ----------

def split_inline(text: str, base, bold, mono):
    """把一行文本切成 [(文本, 字体)]，支持 **粗体** 与 `代码`。"""
    segs = []
    buf = ""
    cur = base
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("**", i):
            if buf:
                segs.append((buf, cur))
                buf = ""
            cur = bold if cur is base else base
            i += 2
            continue
        if text[i] == "`":
            if buf:
                segs.append((buf, cur))
                buf = ""
            end = text.find("`", i + 1)
            if end == -1:
                buf += text[i]
                i += 1
                continue
            segs.append((text[i + 1:end], mono))
            i = end + 1
            continue
        if text[i] in "*_~":
            i += 1
            continue
        buf += text[i]
        i += 1
    if buf:
        segs.append((buf, cur))
    return segs


def tokenize(segs):
    """把字符流切成不可断的单元：连续的英文数字算一个词，其余逐字。"""
    tokens = []
    for text, font in segs:
        buf = ""
        for ch in text:
            if ch.isascii() and (ch.isalnum() or ch in ".%-_"):
                buf += ch
            else:
                if buf:
                    tokens.append((buf, font))
                    buf = ""
                tokens.append((ch, font))
        if buf:
            tokens.append((buf, font))
    return tokens


def wrap_segments(segs, max_width: float) -> list:
    """按字符流断行，返回 [[(字符, 字体), ...], ...]。"""
    tokens = tokenize(segs)
    lines = []
    cur = []
    cur_w = 0.0
    for text, font in tokens:
        if not cur and text.isspace():
            continue
        w = text_width(font, text)
        if cur and cur_w + w > max_width:
            if text[0] in NO_LINE_START:
                for ch in text:
                    cur.append((ch, font))
                    cur_w += advance(font, ch)
                lines.append(cur)
                cur, cur_w = [], 0.0
                continue
            carry = []
            while cur and cur[-1][0] in NO_LINE_END:
                carry.insert(0, cur.pop())
            lines.append(cur)
            cur = carry
            cur_w = sum(advance(f, c) for c, f in cur)
        if w > max_width:
            for ch in text:
                cw = advance(font, ch)
                if cur and cur_w + cw > max_width:
                    lines.append(cur)
                    cur, cur_w = [], 0.0
                cur.append((ch, font))
                cur_w += cw
            continue
        for ch in text:
            cur.append((ch, font))
        cur_w += w
    if cur:
        lines.append(cur)
    # 末行太短时从上一行挪字下来，避免孤字与寡行
    if len(lines) >= 2 and 0 < len(lines[-1]) <= 2 and len(lines[-2]) >= 4:
        while len(lines[-1]) < 3 and len(lines[-2]) > 3:
            lines[-1].insert(0, lines[-2].pop())
    return lines


def line_width(line) -> float:
    return sum(advance(f, c) for c, f in line)


def wrap_code(text: str, font, max_width: float) -> list:
    """代码行折行，优先在空格处断开，避免把一个标识符劈成两半。"""
    if not text:
        return [""]
    out = []
    cur = ""
    cur_w = 0.0
    for ch in text:
        w = advance(font, ch)
        if cur and cur_w + w > max_width:
            idx = cur.rfind(" ")
            if idx > 0:
                out.append(cur[:idx])
                cur = cur[idx + 1:]
                cur_w = sum(advance(font, c) for c in cur)
            else:
                out.append(cur)
                cur, cur_w = "", 0.0
        cur += ch
        cur_w += w
    if cur:
        out.append(cur)
    return out


def draw_line(draw, xy, line, fill, bold_font=None, bold_shift=0):
    x, y = xy
    for ch, font in line:
        # 开引号/开括号的字形落在字框右半边，而步进只占半字宽。
        # 必须一律左移一个步进，让墨迹落在笔位；否则后一个字会压上来盖掉它。
        # 这条对所有块类型都成立：代码块、表格、引用块都曾因跳过左移丢过开引号。
        dx = x - advance(font, ch) if ch in PUNCT_LEAD else x
        if has_glyph(font, ch):
            draw.text((dx, y), ch, font=font, fill=fill)
            # 加粗用「横向错位再画一遍」，不用四面描边：中文横画密集，纵向加厚会把
            # 上下笔画之间的空隙填死，字就黏成一团。浏览器合成粗体也是这么做的
            # （FreeType 的 EmboldenXY 只加 x 方向），advance 不变。
            if bold_shift and bold_font is not None and font is bold_font:
                draw.text((dx + bold_shift, y), ch, font=font, fill=fill)
        x += advance(font, ch)


# ---------- 块解析 ----------

LIST_RE = re.compile(r"^([-*+]|(\d+)[.)])\s+(.*)$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def parse_blocks(md: str) -> list:
    lines = md.replace("\r\n", "\n").split("\n")
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        if s.startswith("```"):
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i].rstrip())
                i += 1
            i += 1
            blocks.append({"kind": "code", "lines": body or [""]})
            continue

        m = HEAD_RE.match(s)
        if m:
            blocks.append({"kind": "h", "text": m.group(2).strip(),
                           "level": len(m.group(1))})
            i += 1
            continue

        if HR_RE.match(s):
            blocks.append({"kind": "hr"})
            i += 1
            continue

        if s.startswith(">"):
            body = []
            while i < n and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"kind": "quote", "text": " ".join(x for x in body if x)})
            continue

        if s.startswith("|"):
            raw = []
            while i < n and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                if not re.match(r"^\|[\s:|-]+\|?$", row):
                    raw.append(row)
                i += 1
            rows = [[c.strip() for c in r.strip("|").split("|")] for r in raw]
            if rows:
                blocks.append({"kind": "table", "rows": rows})
            continue

        m = LIST_RE.match(s)
        if m:
            ordered = m.group(2) is not None
            blocks.append({
                "kind": "li",
                "ordered": ordered,
                "marker": f"{m.group(2)}." if ordered else "•",
                "text": m.group(3).strip(),
            })
            i += 1
            continue

        para = [s]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if not nxt or HEAD_RE.match(nxt) or HR_RE.match(nxt) or LIST_RE.match(nxt) \
                    or nxt.startswith(">") or nxt.startswith("```"):
                break
            para.append(nxt)
            i += 1
        merged = para[0]
        for piece in para[1:]:
            if merged and piece and (is_cjk(merged[-1]) or is_cjk(piece[0])):
                merged += piece
            else:
                merged += " " + piece
        blocks.append({"kind": "p", "text": merged})
    return blocks


# ---------- 布局 ----------

def layout(blocks, fonts, content_w, scale):
    """为每个块算出断好的行、行高、左缩进、上下间距。"""
    def segs(text, base, bold_font):
        return apply_fallback(split_inline(text, base, bold_font, fonts["mono"]), fonts["pairs"])

    items = []
    for b in blocks:
        kind = b["kind"]
        if kind == "hr":
            items.append({**b, "lines": [], "line_h": scale(10), "draw_h": scale(10),
                          "gap": scale(16), "indent": 0, "font": None, "fill": None})
            continue
        if kind == "code":
            lh = int(CODE_SIZE * CODE_LH * fonts["k"])
            lines = []
            for ln in b["lines"]:
                f = fonts["mono_ascii"] if all(ord(c) < 128 for c in ln) else fonts["mono"]
                for piece in wrap_code(ln, f, content_w - scale(24)):
                    lines.append([(c, f) for c in piece])
            drawn = lines or [[]]
            items.append({**b, "lines": drawn, "line_h": lh,
                          "draw_h": len(drawn) * lh + scale(18), "gap": scale(16),
                          "indent": 0, "font": fonts["mono"], "fill": INK2})
            continue
        if kind == "quote":
            font = fonts["body"]
            lh = int(BODY_SIZE * BODY_LH * fonts["k"])
            lines = wrap_segments(segs(b["text"], font, fonts["bold"]), content_w - scale(14))
            items.append({**b, "lines": lines, "line_h": lh, "draw_h": len(lines) * lh + scale(14),
                          "gap": scale(16), "indent": scale(14), "font": font,
                          "fill": MUTED})
            continue
        if kind == "table":
            f = fonts["table"]
            fb = fonts["table_bold"]
            lh = int(TABLE_SIZE * 1.55 * fonts["k"])
            vpad = scale(7)
            cell_pad = scale(8)
            rows = b["rows"]
            ncol = max(len(r) for r in rows)
            rows = [r + [""] * (ncol - len(r)) for r in rows]
            nat = [0.0] * ncol
            for ri, r in enumerate(rows):
                fnt = fb if ri == 0 else f
                for ci, cell in enumerate(r):
                    nat[ci] = max(nat[ci], sum(advance(fnt, ch) for ch in cell))
            room = max(content_w - cell_pad * 2 * ncol, 1)
            if sum(nat) > 0:
                k2 = room / sum(nat)
                nat = [w * k2 for w in nat]
            laid = []
            for ri, r in enumerate(rows):
                fnt = fb if ri == 0 else f
                cells = []
                numeric = []
                max_lines = 1
                for ci, cell in enumerate(r):
                    ls = wrap_segments([(ch, fnt) for ch in cell], nat[ci]) if cell else [[]]
                    cells.append(ls)
                    numeric.append(bool(re.fullmatch(r"[\d\s.,%]+", cell)) and any(c.isdigit() for c in cell))
                    max_lines = max(max_lines, len(ls))
                laid.append({"cells": cells, "h": max_lines * lh + vpad * 2,
                             "header": ri == 0, "numeric": numeric})
            col_numeric = []
            for ci in range(ncol):
                body_rows = [r for r in laid if not r["header"]]
                col_numeric.append(bool(body_rows) and all(r["numeric"][ci] for r in body_rows))
            for r in laid:
                r["numeric"] = col_numeric
            items.append({**b, "lines": [], "line_h": 0, "gap": scale(18),
                          "draw_h": sum(r["h"] for r in laid),
                          "indent": 0, "font": f, "fill": INK, "row": laid,
                          "col_w": nat, "cell_pad": cell_pad, "vpad": vpad, "lh": lh})
            continue

        if kind == "li":
            font = fonts["body"]
            lh = int(BODY_SIZE * BODY_LH * fonts["k"])
            lines = wrap_segments(segs(b["text"], font, fonts["bold"]), content_w - scale(18))
            items.append({**b, "lines": lines, "line_h": lh, "draw_h": len(lines) * lh,
                          "gap": scale(6), "indent": scale(18), "font": font, "fill": INK})
            continue

        if kind == "h":
            level = b.get("level", 3)
            level = 1 if level <= 1 else (2 if level == 2 else 3)
            size = {1: H1_SIZE, 2: H2_SIZE, 3: H3_SIZE}[level]
            font = fonts[f"h{level}"]
            lh = int(size * {1: 1.35, 2: 1.42, 3: 1.5}[level] * fonts["k"])
            lines = wrap_segments(segs(b["text"], font, font), content_w - scale(14))
            items.append({**b, "lines": lines, "line_h": lh, "draw_h": len(lines) * lh,
                          "gap": scale({1: 16, 2: 13, 3: 11}[level]), "indent": 0,
                          "font": font, "fill": INK, "level": level, "h_size": size,
                          "gap_before": scale({1: 26, 2: 22, 3: 18}[level])})
            continue

        font = fonts["body"]
        lh = int(BODY_SIZE * BODY_LH * fonts["k"])
        lines = wrap_segments(segs(b["text"], font, fonts["bold"]), content_w)
        items.append({**b, "lines": lines, "line_h": lh, "draw_h": len(lines) * lh,
                      "gap": scale(PARA_GAP), "indent": 0, "font": font, "fill": INK})

    # 列表项之间只留行间气口，整块收口时要回到段间距，否则最后一项贴着下文
    for i, it in enumerate(items):
        if it["kind"] == "li" and (i + 1 >= len(items) or items[i + 1]["kind"] != "li"):
            it["gap"] = scale(PARA_GAP)
    return items


# ---------- 分页 ----------

def _item_h(it) -> int:
    return it.get("gap_before", 0) + it["draw_h"] + it["gap"]


def _split_item(it, budget: int) -> list:
    """把一个自身高度超预算的块按行（或表格行）切开。"""
    kind = it["kind"]
    if kind == "table":
        out = []
        acc = []
        h = 0
        for r in it["row"]:
            if acc and h + r["h"] > budget:
                out.append({**it, "row": acc, "draw_h": h, "gap": 0, "gap_before": 0})
                acc, h = [], 0
            acc.append(r)
            h += r["h"]
        if acc:
            out.append({**it, "row": acc, "draw_h": h})
        return out or [it]

    lh = it["line_h"]
    lines = it["lines"]
    if kind not in ("p", "li", "h", "quote", "code") or lh <= 0 or len(lines) < 2:
        return [it]

    overhead = it["draw_h"] - len(lines) * lh      # 块自带的内边距
    cap = int((budget - overhead) // lh)
    if cap < 1:
        return [it]
    chunks = [lines[i:i + cap] for i in range(0, len(lines), cap)]
    if len(chunks) < 2:
        return [it]

    out = []
    last = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        gap_before = it.get("gap_before", 0) if i == 0 else 0
        piece = {**it, "lines": chunk, "draw_h": len(chunk) * lh + overhead,
                 "gap_before": gap_before}
        if i < last:
            piece["gap"] = 0
        out.append(piece)
    return out


def paginate(items, base_h: int, page_max: int) -> list:
    """贪心装箱：顺次塞块，超了就换页。块过大先按行切开。"""
    budget = max(page_max - base_h, page_max // 2)
    flat = []
    for it in items:
        if _item_h(it) > budget:
            flat.extend(_split_item(it, budget))
        else:
            flat.append(it)

    pages = []
    cur = []
    cur_h = base_h
    for it in flat:
        h = _item_h(it)
        if cur and cur_h + h > page_max:
            pages.append(cur)
            cur, cur_h = [it], base_h + h
        else:
            cur.append(it)
            cur_h += h
    if cur:
        pages.append(cur)
    return pages or [[]]


# ---------- 绘制 ----------

def v_gradient(size, top, bottom):
    w, h = size
    strip = Image.new("RGB", (1, max(h, 1)))
    px = strip.load()
    for y in range(max(h, 1)):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
    return strip.resize(size, Image.BILINEAR)


def h_gradient(size, stops):
    w, h = size
    strip = Image.new("RGB", (max(w, 1), 1))
    px = strip.load()
    for x in range(max(w, 1)):
        t = x / max(w - 1, 1)
        color = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                color = tuple(int(c0[c] + (c1[c] - c0[c]) * k) for c in range(3))
                break
        px[x, 0] = color
    return strip.resize(size, Image.BILINEAR)


def _load_fonts(px):
    def try_load(path, size):
        try:
            return load(path, size)
        except Exception:
            return None

    def first_of(paths, size):
        for path in paths:
            font = try_load(path, size)
            if font is not None:
                return font
        return None

    body = load(FONT_BODY, px(BODY_SIZE))
    bold = load(FONT_BOLD, px(BODY_SIZE), "bold")
    h1 = load(FONT_BOLD, px(H1_SIZE), "bold")
    h2 = load(FONT_BOLD, px(H2_SIZE), "bold")
    h3 = load(FONT_BOLD, px(H3_SIZE), "bold")
    heading = h3
    table = load(FONT_BODY, px(TABLE_SIZE))
    table_bold = load(FONT_BOLD, px(TABLE_SIZE), "bold")
    kicker_font = load(FONT_BODY, px(KICKER_SIZE))
    header_font = load(FONT_BOLD, px(HEADER_SIZE), "bold")
    foot = load(FONT_BODY, px(FOOT_SIZE))

    # 等宽字体是锦上添花：容器里没有 Noto / DejaVu 时退正文，不能因此不出图
    mono = first_of((FONT_MONO, FONT_MONO_ASCII), px(CODE_SIZE)) or body
    mono_ascii = first_of((FONT_MONO_ASCII, FONT_MONO), px(CODE_SIZE)) or mono

    pairs = []
    for prim, fb_path, size in (
        (body, FONT_FALLBACK, BODY_SIZE),
        (bold, FONT_FALLBACK_BOLD, BODY_SIZE),
        (h1, FONT_FALLBACK_BOLD, H1_SIZE),
        (h2, FONT_FALLBACK_BOLD, H2_SIZE),
        (h3, FONT_FALLBACK_BOLD, H3_SIZE),
        (table, FONT_FALLBACK, TABLE_SIZE),
        (table_bold, FONT_FALLBACK_BOLD, TABLE_SIZE),
        (kicker_font, FONT_FALLBACK, KICKER_SIZE),
        (header_font, FONT_FALLBACK_BOLD, HEADER_SIZE),
        (foot, FONT_FALLBACK, FOOT_SIZE),
    ):
        fb = try_load(fb_path, px(size))
        if fb is not None:
            pairs.append((prim, fb))

    return {
        "body": body,
        "bold": bold,
        "mono": mono,
        "mono_ascii": mono_ascii,
        "h": heading,
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "table": table,
        "table_bold": table_bold,
        "kicker": kicker_font,
        "header": header_font,
        "foot": foot,
        "pairs": pairs,
        "k": int(px(1)),
        # 真 Bold 就不必再错位叠画；单字重字体（用户上传替换成别的 ttf）才兜底
        "bold_shift": 0 if is_true_bold(bold) else px(BOLD_SHIFT),
        "h_bold_shift": 0 if is_true_bold(h1) else px(H_BOLD_SHIFT),
    }


def _tracked_width(text: str, font, tracking: float) -> float:
    """逐字加字距后的总宽。"""
    if not text:
        return 0.0
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def _draw_watermark(draw, fonts, text, right_x, center_y, left_limit, px):
    """右上角的英文水印：小字、拉开字距、极浅色，只作标记不抢眼。

    与左侧唤醒名挤在一起时先压字距，还不够就换页脚字号，不再降级到更小的档。
    """
    font = fonts["kicker"]
    tracking = px(2.6)
    width = _tracked_width(text, font, tracking)
    if left_limit + width + px(24) > right_x:
        tracking = px(1.2)
        width = _tracked_width(text, font, tracking)
    if left_limit + width + px(16) > right_x:
        font = fonts["foot"]
        width = _tracked_width(text, font, tracking)
    ink = font.getbbox(text)
    top = center_y - (ink[1] + ink[3]) / 2
    x = right_x - width
    for ch in text:
        draw.text((x, top), ch, font=font, fill=WATERMARK_INK)
        x += font.getlength(ch) + tracking


def _draw_page(items, fonts, ctx) -> Image.Image:
    S = ctx["S"]
    px = ctx["px"]
    Wp = ctx["Wp"]
    pad_x = ctx["pad_x"]
    content_w = ctx["content_w"]
    brand = ctx["brand"]
    wordmark = ctx["wordmark"]
    footer_left = ctx["footer_left"]
    footer_right = ctx["footer_right"]
    page_no = ctx["page_no"]
    page_total = ctx["page_total"]

    body_h = sum(_item_h(it) for it in items)
    header_h = ctx["header_h"]
    y_body = ctx["pad_top"] + header_h
    total_h = y_body + body_h + ctx["pad_bottom"]
    foot_h = ctx["foot_h"]
    if footer_left or footer_right:
        total_h += foot_h

    img = v_gradient((Wp, total_h), PAPER_TOP, PAPER_BOTTOM)
    draw = ImageDraw.Draw(img)

    bar_h = px(6)
    img.paste(h_gradient((Wp, bar_h), [(0.0, ACCENT), (0.42, ACCENT2), (1.0, PAPER_BOTTOM)]), (0, 0))

    y = ctx["pad_top"]
    if brand or wordmark:
        text_x = pad_x
        center_y = y + fonts["header"].size * 0.5
        if brand:
            brand_font = fonts["header"]
            if brand_font.getlength(brand) > content_w * 0.6:
                brand_font = fonts["kicker"]
            # 方块按墨迹中心对齐：中文与西文的重心不同，固定偏移会飘
            ink = brand_font.getbbox(brand)
            sq = px(11)
            cy = y + (ink[1] + ink[3]) / 2
            center_y = cy
            draw.rectangle([pad_x, cy - sq / 2, pad_x + sq, cy + sq / 2], fill=ACCENT)
            text_x = pad_x + sq + px(14)
            draw.text((text_x, y), brand, font=brand_font, fill=INK)
        if wordmark:
            _draw_watermark(draw, fonts, wordmark, Wp - pad_x, center_y, text_x, px)
        y += int(HEADER_SIZE * 1.35 * S)
        draw.line([(pad_x, y), (Wp - pad_x, y)], fill=LINE, width=max(1, px(1)))
        y += px(2) + px(26)

    for it in items:
        kind = it["kind"]
        y += it.get("gap_before", 0)

        if kind == "hr":
            img.paste(h_gradient((content_w, px(2)), [(0.0, LINE), (0.85, PAPER_TOP)]), (pad_x, y + px(4)))
            y += it["draw_h"] + it["gap"]
            continue

        if kind == "code":
            block_h = it["draw_h"]
            draw.rectangle([pad_x, y, Wp - pad_x, y + block_h], fill=CODE_BG)
            draw.rectangle([pad_x, y, pad_x + px(3), y + block_h], fill=ACCENT2)
            ty = y + px(13)
            for line in it["lines"]:
                draw_line(draw, (pad_x + px(16), ty), line, INK2)
                ty += it["line_h"]
            y += block_h + it["gap"]
            continue

        if kind == "table":
            cp = it["cell_pad"]
            vp = it["vpad"]
            lh = it["lh"]
            ty = y
            for r in it["row"]:
                cx = pad_x + cp
                for ci, cell_lines in enumerate(r["cells"]):
                    ly = ty + vp
                    cw = it["col_w"][ci]
                    right = r["numeric"][ci]
                    for line in cell_lines:
                        lx = cx + cw - line_width(line) if right else cx
                        draw_line(draw, (lx, ly), line, INK if r["header"] else INK2)
                        ly += lh
                    cx += cw + cp * 2
                ty += r["h"]
                draw.line([(pad_x, ty), (Wp - pad_x, ty)],
                          fill=(208, 203, 193) if r["header"] else LINE,
                          width=max(1, px(1)))
            y += it["draw_h"] + it["gap"]
            continue

        if kind == "quote":
            block_h = it["draw_h"]
            draw.rectangle([pad_x, y, Wp - pad_x, y + block_h], fill=QUOTE_BG)
            draw.rectangle([pad_x, y, pad_x + px(3), y + block_h], fill=(198, 194, 185))
            ty = y + px(10)
            for line in it["lines"]:
                draw_line(draw, (pad_x + px(20), ty), line, it["fill"])
                ty += it["line_h"]
            y += block_h + it["gap"]
            continue

        if kind == "li":
            mk = it["marker"]
            if mk == "•":
                cy = y + it["line_h"] * 0.5
                draw.ellipse([pad_x + px(5), cy - px(3.6), pad_x + px(12.2), cy + px(3.6)], fill=ACCENT)
            else:
                marker_font = it["font"]
                w_mk = marker_font.getlength(mk)
                # 标记右对齐到正文左边再留一点缝，否则会被正文第一行压住看不见
                draw.text((pad_x + it["indent"] - px(6) - w_mk, y), mk, font=marker_font, fill=ACCENT)
            ty = y
            for line in it["lines"]:
                draw_line(draw, (pad_x + it["indent"], ty), line, it["fill"],
                          bold_font=fonts["bold"], bold_shift=fonts["bold_shift"])
                ty += it["line_h"]
            y += it["draw_h"] + it["gap"]
            continue

        if kind == "h":
            block_h = it["draw_h"]
            # 竖条只压字的墨迹带，不跟着行高走，太长会显笨。字号越大条越粗一点，
            # 否则 h1 旁边会像一根头发丝。
            h_size = it.get("h_size", H_SIZE)
            bar_h = px(h_size * 1.12)
            bar_w = px(3.4 if h_size < H2_SIZE else 4.2)
            bar_top = y + max(0, (block_h - bar_h) / 2)
            draw.rectangle([pad_x, bar_top, pad_x + bar_w, bar_top + bar_h], fill=ACCENT2)
            for j, line in enumerate(it["lines"]):
                draw_line(draw, (pad_x + px(16), y + j * it["line_h"]), line, it["fill"],
                          bold_font=it["font"], bold_shift=fonts["h_bold_shift"])
            y += block_h + it["gap"]
            continue

        for j, line in enumerate(it["lines"]):
            draw_line(draw, (pad_x + it["indent"], y + j * it["line_h"]), line, it["fill"],
                      bold_font=fonts["bold"], bold_shift=fonts["bold_shift"])
        y += it["draw_h"] + it["gap"]

    if footer_left or footer_right:
        y += px(14)
        draw.line([(pad_x, y), (Wp - pad_x, y)], fill=LINE, width=max(1, px(1)))
        y += px(12)
        if footer_left:
            draw.text((pad_x, y), footer_left, font=fonts["foot"], fill=MUTED)
        right = footer_right
        if page_total > 1:
            right = f"{right}   {page_no}/{page_total}" if right else f"{page_no}/{page_total}"
        if right:
            tw = fonts["foot"].getlength(right)
            draw.text((Wp - pad_x - tw, y), right, font=fonts["foot"], fill=MUTED)

    out_h = max(1, round(total_h / S)) * S
    # 输出必须保留超采样后的物理像素：W=400 是逻辑宽，手机 DPR3 下要有
    # 1200 物理像素才是一比一落地。缩回 400 的话浏览器会把它拉伸三倍，
    # 字反而更糊——那正是「看不清」的根源。
    return img.resize((Wp, out_h), Image.LANCZOS)


def render_markdown(text: str, brand: str = "", wordmark: str = "ANGEL HEART",
                    footer_left: str = "", footer_right: str = "",
                    scale: int = SS, page_max: int = PAGE_MAX_H) -> list:
    """把正文排成一页或多页竖版长图，返回 PIL 图像列表。"""
    S = scale

    def px(v):
        return int(round(v * S))

    fonts = _load_fonts(px)
    Wp = px(W)
    pad_x = px(PAD_X)
    content_w = Wp - pad_x * 2

    blocks = parse_blocks(text)
    if not blocks:
        raise ValueError("正文为空")
    items = layout(blocks, fonts, content_w, px)

    header_h = (int(HEADER_SIZE * 1.35 * S) + px(2) + px(26)) if (brand or wordmark) else 0
    foot_h = px(16 + 12 + FOOT_SIZE + 10) if (footer_left or footer_right) else 0
    base_h = px(PAD_TOP) + header_h + px(PAD_BOTTOM) + foot_h

    pages = paginate(items, base_h, px(page_max))

    tallest = max(sum(_item_h(it) for it in p) + base_h for p in pages)
    if Wp * tallest > MAX_PIXELS and S > 2:
        return render_markdown(text, brand=brand, wordmark=wordmark,
                               footer_left=footer_left, footer_right=footer_right,
                               scale=S - 1, page_max=page_max)

    ctx = {
        "S": S, "px": px, "Wp": Wp, "pad_x": pad_x, "content_w": content_w,
        "pad_top": px(PAD_TOP), "pad_bottom": px(PAD_BOTTOM),
        "header_h": header_h, "foot_h": foot_h,
        "brand": brand, "wordmark": wordmark,
        "footer_left": footer_left, "footer_right": footer_right,
        "page_total": len(pages),
    }
    out = []
    for i, page_items in enumerate(pages):
        ctx["page_no"] = i + 1
        out.append(_draw_page(page_items, fonts, ctx))
    return out


def render_long_text_image(text: str, *, brand: str = "", wordmark: str = "ANGEL HEART",
                           footer_left: str = "", footer_right: str = "",
                           out_dir: str | None = None, prefix: str = "long_image") -> list:
    """排版并落盘，返回图片路径列表（1 到多张）。"""
    pages = render_markdown(text, brand=brand, wordmark=wordmark,
                            footer_left=footer_left, footer_right=footer_right)
    target = Path(out_dir) if out_dir else Path(tempfile.gettempdir()) / "angelheart_long_image"
    target.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000):x}"
    paths = []
    for i, img in enumerate(pages):
        name = f"{prefix}_{stamp}_{i + 1}.webp" if len(pages) > 1 else f"{prefix}_{stamp}.webp"
        path = target / name
        img.save(path, "WEBP", quality=88, method=6)
        paths.append(str(path))
    return paths
