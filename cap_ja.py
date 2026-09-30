# 文章・プロンプト
# 旧: 日本語の質・拡張版。
#
# なぜ足すか: 従来の「日本語の質」は英文3本を要約させ、字数・指定語・禁止語・漢数字・文体を見るだけで、
# 6モデルとも 85.7〜90.5 に固まって識別できなかった（2026-09-09 実測）。落ちるのはほぼ「字数」だけで、
# 実態は「日本語での指示遵守」であり、遵守度の軸と重なっていた。
#
# だからといって他の人がしないわけではない」
# → 実用寄りの2つ（語彙表記・敬語）に加えて、古文・文語も入れる。
#
# 設計の原則:
#   - **全問が番号選択**。採点は出力から最初に現れる有効な番号を拾うだけで、LLM に採点させない
#   - 現代語の言い換えで正解できる問いを避ける（古文は現代語と意味がずれる語だけを使う）
#   - 従来の要約課題は **cap_l3.py 側に無改変で残す**。この結果は別ファイルに書き、make_report で合算する
#
# 使い方:
#   python cap_ja.py --port 8081 --label heretic27b --model heretic-q4-mtp
#   python cap_ja.py --report
from __future__ import annotations

import argparse
import domains as _D
import io
import json
import os
import re
import sys
import time

import requests

import effort_cfg as _E   # 2026-09-11: 思考ON（reasoning_effort）と上限の底上げ

_HOST = os.environ.get("DIAG_HOST", "127.0.0.1")   # 診断.py の --host が入れる

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "results")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# (問い, 選択肢, 正解の番号) — 正解は1始まり
SECTIONS = [
 # 各分野10問に揃えた。
 # 外した問題は、約100モデルの実測で正解率が高すぎて差がつかなかったもの（括弧内＝正解率）。
 ("文学・小説", [          # 外した: 「雪国」の書き出し(97%)・『羅生門』の作者(97%)
  ("『吾輩は猫である』の作者は（　）。", ["夏目漱石", "森鷗外"], 1),
  ("『人間失格』の書き出しは「（　）」。", ["恥の多い生涯を送って来ました", "私はその人を常に先生と呼んでいた"], 1),
  ("『源氏物語』の作者は（　）。", ["紫式部", "清少納言"], 1),
  ("『枕草子』の書き出しは「（　）」。", ["春はあけぼの", "ゆく河の流れは絶えずして"], 1),
  ("「ゆく河の流れは絶えずして、しかも、もとの水にあらず。」で始まるのは（　）。", ["方丈記", "徒然草"], 1),
  ("『銀河鉄道の夜』の作者は（　）。", ["宮沢賢治", "島崎藤村"], 1),
  ("言文一致体で書かれた最初期の小説とされるのは（　）。", ["浮雲", "金色夜叉"], 1),
  ("『細雪』の作者は（　）。", ["谷崎潤一郎", "川端康成"], 1),
  ("「私はその人を常に先生と呼んでいた。」で始まるのは（　）。", ["こころ", "門"], 1),
  ("俳句の「五・七・五」に季語を入れない形を（　）という。", ["無季俳句", "自由律俳句"], 1),
 ]),
 ("語彙・敬語", [          # 外した: 社長の発言(97%)・ご存じですか(97%)・伺います(93%)・檄を飛ばす(88%)・ご覧ください(82%)
  ("「（　）」が正しい言い方。", ["的を得る", "的を射る"], 2),
  ("「他人事」の本来の読みは（　）。", ["たにんごと", "ひとごと"], 2),
  ("「圧巻」の本来の意味は（　）。", ["最も優れた部分", "非常に迫力がある"], 1),
  ("「情けは人の為ならず」の本来の意味は（　）。",
   ["人に情けをかければ巡って自分に返る", "情けをかけるとその人のためにならない"], 1),
  ("「役不足」の本来の意味は（　）。", ["力量に対して役目が軽すぎる", "力量が役目に足りない"], 1),
  ("「なし崩し」の本来の意味は（　）。", ["少しずつ片付ける", "うやむやにする"], 1),
  ("「敷居が高い」の本来の意味は（　）。", ["不義理があって訪ねにくい", "高級すぎて入りにくい"], 1),
  ("取引先に自社の部長の発言を伝えるなら（　）。",
   ["部長が申しております", "部長がおっしゃっています"], 1),
  ("「拝見する」は（　）。", ["謙譲語", "尊敬語"], 1),
  ("「ご覧になられる」は（　）。", ["二重敬語", "正しい尊敬語"], 1),
 ]),
 ("古文・文語", [
  ("古文の「うつくし」の意味は（　）。", ["容姿が美しい", "かわいらしい"], 2),
  ("古文の「やがて」の意味は（　）。", ["そのうち", "そのまま・すぐに"], 2),
  ("古文の「おどろく」の意味は（　）。", ["びっくりする", "目を覚ます"], 2),
  ("古文の「かなし」の意味は（　）。", ["悲しい", "いとしい"], 2),
  ("古文の「すさまじ」の意味は（　）。", ["ものすごい", "興ざめだ"], 2),
  ("古文の「ありがたし」の意味は（　）。", ["感謝したい", "めったにない"], 2),        # 2026-09-28 追加
  ("係り結びで「ぞ・なむ・や・か」を受ける活用形は（　）。", ["連体形", "已然形"], 1),
  ("「死ぬ」の活用の種類は（　）。", ["ナ行変格活用", "四段活用"], 1),
  ("「来（く）」の活用の種類は（　）。", ["カ行変格活用", "サ行変格活用"], 1),
  ("古文の「給ふ」（四段）は（　）。", ["尊敬語", "謙譲語"], 1),
 ]),
 # 漢字は日本語の質とほぼ逆相関した（日本語1位の graft が漢字最下位）。別のものを測っている。
 ("漢字", [
  ("「早急」の本来の読みは（　）。", ["さっきゅう", "そうきゅう"], 1),
  ("「重複」の本来の読みは（　）。", ["ちょうふく", "じゅうふく"], 1),
  ("「間髪をいれず」の「間髪」の読みは（　）。", ["かんはつ", "かんぱつ"], 1),
  ("「独擅場」の本来の読みは（　）。", ["どくせんじょう", "どくだんじょう"], 1),
  ("「凡例」の読みは（　）。", ["はんれい", "ぼんれい"], 1),                      # 2026-09-28 追加
  ("「聞」の部首は（　）。", ["耳", "門"], 1),
  ("「問」の部首は（　）。", ["口", "門"], 1),
  ("「凹」の総画数は（　）。", ["五画", "六画"], 1),
  ("「飛」の総画数は（　）。", ["九画", "十画"], 1),
  ("「必」の書き順で最初に書くのは（　）。", ["中央の点", "左の払い"], 1),
 ]),
]


MODEL_NAME = "x"


def _balance(sections):
    """選択肢の並びを固定の種で入れ替え、正解番号の偏りを潰す。
    2026-09-09 の自己検算で「常に2と答えるだけで69%」だったため。種は固定なので毎回同じ問題になる。"""
    import random
    r = random.Random(SHUFFLE_SEED)
    out = []
    for sec, qs in sections:
        nq = []
        for q, opts, ans in qs:
            correct = opts[ans - 1]
            o = list(opts)
            r.shuffle(o)
            nq.append((q, o, o.index(correct) + 1))
        out.append((sec, nq))
    return out


SHUFFLE_SEED = 20260909
SECTIONS = _balance(SECTIONS)


def prompt(q: str, opts: list[str]) -> str:
    lines = "\n".join(f"{i+1}. {o}" for i, o in enumerate(opts))
    return (f"{q}\n{lines}\n\n"
            f"正しいものを1つ選び、**番号だけ**を出力してください。"
            f"説明・理由・前置きは一切書かないでください。")


def pick(out: str, n_opts: int) -> int | None:
    """出力から最初に現れる有効な選択番号を拾う。1〜n_opts 以外の数字は無視する。"""
    for m in re.finditer(r"[0-9０-９]", out):
        c = m.group(0)
        d = int(c) if c.isascii() else int(chr(ord(c) - 0xFEE0))
        if 1 <= d <= n_opts:
            return d
    return None


def ask(port: int, text: str) -> str:
    body = {"model": MODEL_NAME, "messages": [{"role": "user", "content": text}],
            "max_tokens": _E.cap_tok(40), "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False}}
    r = requests.post(f"http://{_HOST}:{port}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


# ─────────────── 生成プロンプト ───────────────
# 画像（Stable Diffusion のタグ形式）5問・動画（MiniMax H3 の時間区切り形式）5問。題材は健全なものだけ。
# 1問に条件を5つ重ね、**全部守って初めて合格**（前の要約課題は条件が緩く全員 85〜90% に固まった反省）。
# 採点はすべて機械。審判モデルは使わない＝環境で点が変わらない。
PROMPT_TASKS = [
    ("sd", "雨の夜の商店街", ["neon", "wet asphalt", "low angle"], "people"),
    ("sd", "雪山の山小屋", ["snow", "warm light", "wide shot"], "person"),
    ("sd", "朝の喫茶店のテーブル", ["coffee", "morning light", "shallow depth of field"], "text"),
    ("sd", "秋の森の小道", ["autumn leaves", "fog", "golden hour"], "animal"),
    ("sd", "夕暮れの未来都市", ["skyscrapers", "flying cars", "sunset"], "neon"),
    ("h3", "海辺を走る犬", ["dog", "beach", "slow motion"], "person"),
    ("h3", "料理をする手元", ["hands", "knife", "steam"], "face"),
    ("h3", "桜並木を歩く人の後ろ姿", ["cherry blossoms", "back view", "petals"], "face"),
    ("h3", "夜の駅に入ってくる電車", ["train", "platform", "headlights"], "rain"),
    ("h3", "窓辺で伸びをする猫", ["cat", "window", "sunlight"], "dog"),
]
CAMERA = ("pan", "tilt", "dolly", "zoom", "tracking", "crane", "orbit", "push in", "pull back", "handheld", "static")
SEGS = ("0-3s", "3-6s", "6-9s")


def prompt_task_text(kind: str, subject: str, req: list, bad: str) -> str:
    words = "・".join(f"「{w}」" for w in req)
    if kind == "sd":
        return (f"画像生成AI（Stable Diffusion）用のプロンプトを書いてください。題材は「{subject}」です。\n"
                f"条件:\n1. 英語だけで書く\n2. カンマ区切りのタグ形式で、タグを15個以上25個以下にする\n"
                f"3. 次の語をそのまま含める: {words}\n"
                f"4. 最後に「Negative:」で始まる行を1行付け、ネガティブプロンプトを書く\n"
                f"5. 「{bad}」という語はどこにも使わない（ネガティブプロンプトの中も含む）\n"
                f"プロンプト本文だけを出力し、説明は書かないでください。")
    return (f"動画生成AI（MiniMax H3）用のプロンプトを書いてください。題材は「{subject}」の9秒の動画です。\n"
            f"条件:\n1. 英語だけで書く\n2. 「0-3s:」「3-6s:」「6-9s:」の3つの段に分けて、それぞれ行の頭に書く\n"
            f"3. どの段にもカメラの動き（pan・tilt・dolly・zoom・tracking・crane・orbit・push in・pull back・handheld・static のどれか）を1つ以上入れる\n"
            f"4. 次の語をそのまま含める: {words}\n"
            f"5. 「{bad}」という語はどこにも使わず、全体を80語以上150語以下にする\n"
            f"プロンプト本文だけを出力し、説明は書かないでください。")


def _has(text: str, w: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(w.lower()) + r"(?![A-Za-z])", text.lower()) is not None


def check_prompt(kind: str, req: list, bad: str, out: str) -> list:
    """守れなかった条件の一覧を返す（空なら合格）。"""
    t = re.sub(r"```[a-zA-Z]*", "", out).strip()
    miss = []
    if re.search(r"[぀-ヿ㐀-鿿ｦ-ﾟ]", t):
        miss.append("英語だけ")
    if any(not _has(t, w) for w in req):
        miss.append("指定語")
    if _has(t, bad):
        miss.append("禁止語")
    if kind == "sd":
        m = re.search(r"^\s*\**negative[^:：]*[:：]", t, re.I | re.M)
        if not m:
            miss.append("Negative行")
        main = t[:m.start()] if m else t
        tags = [x for x in (y.strip() for y in main.replace("\n", ",").split(",")) if x]
        if not 15 <= len(tags) <= 25:
            miss.append(f"タグ数{len(tags)}")
    else:
        pos = [re.search(r"^\s*\**" + s.replace("-", r"\s*-\s*") + r"\**\s*[:：]", t, re.M) for s in SEGS]
        if not all(pos) or not (pos[0].start() < pos[1].start() < pos[2].start()):
            miss.append("3段")
        else:
            parts = [t[pos[0].start():pos[1].start()], t[pos[1].start():pos[2].start()], t[pos[2].start():]]
            if not all(any(_has(p, c) for c in CAMERA) for p in parts):
                miss.append("各段のカメラ")
        n = len(re.findall(r"[A-Za-z][A-Za-z'-]*", t))
        if not 80 <= n <= 150:
            miss.append(f"語数{n}")
    return miss


def ask_long(port: int, text: str) -> str:
    body = {"model": MODEL_NAME, "messages": [{"role": "user", "content": text}],
            "max_tokens": _E.cap_tok(600), "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False}}
    r = requests.post(f"http://{_HOST}:{port}/v1/chat/completions", json=body, timeout=900)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def run(port: int, label: str) -> None:
    os.makedirs(WORK, exist_ok=True)
    res = {"label": label, "port": port, "model": MODEL_NAME}
    detail, sec_scores = [], {}
    t0 = time.time()
    for sec, qs in SECTIONS:
        ok = 0
        for q, opts, ans in qs:
            try:
                out = ask(port, prompt(q, opts))
                got = pick(out, len(opts))
            except Exception as e:                       # noqa: BLE001
                out, got = repr(e)[:60], None
            good = got == ans
            ok += good
            detail.append({"sec": sec, "q": q[:26], "ans": ans, "got": got,
                           "ok": good, "head": out[:34]})
        sec_scores[sec] = 100.0 * ok / len(qs)
        print(f"  {sec}: {ok}/{len(qs)}  ({sec_scores[sec]:.1f}%)", flush=True)
    # 生成プロンプト（2026-09-28）: 書かせて、条件を全部守ったかを機械で見る
    sec, ok = "生成プロンプト", 0
    for kind, subject, req, bad in PROMPT_TASKS:
        try:
            out = ask_long(port, prompt_task_text(kind, subject, req, bad))
            miss = check_prompt(kind, req, bad, out)
        except Exception as e:                           # noqa: BLE001
            out, miss = repr(e)[:60], ["エラー"]
        ok += not miss
        detail.append({"sec": sec, "q": subject, "ans": "全条件", "got": "・".join(miss) or "全条件",
                       "ok": not miss, "head": out[:60]})
    sec_scores[sec] = 100.0 * ok / len(PROMPT_TASKS)
    print(f"  {sec}: {ok}/{len(PROMPT_TASKS)}  ({sec_scores[sec]:.1f}%)", flush=True)
    res["_節別"] = sec_scores
    # 2026-09-15: 5つの節をそのまま5分野として扱う（達成数がそのまま階位）
    _d = {sec: (sum(1 for x in detail if x["sec"] == sec and x["ok"]), len(qs))
          for sec, qs in SECTIONS}
    _d["生成プロンプト"] = (ok, len(PROMPT_TASKS))
    res.update(_D.summary("ja", _d))
    # 節ごとの平均ではなく**全問の通過率**にする（2026-09-09）。2026-09-28 から各分野10問で揃っている
    total = sum(len(qs) for _, qs in SECTIONS) + len(PROMPT_TASKS)
    res["日本語・拡張"] = 100.0 * sum(1 for x in detail if x["ok"]) / total
    res["_内訳"] = detail
    res["_time"] = round(time.time() - t0, 1)
    path = os.path.join(WORK, f"ja_{label}.json")
    json.dump(res, io.open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"日本語・拡張 {res['日本語・拡張']:.1f}%  ({res['_time']:.0f}s)")
    print("saved:", path)


def report() -> None:
    import glob
    rows = []
    for p in sorted(glob.glob(os.path.join(WORK, "ja_*.json"))):
        d = json.load(io.open(p, encoding="utf-8"))
        rows.append(d)
    if not rows:
        print("結果がありません"); return
    secs = [s for s, _ in SECTIONS] + ["生成プロンプト"]
    print(f"{'モデル':16s}" + "".join(f"{s:>12s}" for s in secs) + f"{'合計':>8s}")
    for d in rows:
        line = f"{d['label']:16s}" + "".join(f"{d['_節別'][s]:11.1f}%" for s in secs)
        print(line + f"{d['日本語・拡張']:7.1f}%")


def main() -> None:
    global MODEL_NAME
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int)
    ap.add_argument("--label")
    ap.add_argument("--model", default="x")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report(); return
    MODEL_NAME = a.model
    run(a.port, a.label)


if __name__ == "__main__":
    main()