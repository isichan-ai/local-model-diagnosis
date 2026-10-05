# -*- coding: utf-8 -*-
"""診断書の数値を「横棒つきの比較表」1枚にする（2026-09-15）。

体裁は 2026-08-31 の無検閲比較の表（`make_table4.py`）に合わせる:
  ダーク背景・互い違いの行・各セルに「大きな数値／小さな内訳／横バー」。

バーの基準（値の種類で変える。ここを間違えると嘘の絵になる）:
  百分率の軸        … そのまま 0〜100%
  実作業/画像の梯子 … 5段ぶんの小バー（各段 0〜10問）
  生成・読込速度    … 並べた中の最大値を満タンとした相対
  VRAM             … **12GB（3060の予算）を満タン**とし、超えたら赤で満タン
  ファイルサイズ    … 同上

使い方:
  python make_compare_table.py --labels ornith9b_bf16 ornith9b_q8 ... --out 比較表.png
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import json   # noqa: E402
import make_report as M   # noqa: E402

BUDGET_GB = 12.0          # 3060 の VRAM 予算。VRAM とファイルサイズのバーはこれを満タンにする
VRAM_OVERRIDE = None      # --vram-gb（2026-10-01）
VRAM_LABEL = None
PREFILL_SUB = None        # --prefill-sub（2026-10-04: 読込速度の下に「中央値（IQR）」など）
VRAM_SUB = None           # --vram-sub（2026-10-04: 列ごとの文脈長など）
COMPACT = False           # --compact
NA_COLS = set()           # --na（2026-10-04: 速度・VRAM を「—」にする列・1始まり）
HL_STYLE = "base"         # --highlight-style（A〜E・2026-10-05）
UNIT = "GB"               # --unit
# --mark
MARK = ""
ADULT_C, PROMPT_C = "#ff5fa2", "#ffd166"
MARK_SUB = {
    "1": {"open": "左端＝成人向け", "ja": "右端＝プロンプト"},
    "3": {"open": "白枠＝アダルト", "ja": "白枠＝プロンプト"},   #
    "4": {"open": f'<span style="color:{ADULT_C}">■</span>＝成人向け',
          "ja": f'<span style="color:{PROMPT_C}">■</span>＝プロンプト'},
}
MARK_CSS = {
    "1": "",
    "2": (" .ax-open, .ax-ja{padding-bottom:14px;}"
          " .ax-open .step:first-child, .ax-ja .step:last-child{position:relative;}"
          " .ax-open .step:first-child::after{content:'成人';}"
          " .ax-ja .step:last-child::after{content:'プロンプト';}"
          " .ax-open .step:first-child::after, .ax-ja .step:last-child::after{position:absolute;bottom:-14px;left:50%;"
          "transform:translateX(-50%);font-size:10px;color:#e6edf3;white-space:nowrap;line-height:1;}"),
    # 白も強くして目立つように」→ 3px＋光彩、見出しの小さい字も白く大きく
    "3": (" .ax-open .step:first-child .bar, .ax-ja .step:last-child .bar{outline:3px solid #ffffff;outline-offset:2px;box-shadow:0 0 8px 2px rgba(255,255,255,.55);}"
          " tbody th .sub.mk{color:#ffffff;font-size:14px;font-weight:700;}"),
    "4": (f" .ax-open .step:first-child .bar i{{background:{ADULT_C} !important;}}"
          f" .ax-ja .step:last-child .bar i{{background:{PROMPT_C} !important;}}"),
    "5": (" .ax-open, .ax-ja{padding-top:17px;}"
          " .ax-open .step:first-child, .ax-ja .step:last-child{position:relative;}"
          f" .ax-open .step:first-child::before{{content:'18+';background:{ADULT_C};}}"
          f" .ax-ja .step:last-child::before{{content:'P';background:{PROMPT_C};}}"
          " .ax-open .step:first-child::before, .ax-ja .step:last-child::before{position:absolute;top:-17px;left:50%;"
          "transform:translateX(-50%);font-size:10px;font-weight:700;color:#0b0f14;padding:1px 4px;border-radius:6px;"
          "white-space:nowrap;line-height:1.2;}"),
}
HIGHLIGHT = 0             # --highlight
SUBROWS = False           # --subrows
COLORS = ["#4aa3ff", "#35c27a", "#f0a02a", "#b07af0", "#e05a5a", "#8b98a9"]


def e(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _j(name: str) -> dict:
    p = os.path.join(M.RES, name)
    return json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else {}


LONG_STEPS = (500, 1500, 2500, 3500)          # 読解力の文書の長さ（cap_l3.LONGREAD_STEPS と同じ）
JA_SECTIONS = ("語彙・表記", "古文・文語", "敬語", "漢字")


def collect(label: str, speed_label: str | None = None) -> dict:
    d = M.load(label)
    if speed_label:
        d = dict(d, speed=_j('speed_' + speed_label + '.json'))
    sc = M.score(d["v"])
    sp = d.get("speed") or {}
    calm = _j("calm_ladder_" + label + ".json")
    calmL = [calm["levels"].get(str(i), 0) for i in range(1, 6)] if calm.get("levels") else []
    l3 = _j("l3_" + label + ".json")
    lk = l3.get("_長文内訳", [])
    longL = [sum(1 for x in lk if x.get("words") == w and x.get("ok")) for w in LONG_STEPS] if lk else []
    sec = (_j("ja_" + label + ".json").get("_節別") or {})
    jaL = [round(sec.get(k, 0)) for k in JA_SECTIONS] if sec else []
    out = {"d": d, "v": d["v"], "sc": sc, "sp": sp,
           "codeL": [x[1] for x in d.get("codeL", [])], "visL": [x[1] for x in d.get("visL", [])],
           "code_rank": (d.get("code_rank") or [0, "—"])[1], "vis_rank": (d.get("vision_rank") or [0, "—"])[1],
           "calmL": calmL, "calm_mark": calm.get("文字", "—"), "longL": longL, "jaL": jaL,
           "jaTop": 100}
    # 2026-09-15: 分野方式の結果があればそちらで上書きする（段→分野・高さは割合）
    dom = d.get("dom", {})
    out["dom"] = dom
    # 2026-10-04 --subrows 用: 拒否・回避（open_v0104）・繰り返し（答えの断片 12字×4回・count_open_loops）・プロンプト（ja の分野）
    sub = {}
    o_ = _j("open_v0104_" + label + ".json")
    if o_:
        sub["拒否"] = o_.get("断った")
        sub["回避"] = o_.get("薄め")
        # 薄めは断らなかった答えにだけ付く判定）
        sx = [i_ for i_ in o_.get("items", []) if i_.get("domain") == "性" and i_.get("type") == "通常"]
        if sx:
            ref_ = sum(1 for i_ in sx if (i_.get("p_refused") or 0) > 0.5)
            thin_ = sum(1 for i_ in sx if i_.get("concrete") is not None and i_["concrete"] <= 0.5)
            ans_ = len(sx) - ref_
            sub["回避率"] = (round(100 * thin_ / ans_, 1) if ans_ else None, thin_, ans_)
    ja_ = _j("ja_" + label + ".json")
    if ja_ and "生成プロンプト" in (ja_.get("分野") or {}):
        sub["プロンプト"] = ja_["分野"]["生成プロンプト"].get("通過")
    ap_ = os.path.join(M.RES, "open_v0104_" + label + ".answers.jsonl")
    if os.path.exists(ap_):
        try:
            sys.path.append(os.path.join(HERE, "ternary-tools"))   # 配布版は HERE 直下に置く（Ver 01.06）
            from count_open_loops import looped, text_of   # noqa: E402
            rows_ = [json.loads(l) for l in io.open(ap_, encoding="utf-8") if l.strip()]
            sub["繰り返し"] = sum(looped(text_of(r_)) for r_ in rows_)
        except Exception:
            pass
    out["sub"] = sub
    for ax, kL, kR in (("code", "codeL", "code_rank"), ("vision", "visL", "vis_rank"),
                       ("calm", "calmL", "calm_mark"), ("long", "longL", "long_rank"),
                       ("ja", "jaL", "ja_rank")):
        o_ = dom.get(ax)
        if not o_:
            continue
        out[kL] = [(x["通過"], x["問題数"]) for x in o_.get("分野", {}).values()]
        out[kR] = o_.get("階位") or o_.get("文字") or "—"
    return out


def table_data(label: str) -> dict:
    """診断書JSON（lmd-sheet/2）に入れる「比較表の材料」（2026-10-05 Ver 01.06）。
    サイトの「詳細表示」がこれを読み、build() と同じ見た目の表をブラウザの中で組む。
    バーの満タンが列どうしで決まる行（速度・繰り返し）は、ここでは生の値だけを渡す。"""
    c = collect(label)
    OLD = {"calm": ("calmL", "calm_mark", 10, "旧・段"), "code": ("codeL", "code_rank", 10, "旧・段"),
           "long": ("longL", "long_rank", 4, "旧・500→3,500語"), "ja": ("jaL", "ja_rank", 100, "旧・語彙古文敬語漢字"),
           "vision": ("visL", "vis_rank", 10, "旧・段")}
    axes = []
    for k, nm, _a, _th, _dsc in M.AXES:
        a = {"k": k, "name": nm}
        dm = c["dom"].get(k)
        if dm:
            lab = dm.get("階位") or dm.get("文字") or "-"
            a["foot"] = "{}　{:.1f}%".format(lab, dm.get("百分率", 0))
            if dm.get("大枠"):
                a["steps"] = [[round(v or 0), 100] for v in dm["大枠"].values()]
                a["names"] = list(dm["大枠"].keys())
                from cap_open_v0104 import BIG   # 大枠 → 中の分野
                dd = dm.get("分野") or {}
                a["detail"] = [chr(10).join(f"・{d} {dd[d]:.0f}%" for d in BIG.get(b, []) if dd.get(d) is not None)
                               for b in a["names"]]
            else:
                fs = dm.get("表示分野") or dm.get("分野") or {}
                a["steps"] = [[x["通過"], x["問題数"]] for x in fs.values()]
                a["names"] = list(fs.keys())
                a["detail"] = [f"{x['通過']}／{x['問題数']}問" for x in fs.values()]
        elif k in OLD and c.get(OLD[k][0]):
            kL, kR, top, dflt = OLD[k]
            a["steps"] = [list(x) if isinstance(x, (tuple, list)) else [x, top] for x in c[kL]]
            a["foot"] = c.get(kR) or dflt
        else:
            x = c["v"].get(k)
            a["pct"] = None if x is None else round(x, 1)
        axes.append(a)
    sub = dict(c.get("sub") or {})
    if isinstance(sub.get("回避率"), tuple):
        sub["回避率"] = list(sub["回避率"])
    meta = _j("meta_" + label + ".json")
    return {"score": c["sc"]["scaled"], "rank": c["sc"]["rank"],
            "code": M.code(c["v"], c["d"]), "epithet": M.epithet(c["d"]),
            "axes": axes, "sub": sub,
            "gen_tps": c["sp"].get("decode_tps"), "pre_tps": c["sp"].get("prefill_tps"),
            "file_bytes": meta.get("file_bytes")}


def cell(big: str, sub: str, ratio: float, color: str, dim: bool = False, over: bool = False) -> str:
    w = max(0.0, min(100.0, ratio * 100))
    c = "#e05a5a" if over else color
    return (f'<td><div class="pct{" dim" if dim else ""}">{e(big)}</div>'
            f'<div class="cnt">{e(sub)}</div>'
            f'<div class="track"><i style="width:{w:.1f}%;background:{c}"></i></div></td>')


def dom_cell(dm: dict, color: str) -> str:
    """分野ごとの縦バー。高さ＝通過/問題数。下に階位（または漢字）と全体の百分率。"""
    # 2026-09-15: 無検閲度は採点12分野・表示6組（dna_domains.GROUPS）
    lab = dm.get("階位") or dm.get("文字") or "-"
    foot = "{}　{:.1f}%".format(lab, dm.get("百分率", 0))
    if dm.get("大枠"):     # 2026-09-28 Ver01.04 の無検閲度: 開/禁を決める4大枠をそのまま4本で出す
        return steps_cell([(round(v or 0), 100) for v in dm["大枠"].values()], color, foot)
    vs = list((dm.get("表示分野") or dm.get("分野") or {}).values())
    return steps_cell([(x["通過"], x["問題数"]) for x in vs], color, foot)


def steps_cell(vals: list, color: str, rank: str, top: int = 10) -> str:
    """段ごとの小バー（各段 0〜top）＋下に一言。数字はバーの空いている所に入れる。"""
    if not vals:
        return '<td><div class="pct dim">—</div></td>'
    # 数字はバーの「空いている所」（上の余白）に置く
    # 2026-09-15: (通過, 問題数) の組なら分野ごとの割合で高さを決める（分野で問題数が違うため）
    pairs = [(x if isinstance(x, (tuple, list)) else (x, top)) for x in vals]

    def one(v, t_):
        pct_ = round(100 * v / t_) if t_ else 0
        h = max(3, pct_)
        return (f'<span class="step"><span class="bar"><b>{pct_}</b>'
                f'<i style="height:{h}%;background:{color}"></i></span></span>')

    PER = 6
    rows_ = [pairs[i:i + PER] for i in range(0, len(pairs), PER)]
    bars = '<div class="sep"></div>'.join(
        '<div class="row">' + "".join(one(v, t_) for v, t_ in r) + "</div>" for r in rows_)
    return (f'<td><div class="steps{" stack" if len(pairs) > PER else ""}">{bars}</div>'
            f'<div class="rank">{e(rank)}</div></td>')


def build(labels: list, names: list, files_gb: list, title: str, note: str,
          speed_labels: list | None = None, ctx_k: list | None = None,
          setups: list | None = None, no_speed: bool = False) -> str:
    C = [collect(x, (speed_labels or [None] * len(labels))[i])
         for i, x in enumerate(labels)]
    n = len(C)
    col = [COLORS[i % len(COLORS)] for i in range(n)]
    rows = []

    def tr(title_, cells, odd):
        # 2026-09-16: 副題つきの見出しは HTML を含むのでエスケープしない
        t_ = title_ if "<br>" in title_ else e(title_)
        rows.append(f'<tr class="{"odd" if odd else ""}"><th>{t_}</th>{"".join(cells)}</tr>')

    odd = False
    # ── 総合 ──
    tr("総合", [cell(f"{c['sc']['scaled']:.1f}", c["sc"]["rank"], c["sc"]["scaled"] / 100, col[i])
                for i, c in enumerate(C)], odd)
    odd = not odd
    tr("十文字", [f'<td><div class="code">{e(M.code(c["v"], c["d"]))}</div>'
                  f'<div class="cnt">{e(M.epithet(c["d"]))}</div></td>' for c in C], odd)
    odd = not odd
    # ── 10軸（分野方式なら縦バー・まだなら従来の出し方）──
    OLD = {"calm": ("calmL", "calm_mark", 10, "旧・段"), "code": ("codeL", "code_rank", 10, "旧・段"),
           "long": ("longL", "long_rank", 4, "旧・500→3,500語"), "ja": ("jaL", "ja_rank", 100, "旧・語彙古文敬語漢字"),
           "vision": ("visL", "vis_rank", 10, "旧・段")}
    for k, nm, _a, _th, _dsc in M.AXES:
        cells = []
        for i, c in enumerate(C):
            dm = c["dom"].get(k)
            if dm:
                # 2026-10-05: 行の種類を印に（--mark で無検閲度の左端＝成人向け・文章の右端＝プロンプトを示すため）
                cells.append(dom_cell(dm, col[i]).replace('<div class="steps', f'<div class="ax-{k} steps', 1))
                continue
            if k in OLD:                       # 旧方式の梯子の結果（測り直す前のモデル）
                kL, kR, top, dflt = OLD[k]
                cells.append(steps_cell(c[kL], col[i], c.get(kR) or dflt, top))
                continue
            x = c["v"].get(k)
            cells.append(cell("—", "未測定", 0, col[i], dim=True) if x is None
                         else cell(f"{x:.1f}%", "", x / 100, col[i]))
        SUB = {"answer": "エージェント", "code": "Python 50本"}
        if MARK in ("1", "3", "4"):
            SUB = dict(SUB, **MARK_SUB[MARK])
        mk_ = ' mk' if (MARK in MARK_SUB and k in MARK_SUB[MARK]) else ''
        label = (f'{nm}<br><span class="sub{mk_}">（{SUB[k]}）</span>' if k in SUB else nm)
        tr(label, cells, odd)
        odd = not odd
        if SUBROWS and k == "open":
            # 分母は小さい字に
            # 拒否＝％（106問が分母）。バーは％そのもの
            cs = []
            for i, c in enumerate(C):
                v_ = c.get("sub", {}).get("拒否")
                cs.append(cell("—", "", 0, col[i], dim=True) if v_ is None
                          else cell(f"{100 * v_ / 106:.0f}%", f"{v_}／106問", v_ / 106, col[i]))
            tr("　└ 拒否", cs, odd)
            odd = not odd
            # 回避＝％（分母は拒否されなかった問題数）。バーは％そのもの
            cs = []
            for i, c in enumerate(C):
                r_ = c.get("sub", {}).get("回避率")
                if not r_ or r_[0] is None:
                    cs.append(cell("—", "", 0, col[i], dim=True))
                else:
                    cs.append(cell(f"{r_[0]:.0f}%", f"{r_[1]}／{r_[2]}問", r_[0] / 100, col[i]))
            tr("　└ 回避", cs, odd)
            odd = not odd
        if SUBROWS and k == "ja":
            cs = []
            for i, c in enumerate(C):
                v_ = c.get("sub", {}).get("プロンプト")
                cs.append(cell("—", "", 0, col[i], dim=True) if v_ is None
                          else cell(f"{v_}", "問／10問", v_ / 10, col[i]))
            tr("　└ プロンプト", cs, odd)
            odd = not odd
            vals_ = [c.get("sub", {}).get("繰り返し") for c in C]
            mx_ = max([v for v in vals_ if v is not None] or [1]) or 1
            cs = []
            for i, v_ in enumerate(vals_):
                cs.append(cell("—", "", 0, col[i], dim=True) if v_ is None
                          else cell(f"{v_}", "回／106問", v_ / mx_, col[i]))
            # 数えた場所（無検閲の106問の答え）は小さく添える
            tr('　└ 繰り返し', cs, odd)
            odd = not odd
    # ── 速度・大きさ ──
    gen = [c["sp"].get("decode_tps") or 0 for c in C]
    pre = [c["sp"].get("prefill_tps") or 0 for c in C]
    vram = [(c["sp"].get("vram_mib") or 0) / 1024 for c in C]
    # 2026-09-25: 文脈2本立ての表記。vram_mib_131k があれば「262K / 131K」を並べる
    vram131 = [(c["sp"].get("vram_mib_131k") or 0) / 1024 for c in C]
    two_ctx = all(v > 0 for v in vram131)
    if not no_speed:
        gmax = max([g for i, g in enumerate(gen) if (i + 1) not in NA_COLS] or [1])
        tr("生成速度", [(cell("—", "", 0, col[i], dim=True) if (i + 1) in NA_COLS
                      else cell(f"{gen[i]:.1f}", "t/s", gen[i] / gmax, col[i]))
                     for i in range(n)], odd)
        odd = not odd
        pmax = max([g for i, g in enumerate(pre) if (i + 1) not in NA_COLS] or [1])
        tr("読込速度", [(cell("—", "", 0, col[i], dim=True) if (i + 1) in NA_COLS
                      else cell(f"{pre[i]:,.0f}", (PREFILL_SUB[i] if PREFILL_SUB and PREFILL_SUB[i] != "-" else "t/s"), pre[i] / pmax, col[i]))
                     for i in range(n)], odd)
        odd = not odd
        if two_ctx:
            tr("必要VRAM", [cell(f"{vram[i]:.1f} / {vram131[i]:.1f}{UNIT}",
                               f"文脈 262K / 131K",
                               vram[i] / BUDGET_GB, col[i], over=vram[i] > BUDGET_GB)
                         for i in range(n)], odd)
        elif VRAM_OVERRIDE:
            # 同居していた処理の分が混ざる（Mitsuba 14.0 は約4GB上乗せ）。llama-server 自身の確保量を外から渡す。
            # 「~54.3」のように ~ を付けた値は見積もり（「約」を付ける）
            est = [v.startswith("~") for v in VRAM_OVERRIDE]
            na_v = [v.strip() == "-" for v in VRAM_OVERRIDE]
            vo_ = ["0" if x else v for x, v in zip(na_v, VRAM_OVERRIDE)]   # 関数内で VRAM_OVERRIDE に代入すると局所になる
            # 2026-10-04: 「9.8/12.6」のように2つ渡すと CTX 131K / 262K を並べる（バーは大きい方）
            pair = [v.lstrip("~").split("/") for v in vo_]
            vv = [max(float(x) for x in p) for p in pair]
            big = [" / ".join(f"{float(x):.1f}" for x in p) for p in pair]
            tr(VRAM_LABEL or "必要VRAM", [cell("—", "", 0, col[i], dim=True) if na_v[i] else cell(f"{'約' if est[i] else ''}{big[i]}{UNIT}", (VRAM_SUB[i] if VRAM_SUB else f"{BUDGET_GB:.0f}GBの{vv[i] / BUDGET_GB * 100:.0f}%"),
                                              vv[i] / BUDGET_GB, col[i], over=vv[i] > BUDGET_GB)
                                         for i in range(n)], odd)
        else:
            tr("必要VRAM", [cell(f"{vram[i]:.1f}{UNIT}", f"{BUDGET_GB:.0f}GBの{vram[i] / BUDGET_GB * 100:.0f}%",
                               vram[i] / BUDGET_GB, col[i], over=vram[i] > BUDGET_GB)
                         for i in range(n)], odd)
    odd = not odd
    if setups:
        odd = not odd
        tr("設定", ['<td><div class="pct" style="font-size:19px">%s</div>'
                   '<div class="cnt">%s</div></td>'
                   % (e(x.split(",")[0]), e((x.split(",") + [""])[1]))
                   for x in setups], odd)
    if ctx_k:
        mx = max(ctx_k)
        odd = not odd
        tr("乗せれる文脈", [cell(f"{ctx_k[i]:,}K", "トークン", ctx_k[i] / mx, col[i])
                      for i in range(n)], odd)
    odd = not odd
    tr("ファイル", [cell(f"{files_gb[i]:.1f}{UNIT}", "", files_gb[i] / BUDGET_GB, col[i],
                       over=files_gb[i] > BUDGET_GB) for i in range(n)], odd)

    heads = "".join((f'<th class="mh"><span class="maker">{e(sub)}</span><br>{e(main)}</th>' if sub.strip()
                     else f'<th class="mh">{e(main)}</th>')   # 2026-10-04: 副題が空なら上の行を出さない
                    for main, sub in names)
    compact_css = ("" if not COMPACT else
        " th,td{padding:8px 5px;} tbody td{min-width:0;}"
        " .wrap{padding:20px 14px 16px;} .steps,.steps .row{gap:3px;} .bar{width:19px;}"
        # 2026-10-05: 二つ名「千里眼の見習い検閲官」が 132px で2行になった→ 150px
        " .track{width:112px;} .cnt{max-width:150px;margin-left:auto;margin-right:auto;line-height:1.25;}"
        " .pct{font-size:21px;} .cnt{white-space:pre-line;} .code{font-size:16px;letter-spacing:0;} thead th{font-size:17px;}")
    if HIGHLIGHT:
        # 選んだ1列だけ目立たせる（行の縞の上にも効くよう td/th に直接）。
        # 5パターン出して」→ --highlight-style A〜E
        hc = COLORS[(HIGHLIGHT - 1) % len(COLORS)]
        k = HIGHLIGHT + 1
        th_, td_ = f" thead th:nth-child({k})", f" tbody td:nth-child({k})"
        styles = {
            "base": f"{th_},{td_}{{background:{hc}26;}}{th_}{{border-top:3px solid {hc};border-radius:10px 10px 0 0;}}",
            "A": (f"{th_},{td_}{{box-shadow:inset 2px 0 0 #e6edf3, inset -2px 0 0 #e6edf3;}}"
                  f"{th_}{{box-shadow:inset 2px 0 0 #e6edf3, inset -2px 0 0 #e6edf3, inset 0 2px 0 #e6edf3;}}"
                  f" tbody tr:last-child td:nth-child({k}){{box-shadow:inset 2px 0 0 #e6edf3, inset -2px 0 0 #e6edf3, inset 0 -2px 0 #e6edf3;}}"),
            "B": f"{th_},{td_}{{background:#14233b;}}{th_}{{border-top:3px solid #4aa3ff;}}",
            "C": (f"{th_},{td_}{{background:#d4af3714;box-shadow:inset 2px 0 0 #d4af37, inset -2px 0 0 #d4af37;}}"
                  f"{th_}{{box-shadow:inset 2px 0 0 #d4af37, inset -2px 0 0 #d4af37, inset 0 2px 0 #d4af37;color:#f3d77a;}}"
                  f" tbody tr:last-child td:nth-child({k}){{box-shadow:inset 2px 0 0 #d4af37, inset -2px 0 0 #d4af37, inset 0 -2px 0 #d4af37;}}"),
            "D": (f"{td_}{{background:#202b3a;}}{th_}{{background:#e6edf3;color:#0b0f14;border-radius:10px 10px 0 0;}}"
                  f"{th_} .maker{{color:#3a4657;}}"),
            "E": f"{th_},{td_}{{background:#0f2b2e;box-shadow:inset 2px 0 0 #2ec4b6, inset -2px 0 0 #2ec4b6;}}",
        }
        compact_css += styles.get(HL_STYLE, styles["base"])
    compact_css += MARK_CSS.get(MARK, "")
    return f"""<meta charset="utf-8">
<style>
 body{{margin:0;background:#0b0f14;font-family:"Yu Gothic UI","Meiryo",system-ui,sans-serif;}}
 .wrap{{padding:26px 30px 20px;display:inline-block;}}
 h1{{color:#f2f6fa;font-size:26px;margin:0 0 18px;font-weight:700;}}
 table{{border-collapse:collapse;}}
 th,td{{padding:11px 14px;}}
 thead th{{color:#e6edf3;font-size:19px;font-weight:600;text-align:center;border-bottom:1px solid #263041;}}
 thead th:first-child{{text-align:left;}}
 .maker{{font-family:Consolas,monospace;font-size:13px;color:#8b98a9;font-weight:400;}}
 tbody th{{color:#e6edf3;font-size:18px;font-weight:500;text-align:left;white-space:nowrap;line-height:1.3;}}
 tbody th .sub{{color:#8b98a9;font-size:12px;font-weight:400;}}
 tbody td{{text-align:center;vertical-align:middle;min-width:186px;}}
 tr.odd{{background:#111823;}}
 .pct{{color:#f2f6fa;font-size:25px;font-weight:700;line-height:1.15;}}
 .pct.dim{{color:#5b6675;font-weight:600;}}
 .code{{color:#f2f6fa;font-size:19px;font-weight:700;font-family:Consolas,monospace;letter-spacing:1px;}}
 .cnt{{color:#8b98a9;font-size:14px;margin-top:2px;min-height:17px;}}
 .track{{margin:8px auto 0;width:170px;height:7px;border-radius:4px;background:#222c3a;overflow:hidden;}}
 .track i{{display:block;height:100%;border-radius:4px;}}
 .steps{{display:flex;gap:7px;justify-content:center;align-items:flex-end;}}
 .step{{display:flex;flex-direction:column;align-items:center;}}
 .bar{{position:relative;width:24px;height:58px;background:#222c3a;border-radius:3px;
   display:flex;align-items:flex-end;overflow:hidden;}}
 .steps.stack{{flex-direction:column;gap:0;align-items:center;}}
 .steps .row{{display:flex;gap:7px;justify-content:center;align-items:flex-end;}}
 .steps.stack .bar{{height:44px;}}
 .sep{{width:100%;height:1px;background:#3a4657;margin:5px 0;}}
 .bar i{{display:block;width:100%;border-radius:3px;}}
 .bar b{{position:absolute;top:2px;left:0;right:0;text-align:center;z-index:2;
   color:#f2f6fa;font-size:13px;font-weight:700;line-height:1;text-shadow:0 1px 2px #0b0f14;}}
 .rank{{color:#8b98a9;font-size:14px;margin-top:6px;}}
 .note{{color:#5b6675;font-family:Consolas,monospace;font-size:15px;margin:16px 4px 0;}}
{compact_css}
</style>
<div class="wrap">
{('<h1>' + e(title) + '</h1>') if title.strip() else ''}
<table>
 <thead><tr><th>項目</th>{heads}</tr></thead>
 <tbody>{"".join(rows)}</tbody>
</table>
<div class="note">{e(note)}</div>
</div>"""


def shoot(html_path: str, png_path: str) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 2200, "height": 2400}, device_scale_factor=1)
        pg.goto("file:///" + html_path.replace("\\", "/"), wait_until="networkidle")
        pg.locator(".wrap").screenshot(path=png_path)
        b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", nargs="+", required=True)
    ap.add_argument("--names", nargs="+", required=True, help="列の見出し（'主,副' の形）")
    ap.add_argument("--gb", nargs="+", type=float, required=True, help="ファイルサイズGB")
    ap.add_argument("--title", default="比較表")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed-labels", nargs="+", default=None,
                    help="速度とVRAMを読むラベル（省略時は --labels と同じ）")
    ap.add_argument("--ctx", nargs="+", type=int, default=None,
                    help="その設定で乗せれる文脈長（千トークン）")
    ap.add_argument("--budget", type=float, default=None, help="VRAMのバーの満タン値GB")
    ap.add_argument("--setup", nargs="+", default=None,
                    help="枠に収めた手（'主,副' の形）")
    ap.add_argument("--no-speed", action="store_true",
                    help="生成速度・読込速度・必要VRAMの行を省く")
    ap.add_argument("--vram-gb", nargs="+", default=None,
                    help="必要VRAMを外から渡す（GiB・列の順。~ を付けると見積もり）2026-10-01")
    ap.add_argument("--prefill-sub", nargs="+", default=None, help="読込速度の小さい字（列の順・- で既定）")
    ap.add_argument("--vram-sub", nargs="+", default=None, help="VRAMの小さい字（列の順）")
    ap.add_argument("--compact", action="store_true", help="列の間を詰めた版（スマホ向け）")
    ap.add_argument("--highlight", type=int, default=0, help="背景の色を変えて目立たせる列（1始まり）")
    ap.add_argument("--unit", default="GB", help="VRAM とファイルの単位の表記（GB / GiB）。数値は渡した値のまま")
    ap.add_argument("--mark", default="", help="無検閲度の左端（成人向け）と文章の右端（プロンプト）の示し方 1〜5")
    ap.add_argument("--highlight-style", default="base", help="目立たせ方 base / A白枠 / B濃紺 / C金枠 / D明るい灰 / E青緑")
    ap.add_argument("--na", nargs="*", type=int, default=[], help="速度・VRAM を「—」にする列（1始まり）")
    ap.add_argument("--subrows", action="store_true", help="無検閲度の下に拒否・回避・繰り返し、文章の下にプロンプトを出す（2026-10-04）")
    ap.add_argument("--vram-label", default=None, help="必要VRAMの行の名前（例 必要VRAM（CTX 131K））")
    a = ap.parse_args()
    global VRAM_OVERRIDE, SUBROWS, NA_COLS, VRAM_LABEL
    VRAM_OVERRIDE, VRAM_LABEL = a.vram_gb, a.vram_label
    global PREFILL_SUB, VRAM_SUB, COMPACT
    COMPACT = a.compact
    PREFILL_SUB, VRAM_SUB = a.prefill_sub, a.vram_sub
    SUBROWS = a.subrows
    NA_COLS = set(a.na or [])
    global HIGHLIGHT, HL_STYLE, MARK, UNIT
    HIGHLIGHT, HL_STYLE, MARK, UNIT = a.highlight, a.highlight_style, a.mark, a.unit
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    names = [tuple((x.split(",", 1) + [""])[:2]) for x in a.names]
    global BUDGET_GB
    if a.budget:
        BUDGET_GB = a.budget
    html = build(a.labels, names, a.gb, a.title, a.note, a.speed_labels, a.ctx,
                 a.setup, a.no_speed)
    hp = os.path.join(HERE, "web", "_check", "compare.html")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    io.open(hp, "w", encoding="utf-8", newline="\n").write(html)
    out = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    shoot(hp, out)
    print("PNG:", out, f"{os.path.getsize(out) / 1024:.0f} KB")


if __name__ == "__main__":
    main()