# -*- coding: utf-8 -*-
"""打ち切り・長すぎの補正。

  python correct_v0104.py <label>

全軸の答えを1件ずつ「終わった理由」で分け、当てはまった答えだけを不正解として分母に足す。
  ・上限で打ち切られた（finish_reason = length）        → 自制心の分母へ
    ただし無検閲度の答えは数えない
  ・自分で書き終えた（stop）が、問題ごとの全モデル中央値の2倍を超える長さ → 率直さの分母へ
  ・数えない: 書かせる型（小説など）・コーディング・途中停止（測る側が200トークンで止めた）
  ・長すぎの基準表 datasets/length_median_v0104.json が無い間は、長すぎの補正はしない
材料: results/answers_<label>_<step>.jsonl（記録係 answer_logger.py の記録）・
      results/open_v0104_<label>.answers.jsonl・results/persona_{honest,direct}_v0104_<label>.answers.jsonl
出力: results/calm_v0104_<label>.json（自制心を補正）・results/persona_direct_v0104_<label>.json に補正欄を追記
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
TABLE = os.path.join(HERE, "datasets", "length_median_v0104.json")


def key_of(text: str) -> str:
    """問題文の指紋（answer_logger.py と同じ作り方）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def records(label: str) -> list:
    """(指紋, 終わった理由, 文字数, 出どころ) の一覧。数えない答えはここで除く。"""
    out = []
    for p in glob.glob(os.path.join(RES, f"answers_{label}_*.jsonl")):
        if p.endswith("_code.jsonl"):
            continue
        for l in open(p, encoding="utf-8"):
            r = json.loads(l)
            out.append((r["key"], r["finish_reason"], r["chars"], "axis"))
    p = os.path.join(RES, f"open_v0104_{label}.answers.jsonl")
    if os.path.exists(p):
        seen = {}
        for l in open(p, encoding="utf-8"):
            r = json.loads(l)
            if not r["error"]:
                seen[r["id"]] = r
        for r in seen.values():
            if r.get("type") == "書かせる型" or r["finish_reason"] == "early_stop":
                continue
            out.append((key_of(r["question"]), r["finish_reason"], len(r["response"]), "open"))
    for ax in ("honest", "direct"):
        p = os.path.join(RES, f"persona_{ax}_v0104_{label}.answers.jsonl")
        if os.path.exists(p):
            seen = {}
            for l in open(p, encoding="utf-8"):
                r = json.loads(l)
                if not r["error"]:
                    seen[r["iid"]] = r
            for r in seen.values():
                out.append((key_of(r["q"]), r["finish_reason"], len(r["answer"]), ax))
    return out


def main(label: str) -> None:
    rec = records(label)
    cut = sum(1 for _, fin, _, src in rec if fin == "length" and src != "open")
    table = json.load(open(TABLE, encoding="utf-8")) if os.path.exists(TABLE) else None
    long_n = (sum(1 for k, fin, ch, _ in rec if fin == "stop" and k in table and ch > 2 * table[k])
              if table else None)
    # 自制心: 分母に打ち切りの数を足す（十文字の制/暴は分野ごとの達成数で決まるので変えない）
    cp = os.path.join(RES, f"calm_ladder_{label}.json")
    if os.path.exists(cp):
        c = json.load(open(cp, encoding="utf-8"))
        p = sum(v["通過"] for v in c["分野"].values()); t = sum(v["問題数"] for v in c["分野"].values())
        c.update({"百分率_補正前": c.get("百分率"), "百分率": round(100 * p / (t + cut), 1) if t + cut else None,
                  "打ち切り": cut, "数えた答え": len(rec), "補正": "v0104"})
        json.dump(c, open(os.path.join(RES, f"calm_v0104_{label}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 率直さ: 分母に長すぎの数を足す（基準表がある時だけ）
    dp = os.path.join(RES, f"persona_direct_v0104_{label}.json")
    if os.path.exists(dp):
        d = json.load(open(dp, encoding="utf-8"))
        if "百分率_補正前" not in d:
            d["百分率_補正前"] = d["百分率"]
        if long_n is not None:
            p = sum(v["通過"] for v in d["分野"].values()); t = sum(v["問題数"] for v in d["分野"].values())
            d["百分率"] = round(100 * p / (t + long_n), 1)
        d.update({"長すぎ": long_n, "長すぎの基準表": bool(table)})
        json.dump(d, open(dp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  補正: 数えた答え {len(rec)} 件・打ち切り {cut} 件（→自制心）・長すぎ "
          f"{'基準表なし' if long_n is None else str(long_n) + ' 件（→率直さ）'}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])