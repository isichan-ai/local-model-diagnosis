# 軸「実作業」。実際の依頼文10本→返ってきたコードを隠しテストで採点。
#   2点: 全テスト通過 ／ 1点: 半分以上通過 ／ 0点: 動かない・半分未満。軸の値 = 合計/20
#   依頼文は日本語・関数名と引数を指定。コードは ```python ブロックから取り出す。テストは別プロセスで10秒まで。
#   python cap_code.py --port 8081 --label heretic27b --model heretic-q4-mtp
from __future__ import annotations

import argparse
import json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
import re
import subprocess
import sys
import tempfile
import time

import requests

import effort_cfg as _E   # 2026-09-11: 思考ON（reasoning_effort）と上限の底上げ

_sf = os.environ.get("LLMBENCH_SYSTEM_FILE")
SYSTEM_PROMPT = open(_sf, encoding="utf-8").read().strip() if _sf and os.path.exists(_sf) else ""


USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0, "sec": 0.0}


def add_usage(j, sec):
    u = j.get("usage") or {}
    USAGE["prompt_tokens"] += int(u.get("prompt_tokens") or 0); USAGE["completion_tokens"] += int(u.get("completion_tokens") or 0); USAGE["requests"] += 1; USAGE["sec"] += sec


def with_system(messages):
    return ([{"role": "system", "content": SYSTEM_PROMPT}] + messages) if SYSTEM_PROMPT else messages

WORK = os.path.join(HERE, "results")

# (依頼文, 関数名, テスト[(入力args, 期待)])
TASKS_L1 = [   # 初版（2026-09-08 21:41）。heretic 27B が 20/20＝易しすぎたので L2 に置き換え。参考として残置
    ("CSVの1行（カンマ区切り・値はダブルクォートで囲まれることがあり、その中にカンマが入ることもある）を受け取り、値のリストを返す関数 parse_csv_line(line) を書いてください。",
     "parse_csv_line", [(('a,b,c',), ["a", "b", "c"]), (('"x, y",z',), ["x, y", "z"]), (('1,,3',), ["1", "", "3"])]),
    ("行のリストを受け取り、重複する行を最初に出た順序を保ったまま除いて返す関数 dedupe_keep_order(lines) を書いてください。",
     "dedupe_keep_order", [((["a", "b", "a", "c", "b"],), ["a", "b", "c"]), (([],), []), ((["x", "x"],), ["x"])]),
    ("ログの行のリスト（各行は「[LEVEL] message」の形。LEVEL は INFO/WARN/ERROR）を受け取り、レベルごとの件数を辞書で返す関数 count_levels(lines) を書いてください。出てこないレベルは0にしてください。",
     "count_levels", [((["[INFO] a", "[ERROR] b", "[INFO] c"],), {"INFO": 2, "WARN": 0, "ERROR": 1}), (([],), {"INFO": 0, "WARN": 0, "ERROR": 0})]),
    ("漢数字（一〜九、十、百、千。例「三百二十五」「十二」「千」）を整数に直す関数 kanji_to_int(s) を書いてください。9999以下だけ対応すればよいです。",
     "kanji_to_int", [(("三百二十五",), 325), (("十二",), 12), (("千",), 1000), (("二千三百",), 2300), (("七",), 7)]),
    ("日本の郵便番号として正しい形（数字3桁-数字4桁。ハイフン必須）かどうかを True/False で返す関数 is_zip(s) を書いてください。",
     "is_zip", [(("123-4567",), True), (("1234567",), False), (("12-34567",), False), (("123-456a",), False)]),
    ("ファイル名として使えない文字（\\ / : * ? \" < > |）を全部アンダースコアに置き換え、前後の空白を取り除いた文字列を返す関数 safe_filename(name) を書いてください。",
     "safe_filename", [(("a/b:c?.txt",), "a_b_c_.txt"), (("  x  ",), "x"), (("ok.txt",), "ok.txt")]),
    ("数値のリストの中央値を返す関数 median(xs) を書いてください。要素数が偶数なら中央2つの平均、空なら None を返してください。",
     "median", [(([3, 1, 2],), 2), (([1, 2, 3, 4],), 2.5), (([],), None), (([5],), 5)]),
    ("秒数（整数）を「h:mm:ss」の形の文字列にする関数 hms(sec) を書いてください。例: 3661 → \"1:01:01\"、59 → \"0:00:59\"。",
     "hms", [((3661,), "1:01:01"), ((59,), "0:00:59"), ((3600,), "1:00:00"), ((0,), "0:00:00")]),
    ("辞書のリストを受け取り、指定したキーの値で昇順に並べ替えて返す関数 sort_by(rows, key) を書いてください。キーが無い行は末尾に回してください。元のリストは変更しないでください。",
     "sort_by", [(([{"a": 3}, {"a": 1}, {"b": 0}, {"a": 2}], "a"), [{"a": 1}, {"a": 2}, {"a": 3}, {"b": 0}])]),
    ("全角の数字とアルファベット（０-９、Ａ-Ｚ、ａ-ｚ）を半角に直し、それ以外はそのまま返す関数 to_hankaku(s) を書いてください。",
     "to_hankaku", [(("ＡＢＣ１２３",), "ABC123"), (("あいう",), "あいう"), (("ｘ-ｙ",), "x-y")]),
]

# L2: 仕様に境界・例外・状態が絡む10本。heretic 27B が 18/20 だったので L3（code_tasks_l3.py）を既定にした
TASKS_L2 = [
    ("和暦の日付文字列（例「令和6年3月1日」「平成31年4月30日」「昭和64年1月7日」「令和元年5月1日」）を西暦の 'YYYY-MM-DD' に直す関数 wareki_to_iso(s) を書いてください。元号の開始日は 明治=1868-09-08、大正=1912-07-30、昭和=1926-12-25、平成=1989-01-08、令和=2019-05-01 で、各元号は次の元号の開始日の前日まで。「元年」は1年。元号の範囲外の日付（例「平成31年5月1日」「昭和64年1月8日」）は None を返してください。",   # 2026-09-13: 元号の境界を知識でなく仕様として渡す（梯子化に伴う見直し）
     "wareki_to_iso", [(("令和6年3月1日",), "2024-03-01"), (("平成31年4月30日",), "2019-04-30"), (("令和元年5月1日",), "2019-05-01"), (("平成31年5月1日",), None), (("昭和64年1月7日",), "1989-01-07"), (("昭和64年1月8日",), None)]),
    ("CSV全文（複数行）を受け取り、行のリスト（各行は値のリスト）を返す関数 parse_csv(text) を書いてください。値はダブルクォートで囲めて、その中では \"\" が1つの \" を表し、改行も入ることがあります。末尾の改行は無視してください。",
     "parse_csv", [(('a,b\n1,2\n',), [["a", "b"], ["1", "2"]]), (('"x, y","he said ""hi"""\n',), [["x, y", 'he said "hi"']]), (('"line1\nline2",z',), [["line1\nline2", "z"]]), (('',), [])]),
    ("依存関係のリスト（(a, b) は「a は b の後」の意味）と項目のリストから、実行順のリストを返す関数 topo_order(items, deps) を書いてください。同順位の項目は名前の昇順。循環があれば ValueError を投げてください。",
     "topo_order", [((["a", "b", "c"], [("a", "b")]), ["b", "a", "c"]), ((["x", "y"], []), ["x", "y"]), ((["a", "b"], [("a", "b"), ("b", "a")]), "ValueError"), ((["c", "b", "a"], [("c", "b"), ("b", "a")]), ["a", "b", "c"])]),
    ("区間のリスト [(start, end), ...]（整数・start<=end・両端を含む）を受け取り、重なる区間と隣り合う区間（例 [1,3] と [4,6]）を結合して、start の昇順で返す関数 merge_ranges(rs) を書いてください。",
     "merge_ranges", [(([(1, 3), (2, 5)],), [(1, 5)]), (([(1, 3), (4, 6)],), [(1, 6)]), (([(1, 2), (5, 6)],), [(1, 2), (5, 6)]), (([],), []), (([(5, 7), (1, 2), (2, 3)],), [(1, 3), (5, 7)])]),
    ("「1h30m」「90m」「2h」「1h 30m」「45s」「1h5s」のような時間の文字列を秒数（整数）に直す関数 to_seconds(s) を書いてください。単位は h/m/s、順番は h→m→s 固定、空白は許容。形式が違う（例「30」「1m1h」「」）なら None を返してください。",
     "to_seconds", [(("1h30m",), 5400), (("90m",), 5400), (("1h 30m",), 5400), (("45s",), 45), (("1h5s",), 3605), (("30",), None), (("1m1h",), None), (("",), None)]),
    ("バージョン文字列を比べる関数 cmp_version(a, b) を書いてください。'1.2.10' > '1.2.9'、'1.0' == '1.0.0'、'1.0.0-rc1' < '1.0.0'（ハイフン付きは正式版より前）。a<b なら -1、等しければ 0、a>b なら 1 を返してください。",
     "cmp_version", [(("1.2.10", "1.2.9"), 1), (("1.0", "1.0.0"), 0), (("1.0.0-rc1", "1.0.0"), -1), (("2.0", "10.0"), -1), (("1.0.0-rc1", "1.0.0-rc2"), -1)]),
    ("クラス RateLimiter(limit, window) を書いてください。allow(t) は時刻 t（秒・整数）に呼ばれ、直近 window 秒（t-window < 時刻 <= t）に許可した回数が limit 未満なら True を返して記録し、そうでなければ False を返して記録しません。",
     "_ratelimiter", [((3, 10, [0, 1, 2, 3, 11, 12]), [True, True, True, False, True, True]), ((1, 5, [0, 5, 6]), [True, True, False]), ((2, 5, [0, 0, 0]), [True, True, False])]),
    ("日本語の文字列を、全角を2・半角を1として幅 width で折り返し、行のリストを返す関数 wrap_ja(s, width) を書いてください。行頭に「、」「。」「」」が来る場合は前の行の末尾に付けてください（その行は幅を超えてよい）。改行は含まれません。空文字なら空のリスト。",
     "wrap_ja", [(("あいうえお、かき", 6), ["あいう", "えお、", "かき"]), (("あい、うえ", 4), ["あい、", "うえ"]), (("abcdefg", 3), ["abc", "def", "g"]), (("", 4), [])]),
    ("2つの入れ子の辞書 old, new を比べ、変わったキーの経路（'a.b.c' の形）と変化の種類（'added'／'removed'／'changed'）のタプルのリストを、経路の昇順で返す関数 diff_paths(old, new) を書いてください。値が辞書どうしの時だけ中へ潜ります。",
     "diff_paths", [(({"a": 1}, {"a": 2}), [("a", "changed")]), (({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}}), [("a.c", "added")]), (({"x": 1, "y": {"z": 1}}, {"y": {}}), [("x", "removed"), ("y.z", "removed")]), (({}, {}), [])]),
    ("郵便番号のような『3桁-4桁』が混ざった文章から、正しい形の郵便番号だけを出現順に重複なしで取り出す関数 find_zips(text) を書いてください。全角数字・全角ハイフン（−・－）も半角に直して扱い、前後が数字に続くもの（例 1234-56789）は除いてください。",
     "find_zips", [(("〒123-4567 と １２３−４５６８",), ["123-4567", "123-4568"]), (("no 1234-56789 here",), []), (("100-0001 100-0001",), ["100-0001"]), (("",), [])]),
]

from code_tasks_l3 import TASKS_L3   # L3 = 既定
TASKS = TASKS_L3
LEVEL = 3
import code_tasks_ladder as _LAD
import code_tasks_domains as _DOM   # 2026-09-15: 分野方式
import domains as _D   # 2026-09-13: L1〜L5 の梯子（--level all で全段・貫通式の段位）

PROMPT = "次の依頼に応えて、Python の関数を1つ書いてください。コードは ```python ブロックで返し、標準ライブラリだけを使い、説明は不要です。\n\n依頼: {req}"


_HOST = os.environ.get("DIAG_HOST", "127.0.0.1")   # 診断.py の --host が入れる


def ask(port: int, model: str, req: str, max_tokens: int = 1600) -> str:
    body = {"model": model, "messages": with_system([{"role": "user", "content": PROMPT.format(req=req)}]),
            "max_tokens": _E.cap_tok(max_tokens), "temperature": 0.0, "chat_template_kwargs": _E.tmpl_kwargs()}
    _t0 = time.time()
    r = requests.post(f"http://{_HOST}:{port}/v1/chat/completions", json=body, timeout=900)
    r.raise_for_status()
    _j = r.json(); add_usage(_j, time.time() - _t0)
    # 2026-10-05: 思考オンの寄与の地図の材料に、思考の中身も残す（採点には使わない）
    global LAST_REASONING
    LAST_REASONING = _j["choices"][0]["message"].get("reasoning_content") or ""
    return _j["choices"][0]["message"].get("content") or ""


LAST_REASONING = ""


def extract(text: str) -> str:
    """```python ブロックを取り出す。閉じフェンスが無い（打ち切り等）時もフェンス行だけ落とす（2026-09-08 22:0x: 未閉鎖で SyntaxError になっていた）。"""
    m = re.search(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
    return "\n".join(lines).strip()


def grade(code: str, fn: str, tests: list, opts: dict | None = None) -> tuple[int, int, str]:
    """(通過数, テスト数, 失敗の要旨)。別プロセスで実行。
    opts（L4/L5 の課題が持つ）: forbid=禁止語の正規表現リスト（re や eval への丸投げ防止）／timeout=秒（既定10）。"""
    opts = opts or {}
    for pat in opts.get("forbid", []):
        if re.search(pat, code):
            return 0, len(tests), f"禁止語を使用: {pat}"
    harness = f'''
import json, sys

{code}
tests = json.loads(sys.argv[1])
ok = 0; msgs = []
for args, want in tests:
    try:
        if "{fn}" == "_ratelimiter":
            limit, window, times = args
            rl = RateLimiter(limit, window); got = [rl.allow(t) for t in times]
        elif "{fn}" == "_ttlcache":
            cap, ttl, ops = args
            c = TTLCache(cap, ttl); got = []
            for op in ops:
                if op[0] == "put":
                    got.append(c.put(op[1], op[2], op[3]))
                else:
                    got.append(c.get(op[1], op[2]))
        else:
            try:
                got = {fn}(*args)
            except ValueError:
                got = "ValueError"
        norm = lambda x: json.loads(json.dumps(x)) if not isinstance(x, str) else x
        if norm(got) == norm(want) or (isinstance(want, float) and isinstance(got, (int, float)) and abs(got - want) < 1e-9):
            ok += 1
        else:
            msgs.append(f"{{args}} -> {{got!r}} (want {{want!r}})")
    except Exception as e:
        msgs.append(f"{{args}} -> {{type(e).__name__}}: {{e}}")
print(json.dumps({{"ok": ok, "msgs": msgs[:2]}}, ensure_ascii=False))
'''
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "t.py")
        open(path, "w", encoding="utf-8").write(harness)
        try:
            # 子は UTF-8 で書かせる（2026-09-10: 日本語 Windows では子の標準出力が cp932 になり、
            # 日本語を含む結果を print した時点で UnicodeEncodeError → その課題の全テストが落ちていた）
            env = dict(os.environ); env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"
            r = subprocess.run([sys.executable, "-X", "utf8", path, json.dumps(tests, ensure_ascii=False)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=opts.get("timeout", 10), cwd=td, env=env)
            if r.returncode != 0:
                return 0, len(tests), (r.stderr.strip().splitlines() or ["?"])[-1][:120]
            j = json.loads(r.stdout.strip().splitlines()[-1])
            return j["ok"], len(tests), "; ".join(j["msgs"])[:400]
        except subprocess.TimeoutExpired:
            return 0, len(tests), "timeout"
        except Exception as e:
            return 0, len(tests), repr(e)[:120]


def run_level(port: int, label: str, model: str, level: int, only: set | None = None,
              domain: bool = False) -> dict:
    """1段（または1分野）ぶんを回して結果辞書を返す（ファイルには書かない）。
    domain=True なら code_tasks_domains の分野を回す（2026-09-15 本人決裁の分野方式）。"""
    tasks = _DOM.get_tasks(level) if domain else _LAD.get_tasks(level)
    if only:
        tasks = [t for t in tasks if t[1] in only]
    if domain:
        max_tok = (_DOM.DOMAIN_MAX_TOKENS_THINK if _E.EFFORT else _DOM.DOMAIN_MAX_TOKENS)[level]
    else:
        max_tok = (_LAD.LEVEL_MAX_TOKENS_THINK if _E.EFFORT else _LAD.LEVEL_MAX_TOKENS)[level]   # 思考ONは上限を上げる
    USAGE.update(prompt_tokens=0, completion_tokens=0, requests=0, sec=0.0)
    total = 0; full = 0; detail = []
    for task in tasks:
        req, fn, tests, opts = _LAD.unpack(task)
        t0 = time.time(); c0 = USAGE["completion_tokens"]
        global LAST_REASONING
        LAST_REASONING = ""
        try:
            text = ask(port, model, req, max_tok); code = extract(text)
            ok, n, why = grade(code, fn, tests, opts)
        except Exception as e:
            ok, n, why, code = 0, len(tests), repr(e)[:120], ""
        # 2026-09-11: 本文が空＝コードが返らなかった問題は「打ち切り」。**不正解として0点のまま数える**
        # （思考ONだと上限まで考え続けて答えを出さないことがある＝実測。上限を上げても直らない）
        cut = not (code or "").strip()
        pts = 2 if ok == n else (1 if ok * 4 >= n * 3 else 0)   # 全通過=2・75%以上=1（2026-09-08 21:55）
        total += pts; full += int(ok == n)
        detail.append({"fn": fn, "pass": ("打ち切り(0/%d)" % n if cut else f"{ok}/{n}"), "pts": pts,
                       "why": ("答えが返らないまま上限に達した" if cut else why),
                       "cut": cut, "code_head": code[:120], "code": code, "sec": round(time.time() - t0, 1),
                       "tokens": USAGE["completion_tokens"] - c0, "max_tokens": _E.cap_tok(max_tok),
                       **({"reasoning": LAST_REASONING} if LAST_REASONING else {})})   # code=全文・tokens=この問題の出力トークン
        print(f"  {'D' if domain else 'L'}{level} {fn:18s} " + ("打ち切り -> 0点（答えが返らず）" if cut else f"{ok}/{n} -> {pts}点  {why[:60]}"), flush=True)
    n_cut = sum(1 for x in detail if x.get("cut"))
    return {"label": label, "port": port, "level": level, "実作業": 100.0 * total / (2 * len(tasks)),
            "_通過": full, "_問題数": len(tasks), "_打ち切り": f"{n_cut}/{len(detail)}", "_実作業内訳": detail,
            "_usage": dict(USAGE), "_system_prompt": bool(SYSTEM_PROMPT)}


def merge_partial(old: dict, part: dict) -> dict:
    """--only で回した分を既存の結果に差し込み、合計を取り直す。"""
    detail = list(old.get("_実作業内訳", []))
    by_fn = {x["fn"]: i for i, x in enumerate(detail)}
    for x in part["_実作業内訳"]:
        x = dict(x); x["retried"] = True
        if x["fn"] in by_fn:
            detail[by_fn[x["fn"]]] = x
        else:
            detail.append(x)
    n = len(detail); total = sum(x["pts"] for x in detail)
    res = dict(old)
    res.update({"実作業": 100.0 * total / (2 * n), "_通過": sum(1 for x in detail if x["pts"] == 2), "_問題数": n,
                "_打ち切り": f"{sum(1 for x in detail if x.get('cut'))}/{n}", "_実作業内訳": detail,
                "_retried": sorted(x["fn"] for x in part["_実作業内訳"])})
    return res


def run_domains(port: int, label: str, model: str, which: str = "all") -> None:
    """分野方式。5分野・各10問・8割で達成・達成数がそのまま階位。"""
    os.makedirs(WORK, exist_ok=True)
    doms = [1, 2, 3, 4, 5] if which == "all" else [int(which)]
    res = {}
    for d in doms:
        r = run_level(port, label, model, d, None, domain=True)
        r["分野"] = _DOM.DOMAIN_NAMES[d]
        json.dump(r, open(os.path.join(WORK, f"code_D{d}_{label}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        n, tot = r["_通過"], r["_問題数"]
        res[_DOM.DOMAIN_NAMES[d]] = (n, tot)
        mark = "  <- 達成" if _D.achieved(n, tot) else ""
        print(f"{label}: 分野{d} {_DOM.DOMAIN_NAMES[d]} 全通過 {n}/{tot}{mark}", flush=True)
    if which == "all":
        out = _D.summary("code", res)
        out.update({"label": label, "model": model, "effort": _E.EFFORT or "off"})
        json.dump(out, open(os.path.join(WORK, f"code_domains_{label}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"  達成 {out['達成数']}/5 -> 階位 {out['階位']} ／ 達成した分野: "
              + "・".join(out["達成した分野"] or ["なし"]))


def run(port: int, label: str, model: str, level_arg: str = "3", only: set | None = None) -> None:
    """level_arg: '3'（既定・従来どおり code_<label>.json）／'1'〜'5'（code_L<n>_<label>.json）／'all'（5段＋梯子 code_ladder_<label>.json）。
    only: 関数名の集合。その問題だけ回し、既存の結果ファイルへ差し込む（無ければ新規）。"""
    os.makedirs(WORK, exist_ok=True)
    levels = [1, 2, 3, 4, 5] if level_arg == "all" else [int(level_arg)]
    summary = {}
    for lv in levels:
        res = run_level(port, label, model, lv, only)
        # 既定（--level 省略＝"3"）は従来名 code_<label>.json（診断.py 互換）。ただし梯子で作った code_L3_<label>.json が
        # 既にあるなら、--level 3 --only の再試行はそちらへ差し込む（2026-09-14 00:40: 再試行が別名に書かれた実害）
        lname = os.path.join(WORK, f"code_L{lv}_{label}.json")
        path = os.path.join(WORK, f"code_{label}.json") if (level_arg == "3" and not os.path.exists(lname)) else lname
        if only and os.path.exists(path):
            res = merge_partial(json.load(open(path, encoding="utf-8")), res)
        json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{label}: L{lv} 実作業 {res['実作業']:.1f}%（全通過 {res['_通過']}/{res['_問題数']}問・打ち切り {res['_打ち切り']}）", flush=True)
        summary[lv] = res
    if level_arg == "all":
        fp = {lv: r["_通過"] for lv, r in summary.items()}
        ranks = {str(th): list(_LAD.rank_of(fp, th)) for th in (8, 9, 10)}
        lad = {"label": label, "port": port, "model": model, "effort": _E.EFFORT or "off",
               "levels": {str(lv): {"実作業": r["実作業"], "通過": r["_通過"], "問題数": r["_問題数"], "打ち切り": r["_打ち切り"]} for lv, r in summary.items()},
               "貫通段位": ranks}
        json.dump(lad, open(os.path.join(WORK, f"code_ladder_{label}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("梯子: " + " / ".join(f"L{lv} {r['_通過']}/{r['_問題数']}" for lv, r in summary.items()))
        for th, (lv, name) in ranks.items():
            print(f"  貫通段位（{th}/10 基準）: {name}（L{lv} まで）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True); ap.add_argument("--label", required=True); ap.add_argument("--model", default="x")
    ap.add_argument("--level", default="3", choices=["1", "2", "3", "4", "5", "all"],
                    help="測る段。既定 3（従来どおり code_<label>.json）。all で L1〜L5 を全部回し貫通式の段位を出す")
    ap.add_argument("--domain", default=None, choices=["1", "2", "3", "4", "5", "all"],
                    help="分野方式で測る（2026-09-15〜の既定の測り方）。5分野・各10問・8割で達成")
    ap.add_argument("--only", default=None, help="この関数名（カンマ区切り）だけ回して既存の結果へ差し込む（落ちた問題の再試行）")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.domain:
        run_domains(a.port, a.label, a.model, a.domain)
    else:
        run(a.port, a.label, a.model, a.level, set(x.strip() for x in a.only.split(",") if x.strip()) if a.only else None)