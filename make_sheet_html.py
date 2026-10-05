# 診断書（A4 1枚）を**パワポと同じ座標のまま** HTML にする。
#
# 作り: make_pptx.py の build() が使っている inch 座標をそのまま CSS へ移す。
#   用紙 7.5 × 10 in。1インチ ＝ --u（親の幅の 1/7.5）なので、
#   幅が変わっても中身の比率は崩れない。文字も pt/72 * --u で決める。
#   数値は make_report から取るので、パワポと**同じ原本**から出る（別々に書かない）。
#
#   python make_sheet_html.py --label heretic27b            # web/index.html を作り直す
#   python make_sheet_html.py --label heretic27b --sheet-only  # 診断書の断片だけ標準出力へ
from __future__ import annotations

import argparse
import base64
import html
import io
import os
import re
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_report as M            # noqa: E402
import sheet_layout as P           # noqa: E402  版面の定義（パワポと共有）

WEB = os.path.join(HERE, "web")
# 32職の絵が出来たら True に戻す。
WATERMARK = True     # 2026-09-10 段位×職の合成絵（色付き）が揃ったので戻した（rank_art.py が引く）
E = html.escape

# ---- パワポと同じ色 ----
CSS_VARS = """
  --ink:#151A1F; --muted:#5F6B76; --accent:#00959A;
  --line:#D5DBE0; --bad:#B3261E; --frame:#C8CFD6; --paper:#FFFFFF;
"""


def px(v: float) -> str:
    """inch を --u 単位の CSS 長さにする。"""
    return f"calc(var(--u) * {v:.4f})"


def fs(pt: float) -> str:
    """pt を --u 単位の font-size にする（1pt = 1/72 in）。"""
    return f"calc(var(--u) * {pt / 72:.5f})"


# サイトの版と揃える
SHEET_VER = "Ver 01.06"   # 2026-10-05 詳細表示（複数の診断書を比較表に）。測り方は 01.05 と同じ
# 旧: "Ver 01.05"   # 2026-09-30 審判方式・4大枠・文章プロンプト（旧 01.04＝2026-09-15 分野方式）


def measured_at(label: str) -> str:
    """この診断書がいつ測られたか。results/ の結果ファイルの中でいちばん新しい時刻。

    診断書HTMLを作り直しただけでは動かない（作り直しは測り直しではない）。
    測り直した軸があればその時刻に更新される。"""
    import glob
    ts = []
    for f in glob.glob(os.path.join(HERE, "results", f"*_{label}.json")):
        if os.path.basename(f).startswith("meta_"):
            continue
        try:
            ts.append(os.path.getmtime(f))
        except OSError:
            pass
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(max(ts)))


def board_uri() -> str:
    """点数と称号の後ろに敷く木の看板。1枚で完結させるため data URI で埋める。

    素材が無い時は何も出さない（配布版で画像を同梱しない選択もできるように）。"""
    import base64, functools
    return _board_cached()


import functools


@functools.lru_cache(maxsize=1)
def _board_cached() -> str:
    import base64
    f = os.path.join(HERE, "assets", "board.png")
    if not os.path.exists(f):
        return ""
    return "data:image/png;base64," + base64.b64encode(io.open(f, "rb").read()).decode()


def stamp_svg(rank: str) -> str:
    """総合ランクのハンコ（SVG文字列）。scripts/rank_stamp.py が原本。"""
    import os, sys
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
    if d not in sys.path:
        sys.path.insert(0, d)
    import rank_stamp
    return rank_stamp.stamp(rank)


def box(x, y, w, h=None, extra="") -> str:
    s = f"left:{px(x)};top:{px(y)};width:{px(w)}"
    if h is not None:
        s += f";height:{px(h)}"
    return s + (";" + extra if extra else "")


ART_VEIL = 0.45      # 背景に掛ける白の割合（0=そのまま・1=真っ白）。多角形を主役にするため


def art_data_uri(v: dict, total_rank: str | None, size_px: int = 620,
                 d: dict | None = None) -> str | None:
    """段位×職の合成絵を data URI にする。無ければ従来の透かしへ。

    2026-09-17: d を受け取るようにした。渡さないと職が古い百分率の閾値で決まり、
    二つ名（分野方式の漢字で決まる）と食い違う。
    """
    import rank_art
    src = rank_art.art_path(v, total_rank, d)
    if not src:
        return None
    from PIL import Image
    im = Image.open(src).convert("RGB")
    w, h = im.size
    s0 = min(w, h)
    im = im.crop(((w - s0) // 2, (h - s0) // 2, (w - s0) // 2 + s0, (h - s0) // 2 + s0)).resize((size_px, size_px))
    im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), ART_VEIL)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def watermark_data_uri(name: str, size_px: int = 620) -> str | None:
    """レーダーの下に敷く透かし。パワポと同じデュオトーン処理をして data URI にする。"""
    cand = [os.path.join(P.CHAR_DIR, f"{name}.png"), os.path.join(P.CHAR_DIR, "_placeholder.png")]
    src = next((c for c in cand if os.path.exists(c)), None)
    if not src:
        return None
    try:
        from PIL import Image, ImageOps, ImageEnhance
    except ImportError:
        return None
    im = Image.open(src).convert("L")
    w, h = im.size
    s0 = min(w, h)
    im = im.crop(((w - s0) // 2, (h - s0) // 2, (w - s0) // 2 + s0, (h - s0) // 2 + s0)).resize((size_px, size_px))
    im = ImageEnhance.Contrast(im).enhance(1.1)
    im = ImageOps.colorize(im, black=(30, 60, 110), white=(225, 232, 240))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=78, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def radar_svg(v: dict, ls: list, x: float, y: float, size: float, wm: str | None) -> str:
    """make_pptx._draw_radar_lines と同じ角度・同じ半径で SVG を描く。"""
    import math
    n = len(M.AXES)
    cx = cy = size / 2
    r = size * 0.33
    S = 100.0                                   # SVG の内部単位 = 1/100 inch
    # 2026-09-10 絵は白ベールで薄いので、目盛り線と文字は濃い色・文字は白の縁取り（下の <style>）。多角形は黄で主役
    GRID, GRID2 = "#2F3A44", "#2F3A44"
    LBL, FILL = "#1F1B14", "#FFD400"
    if wm is None:
        GRID, GRID2, LBL, FILL = "#C8CFD6", "#E3E7EB", "#151A1F", "#00959A"

    def ang(i):
        # 2026-09-11: 上下に割らず、1（無検閲度）を左上に置いて時計回りに一周（表と同じ並び）
        return math.radians(M.ANG[i])

    def pt(i, rr):
        a = ang(i)
        return (cx + rr * math.cos(a)) * S, (cy + rr * math.sin(a)) * S

    order = list(range(10))
    out = [f'<svg class="radar" viewBox="0 0 {size*S:.0f} {size*S:.0f}" aria-hidden="true">']
    if wm is not None:
        out.append(f'<image class="art" href="{wm}" x="0" y="0" width="{size*S:.0f}" height="{size*S:.0f}"/>')
        out.append('<style>text{paint-order:stroke;stroke:#FFFFFF;stroke-width:3px;stroke-linejoin:round}</style>')
        out.append('<rect class="veil" x="0" y="0" width="100%" height="100%" fill="#FFFFFF" fill-opacity="0"/>')
    for f in (0.2, 0.4, 0.6, 0.8, 1.0):
        pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (pt(i, r * f) for i in order))
        out.append(f'<polygon class="grid" points="{pts}" fill="none" stroke="{GRID if f == 1.0 else GRID2}" '
                   f'stroke-width="{2.0 if f == 1.0 else 0.75}" stroke-opacity="{1 if f == 1.0 else .5}"/>')
    for i in range(n):
        ex, ey = pt(i, r)
        out.append(f'<line class="grid" x1="{cx*S:.1f}" y1="{cy*S:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                   f'stroke="{GRID2}" stroke-width="0.75" stroke-opacity=".45"/>')
    vals = [0 if v.get(k) is None else v[k] / 100 for k, *_ in M.AXES]
    pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (pt(i, r * vals[i]) for i in order))
    out.append(f'<polygon class="val" points="{pts}" fill="{FILL}" fill-opacity="{.65 if wm is not None else .35}" '
               f'stroke="{"#B38600" if wm is not None else FILL}" stroke-width="{2.5 if wm is not None else 2.25}"/>')
    gc = M.group_colors()
    for i in range(n):
        if v.get(M.AXES[i][0]) is None:
            continue
        a, b = pt(i, r * vals[i])
        # その軸のまとまりの色にする（縁は白のまま＝絵の上でも浮く）
        out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{0.045*S:.1f}" fill="{gc[i]}" '
                   f'stroke="#FFFFFF" stroke-width="1.8"/>')
    # 2026-09-11（Astra⑧）: 目盛りの数字は多角形より後に描く。先に描くと塗りに埋もれる
    for f in (0.2, 0.4, 0.6, 0.8, 1.0):
        out.append(f'<text class="tick" x="{cx*S:.1f}" y="{(cy - r*f)*S:.1f}" text-anchor="middle">'
                   f'{int(f*100)}</text>')
    # まとまりの帯。頂点で止め、端は丸く。絵の上では白を下に敷いて沈ませない
    gc = M.group_colors()
    BW = 0.075 * S                              # 帯の太さ
    TRIM = math.degrees((BW / 2) / ((r + 0.13) * S))
    for (p1, p2), col, _nm in M.GROUPS:
        a1 = math.radians(M.ANG[p1 - 1] + TRIM)
        a2 = math.radians(M.ANG[p2 - 1] - TRIM)
        rr = (r + 0.13) * S
        x1, y1 = cx * S + rr * math.cos(a1), cy * S + rr * math.sin(a1)
        x2, y2 = cx * S + rr * math.cos(a2), cy * S + rr * math.sin(a2)
        large = 1 if (math.degrees(a2 - a1) % 360) > 180 else 0
        d_ = f'M {x1:.1f} {y1:.1f} A {rr:.1f} {rr:.1f} 0 {large} 1 {x2:.1f} {y2:.1f}'
        if wm is not None:
            out.append(f'<path class="band" d="{d_}" fill="none" stroke="#FFFFFF" stroke-width="{BW*1.6:.1f}" '
                       f'stroke-linecap="round" stroke-opacity=".85"/>')
        out.append(f'<path class="band" d="{d_}" fill="none" stroke="{col}" stroke-width="{BW:.1f}" '
                   f'stroke-linecap="round" stroke-opacity=".75"/>')
    for i, (k, name, *_rest) in enumerate(M.AXES):
        lx, ly = pt(i, r + 0.34)
        val = v.get(k)
        vcol = LBL if wm is not None else "#00959A"
        if val is None:
            vcol = GRID if wm is not None else "#5F6B76"
        # 2026-09-11: 図の英字（O・T・A…）をやめ、表と同じ文字にする（Astra④）
        # 性格は漢字（開/誠/制…）、性能は0〜9の数字。表の「文字」列と1対1で対応する
        th_i = M.AXES[i][3]
        # 2026-09-16: 漢字は**渡された ls**（分野方式の判定が入っている）から引く。
        # ここだけ閾値で決めていたため、十文字が「制」でも図のラベルが「暴」になっていた
        if k in M.KANJI:
            if val is None:
                mk = "？"
            elif i < len(ls) and ls[i] != "?":
                mk = M.KANJI[k][0 if ls[i] == M.AXES[i][2][0] else 1]
            else:
                mk = M.KANJI[k][0 if val >= th_i else 1]
        else:
            mk = "？" if val is None else str(min(9, int(val // 10)))
        out.append(f'<text class="lb" x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" fill="{LBL}">'
                   f'{E(mk)}  {E(name)}</text>')
        out.append(f'<text class="lv" x="{lx:.1f}" y="{ly + 12.0:.1f}" text-anchor="middle" fill="{vcol}">'
                   f'{"未測定" if val is None else f"{val:.0f}%"}</text>')
    out.append("</svg>")
    return "".join(out)


def sheet(label: str) -> str:
    """診断書1枚を HTML の断片で返す（座標は make_pptx.build と同じ）。"""
    d = M.load(label)
    v = d["v"]
    ls = M.letters(v, d)
    sc = M.score(v)
    code = M.code(v, d)
    nm, desc = M.TYPENAME.get(M.type_key(v, d), ("（名前未作成）", ""))
    name, full, eng = M.names(label)
    sp = d.get("speed")
    RX, RW = 4.44, 2.85   #
    o = []
    a = o.append

    def T(x, y, w, h, text, pt_, *, bold=False, color="var(--ink)", mono=False,
          align="left", cls="", lh=1.2):
        style = (box(x, y, w, h) + f";font-size:{fs(pt_)};color:{color};text-align:{align};"
                 f"line-height:{lh}" + (";font-weight:700" if bold else "")
                 + (";font-family:var(--mono)" if mono else ""))
        a(f'<div class="t {cls}" style="{style}">{text}</div>')

    # ---- 枠とタブ（decor style=5）----
    for bname, x, y, w, h, tab in P.BLOCKS:
        a(f'<div class="frame" style="{box(x, y, w, h)}"></div>')
        if tab:
            a(f'<div class="tab" style="{box(x, y, min(1.1, w), 0.14)};font-size:{fs(6.5)}">'
              f'{E(bname)}</div>')

    # ---- 題名 ----
    # 名前を切ると effort 違いの3枚が見分けられなくなるので、字を小さくして1行に収める
    TITLE_W = 4.04
    _nw = sum(1.0 if unicodedata.east_asian_width(c) in "WFA" else 0.52 for c in full)   # 半角の実測は0.51em。少し多めに見る
    _ns = max(7.0, min(12.0, (TITLE_W - 0.06) * 72 / max(_nw, 1)))   # 0.06は安全代
    T(0.31, 0.14, TITLE_W, None,
      f'<div style="font-size:{fs(20)};font-weight:700;line-height:1.15">診断書</div>'
      f'<div style="font-size:{fs(_ns)};font-weight:700;line-height:1.25;'
      f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{E(full)}</div>', 20)
    _eng = re.sub(r"\s*/\s*思考ON[^/]*$", "", eng)
    T(RX, 0.25, RW, 0.29, f"{E(_eng)}　／　設定文なし・L2", 8, color="var(--muted)")
    # 2026-09-13: 異常値ガードで decode_tps が None になることがある
    _gen = (f"{sp['decode_tps']:.1f} t/s" if sp and sp.get('decode_tps') else "測定不能")
    spec = (f"生成 {_gen}　読込 {sp['prefill_tps']:.0f} t/s　"
            f"VRAM {sp['vram_mib']/1024:.1f} GB" if sp else "速度は未測定")
    # 2026-09-11（Astra⑤）: スペック欄（旧 上端0.39・高さ0.24）と十文字（旧 上端0.50）が重なっていた。
    # スペックを上へ詰め、高さも縮める
    T(RX, 0.39, RW, 0.15, "スペック: " + E(spec), 7, color="var(--muted)")

    # ---- 十文字 ----
    # 2026-09-11（Astra⑤）: 前半と後半の枠が横に0.28インチ重なっていた。1つの枠にまとめる
    head, _, tail = code.partition("-")
    # 十角形と表で使っている組の色にする
    _gc = M.group_colors()
    _cc = ("".join(f'<span style="color:{_gc[i]}">{E(c)}</span>' for i, c in enumerate(head))
           + '<span style="color:var(--muted)"> – </span>'
           + "".join(f'<span style="color:{_gc[5 + i]}">{E(c)}</span>'
                     for i, c in enumerate(tail)))
    T(RX, 0.55, RW, 0.31, _cc, 19, bold=True, mono=True)
    if sc["scaled"] is not None:
        _bd = board_uri()
        if _bd:
            a(f'<img class="board" src="{_bd}" style="{box(4.30, 0.89, 2.02, 0.83)}">')
        T(RX, 0.94, 1.60, 0.48, f"{sc['scaled']:.1f} 点", 24, bold=True, mono=True)
        if sc.get("rank"):
            a(f'<div class="stamp" style="{box(RX + 1.92, 0.80, 1.03, 1.03)}">'
              f'{stamp_svg(sc["rank"])}</div>')
        # ① 換算の一行は表の下（評価の右）へ移した
    # ハンコの手前までに収め、長い称号は字を詰めて1行を保つ
    _ep = M.epithet(d)
    T(RX, 1.41, 1.74, 0.30, E(_ep), min(14.0, 125.0 / max(len(_ep), 1)), bold=True, lh=1.15)
    _ds = E(desc)
    _i = _ds.find("。")
    if 0 <= _i < len(_ds) - 1:
        _ds = _ds[:_i + 1] + "\n" + _ds[_i + 1:]
    # ハンコの場所を「見えない場所取り」で確保し、1行目だけ手前で折り返させる
    _keep = f'<span style="float:right;width:{px(0.99)};height:{px(0.12)}"></span>'
    T(RX, 1.71, RW, 0.44, _keep + _ds, 7.5, color="var(--muted)", lh=1.35)

    # ---- レーダー ----
    # 段位×職の鍵。絵が手元に無い（配布版の利用者）時は href 空の枠だけ出し、サイトが art/<rank>/<job>.jpg を差し込む
    import rank_art
    art_key = rank_art.art_key(v, sc.get("rank"), d)
    a(f'<div class="radarbox" data-art="{art_key[0] + "/" + art_key[1] if art_key else ""}" style="{box(0.308, 0.68, 3.78, 3.78)}">'
      f'{radar_svg(v, ls, 0.308, 0.68, 3.78, (art_data_uri(v, sc.get("rank"), d=d) or "") if (WATERMARK and art_key) else None)}</div>')

    # ---- 性格（本文）----
    # 行数が多い型では字を自動で縮める
    PH = 5.23                                   # 本文の高さ（2026-09-11 ハンコ2倍で枠を 2.34/5.35 へ下げた）
    plines = list(M.personality_lines(d, width=27))
    heads = sum(1 for k, _ in plines if k == "h")
    avail = PH - 0.042 * max(0, heads - 1)      # 見出しの上余白ぶんを引く
    pfs = min(7.0, avail * 72 / (1.35 * max(1, len(plines))))   # 行高 = 字の大きさ × 1.35
    rows = []
    # 節ごとの色（隣り合う塊が同じ色にならない並び）。2番目の節だけは記述ごとに組の色を使う
    PURPLE, GOLD, BLUE, GREEN = "#7B5BD6", "#B8860B", "#1F6FEB", "#0B9A6D"
    SEC_COLS = [GOLD, None, BLUE, PURPLE]          # 性格 / 複数の軸（下で個別）/ 強み / 弱み
    GRP_COLS = [PURPLE, GOLD, BLUE, GREEN]         # group_notes の並びと同じ
    sec, gidx = -1, -1
    for kind, line in plines:
        if kind == "h":
            sec += 1; gidx = -1
        elif kind == "b" and sec == 1:
            gidx += 1
        strong = (GRP_COLS[min(gidx, 3)] if (sec == 1 and gidx >= 0) else
                  (GRP_COLS[0] if sec == 1 else SEC_COLS[min(max(sec, 0), 3)]))
        tint = M.GROUP_TINT.get(strong, "#FFFFFF")
        pre = {"h": "", "b": "・", "c": "　"}[kind]
        cls = "h" if kind == "h" else ("b" if kind == "b" else "c")
        # 本文は同じ幅の透明な線で字下げを揃える
        bar = strong if kind == "h" else "transparent"
        st = (f'background:{tint};border-left:{px(0.035)} solid {bar};'
              f'padding-left:{px(0.03)};padding-right:{px(0.02)}')
        if kind == "h":
            st += ";color:#1F2933"
        rows.append(f'<div class="pl {cls}" style="{st}">{pre}{E(line)}</div>')
    a(f'<div class="persona" style="{box(RX, 2.21, RW, PH)};font-size:{fs(pfs)}">'
      + "".join(rows) + "</div>")

    # ---- 10軸の表 ----
    TH = {k: t for k, _, _, t, _ in M.AXES}
    AXD = {k: (n, dsc) for k, n, _, _, dsc in M.AXES}
    keys = P.PERSONA + P.PERF
    GC = M.group_tints()                      # 2026-09-11: 行全体を十角形と同じ色の薄い版で塗る
    kpos = {k: i for i, (k, *_r) in enumerate(M.AXES)}
    cols = (0.28, 0.91, 2.06, 0.45, 0.36)   # 境目を左へ（0.97/2.00→0.91/2.06）
    tr = ['<colgroup>' + "".join(f'<col style="width:{px(w)}">' for w in cols) + "</colgroup>",
          "<thead><tr>" + "".join(
              f'<th style="height:{px(0.18)}">{h}</th>'
              for h in ("文字", "軸", "意味・称号", "実測", "評価")) + "</tr></thead><tbody>"]
    DOM = d.get("dom", {})
    for k in keys:
        x = v.get(k)
        n, dsc = AXD[k]
        persona = k in P.PERSONA
        dm = DOM.get(k)
        if dm:
            # 2026-09-15 分野方式: 説明欄は「分野 3/5」＋各分野の通過数
            vs = list(dm.get("分野", {}).values())
            hd = f'分野 {dm.get("達成数", 0)}/{dm.get("分野数", 0)}'
            if len(vs) > 6:                       # 無検閲度の12分類は内訳を出すと溢れる
                dsc = hd
            elif len({o_["問題数"] for o_ in vs}) == 1:   # 問題数がそろっている軸は通過数だけ
                dsc = hd + ": " + "・".join(str(o_["通過"]) for o_ in vs)
            else:
                dsc = hd + ": " + "・".join(f'{o_["通過"]}/{o_["問題数"]}' for o_ in vs)
            # 2026-09-16: 長いと軸の欄で折り返し、行が伸びて最下行が凡例と重なっていた。
            # 折り返す長さを超えたら内訳を落として「分野 n/N」だけにする
            if len(dsc) > 16:
                dsc = hd
        mark = ("？" if x is None else M.KANJI[k][0 if x >= TH[k] else 1]) if persona else \
               ("？" if x is None else str(min(9, int(x // 10))))   #
        if persona and dm:
            mark = dm.get("文字") or mark
        if persona:
            mean = f'<span>{E(P.MEANING[k][0])}</span><br><span>{E(P.MEANING[k][1])}</span>'
        else:
            hit = M.title_hit(k, d)   # Ver 01.04: 分野方式は達成数がそのまま階位
            L = None if dm else (d.get("codeL") if k == "code"
                                 else d.get("visL") if k == "vision" else None)
            if L:                     # 旧・梯子方式の結果。段ごとの通過数を説明欄に（例: 10/9/7/7/4）
                dsc = ("L" if k == "code" else "V") + "1〜5: " + "/".join(str(g_) for _, g_, _ in L)
            mean = "".join(
                ("" if i == 0 else "／") + (f'<b>{E(t)}</b>' if i == hit else E(t))
                for i, t in enumerate(M.TITLES[k]))
        # 実測の内訳ではなく固定の副題を出す
        if k in ("code", "vision", "ja"):
            dsc = AXD[k][1]
        tr.append(
            f'<tr style="height:{px(0.295)};background:{GC[kpos[k]]}">'
            f'<td class="mk{"" if persona else " num"}">{mark}</td>'
            f'<td class="ax">{E(n)}<em>{E(dsc)}</em></td>'
            f'<td class="mean{"" if persona else " ti"}">{mean}</td>'
            f'<td class="val">{E(M.fmt(x))}</td>'
            f'<td class="rk">{E(M.rank(x))}</td></tr>')
    tr.append("</tbody>")
    a(f'<table class="axes" style="{box(0.27, 4.48, 4.06)}">' + "".join(tr) + "</table>")
    T(0.31, 7.64, 2.15, 0.20, "評価: SS≥95／S≥85／A≥70／B≥50／C＜50", 7.5, color="var(--muted)")
    # 「（未測定は除外）」は落とす
    if sc["scaled"] is not None:
        T(2.48, 7.64, 1.86, 0.20,
          f"{sc['n']}軸×10点＝{sc['raw']:.1f}／{10*sc['n']} を100点換算", 7, color="var(--muted)")

    # ---- 下段: 向く作業／不向きな作業 ----
    fitd, unfitd = M.work_fit_detail(d)

    FIT_STRONG, FIT_TINT = "#0B9A6D", M.GROUP_TINT["#0B9A6D"]

    def speed_scale(x, y, w, h):
        """速さの目安。当てはまる段を強調する。

        速さはPCで変わるので、数字そのものではなく体感で示す。
        読込の待ち時間は 5,000トークン（A4で3〜4枚ぶん）を投げた場合。
        """
        GEN = [(0, 10, "〜10", "ゆっくり音読する速度"),
               (10, 50, "15〜30", "文章を黙読する速度"),
               (50, 100, "50〜80", "速読を遥かに超える速度"),
               (100, 300, "100〜200", "コードや段落が一瞬で出る"),
               (300, 10 ** 9, "300〜", "文字を追えない速さ")]
        RD = [(0, 500, "〜500", "A4数枚で10秒以上待つ"),
              (500, 2000, "500〜2千", "数秒待つ"),
              (2000, 10000, "2千〜1万", "1秒ほどで書き始める"),
              (10000, 10 ** 9, "1万〜", "待ち時間を感じない")]
        gen = (sp or {}).get("decode_tps")
        rd = (sp or {}).get("prefill_tps")

        def block(title, val, unit, rows, gap=0.02, lead=""):
            """"""
            hit = next((i for i, (lo, hi, *_r) in enumerate(rows)
                        if val and lo <= val < hi), None)
            out = [f'<div style="font-size:{fs(7.2)};font-weight:700;color:var(--muted);line-height:1.3;'
                   f'border-bottom:{px(0.012)} solid #C8CFD6;padding-bottom:{px(0.012)};'
                   f'margin:{px(gap)} 0 {px(0.03)}">{E(title)}'
                   + (f'<span style="font-weight:400">（{lead}<b style="color:#1F6FEB">'
                      f'{val:,.0f}</b> {unit}）</span>' if val else "") + "</div>"]
            for i, (_lo, _hi, rng, txt) in enumerate(rows):
                on = (i == hit)
                out.append(
                    f'<div style="display:flex;gap:{px(0.04)};align-items:baseline;line-height:1.25;'
                    f'background:{"#1F6FEB" if on else "transparent"};'
                    f'color:{"#FFFFFF" if on else "var(--muted)"};border-radius:{px(0.018)};'
                    f'padding:{px(0.007)} {px(0.028)};margin-bottom:{px(0.005)};'
                    f'font-weight:{"700" if on else "400"}">'
                    f'<span style="font-size:{fs(6.6)};font-family:var(--mono);'
                    f'min-width:{px(0.42)};text-align:right">{E(rng)}</span>'
                    f'<span style="font-size:{fs(6.3)};line-height:1.2">{E(txt)}</span></div>')
            return "".join(out)

        a(f'<div class="scalebox" style="{box(x, y, w, h)}">'
          + block("生成速度の目安", gen, "t/s", GEN, 0.0, " ")
          + block("読込速度の目安", rd, "t/s", RD, 0.225, " ")
          + "</div>")

    def work(y, h, title, items, color, strong=None, tint=None):
        # 2026-09-11: 見出しごと1つの枠に入れる（前は見出しが枠の外にあって縦にずれていた）。
        # 縦線は見出しの行だけ。本文は同じ幅の透明な線で字下げを揃える
        li = "".join(
            f'<div class="wl" style="border-left:{px(0.035)} solid transparent;padding-left:{px(0.03)}">'
            f'<b style="color:{color}">・{E(nm2)}　</b>'
            f'<span>{E(why)}</span></div>' for nm2, why in items)
        hd = (f'<div style="font-size:{fs(9)};font-weight:700;color:{color};'
              f'border-left:{px(0.035)} solid {strong or FIT_STRONG};padding-left:{px(0.03)};'
              f'margin-bottom:{px(0.03)}">{E(title)}</div>')
        # 高さは内容に合わせる（固定だと色の枠が上下に余って、隣の枠と重なる）
        # 箱を縮めると文章が折り返すので、色だけを 5.60in で切る
        _bg = (f'linear-gradient(to right,{tint or FIT_TINT} 0,'
               f'{tint or FIT_TINT} {px(5.204)},transparent {px(5.204)})')
        a(f'<div class="work" style="{box(0.40, y, 6.90)};font-size:{fs(7.6)};'
          f'background:{_bg};padding:{px(0.03)} {px(0.02)} {px(0.02)} 0">{hd}{li}</div>')

    work(7.98, 0.78, "向く作業", fitd[:3] or [("（該当なし）", "高い軸が条件に届いていない")],
         "var(--accent)", strong="#1F6FEB", tint=M.GROUP_TINT["#1F6FEB"])
    work(8.78, 0.86, "不向きな作業", unfitd[:4] or [("（目立つものなし）", "落ちた軸が無い")],
         "var(--bad)", strong="#7B5BD6", tint=M.GROUP_TINT["#7B5BD6"])



    # 生成の速さの目安に差し替え
    # 高さも中身に合わせて 1.58→1.66
    speed_scale(5.70, 7.85, 1.58, 1.80)   # 色帯の右端(5.60)より右へ
    T(0.31, 9.73, 7.08, 0.17, "モデルの動作を邪魔しない、完全CPU処理の音声入力ツール　<a href='https://vorice.pages.dev/' target='_blank' rel='noopener' style='color:inherit;text-decoration:underline'>Vorice</a>　長い日本語プロンプトをキーボードで打つのが面倒な方にお勧めです。", 6,
      color="var(--muted)")

    _at = measured_at(label)
    T(4.10, 9.73, 3.19, 0.17, E(SHEET_VER + ("　" + _at if _at else "")), 6,
      color="var(--muted)", align="right")

    return ('<div class="sheet-fit"><div class="sheet-page">'
            + "".join(o) + "</div></div>")


SHEET_CSS = """
.radarbox.noart .radar image{display:none}
.radarbox.noart .radar .grid{stroke:#C8CFD6}
.radarbox.noart .radar .val{fill:#00959A;fill-opacity:.35;stroke:#00959A;stroke-width:2.25}
.radarbox.noart .radar text{stroke:none}
.radarbox.noart .radar text:not(.tick){fill:#151A1F}

A4縦は高さ=幅x1.333なので、幅の上限を (画面高-余白)x0.75 にすると1枚が丸ごと入る */
.sheet-fit{container-type:inline-size;width:100%;margin:0 auto;max-width:min(820px,calc((100vh - 48px)*.75));max-width:min(820px,calc((100svh - 48px)*.75))}
.sheet-page{--u:13.3333cqw;--mono:Consolas,"SF Mono",monospace;
  position:relative;width:100%;aspect-ratio:7.5/10;background:var(--paper);
  color:var(--ink);font-family:"Yu Gothic UI","Yu Gothic","Noto Sans JP",sans-serif;
  border:1px solid var(--frame);box-shadow:0 1px 3px rgba(0,0,0,.10);overflow:hidden}
.sheet-page *{box-sizing:border-box}
.sheet-page,.sheet-page *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.sheet-page .t{position:absolute;white-space:pre-wrap}
.sheet-page .frame{position:absolute;border:0.75px solid var(--frame)}
.sheet-page .tab{position:absolute;background:var(--accent);color:#fff;font-weight:700;
  padding-left:calc(var(--u)*0.0394);display:flex;align-items:center;line-height:1}
.sheet-page .radarbox{position:absolute}
/* 2026-09-11 総合ランクのハンコ（scripts/rank_stamp.py） */
.sheet-page .stamp{position:absolute}
/* 2026-09-11 速さ×正確さの4象限（下段の右の空きに重ねる） */
.sheet-page .scalebox{position:absolute}
/* 2026-09-11 点数と称号の後ろに敷く木の看板 */
.sheet-page .board{position:absolute;object-fit:fill;z-index:0;opacity:.92}
.sheet-page .t{z-index:1}
.sheet-page .scalebox *{box-sizing:border-box}
.sheet-page .stamp svg{width:100%;height:100%;display:block}
.sheet-page .radar{width:100%;height:100%;display:block}
.sheet-page .radar .tick{font-size:9.03px;font-family:Consolas,monospace;fill:#D5DBE0}
.sheet-page .radar .lb{font-size:10.42px;font-weight:700;
  font-family:"Yu Gothic UI","Noto Sans JP",sans-serif}
.sheet-page .radar .lv{font-size:9.72px;font-family:Consolas,monospace}
.sheet-page .persona{position:absolute;line-height:1.35}
.sheet-page .persona .h{font-weight:700;color:var(--accent);margin-top:calc(var(--u)*0.042)}
.sheet-page .persona .h:first-child{margin-top:0}
.sheet-page .axes{position:absolute;border-collapse:collapse;table-layout:fixed}
.sheet-page .axes th{font-size:calc(var(--u)*0.1111);font-weight:700;text-align:center;
  padding:calc(var(--u)*0.020);border:0.5px solid var(--line);background:#F5F7F8;line-height:1.1}
.sheet-page .axes td{padding:calc(var(--u)*0.020);border:0.5px solid var(--line);border-top:0.5px solid rgba(21,26,31,.35);border-bottom:0.5px solid rgba(21,26,31,.35);
  vertical-align:middle;line-height:1.2}
.sheet-page .axes .mk{font-size:calc(var(--u)*0.1528);font-weight:700;text-align:center}
.sheet-page .axes .mk.num{font-family:var(--mono);font-size:calc(var(--u)*0.1389)}
.sheet-page .axes .ax{font-size:calc(var(--u)*0.1111);font-weight:700}
.sheet-page .axes .ax em{display:block;font-style:normal;font-weight:400;
  font-size:calc(var(--u)*0.0880);color:var(--muted);line-height:1.12}
.sheet-page .axes .mean{font-size:calc(var(--u)*0.1);color:var(--ink)}
.sheet-page .axes .mean.ti{font-size:calc(var(--u)*0.0819);color:var(--muted)}
.sheet-page .axes .mean.ti b{font-size:calc(var(--u)*0.1028);color:var(--accent);font-weight:700}
.sheet-page .axes .val,.sheet-page .axes .rk{font-family:var(--mono);font-weight:700;
  font-size:calc(var(--u)*0.1111);font-variant-numeric:tabular-nums}
.sheet-page .axes .rk{text-align:center}
.sheet-page .work{position:absolute;line-height:1.14}
.sheet-page .work .wl{margin-bottom:calc(var(--u)*0.0347)}
.sheet-page .work .wl b{font-weight:700}
.sheet-page .work .wl span{font-size:calc(var(--u)*0.0944);color:var(--muted)}
"""


def build_page(label: str) -> str:
    tpl = io.open(os.path.join(WEB, "_template.html"), encoding="utf-8").read()
    return tpl.replace("<!--SHEET-CSS-->", SHEET_CSS).replace("<!--SHEET-->", sheet(label))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="heretic27b")
    ap.add_argument("--sheet-only", action="store_true")
    ap.add_argument("--out", default=os.path.join(WEB, "index.html"))
    ar = ap.parse_args()
    if ar.sheet_only:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(SHEET_CSS + sheet(ar.label))
        raise SystemExit
    io.open(ar.out, "w", encoding="utf-8", newline="\n").write(build_page(ar.label))
    print("saved:", ar.out)