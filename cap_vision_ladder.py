# -*- coding: utf-8 -*-
"""画像認識の梯子 V1〜V5。

  V1 物・数・文字 … 実写真＋GPTラベル。何個（count_questions）・何が書いてあるか（text_in_image）・文字の無い写真で「無い」と言えるか
  V2 図表         … 棒・折れ線・円・表を乱数データで描画。正解は描いた側が知っている
  V3 場面の説明   … 実写真＋GPTラベル（must_mention／decoys）。必須要素の網羅率−囮の減点
  V4 人物の細部   … 実写真＋GPTラベル（person）。服の種類・髪・ポーズ・向き・指の本数・人数（「場面の説明」の上位＝認識のレベル差）
  V5 複数枚の比較 … 同一人物の組（pairs.json）＋写真を機械で1か所だけ加工した2枚

各段 10 問。段位は実作業と同じ貫通式（code_tasks_ladder.PASS_THRESHOLD＝8/10・思考OFFで決める）。

  python cap_vision_ladder.py --port 8081 --label heretic27b --model heretic-q4-mtp --level all
  python cap_vision_ladder.py --dry            # 画像と問いだけ生成して assets/vision/generated/ に置く（モデル不要）

写真の置き場: assets/vision/photos/pNN.jpg（縮小版 small/）・ラベル pNN.json・pairs.json
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import random
import re
import sys
import time

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import effort_cfg as _E
import cap_vision as V1MOD                 # V1 の生成（cases）と見たふり検出（DENY）を流用
from code_tasks_ladder import PASS_THRESHOLD, rank_of
import domains as _D   # 2026-09-15: 分野方式
# 画像認識の段位名
RANK_NAMES = ["暗闇", "隻眼", "遠見", "鷹目", "千里眼"]
NO_RANK = "暗闇"

WORK = os.path.join(HERE, "results")
PHOTOS = os.path.join(HERE, "assets", "vision", "photos")
PHOTO_DIRS = [PHOTOS, os.path.join(HERE, "assets", "vision", "photos2"), os.path.join(HERE, "assets", "vision", "gen")]   # 実写1・実写2（手）・合成
GEN = os.path.join(HERE, "assets", "vision", "generated")
SEED = 20260914
_HOST = os.environ.get("DIAG_HOST", "127.0.0.1")
_sf = os.environ.get("LLMBENCH_SYSTEM_FILE")
SYSTEM_PROMPT = open(_sf, encoding="utf-8").read().strip() if _sf and os.path.exists(_sf) else ""
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0, "sec": 0.0}

_JP_FONTS = [os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", f) for f in ("NotoSansJP-VF.ttf", "meiryo.ttc", "msgothic.ttc", "YuGothM.ttc")]
JP_FONT = next((f for f in _JP_FONTS if os.path.exists(f)), None)

LEVEL_NAMES = {1: "物・数・文字", 2: "図表", 3: "場面の説明", 4: "人物の細部", 5: "複数枚の比較"}
# 2026-09-14 11:40 並びは実測（8本×15問の合計）: 物116 → 人物106 → 図表90 → 場面83 → 複数枚75
N_ITEMS = 15   # 通過は 80%（12/15）
DENY = re.compile(V1MOD.DENY.pattern + r"|写ってい(ない|ません)|含まれてい(ない|ません)|読める文字は(ない|ありません)|見当たら", re.I)   # 「文字はありません」系（cap_vision の型に、8本の実測で出た言い回しを追加・2026-09-14 08:05）


# ─────────────────────────── 共通 ───────────────────────────
def to_data_url(im: Image.Image) -> str:
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def ask(port: int, model: str, images: list, q: str, max_tokens: int = 160) -> str:
    content = [{"type": "text", "text": q}] + [{"type": "image_url", "image_url": {"url": to_data_url(im)}} for im in images]
    msgs = ([{"role": "system", "content": SYSTEM_PROMPT}] if SYSTEM_PROMPT else []) + [{"role": "user", "content": content}]
    body = {"model": model, "max_tokens": _E.cap_tok(max_tokens), "temperature": 0.0,
            "chat_template_kwargs": _E.tmpl_kwargs(), "messages": msgs}
    t0 = time.time()
    r = requests.post(f"http://{_HOST}:{port}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    j = r.json(); u = j.get("usage") or {}
    USAGE["prompt_tokens"] += int(u.get("prompt_tokens") or 0); USAGE["completion_tokens"] += int(u.get("completion_tokens") or 0)
    USAGE["requests"] += 1; USAGE["sec"] += time.time() - t0
    return (j["choices"][0]["message"].get("content") or "").strip()


def z2h(s: str) -> str:
    """全角英数字と記号を半角へ（数の読み取り用）。"""
    return s.translate(str.maketrans("０１２３４５６７８９：％，．", "0123456789:%,.")).replace("，", ",")


def first_int(s: str):
    m = re.search(r"-?\d[\d,]*", z2h(s))
    return int(m.group(0).replace(",", "")) if m else None


def last_int(s: str):
    """途中式を書くモデル向け: 最後に出た整数を答えとみなす（2026-09-14 実害: 「12×200+…=10120」の 12 を答えと取っていた）。"""
    ms = re.findall(r"-?\d[\d,]*", z2h(s))
    return int(ms[-1].replace(",", "")) if ms else None


def first_side(out: str, a: str, b: str) -> bool:
    """a が出ていて、b より先に出ている（「右手にスマホ。左手は空」の左手で落とさない）。"""
    ia, ib = out.find(a), out.find(b)
    return ia >= 0 and (ib < 0 or ia < ib)


def photo(n: str) -> Image.Image:
    for d in PHOTO_DIRS:
        for cand in (os.path.join(d, "small", f"{n}.jpg"), os.path.join(d, f"{n}.jpg"), os.path.join(d, f"{n}.png")):
            if os.path.exists(cand):
                im = Image.open(cand).convert("RGB"); im.thumbnail((1024, 1024))
                return im
    raise FileNotFoundError(n)


def labels() -> dict:
    """ラベル pNN.json／qNN.json／合成 <id>.json を全部読む（pairs.json・manifest.json は除く）。無ければ空。"""
    out = {}
    for d in PHOTO_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "*.json"))):
            n = os.path.splitext(os.path.basename(p))[0]
            if n in ("pairs", "manifest") or n.startswith("_"):
                continue
            try:
                lab = json.load(open(p, encoding="utf-8"))
            except Exception as e:
                print(f"  ラベル読み込み失敗 {n}: {e!r}", flush=True); continue
            if isinstance(lab, dict) and "must_mention" in lab:
                out[n] = lab
    out.update(gen_labels())
    return out


# 合成画像（gen/manifest.json）→ ラベル。正解はプロンプト由来だが verified_by_claude が真のものだけ使う（左右などは目視で確かめてから）
HAIR_JP = {"long": "ロング・黒", "ponytail": "ポニーテール・黒", "bun": "お団子・黒", "twintails": "ツインテール・黒", "braid": "三つ編み・黒", "bob": "ボブ・黒"}
FACE_JP = {"front": ("正面", "正面"), "left": ("左", None), "right": ("右", None), "back": ("後ろ", None), "up": ("正面", "上"), "down": ("正面", "下")}
CLOTH_JP = {"uniform": "ブレザー", "casual": "私服", "kimono": "和服", "sports": "スポーツウェア", "suit": "スーツ", "sailor": "セーラー服", "scrubs": "白衣", "gi": "道着"}
POSE_JP = {"stand": "立つ", "sit": "座る", "walk": "歩く", "run": "走る", "jump": "ジャンプ", "raise": "手を挙げる", "turn": "振り向く"}
OBJ_JP = {"cup": "コップ", "phone": "スマートフォン", "book": "本", "umbrella": "傘"}


def gen_manifest() -> dict:
    p = os.path.join(PHOTO_DIRS[2], "manifest.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def gen_labels() -> dict:
    out = {}
    for gid, m in gen_manifest().items():
        if not m.get("verified_by_claude"):
            continue
        meta = dict(m.get("meta") or {}); meta.update(m.get("override") or {})   # override＝目視で直した正解
        person = {"count": 1, "clothing": None, "colors": [], "pose": None, "facing": "正面", "gaze": None, "hair": None, "hands_visible": None, "fingers_visible": None, "hold": None}
        k = m["kind"]
        # メタにある属性は全部ラベルにする（組み合わせ画像なら1枚から髪・服・向き・視線・ポーズ・持ち手の問いが出る）
        if meta.get("hair"):
            person["hair"] = HAIR_JP.get(meta["hair"])
        if meta.get("facing"):
            f, g = FACE_JP.get(meta["facing"], (None, None)); person["facing"] = f; person["gaze"] = g
        if meta.get("outfit"):
            person["clothing"] = CLOTH_JP.get(meta["outfit"])
        if meta.get("pose"):
            person["pose"] = POSE_JP.get(meta["pose"])
        if meta.get("hand") and meta.get("object"):
            person["hold"] = {"hand": meta["hand"], "object": meta["object"]}
        if k in ("same_person", "other_person") and not person["hair"]:
            person["hair"] = "ロング・黒"
        if k == "hard_person":
            person = {"count": meta.get("count", 1), "hard_qs": meta.get("qs", [])}   # 問いは manifest に書いてある（複数人・隠れ・小物の左右・年齢・表情）
        out[gid] = {"objects": [{"name": "人物", "count": 1}], "must_mention": ["人物"], "aliases": {"人物": ["女性", "女の人"]}, "decoys": [],
                    "scene": "スタジオ（合成）", "text_in_image": [], "hard": False, "person": person, "count_questions": [], "synthetic": True, "gen_kind": k, "gen_meta": meta}
    return out


def domain_rank(fp: dict) -> tuple:
    """分野方式。順序を見ず、8割取れた分野の数で階位を決める。
    fp = {段番号: (通過数, 問題数)}。返り値 (達成数, 階位名)。"""
    res = {LEVEL_NAMES[lv]: v for lv, v in fp.items()}
    return _D.rank("vision", res)


def ratio_rank(fp: dict) -> tuple:
    """1段の問題数が段ごとに違っても使える貫通式（通過率 80% 以上で通過）。"""
    reached = 0
    for lv in (1, 2, 3, 4, 5):
        got, n = fp.get(lv, (None, 0))
        if got is None or n == 0 or got < -(-8 * n // 10):   # ceil(0.8n)
            break
        reached = lv
    return reached, (RANK_NAMES[reached - 1] if reached else NO_RANK)


# ─────────────────────────── V1 物・数・文字（実写真） ───────────────────────────
def _norm(s: str) -> str:
    return z2h(s).replace(" ", "").replace("　", "").lower()


def v1_items(r: random.Random) -> list:
    """数える5問＋文字を読む3問＋文字の無い写真で「無い」と言う2問（ラベル pNN.json が要る）。"""
    L = labels()
    counts, texts, notext = [], [], []
    for n, lab in L.items():
        if lab.get("hard"):
            continue                      # V1 は「新人」の段＝迷いようのない写真だけで組む（2026-09-14 07:50・実測で並びを決める）
        for cq in lab.get("count_questions") or []:
            counts.append((n, cq["q"], cq["a"]))
        t = [str(x) for x in (lab.get("text_in_image") or []) if str(x).strip()]
        if lab.get("has_unreadable_text"):
            continue                      # 看板などの文字はあるが正解を確定できない写真＝文字の問いには使わない
        if t:
            texts.append((n, t))
        else:
            notext.append(n)
    r.shuffle(counts); r.shuffle(texts); r.shuffle(notext)
    items = []
    for n, q, a in counts[:8]:                                  # 15問化: 数える8・文字4・文字なし3
        def grade(out, a=a):
            if isinstance(a, int):
                got = first_int(out)
                return got == a, f"want {a} got {got}"
            cands = a if isinstance(a, list) else [a]          # 文字列の正解は複数書ける（時計の 8:46/8:47/8:48 など）
            return any(_norm(str(c)) in _norm(out) for c in cands), f"want {cands}"
        items.append({"id": f"V1-count-{n}", "images": [photo(n)], "q": q + " 答えだけを短く。", "meta": {"a": a, "photo": n}, "grade": grade})
    q_text = "この写真の中に読める文字や数字を、そのまま書き出してください。無ければ「文字はありません」と答えてください。"
    for n, t in texts[:4]:
        # text_in_image のどれか1つでも答えに含まれていれば通す（旧字・部分一致の逃げ道はラベル側で列挙する）
        items.append({"id": f"V1-text-{n}", "images": [photo(n)], "q": q_text, "meta": {"a": t, "photo": n},
                      "grade": (lambda t: lambda out: (any(_norm(x) in _norm(out) for x in t), f"want any of {t}"))(t)})
    for n in notext[:3]:
        items.append({"id": f"V1-notext-{n}", "images": [photo(n)], "q": q_text, "meta": {"a": None, "photo": n},
                      "grade": lambda out: (bool(DENY.search(out)), "want: 無いと言う")})
    return items[:N_ITEMS]


# ─────────────────────────── V3 図表 ───────────────────────────
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if JP_FONT:
        font_manager.fontManager.addfont(JP_FONT)
        matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=JP_FONT).get_name()
    matplotlib.rcParams["axes.unicode_minus"] = False
    return plt


def _fig_to_image(plt, fig) -> Image.Image:
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def v3_items(r: random.Random) -> list:
    """図表の読み取り（2026-09-14 12:15 改訂: 計算を全部外し、読み取り＋比較だけ。差・合計・掛け算は「算数」の指標になるため）。"""
    plt = _plt()
    items = []
    cities = ["東京", "大阪", "名古屋", "福岡", "札幌"]
    months = [f"{i}月" for i in range(1, 7)]
    fruits = ["りんご", "みかん", "ぶどう", "バナナ"]
    goods = ["ノート", "鉛筆", "消しゴム", "定規"]

    def name_grade(want):
        return lambda out: (want in out, f"want {want}")

    def int_grade(want, tol=0):
        def g(out):
            got = last_int(out)
            return got is not None and abs(got - want) <= tol, f"want {want} got {got}"
        return g

    def set_grade(want, universe):
        """条件を満たすものを全部列挙: 欲しいものが全部あり、それ以外の候補が1つも無い。"""
        def g(out):
            have = [u for u in universe if u in out]
            return sorted(have) == sorted(want), f"want {want} got {have}"
        return g

    def seq_grade(want):
        """並べ替え: 出現順が正解と一致。"""
        def g(out):
            pos = [(out.find(w), w) for w in want if w in out]
            got = [w for _, w in sorted(pos)]
            return got == list(want), f"want {want} got {got}"
        return g

    def bar(kind, k):
        vals = r.sample(range(12, 96), 5)
        labels = kind != "read"
        fig, ax = plt.subplots(figsize=(6, 3.6)); ax.bar(cities, vals, color="#4a7ebb")
        ax.set_title("売上（万円）" if k % 2 == 0 else "来場者数（人）"); ax.set_ylim(0, 100); ax.set_yticks(range(0, 101, 10)); ax.grid(axis="y", alpha=0.3)
        if labels:
            for i, v in enumerate(vals):
                ax.text(i, v + 1.5, str(v), ha="center", fontsize=10)
        im = _fig_to_image(plt, fig)
        order = sorted(range(5), key=lambda i: -vals[i])
        if kind == "max":
            top = cities[order[0]]
            return {"id": f"V3-bar-max{k}", "images": [im], "q": "この棒グラフで値が最も大きい都市はどこですか。都市名だけを答えてください。", "meta": {"a": top}, "grade": name_grade(top)}
        if kind == "second":
            sec = cities[order[1]]
            return {"id": f"V3-bar-second{k}", "images": [im], "q": "この棒グラフで値が2番目に大きい都市はどこですか。都市名だけを答えてください。", "meta": {"a": sec}, "grade": name_grade(sec)}
        if kind == "over":
            th = r.choice([40, 50, 60]); want = [c for c, v in zip(cities, vals) if v >= th]
            return {"id": f"V3-bar-over{k}", "images": [im], "q": f"この棒グラフで値が{th}以上の都市を全部挙げてください。都市名だけを「、」で区切って答えてください。", "meta": {"a": want, "th": th}, "grade": set_grade(want, cities)}
        if kind == "min":
            low = cities[order[-1]]
            return {"id": f"V3-bar-min{k}", "images": [im], "q": "この棒グラフで値が最も小さい都市はどこですか。都市名だけを答えてください。", "meta": {"a": low}, "grade": name_grade(low)}
        i = r.randrange(5)
        return {"id": f"V3-bar-read{k}", "images": [im], "q": f"この棒グラフで{cities[i]}の値はおよそいくつですか。目盛りから読み取って数字だけを答えてください。", "meta": {"a": vals[i], "tol": 4}, "grade": int_grade(vals[i], tol=4)}

    def line(kind, k):
        vals = [r.randint(10, 90) for _ in months]
        two = kind == "cross"
        vals2 = [r.randint(10, 90) for _ in months] if two else None
        labels = kind not in ("read", "cross")
        fig, ax = plt.subplots(figsize=(6, 3.6)); ax.plot(months, vals, marker="o", color="#c0504d", label="A")
        if two:
            ax.plot(months, vals2, marker="s", color="#4a7ebb", label="B"); ax.legend()
        ax.set_title("月ごとの気温（℃）" if k % 2 == 0 else "月ごとの件数"); ax.set_ylim(0, 100); ax.set_yticks(range(0, 101, 10)); ax.grid(alpha=0.3)
        if labels:
            for i, v in enumerate(vals):
                ax.text(i, v + 2.5, str(v), ha="center", fontsize=10)
        im = _fig_to_image(plt, fig)
        if kind == "peak":
            top = months[vals.index(max(vals))]
            return {"id": f"V3-line-peak{k}", "images": [im], "q": "この折れ線グラフで値が最も高い月はいつですか。「◯月」の形で答えてください。", "meta": {"a": top}, "grade": name_grade(top)}
        if kind == "drop":
            want = [months[i] for i in range(1, 6) if vals[i] < vals[i - 1]]
            return {"id": f"V3-line-drop{k}", "images": [im], "q": "この折れ線グラフで、前の月より値が下がった月を全部挙げてください。「◯月」を「、」で区切って答えてください（無ければ「なし」）。", "meta": {"a": want}, "grade": set_grade(want, months[1:])}
        if kind == "cross":
            want = [m for m, a, b in zip(months, vals, vals2) if a > b]
            return {"id": f"V3-line-cross{k}", "images": [im], "q": "この折れ線グラフで、AがBより高い月を全部挙げてください。「◯月」を「、」で区切って答えてください（無ければ「なし」）。", "meta": {"a": want, "A": vals, "B": vals2}, "grade": set_grade(want, months)}
        i = r.randrange(6)
        return {"id": f"V3-line-read{k}", "images": [im], "q": f"この折れ線グラフで{months[i]}の値はおよそいくつですか。目盛りから読み取って数字だけを答えてください。", "meta": {"a": vals[i], "tol": 4}, "grade": int_grade(vals[i], tol=4)}

    def pie(kind, k):
        cuts = sorted(r.sample(range(8, 92), 3)); pct = [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1], 100 - cuts[2]]
        fig, ax = plt.subplots(figsize=(4.6, 4.6))
        ax.pie(pct, labels=fruits, autopct="%d%%", startangle=90, colors=["#e15759", "#f28e2b", "#76b7b2", "#edc948"]); ax.set_title("好きな果物の割合")
        im = _fig_to_image(plt, fig)
        order = sorted(range(4), key=lambda i: -pct[i])
        if kind == "pct":
            i = r.randrange(4)
            return {"id": f"V3-pie-pct{k}", "images": [im], "q": f"この円グラフで「{fruits[i]}」の割合は何％ですか。数字だけを答えてください。", "meta": {"a": pct[i]}, "grade": int_grade(pct[i], tol=1)}
        sec = fruits[order[1]]
        return {"id": f"V3-pie-second{k}", "images": [im], "q": "この円グラフで割合が2番目に大きい果物はどれですか。名前だけを答えてください。", "meta": {"a": sec}, "grade": name_grade(sec)}

    def table(kind, k):
        qty = r.sample(range(2, 40), 4); price = r.sample([80, 100, 120, 150, 200, 250, 300], 4)
        fig, ax = plt.subplots(figsize=(5.2, 2.6)); ax.axis("off")
        tbl = ax.table(cellText=[[g, str(q), str(p)] for g, q, p in zip(goods, qty, price)], colLabels=["品名", "数量", "単価（円）"], loc="center", cellLoc="center")
        tbl.scale(1, 1.6); tbl.set_fontsize(12)
        im = _fig_to_image(plt, fig)
        if kind == "cell":
            i = r.randrange(4)
            return {"id": f"V3-table-cell{k}", "images": [im], "q": f"この表で「{goods[i]}」の単価はいくらですか。数字だけを答えてください。", "meta": {"a": price[i]}, "grade": int_grade(price[i])}
        if kind == "maxprice":
            top = goods[price.index(max(price))]
            return {"id": f"V3-table-maxprice{k}", "images": [im], "q": "この表で単価が最も高い品名はどれですか。品名だけを答えてください。", "meta": {"a": top}, "grade": name_grade(top)}
        if kind == "over":
            th = r.choice([10, 15, 20]); want = [g for g, q in zip(goods, qty) if q >= th]
            return {"id": f"V3-table-over{k}", "images": [im], "q": f"この表で数量が{th}以上の品名を全部挙げてください。品名だけを「、」で区切って答えてください。", "meta": {"a": want, "th": th}, "grade": set_grade(want, goods)}
        want = [g for _, g in sorted(zip(qty, goods), reverse=True)]
        return {"id": f"V3-table-order{k}", "images": [im], "q": "この表の品名を数量が多い順に並べてください。品名だけを「、」で区切って答えてください。", "meta": {"a": want}, "grade": seq_grade(want)}

    plan = [(bar, "max"), (bar, "second"), (bar, "over"), (bar, "read"), (bar, "min"),
            (line, "peak"), (line, "drop"), (line, "cross"), (line, "read"),
            (pie, "pct"), (pie, "second"),
            (table, "cell"), (table, "maxprice"), (table, "over"), (table, "order")]
    for k, (fn, kind) in enumerate(plan):
        items.append(fn(kind, k))
    return items[:N_ITEMS]


# ─────────────────────────── V4 場面の説明 ───────────────────────────
def v4_items(r: random.Random) -> list:
    L = labels()
    names = [n for n, lab in L.items() if lab.get("must_mention") and not lab.get("synthetic")]   # 場面の説明は実写だけ
    r.shuffle(names)
    q = "この写真に写っているものを、主なものから順に日本語で箇条書きにしてください（5〜8項目・写っていないものは書かない・個数が分かるものは「りんご 3個」のように個数も書く）。"
    KANJI = {2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    items = []
    for n in names:
        lab = L[n]
        must = [str(x) for x in lab.get("must_mention", [])]
        decoys = [str(x) for x in lab.get("decoys", [])]
        alias = lab.get("aliases") or {}
        # 必須要素のうち個数が2以上のものは、その個数も書けていること（「漏らさず」に量を含める・2026-09-14 07:45）
        counts = {o["name"]: o["count"] for o in lab.get("objects", []) if o.get("name") in must and isinstance(o.get("count"), int) and 2 <= o["count"] <= 5}   # 6個以上の数えは V1 の役目

        def grade(out, must=must, decoys=decoys, alias=alias, counts=counts):
            def hit(w):
                return any(a in out for a in [w] + list(alias.get(w, [])))
            got = [w for w in must if hit(w)]
            # 囮の直後に漢字・カタカナが続く時は別の単語の一部（窓→窓枠・人→人名・本→日本語）として数えない（2026-10-03）
            bad = [w for w in decoys if any(not re.match(r"[一-鿿゠-ヿー]", out[m.end():m.end() + 1]) for m in re.finditer(re.escape(w), out))]
            o2 = z2h(out)
            miss_n = [f"{w}={c}" for w, c in counts.items() if not (str(c) in o2 or KANJI.get(c, "") and KANJI[c] in out)]
            ok = len(got) == len(must) and not bad and not miss_n
            return ok, f"必須 {len(got)}/{len(must)}" + (f" 囮 {bad}" if bad else "") + (f" 個数漏れ {miss_n}" if miss_n else "") + ("" if ok or len(got) == len(must) else f" 漏れ {[w for w in must if w not in got]}")
        items.append({"id": f"V3-{n}", "images": [photo(n)], "q": q, "meta": {"must": must, "decoys": decoys, "counts": counts, "photo": n}, "grade": grade})
        if len(items) >= N_ITEMS:
            break
    return items


# ─────────────────────────── V5 複数枚の比較 ───────────────────────────
EDITS = {
    "flip":   ("左右反転", ["反転", "左右", "鏡", "ミラー", "裏返"]),
    "circle": ("赤い丸を追加", ["丸", "円", "赤", "図形", "追加", "マーク", "点"]),
    "crop":   ("右側を切り落とし", ["切", "狭", "端", "欠", "トリミング", "範囲", "見切れ", "幅"]),
    "hue":    ("色合いを変更", ["色", "色合い", "色味", "色調", "カラー"]),
    "rotate": ("90度回転", ["回転", "横向き", "縦向き", "傾", "向き"]),
    "dark":   ("暗くした", ["暗", "明る", "露出", "光", "照明", "夜", "夕"]),
}


def apply_edit(im: Image.Image, kind: str, r: random.Random) -> Image.Image:
    if kind == "flip":
        return ImageOps.mirror(im)
    if kind == "circle":
        out = im.copy(); d = ImageDraw.Draw(out); w, h = out.size; rad = max(24, min(w, h) // 8)
        cx, cy = r.randint(rad, w - rad), r.randint(rad, h - rad)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(230, 30, 30), outline="white", width=4)
        return out
    if kind == "crop":
        w, h = im.size
        return im.crop((0, 0, int(w * 0.6), h))
    if kind == "hue":
        hsv = im.convert("HSV"); H, S, V = hsv.split()
        H = H.point(lambda x: (x + 100) % 256)
        return Image.merge("HSV", (H, S, V)).convert("RGB")
    if kind == "rotate":
        return im.rotate(90, expand=True)
    if kind == "dark":
        return ImageEnhance.Brightness(im).enhance(0.35)
    raise ValueError(kind)


def v5_items(r: random.Random) -> list:
    items = []
    pairs_path = os.path.join(PHOTOS, "pairs.json")
    pairs = json.load(open(pairs_path, encoding="utf-8")) if os.path.exists(pairs_path) else {}
    q_same = "この2枚の写真に写っている人物は同じ人ですか。「同じ」か「違う」のどちらか一語で答えてください。"

    def same_grade(want):
        def g(out):
            i_s, i_d = out.find("同じ"), out.find("違")
            got = "同じ" if i_s >= 0 and (i_d < 0 or i_s < i_d) else ("違う" if i_d >= 0 else None)
            return got == want, f"want {want} got {got}"
        return g
    # 15問化（2026-09-14 10:00）: 実写の同一人物1組・別人1組 ＋ 合成の同一人物（服も髪も違う）3組・別人（同じ服と髪）3組 ＋ 合成の微差2枚組4組 ＋ 機械加工3件
    for a, b in pairs.get("same_person", [])[:1]:
        items.append({"id": f"V5-same-{a}-{b}", "images": [photo(a), photo(b)], "q": q_same, "meta": {"a": "同じ"}, "grade": same_grade("同じ")})
    for a, b in pairs.get("different_person_same_style", [])[:1]:
        items.append({"id": f"V5-diff-{a}-{b}", "images": [photo(a), photo(b)], "q": q_same, "meta": {"a": "違う"}, "grade": same_grade("違う")})
    M = {k: v for k, v in gen_manifest().items() if v.get("verified_by_claude")}
    combos = sorted(k for k, v in M.items() if v["kind"] in ("combo", "same_person"))
    others = sorted(k for k, v in M.items() if v["kind"] == "other_person")
    gsame = [(combos[i], combos[(i + 5) % len(combos)]) for i in range(len(combos))][:3] if len(combos) >= 2 else []
    for a, b in gsame:
        items.append({"id": f"V5-gsame-{a}-{b}", "images": [photo(a), photo(b)], "q": q_same, "meta": {"a": "同じ"}, "grade": same_grade("同じ")})
    for o in others[:3]:   # 別人＝同じ服・同じ髪の合成人物（MIREI なし）と、その服髪の MIREI 画像を組む
        mo = M[o]["meta"]; mate = next((c for c in combos if M[c]["meta"].get("outfit") == mo.get("outfit") and M[c]["meta"].get("hair", "long") == mo.get("hair", "long")), combos[0] if combos else None)
        if mate:
            items.append({"id": f"V5-gdiff-{o}-{mate}", "images": [photo(mate), photo(o)], "q": q_same, "meta": {"a": "違う"}, "grade": same_grade("違う")})
    SUBTLE = {"cup": ["左右", "持ち手", "持つ手", "右手", "左手", "反対の手"], "cap": ["帽子", "キャップ", "かぶ"], "glasses": ["眼鏡", "メガネ", "めがね"], "scarf": ["スカーフ", "マフラー", "首"],
              "hold": ["本", "スマホ", "スマートフォン", "携帯", "持ち物", "持って"], "arms": ["腕", "ポケット", "組ん", "手の位置", "手を"]}
    q_sub = "1枚目の写真に対して、2枚目の写真はどこが違いますか。一番大きな違いを短く答えてください。"
    subtle_pairs = {}
    for k, v in M.items():
        if v["kind"] == "pair":
            subtle_pairs.setdefault(v["meta"].get("pair"), []).append(k)
    for grp, ids in sorted(subtle_pairs.items())[:4]:
        if len(ids) == 2 and grp in SUBTLE:
            a, b = sorted(ids)
            items.append({"id": f"V5-subtle-{grp}", "images": [photo(a), photo(b)], "q": q_sub, "meta": {"a": grp, "kws": SUBTLE[grp]},
                          "grade": (lambda kws, grp: lambda out: (any(w in out for w in kws), f"want {grp}"))(SUBTLE[grp], grp)})
    # 機械加工（6件）: 加工が目で分かる写真を1種ずつ固定で割り当てる（左右反転は左右非対称な写真でないと分からない）
    edit_plan = [("p01", "hue"), ("p09", "crop"), ("p16", "dark"), ("p13", "flip"), ("p03", "circle"), ("p06", "rotate")]   # dark は p22(時計)→p16: 7/8 が針の位置に釘付けになった
    q_edit = "1枚目の写真に対して、2枚目の写真はどこが違いますか。一番大きな違いを短く答えてください。"
    for n, kind in edit_plan:
        if len(items) >= N_ITEMS:
            break
        if not os.path.exists(os.path.join(PHOTOS, f"{n}.jpg")):
            continue
        base = photo(n); edited = apply_edit(base, kind, r)
        desc, kws = EDITS[kind]
        items.append({"id": f"V5-edit-{kind}-{n}", "images": [base, edited], "q": q_edit, "meta": {"a": desc, "kws": kws},
                      "grade": (lambda kws, desc: lambda out: (any(k in out for k in kws), f"want {desc}"))(kws, desc)})
    return items[:N_ITEMS]


# ─────────────────────────── V4 人物の細部 ───────────────────────────
ALIASES = {
    "セーラー服": ["セーラー"], "ブレザー": ["ブレザー", "学生服", "制服", "ジャケット"], "スーツ": ["スーツ", "ジャケット"],
    "白衣": ["白衣", "スクラブ", "医療", "看護"], "作業着": ["作業", "つなぎ"], "和服": ["和服", "着物", "浴衣", "振袖"],
    "スポーツウェア": ["スポーツ", "ジャージ", "ユニフォーム", "トレーニング", "運動"], "私服": ["私服", "カジュアル", "Tシャツ", "シャツ", "ワンピース", "ドレス"],
    "道着": ["道着", "空手", "柔道", "胴着"],
    "立つ": ["立っ", "起立", "直立", "立ち"], "座る": ["座っ", "腰掛", "着席", "座り"], "歩く": ["歩い", "歩行", "歩き"], "走る": ["走っ", "ランニング", "走り", "ダッシュ"],
    "ジャンプ": ["ジャンプ", "跳", "飛ん", "空中"], "手を挙げる": ["挙げ", "上げ", "掲げ", "万歳"], "振り向く": ["振り向", "振り返", "後ろを"],
    "正面": ["正面", "こちら", "カメラ", "前"], "左": ["左"], "右": ["右"], "後ろ": ["後ろ", "背中", "背面", "背を"],
    "黒": ["黒", "ブラック", "暗い"], "茶": ["茶", "ブラウン", "栗"], "金": ["金", "ブロンド", "金髪"], "赤": ["赤", "レッド"], "白": ["白"],
    "三つ編み": ["三つ編み", "編み込み", "おさげ", "ブレイド"], "コップ": ["コップ", "カップ", "マグ", "コーヒー"], "スマートフォン": ["スマホ", "スマートフォン", "携帯", "電話"], "本": ["本", "書籍", "ブック"], "傘": ["傘", "アンブレラ"],
    "上": ["上", "上方", "見上げ"], "下": ["下", "下方", "見下ろ", "うつむ"],
    "ロング": ["ロング", "長い", "長髪"], "ショート": ["ショート", "短い", "短髪"], "ボブ": ["ボブ", "ショート", "ミディアム", "短い", "肩"], "ミディアム": ["ミディアム", "セミロング", "肩", "ミディアムロング"],
    "ポニーテール": ["ポニーテール", "ポニー", "一つ結び", "ひとつ結び"], "ツインテール": ["ツインテール", "ツイン", "二つ結び"], "お団子": ["お団子", "団子", "シニヨン", "まとめ", "アップ"],
}


def _hit(word: str, out: str) -> bool:
    return word in out or any(a in out for a in ALIASES.get(word, []))


def v_person_items(r: random.Random) -> list:
    """person ラベルから 服・髪・ポーズ・向き・指・人数 の問いを作る。"""
    L = labels()
    pool = []
    for n, lab in L.items():
        pr = lab.get("person") or {}
        if not pr:
            continue
        cnt = pr.get("count")
        if not cnt and not isinstance(pr.get("fingers_visible"), int):
            continue                      # 手だけの写真（人数 None）でも指の本数があれば問いに使う（2026-09-14 08:50）
        if isinstance(cnt, int) and cnt >= 2:
            pool.append((n, "count", "この写真には人が何人写っていますか。数字だけを答えてください。", cnt))
        if cnt == 1:
            if pr.get("clothing"):
                pool.append((n, "clothing", "この写真の人物が着ている服の種類は何ですか（セーラー服・ブレザー・スーツ・白衣・作業着・和服・スポーツウェア・道着・私服 のどれか）。一語で答えてください。", pr["clothing"]))
            if pr.get("hair"):
                pool.append((n, "hair", "この写真の人物の髪について、長さ・色・結び方を「ロング・黒・ポニーテール」のように答えてください。", pr["hair"]))
            if pr.get("pose"):
                pool.append((n, "pose", "この写真の人物の姿勢は何ですか（立つ・座る・歩く・走る・ジャンプ・手を挙げる・振り向く のどれか）。一語で答えてください。", pr["pose"]))
            if pr.get("facing"):
                pool.append((n, "facing", "この写真の人物はどちらを向いていますか（正面・左・右・後ろ のどれか）。一語で答えてください。", pr["facing"]))
        for qd in pr.get("hard_qs", []):
            pool.append((n, "hp", qd["q"], qd))
        if isinstance(pr.get("fingers_visible"), int):
            pool.append((n, "fingers", "この写真で伸ばしている指は全部で何本ですか。折り曲げている指や握っている指は数えません。数字だけを答えてください。", pr["fingers_visible"]))
        if pr.get("gaze"):
            pool.append((n, "gaze", "この写真の人物の視線はどこを向いていますか（上・下・正面 のどれか）。一語で答えてください。", pr["gaze"]))
        if pr.get("hold") and pr["hold"].get("hand") and pr["hold"].get("object"):
            pool.append((n, "hold", "この写真の人物は、どちらの手に何を持っていますか。「右手にコップ」のように答えてください。", pr["hold"]))
    r.shuffle(pool)
    # 服とポーズは8本とも満点で差が出なかった
    by = {}
    for x in pool:
        by.setdefault(x[1], []).append(x)
    # 2026-09-14 10:00 15問化＋合成画像の種類（視線・持ち手）を追加。服・ポーズは各1問
    # 2026-09-14 12:30 複雑な描写に寄せる: hard_person（複数人・隠れ・小物の左右・年齢・表情）を主役に
    quota = [("hp", 99), ("fingers", 2), ("hold", 1), ("hair", 1), ("facing", 1), ("gaze", 1)]   # hp は全部（抽選で難問が消えないように）
    picked = []
    for kind, n in quota:
        for _ in range(n):
            if by.get(kind):
                picked.append(by[kind].pop())
    for kind in ("hp", "fingers", "facing", "hair", "count"):     # 足りない分は落ちやすい種類で埋める
        while len(picked) < 22 and by.get(kind):
            picked.append(by[kind].pop())
    items = []
    for n, kind, q, a in picked:
        if kind == "hp":
            qd = a; kk = qd["k"]; want = qd["a"]; alts = [str(want)] + list(qd.get("alts", []))
            if kk in ("count", "count_hands"):
                g = (lambda w: lambda out: (first_int(out) == w, f"want {w} got {first_int(out)}"))(want)
            elif kk in ("who", "lr"):        # 左/右: 正解があり、反対が無い
                g = (lambda w: lambda out: (first_side(out, w, "右" if w == "左" else "左"), f"want {w}"))(want)
            elif kk == "hold":
                hand_jp = {"right": "右", "left": "左"}[want["hand"]]; obj = OBJ_JP.get(want["object"], want["object"])
                g = (lambda hand_jp, obj: lambda out: (first_side(out, f"{hand_jp}手", f"{'左' if hand_jp == '右' else '右'}手") and _hit(obj, out), f"want {hand_jp}手に{obj}"))(hand_jp, obj)
            elif kk == "yesno":
                g = (lambda w: lambda out: (w in out and ("はい" if w == "いいえ" else "いいえ") not in out.replace(w, ""), f"want {w}"))(want)
            else:
                g = (lambda alts: lambda out: (any(x in out for x in alts), f"want {alts[0]}"))(alts)
            items.append({"id": f"V4-{kk}-{n}", "images": [photo(n)], "q": q, "meta": {"a": want, "photo": n}, "grade": g}); continue
        if kind in ("count", "fingers"):
            g = (lambda a: lambda out: (first_int(out) == a, f"want {a} got {first_int(out)}"))(a)
        elif kind == "hair":
            parts = [p_.strip() for p_ in re.split(r"[・,、/ ]+", str(a)) if p_.strip()]
            g = (lambda parts: lambda out: (all(_hit(p_, out) for p_ in parts[:2]), f"want {parts}"))(parts)
        elif kind == "hold":
            hand_jp = {"right": "右", "left": "左"}[a["hand"]]; obj = OBJ_JP.get(a["object"], a["object"])
            g = (lambda hand_jp, obj: lambda out: (first_side(out, f"{hand_jp}手", f"{'左' if hand_jp == '右' else '右'}手") and _hit(obj, out), f"want {hand_jp}手に{obj}"))(hand_jp, obj)
        else:
            g = (lambda a: lambda out: (_hit(str(a), out), f"want {a}"))(a)
        items.append({"id": f"V4-{kind}-{n}", "images": [photo(n)], "q": q, "meta": {"a": a, "photo": n}, "grade": g})
    return items[:22]   # 人物は最大22問（hard_person 16 + 指2・持ち手1・髪1・向き1・視線1）→ KEEP_IDS で難しい10問に絞る


# 2026-09-14 07:50 並び替え: 8本の実測で「場面の説明」より「図表」の方が難しかった（合計 71 vs 68）ので V2=場面・V3=図表にした。
# 2026-09-14 12:30 描写の複雑さ順: 図表（単純化された線と数字）は人物（服・髪・手・向きが絡む実写）より下の段
LEVELS = {1: v1_items, 2: v3_items, 3: v4_items, 4: v_person_items, 5: v5_items}
# 2026-09-14 13:05 実測（8本×10問）: 物73 → 図表67 → 場面58 → 人物51 → 複数枚38。「複雑な描写」の人物が場面より上の段
# 乱数の種は段番号でなく「段の中身」に紐づける（段を入れ替えても問題が再抽選されない・2026-09-14 12:30）
GEN_SEED = {"v1_items": 1, "v_person_items": 2, "v3_items": 3, "v4_items": 4, "v5_items": 5}
# 空なら15問全部
KEEP_IDS = {
 "1": [
  "V1-count-q05",
  "V1-notext-q11",
  "V1-count-p18",
  "V1-count-p37",
  "V1-text-p02",
  "V1-count-p14",
  "V1-count-p26",
  "V1-count-p40",
  "V1-count-q03",
  "V1-count-q04"
 ],
 "2": [
  "V2-pie-second10",
  "V2-line-cross7",
  "V2-table-cell11",
  "V2-bar-second1",
  "V2-line-drop6",
  "V2-bar-max0",
  "V2-bar-min4",
  "V2-bar-over2",
  "V2-bar-read3",
  "V2-line-peak5"
 ],
 "3": [
  "V3-p25",
  "V3-p17",
  "V3-q13",
  "V3-p10",
  "V3-p31",
  "V3-p28",
  "V3-p44",
  "V3-q07",
  "V3-p01",
  "V3-p29"
 ],
 "4": [
  "V4-lr-hp_watch_left",
  "V4-count_hands-hp_occluded",
  "V4-facing-hp_back_view",
  "V4-fingers-q14",
  "V4-fingers-q15",
  "V4-hold-hold_left_book",
  "V4-lr-hp_bag_right",
  "V4-expr-hp_angry",
  "V4-facing-combo_08",
  "V4-hair-p28"
 ],
 "5": [
  "V5-edit-crop-p09",
  "V5-edit-dark-p16",
  "V5-edit-hue-p01",
  "V5-gdiff-other_combo_1-combo_01",
  "V5-gdiff-other_combo_2-combo_02",
  "V5-gdiff-other_combo_3-combo_03",
  "V5-edit-flip-p13",
  "V5-gsame-combo_03-combo_08",
  "V5-gsame-combo_01-combo_06",
  "V5-gsame-combo_02-combo_07"
 ]
}



# ─────────────────────────── 実行 ───────────────────────────
def run_level(port: int, label: str, model: str, level: int, only: str = "", reuse: dict = None) -> dict:
    r = random.Random(SEED + GEN_SEED[LEVELS[level].__name__])
    items = LEVELS[level](r)
    for it in items:                                   # id の頭は「今の段番号」に揃える（並び替えで中身と番号がずれないように）
        it["id"] = f"V{level}-" + it["id"].split("-", 1)[1]
    if KEEP_IDS.get(str(level)):          # 鍵は文字列（JSON 由来）。2026-09-14 12:55 まで int で引いていて効いていなかった
        items = [it for it in items if it["id"] in KEEP_IDS[str(level)]]
    if only:
        items = [it for it in items if only in it["id"]]
    reuse = reuse or {}
    USAGE.update(prompt_tokens=0, completion_tokens=0, requests=0, sec=0.0)
    detail = []; ok_n = 0
    if not items:
        print(f"  V{level}: 問題が作れない（写真のラベル pNN.json が無い？）", flush=True)
    for it in items:
        if it["id"] in reuse:                          # --sync: 既にある答えを使い回す
            d = reuse[it["id"]]; ok_n += int(d["ok"]); detail.append(d); continue
        t0 = time.time(); c0 = USAGE["completion_tokens"]
        try:
            out = ask(port, model, it["images"], it["q"], 400 if level in (2, 4) else 160)
            ok, why = it["grade"](out)
        except Exception as e:
            out, ok, why = repr(e)[:120], False, "error"
        ok_n += int(ok)
        detail.append({"id": it["id"], "ok": ok, "why": why, "answer": out[:600], "meta": {k: v for k, v in it["meta"].items() if k != "kws"},
                       "sec": round(time.time() - t0, 1), "tokens": USAGE["completion_tokens"] - c0})
        print(f"  V{level} {it['id']:24s} {'○' if ok else '×'}  {out[:50]!r}  {why[:50]}", flush=True)
    n = len(items)
    return {"label": label, "port": port, "model": model, "level": level, "name": LEVEL_NAMES[level],
            "画像認識": (100.0 * ok_n / n) if n else None, "_通過": ok_n, "_問題数": n, "_内訳": detail, "_usage": dict(USAGE), "_effort": _E.EFFORT or "off"}


def merge_partial(fp: str, part: dict) -> dict:
    """--only の結果を既存の段ファイルへ差し替える（同 id を置換・通過数を再計算）。既存が無ければそのまま。"""
    if not os.path.exists(fp):
        return part
    base = json.load(open(fp, encoding="utf-8"))
    new = {d["id"]: d for d in part["_内訳"]}
    base["_内訳"] = [new.pop(d["id"], d) for d in base["_内訳"]] + list(new.values())
    base["_通過"] = sum(int(d["ok"]) for d in base["_内訳"]); base["_問題数"] = len(base["_内訳"])
    base["画像認識"] = (100.0 * base["_通過"] / base["_問題数"]) if base["_問題数"] else None
    base.setdefault("_redo", []).append({"ids": sorted(new.keys()) or [d["id"] for d in part["_内訳"]], "effort": part["_effort"]})
    return base


def write_ladder(label: str, model: str) -> None:
    """vision_V1..5_<label>.json が揃っていれば梯子 json を（再）計算する。"""
    summary = {}
    for lv in (1, 2, 3, 4, 5):
        fp = os.path.join(WORK, f"vision_V{lv}_{label}.json")
        if not os.path.exists(fp):
            return
        summary[lv] = json.load(open(fp, encoding="utf-8"))
    fp_ = {lv: (r_["_通過"], r_["_問題数"]) for lv, r_ in summary.items()}
    lv_, name = ratio_rank(fp_)
    lad = {"label": label, "model": model, "effort": _E.EFFORT or "off",
           "levels": {str(lv): {"name": LEVEL_NAMES[lv], "通過": r_["_通過"], "問題数": r_["_問題数"]} for lv, r_ in summary.items()},
           "貫通段位": [lv_, name], "基準": "80%（10問なら8）"}
    json.dump(lad, open(os.path.join(WORK, f"vision_ladder_{label}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("梯子: " + " / ".join(f"V{lv} {r_['_通過']}/{r_['_問題数']}" for lv, r_ in summary.items()))
    print(f"  貫通段位（80% 基準）: {name}（V{lv_} まで）")


def run(port: int, label: str, model: str, level_arg: str, only: str = "", sync: bool = False) -> None:
    os.makedirs(WORK, exist_ok=True)
    levels = [1, 2, 3, 4, 5] if level_arg == "all" else [int(level_arg)]
    summary = {}
    for lv in levels:
        fp = os.path.join(WORK, f"vision_V{lv}_{label}.json")
        reuse = {}
        if sync and os.path.exists(fp):
            reuse = {d["id"]: d for d in json.load(open(fp, encoding="utf-8"))["_内訳"]}
        res = run_level(port, label, model, lv, only, reuse)
        if sync:
            asked = [d["id"] for d in res["_内訳"] if d["id"] not in reuse]
            print(f"  --sync V{lv}: 使い回し {len(res['_内訳']) - len(asked)}・新規 {len(asked)} {asked}", flush=True)
        if only:
            res = merge_partial(fp, res)
        json.dump(res, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{label}: V{lv} {LEVEL_NAMES[lv]} {res['_通過']}/{res['_問題数']}", flush=True)
        summary[lv] = res
    if only or sync or level_arg != "all":
        write_ladder(label, model)
    if level_arg == "all":
        fp = {lv: (r_["_通過"], r_["_問題数"]) for lv, r_ in summary.items()}
        lv_, name = ratio_rank(fp)
        res = {LEVEL_NAMES[lv]: (r_["_通過"], r_["_問題数"]) for lv, r_ in summary.items()}
        dom = _D.summary("vision", res)
        lad = {"label": label, "model": model, "effort": _E.EFFORT or "off",
               "levels": {str(lv): {"name": LEVEL_NAMES[lv], "通過": r_["_通過"], "問題数": r_["_問題数"]} for lv, r_ in summary.items()},
               "貫通段位": [lv_, name], "基準": "80%（10問なら8）"}
        lad.update(dom)          # 分野方式の達成数・階位（2026-09-15〜こちらが正）
        json.dump(lad, open(os.path.join(WORK, f"vision_ladder_{label}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("梯子: " + " / ".join(f"V{lv} {r_['_通過']}/{r_['_問題数']}" for lv, r_ in summary.items()))
        print(f"  貫通段位（80% 基準）: {name}（V{lv_} まで）")


def dry() -> None:
    """モデルを呼ばずに、各段の画像と問い・正解を書き出して目で確かめる。"""
    os.makedirs(GEN, exist_ok=True)
    lines = []
    for lv in (1, 2, 3, 4, 5):
        r = random.Random(SEED + GEN_SEED[LEVELS[lv].__name__])
        try:
            items = LEVELS[lv](r)
        except Exception as e:
            lines.append(f"V{lv}: 生成失敗 {e!r}"); continue
        lines.append(f"===== V{lv} {LEVEL_NAMES[lv]}: {len(items)} 問 =====")
        for it in items:
            for k, im in enumerate(it["images"]):
                im.save(os.path.join(GEN, f"{it['id']}_{k}.png"))
            lines.append(f"{it['id']:26s} 正解={json.dumps({k: v for k, v in it['meta'].items() if k not in ('kws',)}, ensure_ascii=False)}  問い={it['q'][:60]}")
    open(os.path.join(GEN, "_問いと正解.txt"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int); ap.add_argument("--label"); ap.add_argument("--model", default="x")
    ap.add_argument("--level", default="all", choices=["1", "2", "3", "4", "5", "all"])
    ap.add_argument("--dry", action="store_true", help="画像と問いだけ生成（モデル不要）")
    ap.add_argument("--only", default="", help="id の部分一致で該当問題だけ取り直す（既存の段ファイルへ差し替え）")
    ap.add_argument("--sync", action="store_true", help="今の問題集合に揃える（ある id は使い回し・無い id だけ聞く・外れた id は落とす）")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.dry:
        dry()
    else:
        if not (a.port and a.label):
            ap.error("--port と --label が要る（--dry 以外）")
        run(a.port, a.label, a.model, a.level, a.only, a.sync)