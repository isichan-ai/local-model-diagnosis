# -*- coding: utf-8 -*-
"""分野方式の一括測定（2026-09-15 新設）。

10軸のうち**分野方式に作り替えた8本**を1モデルぶん通しで回す。
古い方式の l2/l3/cap_core は回さない（軸の値はもう使わないため）。
ただし診断書の「特徴」の文は l2/l3 の内訳を今も読むので、**古い結果ファイルは消さない**。

  無検閲度  dna_domains.py        （すでに取ってある答えを12分類で採点し直すだけ・モデル不要）
  正直さ    cap_persona_domains.py --axis honest
  率直さ    cap_persona_domains.py --axis direct
  正答率    cap_persona_domains.py --axis rule
  到達率    cap_reach_domains.py
  読解力    cap_read_domains.py
  自制心    cap_calm_ladder.py
  日本語    cap_ja.py
  実作業    cap_code.py --domain all
  画像      cap_vision_ladder.py

使い方:
  python run_domains.py --label heretic27b                （登録済みのモデルを1本）
  python run_domains.py --label heretic27b ornith_q4 ...   （続けて何本でも）
  python run_domains.py --all                             （登録した全部）
  python run_domains.py --label x --only code vision      （一部の軸だけ）
  python run_domains.py --label x --redo                  （済んでいても取り直す）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
PY = sys.executable

# label: (ポート, モデルID, 追加の環境変数)
REG = {
    "heretic27b":         (8081, "heretic-q4-mtp", {}),
    "qwen38official":     (8081, "qwen38-official-q4-mtp", {}),
    "selfmade_c4":        (8081, "selfmade-c4-q4-mtp", {}),
    "ornith_q4":          (8081, "ornith-1.5-35b-abliterated", {}),
    "huihui_q4kxl":       (8081, "huihui-q4kxl-mtp", {}),
    "gemma4_official_q8": (8081, "gemma4-official-q8-mtp", {}),
    "gemma4_hui_q8":      (8081, "gemma4-hui-q8", {}),
    "gemma4_heretic_q6":  (8081, "gemma4-heretic-q6", {}),
    # Muse Glimmer は思考を切れない（どの指定も効かない）。上限を上げないと content が空になる
    "glimmer_q4_think":   (8080, "muse-glimmer-30b-Q4_K_XL", {"LLMBENCH_EFFORT": "medium"}),
    "ornith9b_q4":        (8081, "ornith-9b-q4", {}),
    "ornith9b_q5":        (8081, "ornith-9b-q5", {}),
    "ornith9b_q6":        (8081, "ornith-9b-q6", {}),
    "ornith9b_q8":        (8081, "ornith-9b-q8", {}),
    "ornith9b_bf16":      (8081, "ornith-9b-bf16", {}),
    # 2026-09-16 3060 12GB に載る候補（9B / 12B / 27B / 30B を12GBの枠で比べる）
    "qwen9b_q5":          (8081, "qwen38-9b-q5", {}),
    "qwen9b_hui_q4":      (8081, "qwen38-9b-hui-q4", {}),
    "gemma4_hui_q5":      (8081, "gemma4-hui-q5", {}),
    "huihui27b_iq2s":     (8081, "huihui27b-iq2s", {}),
    "glimmer30b_iq2xxs":  (8081, "glimmer30b-iq2xxs", {}),
    # 2026-09-16 Ornith-1.5-9B 公式Q5 と無検閲3種（手術した人が全員違う）
    "ornith9b_unc_q5":    (8081, "ornith9b-unc-q5", {}),
    "ornith9b_hui_q5":    (8081, "ornith9b-hui-q5", {}),
    "ornith9b_her_q5":    (8081, "ornith9b-her-q5", {}),
    # 2026-09-17 Qwen3.8-35B-A3B Distill（Q4/Q5/Q6）
    "a3b_q4":             (8081, "qwen38-a3b-q4", {}),
    "a3b_q5":             (8081, "qwen38-a3b-q5", {}),
    "a3b_q6":             (8081, "qwen38-a3b-q6", {}),
    "q38_27b_bf16":      (8081, "q38-27b-bf16", {}),
    "bonsai2_crack":       (8083, "bonsai2-crack", {}),
    "a3b_agent_iq2m":      (8081, "a3b-agent-iq2m", {}),
    "q38_27b_q2kxl":       (8081, "q38-27b-q2kxl", {}),
    "q38_27b_iq2xxs":      (8081, "q38-27b-iq2xxs", {}),
    "bonsai2_pq2":         (8083, "bonsai2-pq2", {}),   # PQ2_0は llamacpp-prism-b10685 でしか読めない
    "a3b_a_iq2m":          (8081, "a3b-a-all-a10-iq2m", {}),
    "a3b_a_q2k":           (8081, "a3b-a-all-a10-q2k", {}),
    "a3b_a_iq4xs":         (8081, "a3b-a-all-a10-iq4xs", {}),
    "a3b_a_q4km":          (8081, "a3b-a-q4km", {}),
    "q38_27b_q4km":          (8081, "q38-27b-q4km", {}),
    "q38_27b_iq4xs":         (8081, "q38-27b-iq4xs", {}),
    "gemma4_26b_qat":        (8081, "gemma4-26b-qat", {}),
    "gemma4_26b_qat_hui":    (8081, "gemma4-26b-qat-hui", {}),
    "huihui27b_q4ks":        (8081, "huihui-27b-q4ks", {}),
    "gemma4_a4b_q4ks":     (8080, "gemma4-a4b-q4ks", {}),   # 3060機を中継(fwd.py)経由で叩く
    "huihui_q5kxl":          (8081, "huihui-q5kxl-mtp", {}),
    "heretic27b_q6":         (8081, "heretic-q6-mtp", {}),
    "orcarouter_q4":         (8081, "orcarouter-q4-mtp", {}),
    "huihui_q4km":           (8081, "huihui-q4km-mtp", {}),
    "huihui_q4k":            (8081, "huihui-q4k-mtp", {}),
    "gemma_coder_q8":        (8081, "gemma-coder-q8", {}),
    "dsv4_q2k":              (8081, "deepseek-v4-flash-q2k", {}),
    "a3b_b_back_a05":      (8081, "a3b-b-back-a05", {}),
    "a3b_a_all_a10":       (8081, "a3b-a-all-a10", {}),
    "a3b_iq2m":            (8081, "qwen38-a3b-iq2m", {}),
    "a3b_q2k":             (8081, "qwen38-a3b-q2k", {}),
    "a3b_iq3m":            (8081, "qwen38-a3b-iq3m", {}),
    "a3b_q3km":            (8081, "qwen38-a3b-q3km", {}),
    "a3b_iq4xs":           (8081, "qwen38-a3b-iq4xs", {}),
    # BF16 は VRAM に載らないので MoE 層を28枚 DRAM へ（余裕6.4GB・25.0t/s）
    "a3b_bf16":           (8081, "a3b-bf16-m28", {}),
    # 素の Qwen3.5-35B-A3B BF16（蒸留の対照・同じ設定）
    "a35_base_bf16":      (8081, "a35-base-bf16-m28", {}),
    # 2026-09-22 非Qwen系8系統（Mistral/LLM-jp/Granite/GLM/Cohere/Laguna/Nemotron/EXAONE/Meta）
    "min3_14b":                (8081, "min3-14b", {}),
    "min3_14b_abl":            (8081, "min3-14b-abl", {}),
    "llmjp4_8b":               (8081, "llmjp4-8b", {"LLMBENCH_EFFORT": "low"}),
    "llmjp4_33b":              (8081, "llmjp4-33b", {"LLMBENCH_EFFORT": "low"}),
    "granite42_8b":            (8081, "granite42-8b", {}),
    "granite42_8b_her":        (8081, "granite42-8b-her", {}),
    "granite42_30b":           (8081, "granite42-30b", {}),
    "granite42_30b_abl":       (8081, "granite42-30b-abl", {}),
    "glm47_flash":             (8081, "glm47-flash", {}),
    "glm47_flash_hui":         (8081, "glm47-flash-hui", {}),
    "devstral2_24b":           (8081, "devstral2-24b", {}),
    "devstral2_24b_hui":       (8081, "devstral2-24b-hui", {}),
    "exaone45_33b":            (8081, "exaone45-33b", {}),
    "north_mini_code":         (8081, "north-mini-code", {}),
    "laguna_xs21":             (8081, "laguna-xs21", {}),
    "nemo35_30b":              (8081, "nemo35-30b", {}),
    "nemo35_30b_abl":          (8081, "nemo35-30b-abl", {}),
    "glimmer_q4kxl":           (8081, "glimmer-q4kxl", {"LLMBENCH_EFFORT": "medium"}),
    # 2026-09-23 GSQ-RCO（ISTA-DASLabの新量子化）heretic無検閲＋MTP
    "q38_27b_gsq_iq2xs":       (8081, "q38-27b-gsq-iq2xs", {}),
    "mimo9b_q8":       (8081, "mimo9b-q8", {}),
    "mimo9b_abl_q8":       (8081, "mimo9b-abl-q8", {}),
    # 2026-09-25 Bonsai-2 の別の無検閲版（Hikari07jp abliterated）
    "bonsai2_abl":          (8085, "bonsai2-abl", {}),   # prismルーターは8085
    "bonsai2_bb_abl":     (8085, "bonsai2-bb-abl", {}),
    "bonsai2_bb_mtp":     (8085, "bonsai2-bb-mtp", {}),
    "bonsai2_os_her":     (8085, "bonsai2-os-her", {}),
    "bonsai2_os_1bit":     (8085, "bonsai2-os-1bit", {}),
    "bonsai2_bb_1bit":    (8085, "bonsai2-bb-1bit", {}),
}

# (短い名, 出来上がるファイル, スクリプト, 追加の引数)
STEPS = [
    ("open",   "dna_domains_{L}.json",     "dna_domains.py",          ["{L}"]),
    ("honest", "persona_honest_{L}.json",  "cap_persona_domains.py",  ["--axis", "honest"]),
    ("direct", "persona_direct_{L}.json",  "cap_persona_domains.py",  ["--axis", "direct"]),
    ("rule",   "persona_rule_{L}.json",    "cap_persona_domains.py",  ["--axis", "rule"]),
    ("answer", "reach_domains_{L}.json",   "cap_reach_domains.py",    []),
    ("long",   "read_domains_{L}.json",    "cap_read_domains.py",     []),
    ("calm",   "calm_ladder_{L}.json",     "cap_calm_ladder.py",      []),
    ("ja",     "ja_{L}.json",              "cap_ja.py",               []),
    ("code",   "code_domains_{L}.json",    "cap_code.py",             ["--domain", "all"]),
    ("vision", "vision_ladder_{L}.json",   "cap_vision_ladder.py",    []),
]
NO_MODEL = {"open"}          # モデルを呼ばない（採点し直すだけ）


def alive(port: int, model: str, tries: int = 60) -> bool:
    """1問通してからでないと走らせない（ルーターが落ちていると全部1秒で失敗するため）。"""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 4}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                         data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                json.load(r)
            return True
        except Exception as ex:                       # noqa: BLE001
            if i == 0:
                print(f"    （読み込み待ち… {type(ex).__name__}）", flush=True)
            time.sleep(10)
    return False


def done(name: str, label: str, after: float = 0.0) -> bool:
    """出来上がっているか。
    2026-09-15 実害: 測定器が起動直後に落ちても**古い結果ファイル**が残っていると
    「OK」と表示され、古い数字を新しい結果として読んでしまった。
    `after` を渡した時は、**その時刻より新しいファイル**でなければ未完了とする。"""
    p = os.path.join(RES, name.replace("{L}", label))
    if not os.path.exists(p):
        return False
    if after and os.path.getmtime(p) < after:
        return False
    try:
        return json.load(io.open(p, encoding="utf-8")).get("方式") == "分野"
    except Exception:                                 # noqa: BLE001
        return False


def run_one(label: str, only: list, redo: bool) -> None:
    if label not in REG:
        print(f"{label}: 登録が無い（REG に足す）"); return
    port, model, env_extra = REG[label]
    print(f"\n===== {label}  :{port}  {model} =====", flush=True)
    if not alive(port, model):
        print(f"  ルーター :{port} が応えない。start-*.bat を起動してから回す"); return
    log = os.path.join(RES, f"domains_run_{label}.log")
    for short, out, script, extra in STEPS:
        if only and short not in only:
            continue
        if not redo and done(out, label):
            print(f"  [済] {short}", flush=True); continue
        env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_extra)
        cmd = [PY, script] + (extra if short in NO_MODEL else
                              ["--port", str(port), "--label", label, "--model", model] + extra)
        cmd = [c.replace("{L}", label) for c in cmd]
        t0 = time.time()
        print(f"  [走] {short} … ", end="", flush=True)
        with io.open(log, "a", encoding="utf-8") as lf:
            lf.write(f"\n### {short} {time.strftime('%Y-%m-%d %H:%M:%S')}\n{' '.join(cmd)}\n")
            rc = subprocess.call(cmd, cwd=HERE, env=env, stdout=lf, stderr=subprocess.STDOUT)
        ok = done(out, label, after=t0)   # 走らせた後に書かれたファイルだけを成功とみなす
        print(f"{'OK' if ok else ('rc=' + str(rc))}  {time.time() - t0:.0f}s", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", nargs="+", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="+", default=[])
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    labels = list(REG) if a.all else a.label
    if not labels:
        print(__doc__); return
    for lab in labels:
        run_one(lab, a.only, a.redo)
    print("\n終わり")


if __name__ == "__main__":
    main()