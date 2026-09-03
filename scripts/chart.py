#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart.py — 外部ライブラリなしでPNG画像を描く

  python chart.py --selftest   ネット無しで検証
  python chart.py --demo       架空データで /tmp に見本を1枚書き出す

なぜ自前で描くのか（正直な理由）:
  matplotlib や Pillow を入れれば早いのですが、GitHub Actions で毎朝
  pip install が失敗すると、その日の通知がまるごと落ちます。
  このリポジトリは「外部ライブラリなしの純Python」で通してきたので、
  PNG（zlib + struct）とビットフォントを自前で持つことにしました。

**この描画器は日本語を出せません。** 文字は5×7のビットマップフォントで、
ASCII（英数字と記号）だけです。日本語はFlexメッセージの本文側に載せます。
日本語をどうしても画像に入れたい場合は、フォントファイル（Noto Sans JP 等）と
Pillow が必要になります。その判断は使う側に委ねます。
"""

from __future__ import annotations

import struct
import zlib

# ==========================================================================
# 5×7 ビットマップフォント（ASCIIのみ）
# 1文字を35文字の文字列で持つ。'#' が点灯、'.' が消灯。上の行から順に7行。
# 小文字は大文字に読み替える。未登録の文字は空白として扱う。
# ==========================================================================
# 1文字を「7行 × 5点」で持つ。行は "/" で区切る。'#' が点灯。
# 小文字は大文字に読み替える。未登録の文字は空白として扱う。
_GLYPHS: dict[str, str] = {
    " ": "...../...../...../...../...../...../.....",
    "!": "..#../..#../..#../..#../..#../...../..#..",
    '"': ".#.#./.#.#./...../...../...../...../.....",
    "#": ".#.#./.#.#./#####/.#.#./#####/.#.#./.#.#.",
    "$": "..#../.####/#.#../.###./..#.#/####./..#..",
    "%": "##..#/##..#/...#./..#../.#.../#..##/#..##",
    "&": ".##../#..#./#.#../.#.../#.#.#/#..#./.##.#",
    "'": "..#../..#../...../...../...../...../.....",
    "(": "...#./..#../.#.../.#.../.#.../..#../...#.",
    ")": ".#.../..#../...#./...#./...#./..#../.#...",
    "*": "...../#.#.#/.###./#####/.###./#.#.#/.....",
    "+": "...../..#../..#../#####/..#../..#../.....",
    ",": "...../...../...../...../..##./..#../.#...",
    "-": "...../...../...../#####/...../...../.....",
    ".": "...../...../...../...../...../.##../.##..",
    "/": "....#/...#./..#../..#../..#../.#.../#....",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": "..#../.##../..#../..#../..#../..#../.###.",
    "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "#####/...#./..#../...#./....#/#...#/.###.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
    ":": "...../.##../.##../...../.##../.##../.....",
    ";": "...../.##../.##../...../.##../..#../.#...",
    "<": "...#./..#../.#.../#..../.#.../..#../...#.",
    "=": "...../...../#####/...../#####/...../.....",
    ">": ".#.../..#../...#./....#/...#./..#../.#...",
    "?": ".###./#...#/....#/...#./..#../...../..#..",
    "@": ".###./#...#/....#/.##.#/#.#.#/#.#.#/.##..",
    "A": "..#../.#.#./#...#/#...#/#####/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "###../#..#./#...#/#...#/#...#/#..#./###..",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.####",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/#...#/##..#/#.#.#/#..##/#...#/#...#",
    "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "[": "..###/..#../..#../..#../..#../..#../..###",
    "]": "###../..#../..#../..#../..#../..#../###..",
    "^": "..#../.#.#./#...#/...../...../...../.....",
    "_": "...../...../...../...../...../...../#####",
    "|": "..#../..#../..#../..#../..#../..#../..#..",
    "~": "...../...../.##.#/#..#./...../...../.....",
}

# 展開して 35文字の平坦な文字列にする。
# 行数や桁数がずれた字形はここで見つけ、セルフテストで名指しできるようにする。
FONT: dict[str, str] = {}
FONT_BAD: list[str] = []
for _ch, _g in _GLYPHS.items():
    _rows = _g.split("/")
    if len(_rows) != 7 or any(len(r) != 5 for r in _rows):
        FONT_BAD.append(f"{_ch}({len(_rows)}行)")
        _rows = [(r + ".....")[:5] for r in (_rows + ["....."] * 7)[:7]]
    FONT[_ch] = "".join(_rows)


CW, CH = 5, 7          # 1文字の点の数
GAP = 1                # 文字の間隔（点）


def text_width(s: str, scale: int = 1) -> int:
    if not s:
        return 0
    return (len(s) * (CW + GAP) - GAP) * scale


def text_height(scale: int = 1) -> int:
    return CH * scale


# ==========================================================================
# 画布
# ==========================================================================
def rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class Canvas:
    """RGB の点の集まり。座標は左上が (0,0)。"""

    def __init__(self, w: int, h: int, bg: tuple[int, int, int] = (0, 0, 0)):
        self.w, self.h = int(w), int(h)
        self.buf = bytearray(bytes(bg) * (self.w * self.h))

    # ---- 点と面 ----
    def px(self, x: int, y: int, c: tuple[int, int, int]) -> None:
        x, y = int(x), int(y)
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(c)

    def fill_rect(self, x, y, w, h, c) -> None:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, int(x + w)), min(self.h, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(c) * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 3
            self.buf[i:i + (x1 - x0) * 3] = row

    def rect(self, x, y, w, h, c, t: int = 1) -> None:
        self.fill_rect(x, y, w, t, c)
        self.fill_rect(x, y + h - t, w, t, c)
        self.fill_rect(x, y, t, h, c)
        self.fill_rect(x + w - t, y, t, h, c)

    def round_rect(self, x, y, w, h, c, r: int = 8) -> None:
        """角を落とした塗り。半径ぶんだけ行の幅を詰める簡易版。"""
        self.fill_rect(x, y + r, w, h - 2 * r, c)
        for i in range(r):
            # 円弧の代わりに、行ごとの食い込み量を円で求める
            dy = r - i
            dx = r - int((r * r - dy * dy) ** 0.5)
            self.fill_rect(x + dx, y + i, w - 2 * dx, 1, c)
            self.fill_rect(x + dx, y + h - 1 - i, w - 2 * dx, 1, c)

    # ---- 線 ----
    def line(self, x0, y0, x1, y1, c, t: int = 1) -> None:
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            if t <= 1:
                self.px(x0, y0, c)
            else:
                o = t // 2
                self.fill_rect(x0 - o, y0 - o, t, t, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def dashed_hline(self, x, y, w, c, on: int = 6, off: int = 6) -> None:
        i = 0
        while i < w:
            self.fill_rect(x + i, y, min(on, w - i), 1, c)
            i += on + off

    # ---- 文字 ----
    def text(self, x, y, s: str, c, scale: int = 2, align: str = "left") -> int:
        """左上を (x,y) として描く。align は left / right / center。戻り値は幅。"""
        s = (s or "").upper()
        w = text_width(s, scale)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w // 2
        for i, ch in enumerate(s):
            pat = FONT.get(ch)
            if pat is None:
                continue
            ox = x + i * (CW + GAP) * scale
            for row in range(CH):
                line = pat[row * CW:(row + 1) * CW]
                run = 0
                for col in range(CW + 1):
                    on = col < CW and line[col] == "#"
                    if on:
                        run += 1
                    elif run:
                        self.fill_rect(ox + (col - run) * scale, y + row * scale,
                                       run * scale, scale, c)
                        run = 0
        return w

    # ---- 縮小 ----
    def scaled(self, f: int) -> "Canvas":
        """1/f に縮める。f×f の平均を取る（プレビュー画像用）。"""
        f = max(1, int(f))
        nw, nh = self.w // f, self.h // f
        out = Canvas(nw, nh)
        for y in range(nh):
            for x in range(nw):
                r = g = b = 0
                for dy in range(f):
                    base = ((y * f + dy) * self.w + x * f) * 3
                    for dx in range(f):
                        i = base + dx * 3
                        r += self.buf[i]
                        g += self.buf[i + 1]
                        b += self.buf[i + 2]
                n = f * f
                out.px(x, y, (r // n, g // n, b // n))
        return out

    # ---- 書き出し ----
    def png_bytes(self) -> bytes:
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)                       # フィルタなし
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                + chunk(b"IEND", b""))

    def save(self, path: str) -> int:
        data = self.png_bytes()
        with open(path, "wb") as f:
            f.write(data)
        return len(data)


# ==========================================================================
# グラフの部品
# ==========================================================================
def nice_bounds(lo: float, hi: float) -> tuple[float, float]:
    """目盛りとして気持ちのよい上下端に丸める。"""
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    step = 10 ** (len(str(int(abs(span)))) - 1) if abs(span) >= 1 else 10 ** -2
    for s in (step, step / 2, step / 5, step / 10):
        if span / s <= 8:
            step = s
            break
    return (int(lo / step) * step - (step if lo < 0 else 0),
            int(hi / step) * step + step)


def fmt_num(v: float) -> str:
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


class Plot:
    """折れ線を描く枠。値→画素の写像だけを持つ。"""

    def __init__(self, cv: Canvas, x, y, w, h, ymin, ymax):
        self.cv, self.x, self.y, self.w, self.h = cv, x, y, w, h
        self.ymin, self.ymax = ymin, ymax
        if self.ymax - self.ymin < 1e-12:
            self.ymax = self.ymin + 1

    def py(self, v: float) -> float:
        t = (v - self.ymin) / (self.ymax - self.ymin)
        return self.y + self.h - t * self.h

    def px_at(self, i: int, n: int) -> float:
        return self.x if n <= 1 else self.x + self.w * i / (n - 1)

    def grid(self, color, n: int = 4, label_color=None, scale: int = 2) -> None:
        # 目盛りの数字は、上下の幅から桁数を決めて全段そろえる。
        # 段ごとに桁数が変わると（0.000 と 1.00 が並ぶなど）読みにくいため。
        span = self.ymax - self.ymin
        dec = 0 if span >= 100 else (1 if span >= 10 else (2 if span >= 1 else 3))
        for i in range(n + 1):
            v = self.ymin + span * i / n
            yy = self.py(v)
            self.cv.fill_rect(self.x, yy, self.w, 1, color)
            if label_color:
                lab = f"{v:,.{dec}f}"
                self.cv.text(self.x - 8, yy - text_height(scale) // 2,
                             lab, label_color, scale, "right")

    def zero_line(self, color) -> None:
        if self.ymin <= 0 <= self.ymax:
            self.cv.dashed_hline(self.x, self.py(0), self.w, color, 8, 6)

    def series(self, vals: list, color, t: int = 2) -> None:
        pts = [(self.px_at(i, len(vals)), self.py(v))
               for i, v in enumerate(vals) if v is not None]
        for a, b in zip(pts, pts[1:]):
            self.cv.line(a[0], a[1], b[0], b[1], color, t)

    def last_dot(self, vals: list, color, r: int = 4) -> None:
        idx = [i for i, v in enumerate(vals) if v is not None]
        if not idx:
            return
        i = idx[-1]
        self.cv.fill_rect(self.px_at(i, len(vals)) - r, self.py(vals[i]) - r,
                          2 * r, 2 * r, color)


# ==========================================================================
def selftest() -> int:
    print("chart.py セルフテスト")
    print("-" * 70)
    fails, n = [], [0]

    def ck(name, cond, got=""):
        n[0] += 1
        print(f"  {'OK' if cond else 'NG'}  {name}" + ("" if cond else f"  {got}"))
        if not cond:
            fails.append(name)

    ck("字形が7行×5点に揃っている", FONT_BAD == [], FONT_BAD)
    ck("展開後の長さが35点", all(len(v) == 35 for v in FONT.values()))
    ck("英数字がすべて登録されている",
       all(c in FONT for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
    ck("記号が登録されている", all(c in FONT for c in "+-.%:()/"))

    cv = Canvas(40, 20, (0, 0, 0))
    ck("初期化で黒く塗られる", set(cv.buf) == {0})
    cv.px(5, 5, (255, 0, 0))
    i = (5 * 40 + 5) * 3
    ck("点が置ける", cv.buf[i] == 255 and cv.buf[i + 1] == 0)
    cv.fill_rect(0, 0, 10, 10, (0, 255, 0))
    ck("面が塗れる", cv.buf[(0 * 40 + 0) * 3 + 1] == 255)
    cv.fill_rect(-5, -5, 100, 100, (0, 0, 255))
    ck("画布の外にはみ出しても落ちない", cv.buf[2] == 255)

    cv2 = Canvas(60, 20)
    w = cv2.text(2, 2, "AB1", (255, 255, 255), 2)
    ck("文字幅の計算が一致", w == text_width("AB1", 2), w)
    ck("文字が描かれている", any(v for v in cv2.buf))
    cv3 = Canvas(60, 20)
    cv3.text(2, 2, "ab1", (255, 255, 255), 2)
    ck("小文字は大文字として描く", bytes(cv2.buf) == bytes(cv3.buf))
    cv4 = Canvas(60, 20)
    cv4.text(58, 2, "AB1", (255, 255, 255), 2, "right")
    ck("右寄せでも画布内に収まる", any(v for v in cv4.buf))

    png = Canvas(8, 8, (18, 36, 45)).png_bytes()
    ck("PNGの先頭が正しい", png[:8] == b"\x89PNG\r\n\x1a\n", png[:8])
    ck("IHDRがある", png[12:16] == b"IHDR")
    ck("IENDで終わる", png[-8:-4] == b"IEND")
    # 中身を復号して、指定した色で塗られているか確かめる
    idat = png[png.index(b"IDAT") + 4:-12]
    raw = zlib.decompress(idat)
    ck("復号後の長さが 高さ×(1+幅×3)", len(raw) == 8 * (1 + 8 * 3), len(raw))
    ck("フィルタ種別が0", raw[0] == 0)
    ck("塗った色が出てくる", tuple(raw[1:4]) == (18, 36, 45), tuple(raw[1:4]))

    big = Canvas(9, 9, (100, 100, 100))
    sm = big.scaled(3)
    ck("縮小で寸法が1/3になる", (sm.w, sm.h) == (3, 3))
    ck("縮小しても色が保たれる", sm.buf[0] == 100)

    lo, hi = nice_bounds(-0.42, 0.61)
    ck("目盛りが元の範囲を含む", lo <= -0.42 and hi >= 0.61, (lo, hi))
    lo, hi = nice_bounds(5.0, 5.0)
    ck("上下が同じ値でも壊れない", hi > lo, (lo, hi))

    p = Plot(Canvas(100, 100), 0, 0, 100, 100, -1, 1)
    ck("上端が上", p.py(1) < p.py(-1))
    ck("中央が中央", abs(p.py(0) - 50) < 1e-6, p.py(0))
    ck("欠損値があっても線が引ける",
       p.series([0.1, None, 0.3], (255, 255, 255)) is None)

    ck("数の書式（千区切り）", fmt_num(12345) == "12,345", fmt_num(12345))
    ck("数の書式（小数）", fmt_num(0.5) == "0.500", fmt_num(0.5))
    ck("数の書式（None）", fmt_num(None) == "-")

    print("-" * 70)
    print(f"結果: {n[0] - len(fails)}/{n[0]} 合格")
    return 1 if fails else 0


def demo(path: str = "/tmp/chart_demo.png") -> int:
    """架空データ（実測値ではありません）で描画の見本を作る。"""
    import math
    import random
    random.seed(7)
    cv = Canvas(900, 300, rgb("#0E161C"))
    cv.round_rect(20, 20, 860, 260, rgb("#18242D"), 10)
    cv.text(40, 40, "DEMO / FICTIONAL DATA", rgb("#B0F1F0"), 3)
    vals = [math.sin(i / 12) + random.gauss(0, 0.15) for i in range(160)]
    lo, hi = nice_bounds(min(vals), max(vals))
    p = Plot(cv, 90, 90, 760, 170, lo, hi)
    p.grid(rgb("#22323D"), 4, rgb("#8CA3B2"), 2)
    p.zero_line(rgb("#3B5262"))
    p.series(vals, rgb("#84D2F5"), 2)
    p.last_dot(vals, rgb("#FFEB3B"))
    size = cv.save(path)
    print(f"見本を書き出しました: {path}（{size/1024:.1f}KB・架空データ）")
    return 0


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        raise SystemExit(demo())
    raise SystemExit(selftest())
