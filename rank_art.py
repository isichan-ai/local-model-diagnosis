# -*- coding: utf-8 -*-
r"""診断書の下に敷く絵（段位×職）の鍵を決め、手元に絵があれば引く（2026-09-10）。

  段位 rank = 実作業の5段 make_report.tier5(v["code"])。未測定なら総合ランク（C/B/A/S/SS）で代用
  職   job  = 型の鍵 make_report.type_key(v) → 32職のローマ字（TYPE_TO_JOB）
  絵         = characters/合成選抜.json→ ComfyUI/output/Compose/…
             配布版の利用者のPCには絵が無いので鍵だけ渡し、サイトが art/<rank>/<job>.jpg を差し込む
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHAR_DIR = os.path.join(HERE, "characters")
COMPOSE_ROOT = r"D:\AI\ComfyUI_windows_portable-v0-21-1\ComfyUI\output"
RANKS = ["rookie", "apprentice", "journeyman", "veteran", "hero"]
RANK_JA = {"rookie": "新人", "apprentice": "見習い", "journeyman": "一人前", "veteran": "熟練", "hero": "英雄"}
TOTAL_RANK = {"C": 0, "B": 1, "A": 2, "S": 3, "SS": 4}

# 型の鍵（5文字）→ (日本語, ローマ字)。characters/jobs_prompt.py の COSTUME から写した（2026-09-10）
TYPE_TO_JOB = {
    "TRAOD": [
        "騎士",
        "knight"
    ],
    "TRAGD": [
        "衛兵",
        "guard"
    ],
    "TRAGP": [
        "執事",
        "butler"
    ],
    "TRAOP": [
        "教官",
        "instructor"
    ],
    "TRWGD": [
        "処刑人",
        "headsman"
    ],
    "TRWGP": [
        "審問官",
        "inquisitor"
    ],
    "TRWOD": [
        "突撃兵",
        "stormtrooper"
    ],
    "TRWOP": [
        "伝道師",
        "preacher"
    ],
    "TFAGD": [
        "刀鍛冶",
        "swordsmith"
    ],
    "TFAGP": [
        "隠者",
        "hermit"
    ],
    "TFAOD": [
        "冒険者",
        "adventurer"
    ],
    "TFAOP": [
        "語り部",
        "bard"
    ],
    "TFWGD": [
        "野伏",
        "ranger"
    ],
    "TFWGP": [
        "山伏",
        "yamabushi"
    ],
    "TFWOD": [
        "狂戦士",
        "berserker"
    ],
    "TFWOP": [
        "炎術士",
        "pyromancer"
    ],
    "IRAGD": [
        "検閲官",
        "censor"
    ],
    "IRAGP": [
        "官僚",
        "bureaucrat"
    ],
    "IRAOD": [
        "代書人",
        "scrivener"
    ],
    "IRAOP": [
        "廷臣",
        "courtier"
    ],
    "IRWGD": [
        "密偵",
        "spy"
    ],
    "IRWGP": [
        "陰謀家",
        "schemer"
    ],
    "IRWOD": [
        "触れ役",
        "crier"
    ],
    "IRWOP": [
        "扇動家",
        "agitator"
    ],
    "IFAGD": [
        "密輸人",
        "smuggler"
    ],
    "IFAGP": [
        "占い師",
        "fortuneteller"
    ],
    "IFAOD": [
        "盗賊",
        "thief"
    ],
    "IFAOP": [
        "商人",
        "merchant"
    ],
    "IFWGD": [
        "詐欺師",
        "swindler"
    ],
    "IFWGP": [
        "教祖",
        "cultleader"
    ],
    "IFWOD": [
        "山師",
        "prospector"
    ],
    "IFWOP": [
        "道化",
        "jester"
    ]
}

_table = None


def table() -> dict:
    global _table
    if _table is None:
        p = os.path.join(CHAR_DIR, "合成選抜.json")
        _table = {}
        if os.path.exists(p):
            for it in json.load(open(p, encoding="utf-8"))["items"]:
                _table[(it["rank"], it["job"])] = os.path.join(COMPOSE_ROOT, it["file"].replace("/", os.sep))
    return _table


def tier(v: dict, total_rank: str | None = None, d: dict | None = None) -> int:
    """実作業の段位。

    2026-09-18: 二つ名の位は分野方式（title_hit）で決まるのに、ここだけ古い
    百分率の閾値（tier5）を使っていて、code=50.0 で「新人」と「見習い」に割れた。
    d があれば二つ名と同じ決め方に揃える。d の無い呼び出しは今までどおり。
    """
    import make_report as M
    if d is not None:
        hit = M.title_hit("code", d)
        if hit is not None:
            return min(hit, len(RANKS) - 1)
    x = v.get("code")
    if x is not None:
        return M.tier5(x)
    return TOTAL_RANK.get(total_rank or "", 2)


def art_key(v: dict, total_rank: str | None = None, d: dict | None = None):
    """(rank, job) の鍵。型が決まらなければ None。

    2026-09-17: d（診断の結果一式）を受け取るようにした。二つ名は分野方式の漢字で
    職を決めるのに、ここだけ古い百分率の閾値で決めていて食い違っていた。
    """
    import make_report as M
    ent = TYPE_TO_JOB.get(M.type_key(v, d))
    if not ent:
        return None
    return RANKS[tier(v, total_rank, d)], ent[1]


def art_path(v: dict, total_rank: str | None = None, d: dict | None = None):
    """段位×職の絵のパス（手元にある時だけ）。無ければ None。"""
    k = art_key(v, total_rank, d)
    if not k:
        return None
    p = table().get(k)
    return p if p and os.path.exists(p) else None