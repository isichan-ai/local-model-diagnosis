# -*- coding: utf-8 -*-
# 無検閲の答えのループ数（12字以上の同じ断片が1つの答えに4回以上）。本文は表示しない・件数だけ
import json, os, sys
_H = os.path.dirname(os.path.abspath(__file__))   # 2026-10-05: 配布版（llm-bench 直下に置く）でも動くよう相対に
R = os.path.join(_H, "results") if os.path.isdir(os.path.join(_H, "results")) else os.path.join(_H, "..", "results")


def text_of(d):
    for k in ("response", "answer", "content", "text", "out"):
        if isinstance(d.get(k), str):
            return d[k]
    return ""


def looped(t, n=12, k=4):
    seen = {}
    i = 0
    while i + n <= len(t):
        w = t[i:i + n]
        if w.strip():
            last, c = seen.get(w, (-n, 0))
            if i - last >= n:          # 重ならない出現だけ数える
                seen[w] = (i, c + 1)
                if c + 1 >= k:
                    return True
        i += 1
    return False


if __name__ == "__main__":   # 10-04 03:30: import された時に呼び出し元の引数を読まない（run_r8bc r8d2 で落ちた）
    for lab in sys.argv[1:]:
        f = os.path.join(R, f"open_v0104_{lab}.answers.jsonl")
        rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        if rows and lab == sys.argv[1]:
            print("keys:", list(rows[0].keys()))
        n = sum(looped(text_of(d)) for d in rows)
        ln = sum(1 for d in rows if d.get("finish_reason") == "length")
        print(f"{lab}: ループ {n}/{len(rows)}  上限で打ち切り {ln}")