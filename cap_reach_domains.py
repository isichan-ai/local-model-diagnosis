# -*- coding: utf-8 -*-
"""到達率（性能）を分野方式で測る（2026-09-15 新設）。

「答えまでたどり着けるか」を、**たどり着けなくなる原因の種類**で5分野に分ける。
正誤（規則どおりに正解したか）は 規/俺 の領域なので見ない。ここで見るのは
**道具を使い切って最後に数字を出せたか**だけ。

  ① 単純な多段     必要な道具を順に呼べば出る
  ② エラー復帰     1回目の呼び出しが必ず失敗する（引数を直せば通る）
  ③ 分岐のある手順 在庫を見て、足りなければ代替品の単価を使う
  ④ 情報の欠落     片方の値が台帳に無く、別の道具から取る必要がある
  ⑤ 長い手順       5品目ぶん呼んで合計する（10回以上の呼び出しが要る）

各分野4問・達成は80%（4問なら4問…では厳しいので3問で達成＝ceil(0.8*4)=4 になるため
**5問**にして4問で達成）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
import random
import re
import sys
import time

import requests

import domains as _D
import effort_cfg as _E

_HOST = os.environ.get("DIAG_HOST", "127.0.0.1")
WORK = os.path.join(HERE, "results")
MODEL = "x"
SEED = 20260915
N = 5
MAX_ROUNDS = 30
USAGE = {"requests": 0, "completion_tokens": 0, "sec": 0.0}
# 2026-09-15: 「分岐のある手順」は12本全部が満点＝分散ゼロだったので「道具の使い分け」に入れ替え
DOMAIN_NAMES = ["単純な多段", "エラー復帰", "道具の使い分け", "情報の欠落", "長い手順"]


def tool(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


TOOLS = [
    tool("get_order", "注文の明細を返す。1回に3件まで。続きは cursor を渡す",
         {"order_id": {"type": "string"}, "cursor": {"type": "string"}}, ["order_id"]),
    tool("get_price", "品番の**税抜**単価を返す。品番は大文字・ハイフン無しで渡す",
         {"sku": {"type": "string"}, "currency": {"type": "string"}}, ["sku"]),
    tool("get_price_incl", "品番の**税込**単価を返す（税抜で計算する時は使わない）",
         {"sku": {"type": "string"}}, ["sku"]),
    tool("get_list_price", "品番の**定価**を返す（実売価格ではない）",
         {"sku": {"type": "string"}}, ["sku"]),
    tool("get_contract_price", "品番の**契約単価**を返す（契約区分の注文に使う）",
         {"sku": {"type": "string"}}, ["sku"]),
    tool("get_campaign_price", "品番の**キャンペーン単価**を返す（キャンペーン区分の注文に使う）",
         {"sku": {"type": "string"}}, ["sku"]),
    tool("get_stock", "品番の在庫数を返す", {"sku": {"type": "string"}}, ["sku"]),
    tool("get_substitute", "品番の代替品を返す", {"sku": {"type": "string"}}, ["sku"]),
    tool("get_warehouse", "品番を扱う倉庫の番号を返す", {"sku": {"type": "string"}}, ["sku"]),
    tool("get_region", "倉庫の地域コードを返す", {"warehouse": {"type": "string"}}, ["warehouse"]),
    tool("get_price_table", "地域の価格表の番号を返す", {"region": {"type": "string"}}, ["region"]),
    tool("lookup_price_table", "価格表から品番の単価を引く",
         {"table": {"type": "string"}, "sku": {"type": "string"}}, ["table", "sku"]),
    tool("get_fx", "通貨の為替レート（1単位あたりの円）を返す", {"currency": {"type": "string"}}, ["currency"]),
    tool("calc", "式を計算する（例 '12*300+5*120'）", {"expr": {"type": "string"}}, ["expr"]),
]


def chat(port: int, messages: list, max_tokens: int = 700) -> dict:
    body = {"model": MODEL, "messages": messages, "tools": TOOLS,
            "max_tokens": _E.cap_tok(max_tokens), "temperature": 0.0,
            "chat_template_kwargs": _E.tmpl_kwargs()}
    t0 = time.time()
    r = requests.post(f"http://{_HOST}:{port}/v1/chat/completions", json=body, timeout=1800)
    r.raise_for_status()
    j = r.json()
    USAGE["requests"] += 1
    USAGE["completion_tokens"] += int((j.get("usage") or {}).get("completion_tokens") or 0)
    USAGE["sec"] += time.time() - t0
    m = j["choices"][0]["message"]
    return {"text": (m.get("content") or "").strip(), "tool_calls": m.get("tool_calls") or [],
            "reasoning": m.get("reasoning_content") or ""}   # 2026-10-05: 寄与の地図の材料に思考の中身も（送り返さない）


def safe_calc(expr: str):
    """四則だけを構文木で評価する。eval は使わない（任意のコードが動くため）。"""
    import ast
    try:
        node = ast.parse(str(expr or ""), mode="eval").body
    except SyntaxError:
        return None
    OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError("使えない式")

    try:
        v = ev(node)
        return int(v) if isinstance(v, float) and v.is_integer() else v
    except Exception:
        return None


def norm_sku(x: str) -> str:
    return str(x or "").upper().replace("-", "").strip()


# 分野ごとの品目数（難易度 0〜4）。分野の中で階段になるのが分野方式の約束
# 2026-09-16: 段4まで全部通ったので天井を上げた
N_ITEMS = {0: (2, 5, 9, 14, 20), 1: (2, 3, 4, 5, 6), 2: (2, 4, 6, 8, 10),
           3: (1, 2, 3, 4, 5), 4: (4, 8, 12, 16, 20)}


def make_task(kind: int, r: random.Random, lv: int = 2) -> dict:
    """1問。lv は**その分野の中での難易度 0〜4**。"""
    oid = f"ORD-{r.randrange(1000, 9999)}"
    lv = max(0, min(4, lv))
    n_items = N_ITEMS[kind][lv]
    items, price, stock, subs, arch, use = [], {}, {}, {}, {}, {}
    for i in range(n_items):
        sku = f"SKU-{r.randrange(100, 999)}-{chr(65 + i)}"
        qty = r.randrange(2, 30)
        items.append({"sku": sku, "qty": qty})
        price[sku] = r.randrange(120, 980)
        stock[sku] = r.randrange(50, 500)

    t = {"kind": kind, "oid": oid, "items": items, "price": price, "stock": stock,
         "subs": subs, "arch": arch, "use_price": use, "err_done": set(),
         "calls": 0, "busy_done": set(), "tax": 1.1, "fx": {}, "chain": {},
         # 2026-09-15: 半分は税込で作らせる。既定の get_price を漫然と呼ぶと間違える形にして、
         # **注記を読んだかどうか**を道具の選び方で測る
         "incl": bool(r.randrange(2)),
         # 2026-09-15: 価格区分。どの道具で単価を引くかがこれで決まる（注文データの注記に出る）
         # 2026-09-16: 情報の欠落（kind 3）は**価格を連鎖でしか引けない**ので、
         # そこに価格区分を重ねると「指定の道具では引けない」矛盾になる。税抜に固定する
         "kubun": ("税抜" if kind == 3
                   else r.choice(["税抜", "税込", "定価", "契約", "キャンペーン"])),
         "lv": lv, "kubun_by_item": {}}

    if kind == 2 and lv >= 3:
        # 難しい段は**品目ごとに価格区分が違う**。注記を1回読んで終わりにできない。
        # さらに段4では **2ページ目以降で区分が変わる**（1ページ目だけ読むと全部外す）
        ks = ["税抜", "税込", "定価", "契約", "キャンペーン"]
        for i2, it in enumerate(items):
            if lv == 4 and i2 >= 3:
                t["kubun_by_item"][it["sku"]] = ks[(i2 * 2) % len(ks)]
            else:
                t["kubun_by_item"][it["sku"]] = r.choice(ks)
    if kind == 1:
        t["err_kinds"] = lv + 1       # 失敗の種類を段ごとに増やす
    if kind == 0 and lv >= 3:
        # 2026-09-16: 明細に**取消行**を混ぜる。数量が負の行は取り消しで、
        # その品番の単価を引く必要はない（引けば不要な往復になる）
        n_void = 1 if lv == 3 else 2
        for i2 in range(min(n_void, len(items))):
            sku = f"SKU-{r.randrange(100, 999)}-V"
            items.append({"sku": sku, "qty": -r.randrange(2, 10), "備考": "取消"})
            price[sku] = r.randrange(120, 980)
            stock[sku] = 0
            t.setdefault("void", set()).add(sku)
    if kind == 4 and lv >= 3:
        # 2026-09-16: **同じ品番が2回出る**。明細の順に素直に引くと重複呼びになる
        dup_n = 1 if lv == 3 else 2
        for i2 in range(min(dup_n, len(items))):
            items.append({"sku": items[i2]["sku"], "qty": r.randrange(2, 12)})
    if kind == 2:                     # 分岐＋紛らわしい3つの道具
        # 2026-09-15 第2版: ①代替品にも在庫が無く**代替の代替**まで辿らせる
        #                   ②在庫が注文数量と**ちょうど同じ**品目を混ぜる（代替に逃げたら誤り）
        s0 = items[0]["sku"]
        stock[s0] = max(1, items[0]["qty"] - r.randrange(1, 5))
        alt1 = f"SKU-{r.randrange(100, 999)}-Y"
        alt2 = f"SKU-{r.randrange(100, 999)}-Z"
        subs[s0] = alt1
        subs[alt1] = alt2
        price[alt1] = r.randrange(120, 980)
        price[alt2] = r.randrange(120, 980)
        stock[alt1] = 0               # 代替品も欠品。もう一段たどる必要がある
        stock[alt2] = 9999
        use[s0] = price[alt2]
        if len(items) > 1:
            s1 = items[1]["sku"]
            stock[s1] = items[1]["qty"]   # ちょうど同じ＝足りている。代替に替えたら間違い
        if lv >= 2 and len(items) > 2:    # 2件目の短在庫は難しい段だけ
            s2 = items[2]["sku"]
            stock[s2] = max(1, items[2]["qty"] - r.randrange(1, 5))
            alt3 = f"SKU-{r.randrange(100, 999)}-W"
            subs[s2] = alt3
            price[alt3] = r.randrange(120, 980)
            stock[alt3] = 9999
            use[s2] = price[alt3]
    if kind == 3:                     # 間接参照の連鎖（品番→倉庫→地域→価格表→単価）
        # 2026-09-15 第2版: 1品目は**価格表が行き止まり**。案内に従って旧価格表へ乗り換える
        for i2, it in enumerate(items):
            wh = f"WH{r.randrange(10, 99)}"
            rg = f"RG{r.randrange(10, 99)}"
            tb = f"TBL{r.randrange(100, 999)}"
            t["chain"][it["sku"]] = {"wh": wh, "rg": rg, "tb": tb}
            t.setdefault("wh2rg", {})[wh] = rg
            t.setdefault("rg2tb", {})[rg] = tb
            if i2 < max(0, lv - 2):        # 段3で1件、段4で2件が行き止まり
                old_tb = f"TBL{r.randrange(100, 999)}-OLD"
                t.setdefault("moved", {})[(tb, it["sku"])] = old_tb
                t.setdefault("tbl", {})[(old_tb, it["sku"])] = price[it["sku"]]
            else:
                t.setdefault("tbl", {})[(tb, it["sku"])] = price[it["sku"]]
            # 参考値の計算は use を先に見る。移管した品番は新しい表に載っていないので、
            # ここで入れておかないと KeyError になる（2026-09-15 実害）
            use[it["sku"]] = price[it["sku"]]
        t["price"] = {}               # 直接は引けない
    if kind == 4:                     # 長い手順: 代替とドル建ての件数は段で増える
        n_alt = 1 if lv <= 1 else 2
        n_usd = 0 if lv == 0 else (1 if lv <= 2 else 2)
        for s0 in [items[i]["sku"] for i in range(0, min(n_alt * 3, len(items)), 3)][:n_alt]:
            stock[s0] = 0
            alt = f"SKU-{r.randrange(100, 999)}-Z"
            subs[s0] = alt
            price[alt] = r.randrange(120, 980)
            stock[alt] = 9999
            use[s0] = price[alt]
        rate = r.choice([140, 150, 160])
        t["fx"]["USD"] = rate
        for s0 in [items[i]["sku"] for i in range(len(items) - 1, 0, -2)][:n_usd]:
            if s0 in use:
                continue
            usd = r.randrange(2, 9)
            t.setdefault("usd", {})[s0] = usd
            use[s0] = usd * rate

    def unit(sku: str) -> int:
        """参考値のための単価。**dict.get の第2引数は必ず評価される**ので、
        場合分けは関数にして遅らせる（2026-09-15 実害: 移管した品番で KeyError）。"""
        if sku in use:
            return use[sku]
        if kind == 3:
            c = t["chain"][sku]
            v = t["tbl"].get((c["tb"], sku))
            if v is None:                       # 行き止まり＝旧価格表にある
                old_tb = t.get("moved", {}).get((c["tb"], sku))
                v = t["tbl"].get((old_tb, sku), 0)
            return v
        return t["price"].get(sku, 0)

    base = sum(it["qty"] * unit(it["sku"]) for it in items
               if it["sku"] not in t.get("void", set()))
    # 税込の回は各単価を税込に直してから掛けるので、正解は単純な1.1倍にはならない。
    # ただしこの軸では正誤を合否に使わないので、参考値として持つだけ
    t["answer"] = base
    return t


def serve(name: str, a: dict, t: dict) -> str:
    raw = str(a.get("sku", "")).strip()
    sku = raw
    t["calls"] = t.get("calls", 0) + 1
    # 共通: 5回に1回は混雑（同じ引数で呼び直せば通る）
    if t["calls"] % 5 == 0 and t["calls"] not in t["busy_done"]:
        t["busy_done"].add(t["calls"])
        return "error: サーバーが混雑しています。同じ引数でもう一度呼んでください。"

    if name == "get_order":
        if str(a.get("order_id", "")).strip() != t["oid"]:
            return "error: unknown order_id"
        cur = str(a.get("cursor", "") or "0")
        try:
            i = int(cur)
        except ValueError:
            return "error: cursor の形が違います（前回の next_cursor をそのまま渡してください）"
        page = t["items"][i:i + 3]
        nxt = i + 3
        # 2026-09-15: 依頼文から外した「税抜」をここへ移した。道具選びの手掛かりはこの1行だけ
        if t.get("kubun_by_item"):
            body = {"items": [dict(x, 価格区分=t["kubun_by_item"][x["sku"]]) for x in page],
                    "見積条件": "品目ごとの価格区分に従って単価を引くこと"}
        else:
            body = {"items": page, "価格区分": t["kubun"],
                    "見積条件": f"この注文の見積は**{t['kubun']}**の単価で作成すること"}
        if nxt < len(t["items"]):
            body["next_cursor"] = str(nxt)
            body["note"] = "続きがあります。next_cursor を cursor に渡して呼び直してください。"
        return json.dumps(body, ensure_ascii=False)

    if t["kind"] == 3 and name in ("get_price_incl", "get_list_price",
                                   "get_contract_price", "get_campaign_price"):
        # 2026-09-16: 連鎖でしか引けない注文で別の価格道具を呼んだら、進む道を教える
        return json.dumps({"price": None,
                           "note": "この注文の単価は地域別価格表にのみ存在します。"
                                   "get_warehouse から順に辿ってください。"}, ensure_ascii=False)
    if name == "get_price":
        # 引っかけ: 品番は大文字・ハイフン無しでないと通らない（kind 1）
        if t["kind"] == 1 and raw != norm_sku(raw):
            return ('error: 品番の形式が違います。大文字・ハイフン無しに正規化して渡してください'
                    f'（例 {norm_sku(raw)}）')
        key = raw if raw in t["price"] else next((k for k in t["price"] if norm_sku(k) == norm_sku(raw)), None)
        if t["kind"] == 1 and raw not in t["err_done"]:
            t["err_done"].add(raw)
            return 'error: currency is required (use "JPY")'
        if t["kind"] == 4 and key and key in t.get("usd", {}):
            return json.dumps({"sku": key, "price": t["usd"][key], "currency": "USD"}, ensure_ascii=False)
        if key:
            return str(t["price"][key])
        if t["kind"] == 3:
            return json.dumps({"sku": raw, "price": None,
                               "note": "この品番は地域別価格です。get_warehouse から順に辿ってください。"},
                              ensure_ascii=False)
        if raw in t["arch"]:
            return json.dumps({"sku": raw, "price": None,
                               "note": "この品番は移管済みです。"}, ensure_ascii=False)
        return "error: unknown sku"

    if name == "get_contract_price":
        key = next((k for k in t["price"] if norm_sku(k) == norm_sku(raw)), None)
        return str(round(t["price"][key] * 0.85)) if key else "error: unknown sku"
    if name == "get_campaign_price":
        key = next((k for k in t["price"] if norm_sku(k) == norm_sku(raw)), None)
        return str(round(t["price"][key] * 0.7)) if key else "error: unknown sku"
    if name == "get_price_incl":
        key = next((k for k in t["price"] if norm_sku(k) == norm_sku(raw)), None)
        return str(round(t["price"][key] * t["tax"])) if key else "error: unknown sku"
    if name == "get_list_price":
        key = next((k for k in t["price"] if norm_sku(k) == norm_sku(raw)), None)
        return str(round(t["price"][key] * 1.4)) if key else "error: unknown sku"
    if name == "get_stock":
        key = next((k for k in t["stock"] if norm_sku(k) == norm_sku(raw)), None)
        return json.dumps({"sku": raw, "on_hand": t["stock"].get(key, 0)})
    if name == "get_substitute":
        key = next((k for k in t["subs"] if norm_sku(k) == norm_sku(raw)), None)
        return t["subs"][key] if key else "error: no substitute"
    if name == "get_warehouse":
        c = t["chain"].get(next((k for k in t["chain"] if norm_sku(k) == norm_sku(raw)), ""), None)
        return c["wh"] if c else "error: unknown sku"
    if name == "get_region":
        return t.get("wh2rg", {}).get(str(a.get("warehouse", "")).strip(), "error: unknown warehouse")
    if name == "get_price_table":
        return t.get("rg2tb", {}).get(str(a.get("region", "")).strip(), "error: unknown region")
    if name == "lookup_price_table":
        tb = str(a.get("table", "")).strip()
        k = next((k for k in t["chain"] if norm_sku(k) == norm_sku(raw)), None)
        v = t.get("tbl", {}).get((tb, k))
        if v is not None:
            return str(v)
        moved = t.get("moved", {}).get((tb, k))
        if moved:                      # 行き止まり。乗り換え先だけ教える
            return json.dumps({"price": None,
                               "note": f"この品番は {tb} から {moved} へ移管済み。{moved} を参照のこと。"},
                              ensure_ascii=False)
        return "error: not in this table"
    if name == "get_fx":
        cur = str(a.get("currency", "")).strip().upper()
        return str(t["fx"][cur]) if cur in t["fx"] else "error: unknown currency"
    if name == "calc":
        v = safe_calc(str(a.get("expr", "")))
        return "error: bad expr" if v is None else str(v)
    return "error: unknown tool"


# 2026-09-15: 「税抜」は依頼文に書かない（注文データの見積条件に移した）。
# 道具の取り違えを測るため、どの単価を使うかは**自分で判断させる**。
PROMPT = {
    0: "注文 {oid} の合計金額（各品目の 数量×単価 の総和）を求めて、最後に数字だけを書いてください。"
       "明細は一度に全部返らないことがあります。",
    1: "注文 {oid} の合計金額（数量×単価 の総和）を求めて、最後に数字だけを書いてください。"
       "道具がエラーを返したら、指示に従って呼び直してください。",
    2: "注文 {oid} の合計金額を求めて、最後に数字だけを書いてください。"
       "在庫が注文数量に足りない品番は、代替品の単価で計算してください。",
    3: "注文 {oid} の合計金額を求めて、最後に数字だけを書いてください。",
    4: "注文 {oid} の合計金額を円で求めて、最後に数字だけを書いてください。"
       "在庫が足りない品番は代替品の単価を使い、外貨建ての単価は円に直してください。",
}


def last_int(s: str):
    ms = re.findall(r"-?\d[\d,]*", s.replace("，", ","))
    return int(ms[-1].replace(",", "")) if ms else None


# 価格区分ごとの「正しい道具」。それ以外の価格道具を呼んだら取り違え（2026-09-15）
PRICE_TOOL = {"税抜": "get_price", "税込": "get_price_incl", "定価": "get_list_price",
              "契約": "get_contract_price", "キャンペーン": "get_campaign_price"}


def wasted_on_void(log: list, t: dict) -> int:
    """取り消された品番の単価を引いた回数（2026-09-16）。不要な往復として数える。"""
    void = t.get("void") or set()
    if not void:
        return 0
    n = 0
    for name, key, _busy in log:
        if name.startswith("get_") and "price" in name:
            for v in void:
                if v.replace("-", "").upper() in key.replace("-", "").upper():
                    n += 1
    return n


def bad_tools(t: dict) -> tuple:
    """使ってよい価格の道具以外を返す。品目ごとに区分が違う段では、
    その注文に出てくる区分すべてを許す（どの品番にどれを使ったかまでは問わない）。
    2026-09-16: 情報の欠落の分野は連鎖でしか引けないので、取り違えは問わない。"""
    if t["kind"] == 3:
        return ()
    ks = set(t.get("kubun_by_item", {}).values()) or {t["kubun"]}
    right = {PRICE_TOOL[k] for k in ks}
    return tuple(v for v in PRICE_TOOL.values() if v not in right)


def tally(log: list, bad_set: tuple) -> tuple:
    """(重複呼び, 取り違え)。
    重複呼び＝**同じ道具を同じ引数で2度目以降**呼んだ回数。ただし混雑エラーで返された
    呼び出しの直後の呼び直しは数えない（正しい再試行なので罰しない）。"""
    seen, dup, bad = set(), 0, 0
    for name, key, busy in log:
        if name in bad_set:
            bad += 1
        if busy:                      # 混雑で失敗した回。次の同じ呼び出しは再試行なので許す
            seen.discard((name, key))
            continue
        if (name, key) in seen:
            dup += 1
        seen.add((name, key))
    return dup, bad


def run_one(port: int, t: dict) -> dict:
    msgs = [{"role": "user", "content": PROMPT[t["kind"]].format(oid=t["oid"])}]
    used, log = [], []
    # 2026-10-05: 到達率の寄与の地図の材料＝やり取りの全文を記録だけに残す（モデルへ送る msgs は今までどおり）
    transcript = [dict(msgs[0])]

    def done(reached, got):
        dup, bad = tally(log, bad_tools(t))
        dup += wasted_on_void(log, t)   # 取消行の単価を引いたら無駄呼びに数える
        return {"reached": reached, "got": got, "correct": got == t["answer"],
                "steps": len(used), "tools": used, "dup": dup, "bad": bad,
                "incl": t["incl"],
                # 2026-09-15: 重複は**0回**が合格（1回まで許すと誰も落ちなかった）
                "ok": bool(reached) and dup == 0 and bad == 0, "transcript": transcript}

    for _ in range(MAX_ROUNDS):
        o = chat(port, msgs)
        tcs = o["tool_calls"]
        msgs.append({"role": "assistant", "content": o["text"], **({"tool_calls": tcs} if tcs else {})})
        transcript.append({"role": "assistant", "content": o["text"], "reasoning": o["reasoning"],
                           **({"tool_calls": tcs} if tcs else {})})
        if not tcs:
            got = last_int(o["text"])
            return done(got is not None, got)
        for tc in tcs:
            fn = (tc.get("function") or {}).get("name", "")
            used.append(fn)
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except Exception:
                args = {}
            out = serve(fn, args, t)
            log.append((fn, json.dumps(args, ensure_ascii=False, sort_keys=True),
                        out.startswith("error: サーバーが混雑")))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id") or "c0", "content": out})
            transcript.append({"role": "tool", "name": fn, "content": out})
    return done(False, None)


def run(port: int, label: str) -> dict:
    os.makedirs(WORK, exist_ok=True)
    USAGE.update(requests=0, completion_tokens=0, sec=0.0)
    res, detail = {}, []
    for k, name in enumerate(DOMAIN_NAMES):
        r = random.Random(SEED + 100 + k)
        ok_n = 0
        for i in range(N):
            t = make_task(k, r, lv=i)      # 5問＝その分野の難易度 0〜4
            try:
                o = run_one(port, t)
            except Exception as e:
                o = {"reached": False, "got": None, "correct": False, "steps": 0,
                     "tools": [], "dup": 0, "bad": 0, "ok": False, "error": repr(e)[:80]}
            ok_n += int(o["ok"])
            ng = ("" if o["ok"] else
                  ("到達できず" if not o["reached"] else
                   (f"取り違え{o['bad']}回" if o["bad"] else f"重複呼び{o['dup']}回")))
            detail.append({"分野": name, "id": f"{name}-{i + 1}", "ok": o["ok"],
                           "why": f"答え {o['got']}（正解 {t['answer']}・{'合' if o['correct'] else '違'}）"
                                  f" 呼び出し {o['steps']} 重複 {o['dup']} 取り違え {o['bad']}"
                                  + (f" → {ng}" if ng else ""),
                           "tools": o["tools"], "dup": o["dup"], "bad": o["bad"],
                           "steps": o["steps"], "reached": o["reached"],
                           "見積条件": t["kubun"], "transcript": o.get("transcript")})
            print(f"  到達率 {name:8s}#{i + 1} {'○' if o['ok'] else '×'} "
                  f"答え {o['got']} 呼び出し {o['steps']} 重複 {o['dup']} 取り違え {o['bad']}",
                  flush=True)
        res[name] = (ok_n, N)
        print(f"{label}: 到達率 {name} {ok_n}/{N}{'  <- 達成' if _D.achieved(ok_n, N) else ''}", flush=True)
    out = _D.summary("answer", res)
    out.update({"label": label, "model": MODEL, "軸": "到達率", "_内訳": detail,
                "_usage": dict(USAGE), "effort": _E.EFFORT or "off"})
    json.dump(out, io.open(os.path.join(WORK, f"reach_domains_{label}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"  → 到達率: 達成 {out['達成数']}/5 ／ {out['百分率']}% ／ 階位 {out['階位']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default="x")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    MODEL = a.model
    run(a.port, a.label)