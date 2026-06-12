#!/usr/bin/env python3
"""AI気づきツリー MVP

No external dependencies. Run:
  python3 app.py
Then open http://127.0.0.1:8787

This is a local prototype for:
- chat/webhook intake from LINE Harness / Slack / Discord / WhatsApp
- moderation/approval
- public reflection tree page
- simple AI-like tagging stub to be replaced with LLM calls later
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import ssl
import subprocess
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pipeline import common as pipeline_common
from pipeline import export_obsidian

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "kizuki_tree.sqlite3"
STATIC_DEPLOY_DIR = Path(os.environ.get("KIZUKI_STATIC_DEPLOY_DIR", "/Users/noby/product/kizuki-universe-deploy"))


def load_dotenv() -> None:
    if os.environ.get("AI_KIZUKI_SKIP_DOTENV") == "1":
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

THEME_KEYWORDS = {
    "笑い": ["笑", "ラフター", "でたらめ"],
    "ジブリッシュ": ["ジブリッシュ", "意味のない", "でたらめな言葉"],
    "身体感覚": ["身体", "からだ", "呼吸", "胸", "腹", "肩", "緊張", "ゆる", "抜け", "ほどけ"],
    "安心": ["安心", "ほっと", "落ち着", "大丈夫", "あたたか"],
    "感情の解放": ["涙", "感情", "我慢", "解放", "ほどけ", "動かす"],
    "自己受容": ["そのまま", "無理に", "自分", "鎧", "見られ", "大人"],
    "つながり": ["一緒", "ペア", "相手", "家族", "場", "距離", "聞け"],
    "待つ": ["待つ", "待て", "焦", "急"],
    "相手を変えようとする": ["相手", "変え", "コントロール"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    pipeline_common.init_db(DB_PATH)


def infer_tags(text: str) -> list[str]:
    tags: list[str] = []
    for tag, words in THEME_KEYWORDS.items():
        if any(w in text for w in words):
            tags.append(tag)
    return tags or ["未分類"]


MATERIAL_TAG_KEYWORDS = {
    "関係性": ["親子", "夫婦", "仲間", "職場", "関係", "対話"],
    "自己理解": ["自分", "本音", "気づき", "感じ", "思い込み"],
    "問い": ["問い", "質問", "なぜ", "どうしたら", "何が"],
    "実践": ["練習", "実験", "やってみ", "習慣", "一週間"],
}

TAG_QUESTIONS = {
    "待つ": "今日、自分が「待てなかった」場面はどこにありましたか。",
    "安心": "安心が少し戻った瞬間には、何が起きていましたか。",
    "身体感覚": "その気づきは、身体のどこに反応として出ていますか。",
    "相手を変えようとする": "相手を変えようとした奥に、どんな願いがありましたか。",
    "笑い": "笑いが入ったことで、場や自分の感じ方はどう変わりましたか。",
    "関係性": "その気づきは、誰との関係に一番つながっていますか。",
    "自己理解": "自分について、いま少し見え方が変わったことは何ですか。",
    "問い": "今日の話から、自分に持ち帰りたい問いは何ですか。",
    "実践": "次の一週間で、どんな小さな実験をしてみますか。",
}

DEEP_REFLECTION_WORDS = ["気づ", "かもしれ", "本当", "自分", "身体", "胸", "腹", "怖", "安心", "手放", "涙", "変わ"]


def normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").replace("\r", "\n").split())


def clip_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def split_sentences(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？!?])\s*", normalized)
    return [p.strip() for p in parts if p.strip()]


def parse_tags(raw_tags: str | None) -> list[str]:
    try:
        tags = json.loads(raw_tags or "[]")
    except json.JSONDecodeError:
        return ["未分類"]
    if not isinstance(tags, list):
        return ["未分類"]
    clean = [str(t).strip() for t in tags if str(t).strip()]
    return clean or ["未分類"]


def suggested_material_tags(title: str, course: str, raw_text: str) -> list[str]:
    combined = " ".join([title or "", course or "", raw_text or ""])
    tags = [tag for tag in infer_tags(combined) if tag != "未分類"]
    for tag, words in MATERIAL_TAG_KEYWORDS.items():
        if any(w in combined for w in words):
            tags.append(tag)
    deduped = list(dict.fromkeys(tags))
    return deduped[:5] or ["未分類"]


def generate_material_derivatives(title: str, course: str, raw_text: str) -> dict:
    clean_title = (title or "無題の原液").strip()
    clean_course = (course or "").strip()
    clean_text = normalize_text(raw_text)
    sentences = split_sentences(clean_text)
    summary_core = " ".join(sentences[:2]) if sentences else clean_text
    summary = clip_text(summary_core or "本文がまだありません。", 180)
    tags = suggested_material_tags(clean_title, clean_course, clean_text)

    questions: list[str] = []
    for tag in tags:
        if tag in TAG_QUESTIONS:
            questions.append(TAG_QUESTIONS[tag])
    questions.extend(
        [
            "今日の話を聞いて、一番残っている言葉は何ですか。",
            "その気づきは、いまの生活のどの場面につながっていますか。",
            "次の一週間で、ひとつだけ試すなら何をしますか。",
        ]
    )
    questions = list(dict.fromkeys(questions))[:3]

    course_label = f"{clean_course} / " if clean_course else ""
    participant_text_draft = (
        f"【{course_label}{clean_title}】\n"
        f"{clip_text(summary, 90)}\n\n"
        f"よかったら、次の問いにひとこと送ってください。\n"
        f"「{questions[0]}」"
    )
    audio_text_draft = (
        "音声でも大丈夫です。うまくまとめず、いま残っている言葉を30秒だけ話してください。"
        f"入口の問いは「{questions[0]}」です。"
    )
    next_live_question = f"次回ライブの入口にしたい問い：{questions[0]}"

    return {
        "summary": summary,
        "questions": questions,
        "participant_text_draft": participant_text_draft,
        "audio_text_draft": audio_text_draft,
        "next_live_question": next_live_question,
        "tags": tags,
    }


def insert_reflection(source: str, display_name: str, body: str, parent_id: str | None = None, external_user_id: str | None = None, status: str = "pending") -> str:
    rid = uuid.uuid4().hex[:12]
    tags = infer_tags(body)
    star_kind = pipeline_common.infer_star_kind(body)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO reflections
            (id, parent_id, source, external_user_id, display_name, body, tags, status, created_at, space_id, star_kind, visibility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                parent_id,
                source,
                external_user_id,
                display_name,
                body,
                json.dumps(tags, ensure_ascii=False),
                status,
                now_iso(),
                pipeline_common.default_space_id(),
                star_kind,
                "universe",
            ),
        )
    return rid


def insert_media_material(title: str, course: str, raw_text: str) -> str:
    mid = uuid.uuid4().hex[:12]
    derivatives = generate_material_derivatives(title, course, raw_text)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO media_materials
            (id, title, course, raw_text, summary, questions, participant_text_draft, audio_text_draft, next_live_question, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                (title or "無題の原液").strip(),
                (course or "").strip(),
                raw_text or "",
                derivatives["summary"],
                json.dumps(derivatives["questions"], ensure_ascii=False),
                derivatives["participant_text_draft"],
                derivatives["audio_text_draft"],
                derivatives["next_live_question"],
                json.dumps(derivatives["tags"], ensure_ascii=False),
                now_iso(),
            ),
        )
    return mid


def apply_consent(external_user_id: str, consent: str) -> str | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT id FROM reflections
            WHERE external_user_id=? AND status='awaiting_consent'
            ORDER BY created_at DESC LIMIT 1
            """,
            (external_user_id,),
        ).fetchone()
        if not row:
            return None
        rid = row["id"]
        if consent == "name":
            conn.execute("UPDATE reflections SET status='approved', approved_at=? WHERE id=?", (now_iso(), rid))
        elif consent == "anonymous":
            conn.execute("UPDATE reflections SET status='approved', display_name='匿名参加者', approved_at=? WHERE id=?", (now_iso(), rid))
        elif consent == "reject":
            conn.execute("UPDATE reflections SET status='rejected' WHERE id=?", (rid,))
        return rid


def approve(rid: str) -> None:
    with db() as conn:
        conn.execute("UPDATE reflections SET status='approved', approved_at=? WHERE id=?", (now_iso(), rid))


def hide_reflection(rid: str) -> None:
    with db() as conn:
        conn.execute("UPDATE reflections SET status='rejected' WHERE id=? OR parent_id=?", (rid, rid))


def seed_if_empty() -> None:
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM reflections").fetchone()["n"]
    if count:
        return
    a = insert_reflection("seed", "Aさん", "7回目を終えて、待つことが苦手だと気づきました。焦って相手を変えようとしていたかもしれません。", status="approved")
    insert_reflection("seed", "Bさん", "私も同じです。待つ時間に身体がそわそわする感じがあります。", parent_id=a, status="approved")
    insert_reflection("seed", "事務局", "ここはとても大切な気づきです。待つことは何もしないことではなく、自分の内側を整える時間かもしれません。", parent_id=a, status="approved")
    insert_reflection("seed", "Cさん", "笑いの呼吸をすると少し安心できました。", status="pending")


def rows(status: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM reflections"
    params: tuple[str, ...] = ()
    if status:
        q += " WHERE status=?"
        params = (status,)
    q += " ORDER BY created_at ASC"
    with db() as conn:
        return list(conn.execute(q, params))


def media_material_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return list(conn.execute("SELECT * FROM media_materials ORDER BY created_at DESC"))


def esc(s: str | None) -> str:
    return html.escape(s or "")


def row_get(row, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError):
        value = default
    return default if value is None else value


def reflection_depth_score(row) -> int:
    body = row_get(row, "body", "")
    score = min(len(body) // 18, 6)
    score += sum(2 for word in DEEP_REFLECTION_WORDS if word in body)
    if row_get(row, "parent_id"):
        score += 1
    return score


def next_live_question_for_tag(tag: str) -> str:
    if tag in TAG_QUESTIONS:
        return f"次回ライブで扱う問い：{TAG_QUESTIONS[tag]}"
    return f"次回ライブで扱う問い：「{tag}」というテーマは、日常のどこに出ていますか。"


def weekly_insights(reflection_rows) -> dict:
    tag_counter: Counter[str] = Counter()
    tag_summaries: dict[str, list[dict[str, str]]] = {}
    reflections = list(reflection_rows)
    for row in reflections:
        tags = parse_tags(row_get(row, "tags", "[]"))
        for tag in tags:
            tag_counter[tag] += 1
            tag_summaries.setdefault(tag, []).append(
                {
                    "id": row_get(row, "id", ""),
                    "display_name": row_get(row, "display_name", "参加者"),
                    "body": clip_text(row_get(row, "body", ""), 90),
                }
            )

    frequent_themes = [
        {"tag": tag, "count": count, "question": next_live_question_for_tag(tag)}
        for tag, count in tag_counter.most_common()
    ]
    deep_candidates = sorted(
        (
            {
                "id": row_get(row, "id", ""),
                "display_name": row_get(row, "display_name", "参加者"),
                "body": row_get(row, "body", ""),
                "tags": parse_tags(row_get(row, "tags", "[]")),
                "score": reflection_depth_score(row),
            }
            for row in reflections
        ),
        key=lambda item: (-item["score"], -len(item["body"]), item["id"]),
    )[:5]
    next_live_questions = [theme["question"] for theme in frequent_themes[:3]]
    for candidate in deep_candidates[:2]:
        if candidate["body"]:
            next_live_questions.append(f"「{clip_text(candidate['body'], 46)}」という声から、次に何を一緒に見たいですか。")
    next_live_questions = list(dict.fromkeys(next_live_questions))[:5] or ["次回ライブで扱う問い：今週、いちばん残った気づきは何ですか。"]

    return {
        "total": len(reflections),
        "tag_summaries": [
            {"tag": tag, "count": tag_counter[tag], "items": tag_summaries.get(tag, [])[:4]}
            for tag, _ in tag_counter.most_common()
        ],
        "frequent_themes": frequent_themes[:6],
        "deep_candidates": deep_candidates,
        "next_live_questions": next_live_questions,
    }


def cosmos_nodes(reflection_rows) -> list[dict]:
    labels = {row_get(r, "id"): f"{row_get(r, 'display_name', '参加者')}の気づき" for r in reflection_rows}
    nodes: list[dict] = []
    for i, r in enumerate(reflection_rows):
        rid = row_get(r, "id", "")
        parent_id = row_get(r, "parent_id")
        try:
            tags = json.loads(row_get(r, "tags", "[]"))
        except json.JSONDecodeError:
            tags = ["未分類"]
        nodes.append(
            {
                "id": rid,
                "parent_id": parent_id,
                "reply_to": labels.get(parent_id),
                "name": row_get(r, "display_name", "参加者"),
                "body": row_get(r, "body", ""),
                "source": row_get(r, "source", ""),
                "tags": tags or ["未分類"],
                "star_kind": row_get(r, "star_kind", "insight"),
                "visibility": row_get(r, "visibility", "universe"),
                "constellation_id": row_get(r, "constellation_id"),
                "constellation_name": row_get(r, "constellation_name"),
                "lat": ((i * 47) % 100) - 50,
                "lon": ((i * 83) % 260) - 130,
            }
        )
    return nodes


def cosmos_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return list(
            conn.execute(
                """
                SELECT r.*, c.name AS constellation_name
                FROM reflections r
                LEFT JOIN constellations c ON c.id=r.constellation_id
                WHERE r.status='approved' AND r.visibility='universe'
                ORDER BY r.created_at ASC
                """
            )
        )


PAGE_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;600;700'
    '&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap" rel="stylesheet">'
)

PAGE_CSS = """
*{box-sizing:border-box}
:root{
  --deep:#04060f;--panel:#0b1124;--ink:#ece9dd;--dim:#aaa99b;
  --gold:#eec96f;--gold-soft:#ffe9b0;--teal:#9fd9c9;--violet:#bfaef2;
  --line:rgba(255,255,255,.14);
  --serif:'Shippori Mincho','Hiragino Mincho ProN','Yu Mincho','Noto Serif JP',serif;
  --sans:'Zen Kaku Gothic New','Hiragino Sans','Yu Gothic','Noto Sans JP',sans-serif;
}
html{scroll-behavior:smooth}
body{margin:0;font-family:var(--sans);color:var(--ink);background:var(--deep);line-height:1.9;font-size:16px;-webkit-font-smoothing:antialiased}
.sky{position:fixed;inset:0;z-index:-3;background:
  radial-gradient(1100px 700px at 75% -8%,rgba(48,72,150,.32),transparent 60%),
  radial-gradient(900px 650px at 12% 108%,rgba(94,58,140,.22),transparent 65%),
  linear-gradient(178deg,#04060f 0%,#070d22 48%,#04060f 100%)}
.stars{position:fixed;inset:-12%;z-index:-2;pointer-events:none;background-image:
  radial-gradient(rgba(255,255,255,.85) 1px,transparent 1.4px),
  radial-gradient(rgba(255,233,176,.5) 1px,transparent 1.5px);
  background-size:170px 170px,260px 260px;background-position:0 0,40px 70px;
  opacity:.5;animation:twinkle 7s ease-in-out infinite alternate}
.stars2{background-size:90px 90px,150px 150px;opacity:.22;animation:drift 140s linear infinite}
.meteor{position:fixed;top:14%;left:-12%;width:150px;height:2px;z-index:-1;pointer-events:none;border-radius:2px;
  background:linear-gradient(90deg,transparent,var(--gold-soft) 60%,#fff);
  filter:drop-shadow(0 0 6px var(--gold-soft));opacity:0;transform:rotate(16deg);
  animation:meteor 16s linear infinite 4s}
@keyframes twinkle{from{opacity:.32}to{opacity:.6}}
@keyframes drift{to{transform:translate(-110px,-70px)}}
@keyframes meteor{0%,90%{opacity:0;transform:translate(0,0) rotate(16deg)}91%{opacity:.9}100%{opacity:0;transform:translate(120vw,42vh) rotate(16deg)}}
.wrap{max-width:920px;margin:0 auto;padding:26px 22px 60px}
h1,h2,h3,h4{font-family:var(--serif);font-weight:600;letter-spacing:.05em;color:#f6f3e7;line-height:1.5}
h1{font-size:clamp(30px,5vw,44px);margin:20px 0 14px}
h2{font-size:clamp(21px,3.2vw,27px);margin:48px 0 14px}
h2::before{content:'✦';color:var(--gold);margin-right:10px;font-size:.78em}
h3{font-size:18.5px;margin:8px 0}
a{color:var(--teal)}
.topnav{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;flex-wrap:wrap}
.brand{font-family:var(--serif);font-size:19px;letter-spacing:.14em;color:#f6f3e7;text-decoration:none}
.brand b{color:var(--gold)}
.topnav .links{display:flex;gap:4px;flex-wrap:wrap}
.topnav .links a{color:var(--dim);text-decoration:none;padding:8px 14px;border-radius:999px;border:1px solid transparent;font-size:14px;letter-spacing:.06em;transition:.25s}
.topnav .links a:hover{color:var(--gold-soft);border-color:var(--line);background:rgba(255,255,255,.04)}
.adminnav{display:flex;gap:2px;flex-wrap:wrap;align-items:center;margin:6px 0 10px;padding:8px 14px;border:1px dashed rgba(255,255,255,.18);border-radius:14px;font-size:13.5px}
.adminnav-label{color:var(--gold);font-weight:700;letter-spacing:.24em;margin-right:8px;font-size:11.5px}
.adminnav a{color:var(--dim);text-decoration:none;padding:4px 11px;border-radius:999px}
.adminnav a:hover{color:var(--gold-soft);background:rgba(255,255,255,.05)}
.card{background:linear-gradient(168deg,rgba(255,255,255,.085),rgba(255,255,255,.028));border:1px solid var(--line);border-radius:22px;padding:24px 26px;margin:16px 0;backdrop-filter:blur(14px);box-shadow:0 18px 50px rgba(0,0,0,.4),inset 0 0 70px rgba(238,201,111,.04)}
.notice{border-color:rgba(238,201,111,.4);background:linear-gradient(168deg,rgba(238,201,111,.12),rgba(238,201,111,.03))}
.reply{margin-left:22px}
.hero{text-align:center;padding:64px 0 26px}
.kicker{color:var(--gold);letter-spacing:.42em;font-size:12px;font-weight:700;text-transform:uppercase}
.hero h1{font-size:clamp(44px,8vw,78px);margin:16px 0 10px;text-shadow:0 0 60px rgba(238,201,111,.28)}
.tagline{font-family:var(--serif);font-size:clamp(17px,2.6vw,21px);color:var(--gold-soft);letter-spacing:.14em;margin:0 0 18px}
.lead{max-width:640px;margin:0 auto;color:var(--dim);font-size:15.5px}
.cta{display:flex;gap:14px;justify-content:center;margin:34px 0 6px;flex-wrap:wrap}
.stats{display:flex;gap:14px;justify-content:center;margin:34px auto 0;flex-wrap:wrap}
.stat{min-width:150px;padding:16px 22px;text-align:center;margin:0}
.stat b{display:block;font-family:var(--serif);font-size:34px;color:var(--gold);font-weight:600;line-height:1.3}
.stat span{font-size:12.5px;color:var(--dim);letter-spacing:.22em}
.btn,button{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:46px;padding:10px 26px;border-radius:999px;border:1px solid transparent;font-weight:700;font-size:15px;letter-spacing:.06em;text-decoration:none;cursor:pointer;font-family:var(--sans);transition:.25s;background:linear-gradient(135deg,var(--gold),var(--gold-soft));color:#241a05;box-shadow:0 10px 34px rgba(238,201,111,.2)}
.btn:hover,button:hover{transform:translateY(-2px);box-shadow:0 14px 40px rgba(238,201,111,.3)}
.btn.ghost{background:rgba(255,255,255,.05);color:var(--ink);border-color:var(--line);box-shadow:none}
.btn.ghost:hover{border-color:rgba(238,201,111,.5);color:var(--gold-soft)}
.btn.small{min-height:38px;padding:6px 18px;font-size:13.5px}
.tags{margin:10px 0 4px}
.tag{display:inline-block;font-size:12.5px;padding:4px 12px;margin:3px 6px 3px 0;border-radius:999px;background:rgba(159,217,201,.1);color:#b9e3d6;border:1px solid rgba(159,217,201,.22);letter-spacing:.04em}
.star-card header{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.star-dot{width:10px;height:10px;border-radius:50%;background:var(--gold-soft);box-shadow:0 0 12px 2px rgba(255,233,176,.8);animation:pulse 3.4s ease-in-out infinite;flex:none}
@keyframes pulse{50%{box-shadow:0 0 18px 4px rgba(255,233,176,.95)}}
.star-who{font-family:var(--serif);color:var(--gold-soft);font-size:16.5px;letter-spacing:.08em}
.star-kind{margin-left:auto;font-size:11px;color:var(--dim);letter-spacing:.16em;text-transform:uppercase}
.star-body{font-family:var(--serif);font-size:17px;line-height:2.05;margin:8px 0 4px;color:#f1eee2}
.voices{margin:16px 0 12px;padding-left:18px;border-left:2px solid rgba(238,201,111,.28);display:grid;gap:10px}
.voice-who{font-size:13px;color:var(--gold);font-family:var(--serif)}
.voice p{margin:2px 0 0;font-size:14.5px;color:#d8d5c8}
.question-card{text-align:center;padding:34px 28px}
.q-label{color:var(--gold);letter-spacing:.34em;font-size:11.5px;font-weight:700}
.question-card .q{font-family:var(--serif);font-size:clamp(19px,3vw,24px);line-height:2;color:#f6f3e7;margin:12px 0 4px}
.const-card h3{color:var(--gold-soft);margin:0 0 4px}
.const-week{font-size:12px;color:var(--dim);letter-spacing:.14em}
.empty{text-align:center;padding:64px 20px}
.empty .star-dot{margin:0 auto 20px;width:14px;height:14px}
.empty p{font-family:var(--serif);color:var(--dim);letter-spacing:.1em;line-height:2.3;margin:0}
input,textarea{width:100%;font-size:16px;font-family:var(--sans);color:var(--ink);background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.2);border-radius:14px;padding:12px 14px}
input:focus,textarea:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(238,201,111,.18)}
label{font-size:13.5px;color:var(--dim);letter-spacing:.08em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.pre{white-space:pre-wrap;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:14px;padding:14px;font-size:14px}
.small{color:var(--dim);font-size:13px}
details>summary{cursor:pointer;color:var(--gold-soft)}
.cosmos-footer{margin-top:72px;text-align:center;color:var(--dim);font-size:13px;letter-spacing:.08em}
.cosmos-footer a{color:rgba(255,255,255,.32);text-decoration:none;font-size:12px}
:focus-visible{outline:2px solid var(--gold);outline-offset:3px}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none!important;transition:none!important}html{scroll-behavior:auto}}
@media(max-width:640px){.wrap{padding:16px 15px 48px}.card{padding:20px 18px}.stat{min-width:118px;padding:14px 16px}.hero{padding:44px 0 20px}}
"""

PAGE_SHELL = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__｜__UNIVERSE__</title>
__FONTS__
<style>__CSS__</style></head>
<body>
<div class="sky"></div><div class="stars"></div><div class="stars stars2"></div><div class="meteor"></div>
<div class="wrap">
<nav class="topnav"><a class="brand" href="/"><b>✦</b> __UNIVERSE__</a><div class="links"><a href="/">みんなの__STAR__</a><a href="/cosmos">宇宙を旅する</a><a href="/questions">問い</a><a href="/submit">__STAR__を送る</a></div></nav>
__ADMIN_NAV__
<main>__BODY__</main>
<footer class="cosmos-footer"><p>アウトプットした人のおかげで、この宇宙は発展していきます。</p><a href="/admin">管理</a></footer>
</div></body></html>"""


def layout(title: str, body: str, admin: bool = False) -> bytes:
    universe = pipeline_common.worldview_term("universe", "気づきの宇宙")
    star = pipeline_common.worldview_term("star", "星")
    admin_nav = ""
    if admin:
        admin_nav = (
            '<nav class="adminnav"><span class="adminnav-label">管理</span>'
            '<a href="/admin">公開管理</a><a href="/admin/recordings">原液</a>'
            '<a href="/admin/followup-suggestions">応答候補</a><a href="/admin/followups">フォローアップ</a>'
            '<a href="/admin/obsidian-vault">公開Vault</a>'
            '<a href="/factory">教材化</a><a href="/weekly">週次編集</a></nav>'
        )
    page = (
        PAGE_SHELL
        .replace("__TITLE__", esc(title))
        .replace("__FONTS__", PAGE_FONTS)
        .replace("__CSS__", PAGE_CSS)
        .replace("__UNIVERSE__", esc(universe))
        .replace("__STAR__", esc(star))
        .replace("__ADMIN_NAV__", admin_nav)
        .replace("__BODY__", body)
    )
    return page.encode("utf-8")


def public_page() -> bytes:
    universe = pipeline_common.worldview_term("universe", "気づきの宇宙")
    star = pipeline_common.worldview_term("star", "星")
    constellation = pipeline_common.worldview_term("constellation", "星座")
    all_rows = rows("approved")
    by_parent: dict[str | None, list[sqlite3.Row]] = {}
    for r in all_rows:
        by_parent.setdefault(r["parent_id"], []).append(r)
    roots = list(reversed(by_parent.get(None, [])))
    voice_count = len(all_rows) - len(roots)
    with db() as conn:
        consts = list(conn.execute("SELECT * FROM constellations ORDER BY created_at DESC"))

    cards = []
    for r in roots:
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in parse_tags(r["tags"]))
        voices = "".join(
            f'<div class="voice"><span class="voice-who">{esc(c["display_name"])}</span><p>{esc(c["body"])}</p></div>'
            for c in by_parent.get(r["id"], [])
        )
        voices_block = f'<div class="voices">{voices}</div>' if voices else ""
        cards.append(
            f'''<article class="card star-card">
          <header><span class="star-dot"></span><span class="star-who">{esc(r["display_name"])}</span><span class="star-kind">{esc(r["source"])}</span></header>
          <p class="star-body">{esc(r["body"])}</p>
          <div class="tags">{tags}</div>
          {voices_block}
          <a class="btn ghost small" href="/submit?parent_id={esc(r["id"])}">この{esc(star)}に声を寄せる</a>
        </article>'''
        )

    stars_section = "".join(cards) or (
        f'<div class="card empty"><span class="star-dot"></span>'
        f'<p>夜明け前が、いちばん暗い。<br>最初の{esc(star)}を、あなたが灯してください。</p></div>'
    )

    question_section = ""
    if consts:
        latest_q = (consts[0]["generated_question_md"] or "").strip()
        if latest_q:
            question_section = (
                f'<section class="card question-card"><div class="q-label">今週の問い</div>'
                f'<p class="q">{esc(latest_q)}</p>'
                f'<a class="btn ghost small" href="/submit">この問いに{esc(star)}で応える</a></section>'
            )

    const_section = ""
    if consts:
        const_cards = "".join(
            f'<section class="card const-card"><h3>{esc(c["name"])}</h3>'
            f'<div class="const-week">{esc(c["week_of"])} の週</div>'
            f'<p>{esc(c["summary_md"])}</p></section>'
            for c in consts[:3]
        )
        const_section = f'<h2>いま生まれている{esc(constellation)}</h2><div class="grid">{const_cards}</div>'

    body = f'''
    <header class="hero">
      <div class="kicker">Kizuki Universe</div>
      <h1>{esc(universe)}</h1>
      <p class="tagline">あなたの気づきが、{esc(star)}になる。</p>
      <p class="lead">講座で生まれた気づき・感想・問いがこの宇宙にアップされ、ひとつひとつが{esc(star)}として灯ります。{esc(star)}と{esc(star)}はAIによって結ばれて{esc(constellation)}になり、そこから次の問いが生まれていきます。</p>
      <div class="cta"><a class="btn" href="/cosmos">宇宙を旅する</a><a class="btn ghost" href="/questions">問いを見る</a><a class="btn ghost" href="/submit">{esc(star)}を送る</a></div>
      <div class="stats">
        <div class="card stat"><b>{len(roots)}</b><span>{esc(star)}</span></div>
        <div class="card stat"><b>{len(consts)}</b><span>{esc(constellation)}</span></div>
        <div class="card stat"><b>{voice_count}</b><span>寄せられた声</span></div>
      </div>
    </header>
    {question_section}
    {const_section}
    <h2>みんなの{esc(star)}</h2>
    {stars_section}
    '''
    return layout("みんなの" + star, body)


COSMOS_SHELL = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>宇宙を旅する｜__UNIVERSE__</title>
__FONTS__
<style>
*{box-sizing:border-box}
:root{--ink:#ece9dd;--dim:#aaa99b;--gold:#eec96f;--gold-soft:#ffe9b0;--line:rgba(255,255,255,.15);
--serif:'Shippori Mincho','Hiragino Mincho ProN','Yu Mincho',serif;
--sans:'Zen Kaku Gothic New','Hiragino Sans','Yu Gothic',sans-serif}
html,body{height:100%;margin:0;overflow:hidden;font-family:var(--sans);color:var(--ink);background:#04060f}
.sky{position:fixed;inset:0;z-index:-3;background:
 radial-gradient(1100px 700px at 72% -10%,rgba(48,72,150,.36),transparent 60%),
 radial-gradient(900px 650px at 10% 112%,rgba(94,58,140,.26),transparent 65%),
 linear-gradient(178deg,#04060f 0%,#081026 48%,#04060f 100%)}
.stars{position:fixed;inset:-12%;z-index:-2;pointer-events:none;background-image:
 radial-gradient(rgba(255,255,255,.85) 1px,transparent 1.4px),
 radial-gradient(rgba(255,233,176,.5) 1px,transparent 1.5px);
 background-size:170px 170px,260px 260px;background-position:0 0,40px 70px;
 opacity:.5;animation:twinkle 7s ease-in-out infinite alternate}
.stars2{background-size:90px 90px,150px 150px;opacity:.2;animation:drift 150s linear infinite}
@keyframes twinkle{from{opacity:.3}to{opacity:.58}}
@keyframes drift{to{transform:translate(-110px,-70px)}}
#stage{position:fixed;inset:0;width:100vw;height:100vh;display:block;cursor:grab;touch-action:none}
#stage:active{cursor:grabbing}
.glass{background:rgba(10,16,34,.74);border:1px solid var(--line);backdrop-filter:blur(16px) saturate(1.2);border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.hud-brand{position:fixed;top:16px;left:16px;z-index:10;padding:13px 17px;max-width:280px}
.hud-brand a{color:var(--dim);font-size:12.5px;text-decoration:none;letter-spacing:.08em}
.hud-brand a:hover{color:var(--gold-soft)}
.hud-brand h1{font-family:var(--serif);font-weight:600;font-size:21px;margin:5px 0 3px;letter-spacing:.12em;color:#f6f3e7}
.hint{font-size:11px;color:var(--dim);margin:0;letter-spacing:.05em}
.hud-nav{position:fixed;top:16px;right:16px;z-index:10;padding:7px;display:flex;gap:6px}
.bn{display:inline-flex;align-items:center;min-height:40px;padding:6px 16px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.05);color:var(--ink);font-size:13.5px;font-weight:700;text-decoration:none;cursor:pointer;font-family:var(--sans);letter-spacing:.05em;transition:.22s}
.bn:hover{color:var(--gold-soft);border-color:rgba(238,201,111,.5)}
.bn.gold{background:linear-gradient(135deg,var(--gold),var(--gold-soft));color:#241a05;border-color:transparent}
.labels{position:fixed;inset:0;pointer-events:none;z-index:5}
.label{position:absolute;z-index:6;min-width:128px;max-width:200px;padding:10px 13px;border-radius:14px;
 background:rgba(9,14,30,.84);border:1px solid rgba(255,255,255,.18);backdrop-filter:blur(10px);
 color:var(--ink);box-shadow:0 10px 36px rgba(0,0,0,.5),0 0 26px var(--c,transparent);
 transform:translate(-50%,-130%);transition:opacity .2s;pointer-events:auto;cursor:pointer}
.label.hidden{opacity:0;pointer-events:none}
.label b{display:block;color:var(--gold-soft);font-family:var(--serif);font-size:12.5px;letter-spacing:.06em}
.label p{margin:4px 0 6px;font-size:12.5px;line-height:1.6;color:#cfccc0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.label .tag{font-size:10.5px;padding:2px 9px;border-radius:999px;background:rgba(159,217,201,.12);color:#b9e3d6;border:1px solid rgba(159,217,201,.25)}
.label.active{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold),0 16px 50px rgba(0,0,0,.6),0 0 44px var(--c,transparent)}
.dock{position:fixed;z-index:9;left:16px;bottom:16px;max-width:min(420px,calc(100vw - 32px));padding:12px 14px}
.dock h3{margin:0 0 8px;font-family:var(--serif);font-weight:600;color:var(--gold);font-size:12.5px;letter-spacing:.2em}
.filter-status{margin:0 0 10px;color:var(--dim);font-size:12px;letter-spacing:.06em}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{appearance:none;border:1px solid var(--line);background:rgba(255,255,255,.05);color:var(--ink);border-radius:999px;min-height:34px;padding:2px 14px;font-size:12.5px;font-weight:700;cursor:pointer;font-family:var(--sans);transition:.2s;white-space:nowrap;flex:none}
.chip:hover{border-color:rgba(238,201,111,.5)}
.chip.active{background:linear-gradient(135deg,var(--gold),var(--gold-soft));color:#241a05;border-color:transparent}
.zoomers{position:fixed;z-index:9;right:16px;top:50%;transform:translateY(-50%);display:grid;gap:8px;padding:8px}
.zoomers button{appearance:none;width:42px;height:42px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.06);color:var(--ink);font-size:19px;cursor:pointer}
.zoomers button:hover{color:var(--gold-soft);border-color:rgba(238,201,111,.5)}
.detail{position:fixed;z-index:11;right:16px;bottom:16px;width:min(430px,calc(100vw - 32px));padding:20px 22px;opacity:0;transform:translateY(16px);pointer-events:none;transition:.3s}
.detail.show{opacity:1;transform:translateY(0);pointer-events:auto}
.detail .close{position:absolute;top:10px;right:12px;appearance:none;background:none;border:none;color:var(--dim);font-size:18px;cursor:pointer;padding:6px}
.detail .close:hover{color:var(--gold-soft)}
.pill{display:inline-flex;background:linear-gradient(135deg,var(--gold),var(--gold-soft));color:#241a05;border-radius:999px;padding:4px 12px;font-size:11.5px;font-weight:700;letter-spacing:.06em;margin-bottom:10px}
.detail h2{font-family:var(--serif);font-weight:600;font-size:21px;margin:0 0 8px;color:#f6f3e7;letter-spacing:.06em}
.detail .meta{font-size:12px;color:var(--dim);margin:0 0 6px}
.detail .body{font-family:var(--serif);margin:0;color:#efece0;line-height:2;font-size:15px;max-height:34vh;overflow:auto}
.cmd{display:flex;gap:8px;align-items:center;margin-top:14px;background:rgba(0,0,0,.35);border:1px solid var(--line);border-radius:12px;padding:9px 12px;font-size:12px;color:var(--gold-soft);word-break:break-all}
.cmd button{flex:none;appearance:none;border:1px solid rgba(238,201,111,.5);background:none;color:var(--gold-soft);border-radius:999px;padding:4px 12px;font-size:11.5px;font-weight:700;cursor:pointer}
.cmd button:hover{background:rgba(238,201,111,.15)}
:focus-visible{outline:2px solid var(--gold);outline-offset:3px}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
@media(max-width:680px){
 .hud-brand{max-width:190px;padding:10px 13px}.hud-brand h1{font-size:16.5px}.hint{display:none}
 .dock{left:12px;right:74px;bottom:max(12px,env(safe-area-inset-bottom))}
 .chips{flex-wrap:nowrap;overflow-x:auto;padding-bottom:4px}
 .detail{left:12px;right:12px;width:auto;bottom:84px}
 .zoomers{right:12px;top:auto;bottom:max(12px,env(safe-area-inset-bottom));transform:none}
 .label{min-width:104px;max-width:138px}}
</style></head>
<body>
<div class="sky"></div><div class="stars"></div><div class="stars stars2"></div>
<canvas id="stage"></canvas>
<div class="labels" id="labels"></div>
<header class="hud-brand glass"><a href="/">← __UNIVERSE__にもどる</a><h1>宇宙を旅する</h1><p class="hint">ドラッグで回す ・ ホイールでズーム ・ __STAR__を選ぶ</p></header>
<nav class="hud-nav glass"><a class="bn gold" href="/submit">__STAR__を送る</a></nav>
<section class="dock glass"><h3>テーマでたどる</h3><p class="filter-status" id="filterStatus">すべての星を表示中</p><div class="chips" id="chips"><button class="chip active" data-tag="all">すべて</button></div></section>
<div class="zoomers glass"><button id="zin" aria-label="ズームイン">＋</button><button id="zout" aria-label="ズームアウト">−</button></div>
<aside class="detail glass" id="detail" aria-live="polite"></aside>
<script>
const nodes=__DATA__;
const STAR_TERM="__STAR__";
const canvas=document.getElementById('stage'),ctx=canvas.getContext('2d');
const labels=document.getElementById('labels'),detail=document.getElementById('detail'),chips=document.getElementById('chips'),filterStatus=document.getElementById('filterStatus');
const colors=['#ffe9b0','#9fd9c9','#bfaef2','#f2a9b8','#9cc6ff','#f0c869'];
const reduceMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
let W,H,dpr=1,R=270,rotX=-.18,rotY=-.55,zoom=1,drag=false,moved=false,last={x:0,y:0},selected=null,filter='all';
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function color(n){return colors[Math.abs([...n.id].reduce((a,c)=>a+c.charCodeAt(0),0))%colors.length]}
const allTags=[...new Set(nodes.flatMap(n=>n.tags&&n.tags.length?n.tags:['未分類']))];
allTags.forEach(t=>{const b=document.createElement('button');b.className='chip';b.dataset.tag=t;b.textContent=t;b.onclick=()=>setFilter(t,b);chips.appendChild(b)});
document.querySelector('.chip[data-tag="all"]').onclick=function(){setFilter('all',this)};
function setFilter(t,b){filter=t;selected=null;detail.classList.remove('show');document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));b.classList.add('active');const count=nodes.filter(n=>visible(n)).length;filterStatus.textContent=t==='all'?`すべての${STAR_TERM}を表示中（${count}件）`:`${t} の${STAR_TERM}だけを表示中（${count}件）`}
const labelEls=new Map();
nodes.forEach(n=>{
 const el=document.createElement('article');el.className='label';el.style.setProperty('--c',color(n)+'55');
 el.innerHTML=`<b>${escapeHtml(n.name)}</b><p>${escapeHtml(n.body.slice(0,56))}${n.body.length>56?'…':''}</p><span class="tag">${escapeHtml((n.tags&&n.tags[0])||'未分類')}</span>`;
 el.onclick=()=>select(n.id);labels.appendChild(el);labelEls.set(n.id,el)});
function resize(){dpr=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;canvas.width=W*dpr;canvas.height=H*dpr;canvas.style.width=W+'px';canvas.style.height=H+'px';ctx.setTransform(dpr,0,0,dpr,0,0);R=Math.min(W,H)*.32}
resize();addEventListener('resize',resize);
function sphere(n){const lat=(n.lat||0)*Math.PI/180,lon=(n.lon||0)*Math.PI/180;
 let x=Math.cos(lat)*Math.sin(lon),y=Math.sin(lat),z=Math.cos(lat)*Math.cos(lon);
 const cy=Math.cos(rotY),sy=Math.sin(rotY),cx=Math.cos(rotX),sx=Math.sin(rotX);
 const x1=x*cy+z*sy,z1=-x*sy+z*cy,y1=y;
 return{x:x1,y:y1*cx-z1*sx,z:y1*sx+z1*cx}}
function project(p){const per=1.95/(1.95-p.z*.48);return{x:W/2+p.x*R*zoom*per,y:H/2+14-p.y*R*zoom*per,scale:per,z:p.z}}
function visible(n){return filter==='all'||(n.tags||[]).includes(filter)}
filterStatus.textContent=`すべての${STAR_TERM}を表示中（${nodes.length}件）`;
function nodeBy(id){return nodes.find(n=>n.id===id)}
const constellationGroups=[...nodes.reduce((m,n)=>{if(n.constellation_id){if(!m.has(n.constellation_id))m.set(n.constellation_id,[]);m.get(n.constellation_id).push(n)}return m},new Map()).values()];
function drawCore(){const cx=W/2,cy=H/2+14,rr=R*zoom;
 let g=ctx.createRadialGradient(cx,cy,rr*.02,cx,cy,rr*1.06);
 g.addColorStop(0,'rgba(160,200,255,.14)');g.addColorStop(.55,'rgba(60,90,180,.07)');g.addColorStop(1,'rgba(0,0,0,0)');
 ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,rr*1.06,0,Math.PI*2);ctx.fill();
 ctx.strokeStyle='rgba(160,200,255,.07)';ctx.lineWidth=1;
 for(let i=-60;i<=60;i+=30){ctx.beginPath();ctx.ellipse(cx,cy,rr,Math.abs(rr*Math.cos(i*Math.PI/180)),0,0,Math.PI*2);ctx.stroke()}}
function drawLine(a,b,active){const pa=project(sphere(a)),pb=project(sphere(b));
 const g=ctx.createLinearGradient(pa.x,pa.y,pb.x,pb.y);
 if(active){g.addColorStop(0,'rgba(238,201,111,.95)');g.addColorStop(1,'rgba(255,233,176,.6)')}
 else{g.addColorStop(0,'rgba(255,255,255,.16)');g.addColorStop(1,'rgba(255,255,255,.08)')}
 ctx.beginPath();ctx.moveTo(pa.x,pa.y);ctx.lineTo(pb.x,pb.y);ctx.strokeStyle=g;ctx.lineWidth=active?1.7:1;
 if(active){ctx.shadowBlur=8;ctx.shadowColor='rgba(238,201,111,.8)'}
 ctx.stroke();ctx.shadowBlur=0}
function drawStar(x,y,r,c,bright){
 const g=ctx.createRadialGradient(x,y,0,x,y,r*4.2);
 g.addColorStop(0,c);g.addColorStop(.35,c+'88');g.addColorStop(1,'rgba(0,0,0,0)');
 ctx.fillStyle=g;ctx.beginPath();ctx.arc(x,y,r*4.2,0,Math.PI*2);ctx.fill();
 ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(x,y,Math.max(1.4,r*.62),0,Math.PI*2);ctx.fill();
 if(bright){ctx.strokeStyle=c;ctx.lineWidth=1;ctx.globalAlpha=.85;
  ctx.beginPath();ctx.moveTo(x-r*5,y);ctx.lineTo(x+r*5,y);ctx.moveTo(x,y-r*5);ctx.lineTo(x,y+r*5);ctx.stroke();ctx.globalAlpha=1}}
function render(){ctx.clearRect(0,0,W,H);
 if(!drag&&!reduceMotion)rotY+=.0012;
 drawCore();
 constellationGroups.forEach(group=>{for(let i=1;i<group.length;i++){const a=group[i-1],b=group[i];
  if(visible(a)&&visible(b))drawLine(a,b,selected&&selected.constellation_id===a.constellation_id)}});
 nodes.forEach(n=>{if(n.parent_id){const p=nodeBy(n.parent_id);
  if(p&&visible(n)&&visible(p))drawLine(n,p,selected&&(selected.id===n.id||selected.id===p.id))}});
 const projected=nodes.map(n=>({n,p:sphere(n)})).map(o=>({...o,s:project(o.p)}));
 const shown=[],showLabels=new Set();
 projected.slice().sort((a,b)=>b.p.z-a.p.z).forEach(o=>{
  if(!visible(o.n)||o.p.z<=.05)return;
  const important=selected&&selected.id===o.n.id;
  const collides=shown.some(q=>Math.abs(q.x-o.s.x)<170&&Math.abs(q.y-o.s.y)<116);
  if(important||!collides){showLabels.add(o.n.id);shown.push({x:o.s.x,y:o.s.y})}});
 projected.sort((a,b)=>a.p.z-b.p.z).forEach(({n,p,s})=>{
  const el=labelEls.get(n.id),labelVisible=showLabels.has(n.id);
  el.classList.toggle('hidden',!labelVisible||!visible(n));
  el.classList.toggle('active',!!(selected&&selected.id===n.id));
  if(!visible(n))return;
  el.style.left=s.x+'px';el.style.top=s.y+'px';
  const tagEl=el.querySelector('.tag');
  if(tagEl)tagEl.textContent=filter!=='all'&&(n.tags||[]).includes(filter)?filter:((n.tags&&n.tags[0])||'未分類');
  el.style.opacity=labelVisible?Math.min(1,.55+s.scale*.34):0;
  const isSel=selected&&selected.id===n.id;
  drawStar(s.x,s.y,Math.max(2.2,4.6*s.scale)*(isSel?1.5:1),color(n),isSel||p.z>.72)});
 requestAnimationFrame(render)}
render();
function select(id){selected=nodeBy(id);if(!selected)return;
 const constellation=selected.constellation_name?`<p class="meta">☄ ${escapeHtml(selected.constellation_name)}</p>`:'';
 const reply=selected.reply_to?`<p class="meta">↪ ${escapeHtml(selected.reply_to)}への返信</p>`:'';
 detail.innerHTML=`<button class="close" aria-label="閉じる">×</button>
  <span class="pill">${escapeHtml(selected.constellation_name||(selected.tags&&selected.tags[0])||'未分類')}</span>
  <h2>${escapeHtml(selected.name)}の${STAR_TERM}</h2>${constellation}${reply}
  <p class="body">${escapeHtml(selected.body)}</p>
  <p class="hint">気づきが生まれたら、そのまま公式LINEに送るだけで宇宙に反映されます。特定の星に応えたい時だけ、下の返信用テキストを使ってください。</p>
  <div class="cmd"><span>この星に応える時だけ：返信:${escapeHtml(selected.id)} あなたの言葉</span><button id="copycmd">コピー</button></div>`;
 detail.classList.add('show');
 detail.querySelector('.close').onclick=()=>{selected=null;detail.classList.remove('show')};
 const cp=detail.querySelector('#copycmd');
 cp.onclick=()=>{const txt='返信:'+selected.id+' ';
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(()=>{cp.textContent='コピーしました';setTimeout(()=>cp.textContent='コピー',1600)})}
  else{cp.textContent=txt}}}
canvas.addEventListener('pointerdown',e=>{drag=true;moved=false;last={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId)});
canvas.addEventListener('pointermove',e=>{if(!drag)return;moved=true;
 rotY+=(e.clientX-last.x)*.006;rotX+=(e.clientY-last.y)*.006;
 rotX=Math.max(-1.1,Math.min(1.1,rotX));last={x:e.clientX,y:e.clientY}});
canvas.addEventListener('pointerup',()=>{drag=false});
canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.74,Math.min(1.4,zoom-e.deltaY*.0007))},{passive:false});
document.getElementById('zin').onclick=()=>zoom=Math.min(1.4,zoom+.08);
document.getElementById('zout').onclick=()=>zoom=Math.max(.74,zoom-.08);
</script></body></html>"""


def cosmos_page() -> bytes:
    universe = pipeline_common.worldview_term("universe", "気づきの宇宙")
    star = pipeline_common.worldview_term("star", "星")
    nodes = cosmos_nodes(cosmos_rows())
    data_json = json.dumps(nodes, ensure_ascii=False).replace("</", "<\\/")
    page = (
        COSMOS_SHELL
        .replace("__FONTS__", PAGE_FONTS)
        .replace("__UNIVERSE__", esc(universe))
        .replace("__STAR__", esc(star))
        .replace("__DATA__", data_json)
    )
    return page.encode("utf-8")


def questions_page() -> bytes:
    star = pipeline_common.worldview_term("star", "星")
    constellation = pipeline_common.worldview_term("constellation", "星座")
    approved = rows("approved")
    insights = weekly_insights(approved)
    with db() as conn:
        consts = list(conn.execute("SELECT * FROM constellations ORDER BY created_at DESC"))

    latest_questions = []
    for c in consts[:5]:
        q = (c["generated_question_md"] or "").strip()
        if q:
            latest_questions.append((c["name"], q))
    if not latest_questions:
        latest_questions = [("いまの問い", q) for q in insights["next_live_questions"]]

    question_cards = "".join(
        f'<section class="card question-card"><div class="q-label">{esc(label)}</div><p class="q">{esc(q)}</p></section>'
        for label, q in latest_questions[:5]
    )
    theme_cards = "".join(
        f'<section class="card const-card"><h3>{esc(item["tag"])}</h3><div class="const-week">{item["count"]}件の{esc(star)}</div><p>{esc(item["question"])}</p></section>'
        for item in insights["frequent_themes"][:6]
    )
    deep_cards = "".join(
        f'<section class="card star-card"><header><span class="star-dot"></span><span class="star-who">{esc(item["display_name"])}</span></header><p class="star-body">{esc(item["body"])}</p></section>'
        for item in insights["deep_candidates"][:3]
    )
    body = f'''
    <header class="hero">
      <div class="kicker">Questions from the Universe</div>
      <h1>問いのページ</h1>
      <p class="tagline">みんなの{esc(star)}から、次の問いが生まれます。</p>
      <p class="lead">公式LINEにそのまま気づき・感想・問いを送ると、掲載確認のあとすぐにこの公開サイトと宇宙へ反映されます。特定の{esc(star)}に応えたい時だけ、宇宙ページの返信用テキストを使ってください。</p>
      <div class="cta"><a class="btn" href="/cosmos">宇宙を旅する</a><a class="btn ghost" href="#send">LINEで{esc(star)}を送る</a></div>
    </header>
    <h2>今、浮かんでいる問い</h2>
    {question_cards or '<div class="card">まだ問いは生成されていません。</div>'}
    <h2>よく現れているテーマ</h2>
    <div class="grid">{theme_cards or '<div class="card">テーマ集計はまだありません。</div>'}</div>
    <h2>深まりのある{esc(star)}</h2>
    {deep_cards or '<div class="card">公開された気づきが増えると、ここに表示されます。</div>'}
    <section id="send" class="card notice">
      <h2>投稿方法</h2>
      <p>通常は、公式LINEに気づき・感想・問いをそのまま送ってください。掲載確認で「名前ありでOK」または「匿名ならOK」を選ぶと、この公開サイトが自動で更新されます。問題がある投稿だけ、あとから事務局が非公開にします。</p>
      <p class="small">特定の星に返信したい場合だけ、宇宙ページで星を開き、表示される「返信:ID あなたの言葉」を使います。</p>
    </section>
    '''
    return layout("問い", body)


def submit_page(parent_id: str = "") -> bytes:
    star = pipeline_common.worldview_term("star", "星")
    reply_note = (
        f'<p class="small">選んだ{esc(star)}への返信として届きます。</p>' if parent_id else ""
    )
    body = f'''
    <h1>{esc(star)}を送る</h1>
    <div class="card notice">ふだんは公式LINEにメッセージを送るだけで、あなたの気づきがこの宇宙の{esc(star)}になります。掲載は「名前あり・匿名・掲載しない」から選べて、掲載OK後すぐに灯ります。<span class="small">（このフォームは動作確認用の入口です）</span></div>
    <form class="card" method="post" action="/api/line-webhook">
      <input type="hidden" name="parent_id" value="{esc(parent_id)}">
      {reply_note}
      <p><label>表示名<br><input name="display_name" value="参加者"></label></p>
      <p><label>気づき・感想・問い<br><textarea name="body" rows="5" placeholder="いま心に残っていることを、そのままの言葉で"></textarea></label></p>
      <p><button>{esc(star)}として送る</button></p>
    </form>
    '''
    return layout(star + "を送る", body)


def staticize_html(page: bytes) -> str:
    html_text = page.decode("utf-8")
    replacements = {
        'href="/cosmos"': 'href="./cosmos.html"',
        'href="/questions"': 'href="./questions.html"',
        'href="/submit"': 'href="./questions.html#send"',
        'href="/admin"': 'href="./questions.html#send"',
        'href="/"': 'href="./"',
    }
    for old, new in replacements.items():
        html_text = html_text.replace(old, new)
    html_text = re.sub(r'href="/submit\?parent_id=[^"]+"', 'href="./questions.html#send"', html_text)
    return html_text


def export_static_site(deploy_dir: Path = STATIC_DEPLOY_DIR) -> dict[str, str]:
    deploy_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "index.html": staticize_html(public_page()),
        "cosmos.html": staticize_html(cosmos_page()),
        "questions.html": staticize_html(questions_page()),
        "README.md": "# 気づきの宇宙\n\nLINE公式アカウントで掲載OKになった気づき・問い・星座を公開する静的サイトです。\n",
    }
    for filename, content in files.items():
        (deploy_dir / filename).write_text(content, encoding="utf-8")
    return {"path": str(deploy_dir), "files": str(len(files))}


def publish_static_site(reason: str = "Update public kizuki universe") -> dict[str, str]:
    result = export_static_site()
    if not (STATIC_DEPLOY_DIR / ".git").exists():
        result.update({"status": "skipped", "detail": "deploy directory is not a git repository"})
        return result
    env = os.environ.copy()
    steps = [
        ["git", "add", "index.html", "cosmos.html", "questions.html", "README.md"],
        ["git", "diff", "--cached", "--quiet"],
    ]
    subprocess.run(steps[0], cwd=STATIC_DEPLOY_DIR, check=True, env=env)
    diff = subprocess.run(steps[1], cwd=STATIC_DEPLOY_DIR, env=env)
    if diff.returncode == 0:
        result.update({"status": "unchanged", "url": "https://nobykatsu0830.github.io/kizuki-universe/"})
        return result
    subprocess.run(["git", "commit", "-m", reason], cwd=STATIC_DEPLOY_DIR, check=True, env=env)
    subprocess.run(["git", "push"], cwd=STATIC_DEPLOY_DIR, check=True, env=env)
    result.update({"status": "published", "url": "https://nobykatsu0830.github.io/kizuki-universe/"})
    return result


def publish_static_site_safely(reason: str = "Update public kizuki universe") -> dict[str, str]:
    if os.environ.get("KIZUKI_AUTO_PUBLISH", "1") == "0":
        return {"status": "disabled"}
    try:
        return publish_static_site(reason)
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def admin_page() -> bytes:
    published_cards = []
    for r in reversed(rows("approved")):
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in parse_tags(r["tags"]))
        published_cards.append(f"""
        <div class="card">
          <div class="small">公開中｜{esc(r['id'])}｜{esc(r['source'])}｜parent={esc(r['parent_id'])}</div>
          <h3>{esc(r['display_name'])}</h3>
          <p>{esc(r['body'])}</p>
          <div>{tags}</div>
          <form method="post" action="/api/admin/hide"><input type="hidden" name="id" value="{esc(r['id'])}"><button>問題があるので非公開にする</button></form>
        </div>""")
    pending_cards = []
    for r in rows("pending"):
        pending_cards.append(f"""
        <div class="card notice">
          <div class="small">旧承認待ち｜{esc(r['id'])}</div>
          <h3>{esc(r['display_name'])}</h3>
          <p>{esc(r['body'])}</p>
          <form method="post" action="/api/admin/approve"><input type="hidden" name="id" value="{esc(r['id'])}"><button>公開する</button></form>
          <form method="post" action="/api/admin/hide"><input type="hidden" name="id" value="{esc(r['id'])}"><button>非公開にする</button></form>
        </div>""")
    body = """
    <h1>事務局管理</h1>
    <div class="card notice">現在は、LINEで掲載OKが押されたら自動で公開サイトに反映されます。この画面では、問題がある星だけを後から非公開にします。</div>
    <h2>公開中の星</h2>
    """ + ("".join(published_cards) or '<div class="card">公開中の星はありません。</div>')
    if pending_cards:
        body += "<h2>旧承認待ち</h2>" + "".join(pending_cards)
    return layout("事務局管理", body, admin=True)


def factory_page(message: str = "") -> bytes:
    notice = f'<div class="card notice">{esc(message)}</div>' if message else ""
    material_cards = []
    for material in media_material_rows():
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in parse_tags(material["tags"]))
        questions = parse_tags(material["questions"])
        question_items = "".join(f"<li>{esc(q)}</li>" for q in questions)
        material_cards.append(
            f"""
            <section class="card">
              <div class="small">{esc(material['created_at'])}｜{esc(material['course'])}</div>
              <h3>{esc(material['title'])}</h3>
              <div>{tags}</div>
              <h4>要約</h4>
              <p>{esc(material['summary'])}</p>
              <h4>参加者への問い</h4>
              <ol>{question_items}</ol>
              <h4>LINE短文案</h4>
              <div class="pre">{esc(material['participant_text_draft'])}</div>
              <h4>音声投稿案</h4>
              <div class="pre">{esc(material['audio_text_draft'])}</div>
              <h4>次回ライブへの入口</h4>
              <p>{esc(material['next_live_question'])}</p>
            </section>
            """
        )
    body = f"""
    <h1>Media Factory｜原液から教材化</h1>
    <div class="card">
      Nobyの音声文字起こし、ライブメモ、走り書きを貼ると、ローカルルールだけで教材化のたたき台を作ります。
      外部AI/APIには送りません。
    </div>
    {notice}
    <form class="card" method="post" action="/api/factory/create">
      <p><label>講座・シリーズ<br><input name="course" placeholder="例：気づきの宇宙 第7回"></label></p>
      <p><label>タイトル<br><input name="title" placeholder="例：待つことは何もしないことではない"></label></p>
      <p><label>原液テキスト<br><textarea name="raw_text" rows="10" placeholder="音声文字起こし、ライブのメモ、Nobyの走り書きを貼る"></textarea></label></p>
      <p><button>教材化メモを生成して保存</button></p>
    </form>
    <h2>保存済みの原液</h2>
    {''.join(material_cards) or '<div class="card">まだ原液は保存されていません。</div>'}
    """
    return layout("原液から教材化", body, admin=True)


def weekly_page() -> bytes:
    insights = weekly_insights(rows("approved"))
    tag_cards = []
    for tag_summary in insights["tag_summaries"]:
        items = "".join(
            f"<li><b>{esc(item['display_name'])}</b>：{esc(item['body'])}</li>"
            for item in tag_summary["items"]
        )
        tag_cards.append(
            f"""
            <section class="card">
              <h3>{esc(tag_summary['tag'])} <span class="small">({tag_summary['count']}件)</span></h3>
              <ul>{items}</ul>
            </section>
            """
        )

    theme_items = "".join(
        f"<li><b>{esc(theme['tag'])}</b>：{theme['count']}件<br><span class=\"small\">{esc(theme['question'])}</span></li>"
        for theme in insights["frequent_themes"]
    )
    candidate_cards = []
    for candidate in insights["deep_candidates"]:
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in candidate["tags"])
        candidate_cards.append(
            f"""
            <section class="card">
              <div class="small">深まり候補 score={candidate['score']}｜{esc(candidate['id'])}</div>
              <h3>{esc(candidate['display_name'])}の気づき</h3>
              <p>{esc(candidate['body'])}</p>
              <div>{tags}</div>
            </section>
            """
        )
    question_items = "".join(f"<li>{esc(q)}</li>" for q in insights["next_live_questions"])
    body = f"""
    <h1>AI編集者モード｜今週の気づき</h1>
    <div class="card">
      承認済みの参加者の声から、タグ別のまとまり、よく出ているテーマ、深掘り候補、次回ライブの入口になる問いを出します。
      ここも外部AI/APIは使わず、ローカルのルールで集計しています。
    </div>
    <div class="grid">
      <section class="card">
        <div class="small">承認済みの声</div>
        <h2>{insights['total']}件</h2>
      </section>
      <section class="card">
        <h2>頻出テーマ</h2>
        <ul>{theme_items or '<li>まだテーマはありません。</li>'}</ul>
      </section>
    </div>
    <h2>タグ別のまとまり</h2>
    <div class="grid">{''.join(tag_cards) or '<section class="card">承認済みの声がまだありません。</section>'}</div>
    <h2>深い振り返り候補</h2>
    {''.join(candidate_cards) or '<div class="card">候補はまだありません。</div>'}
    <h2>次回ライブに返す問い</h2>
    <div class="card"><ol>{question_items}</ol></div>
    """
    return layout("AI編集者モード", body, admin=True)


def source_recording_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return list(conn.execute("SELECT * FROM source_recordings ORDER BY created_at DESC"))


def derived_content_rows(recording_id: str) -> list[sqlite3.Row]:
    with db() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM derived_contents
                WHERE source_recording_id=?
                ORDER BY created_at ASC
                """,
                (recording_id,),
            )
        )


def recordings_page(message: str = "") -> bytes:
    notice = f'<div class="card notice">{esc(message)}</div>' if message else ""
    cards = []
    for recording in source_recording_rows():
        contents = []
        for content in derived_content_rows(recording["id"]):
            tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in pipeline_common.parse_json_list(content["topic_tags_json"], default=()))
            contents.append(
                f"""
                <details class="card">
                  <summary><b>{esc(content['content_type'])}</b> <span class="small">{esc(content['layer'])}｜{esc(content['visibility'])}</span></summary>
                  <div>{tags}</div>
                  <div class="pre">{esc(content['body_md'])}</div>
                </details>
                """
            )
        cards.append(
            f"""
            <section class="card">
              <div class="small">{esc(recording['id'])}｜{esc(recording['kind'])}｜status={esc(recording['status'])}</div>
              <h3>{esc(recording['title'])}</h3>
              <p class="small">recorded_at: {esc(recording['recorded_at'])}<br>path: {esc(recording['audio_path'])}</p>
              <form method="post" action="/admin/recordings/{esc(recording['id'])}/pipeline">
                <button>ローカルパイプライン実行</button>
              </form>
              {''.join(contents) or '<p class="small">派生コンテンツはまだありません。</p>'}
            </section>
            """
        )
    body = f"""
    <h1>原液登録</h1>
    <div class="card">
      音声ファイル自体は外部サービスに送りません。同名の <code>.txt</code>、または <code>.txt/.md</code> パスを登録すると、ローカルパイプラインが文字起こしとして読み込みます。
    </div>
    {notice}
    <form class="card" method="post" action="/admin/recordings">
      <p><label>タイトル<br><input name="title" placeholder="例：笑いの教養講座 第1回"></label></p>
      <p><label>種別<br><input name="kind" value="weekly_live" placeholder="weekly_live / voice_memo / zoom_live / followup"></label></p>
      <p><label>音声またはテキストのパス<br><input name="audio_path" placeholder="例：inputs/2026-06-12_laughter.txt"></label></p>
      <p><label>収録日時<br><input name="recorded_at" value="{esc(now_iso())}"></label></p>
      <p><button>原液を登録</button></p>
    </form>
    <h2>登録済みの原液</h2>
    {''.join(cards) or '<div class="card">まだ原液は登録されていません。</div>'}
    """
    return layout("原液登録", body, admin=True)


def api_constellations_payload(week: str | None = None) -> dict:
    with db() as conn:
        return pipeline_common.constellations_payload(conn, week=week)


def followup_suggestions_page() -> bytes:
    with db() as conn:
        suggestions = pipeline_common.suggest_followups(conn, limit=3)
    cards = []
    for item in suggestions:
        cards.append(
            f"""
            <section class="card">
              <div class="small">score={item['score']}｜{esc(item['id'])}</div>
              <h3>{esc(item['name'])}</h3>
              <p><b>理由:</b> {esc(item['reason'])}</p>
              <p><b>生まれた問い:</b> {esc(item['generated_question_md'])}</p>
              <div class="pre">{esc(item['summary_md'])}</div>
              <p><a class="btn" href="/admin/followups">この星座にフォローアップを登録</a></p>
            </section>
            """
        )
    body = f"""
    <h1>フォローアップ候補</h1>
    <div class="card">
      AIは参加者へ自動応答しません。星座の密度と質問の数から、推しが声で応える候補を3つまで出します。
    </div>
    {''.join(cards) or '<div class="card">候補はまだありません。<code>python3 pipeline/constellate.py</code> を実行して星座を作ってください。</div>'}
    """
    return layout("フォローアップ候補", body, admin=True)


def constellation_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return list(conn.execute("SELECT * FROM constellations ORDER BY week_of DESC, created_at DESC"))


def followup_rows() -> list[sqlite3.Row]:
    with db() as conn:
        return list(
            conn.execute(
                """
                SELECT f.*, c.name AS constellation_name, s.title AS recording_title, s.audio_path
                FROM followups f
                JOIN constellations c ON c.id=f.constellation_id
                LEFT JOIN source_recordings s ON s.id=f.source_recording_id
                ORDER BY f.created_at DESC
                """
            )
        )


def followups_page(message: str = "") -> bytes:
    constellations = constellation_rows()
    options = "".join(f'<option value="{esc(c["id"])}">{esc(c["week_of"])}｜{esc(c["name"])}</option>' for c in constellations)
    cards = []
    for item in followup_rows():
        cards.append(
            f"""
            <section class="card">
              <div class="small">{esc(item['created_at'])}｜{esc(item['constellation_name'])}</div>
              <div class="pre">{esc(item['note_md'])}</div>
              <p class="small">recording: {esc(item['recording_title'])}｜path: {esc(item['audio_path'])}</p>
            </section>
            """
        )
    notice = f'<div class="card notice">{esc(message)}</div>' if message else ""
    body = f"""
    <h1>フォローアップ登録</h1>
    <div class="card">
      星座単位に、推しが声で応えた記録を登録します。音声パスを入れた場合は <code>source_recordings.kind=followup</code> として原液にも登録します。
    </div>
    {notice}
    <form class="card" method="post" action="/admin/followups">
      <p><label>星座<br><select name="constellation_id">{options}</select></label></p>
      <p><label>フォローアップ音声タイトル<br><input name="title" placeholder="例：待つ星座への返答"></label></p>
      <p><label>音声またはテキストのパス<br><input name="audio_path" placeholder="任意"></label></p>
      <p><label>メモ<br><textarea name="note_md" rows="6" placeholder="この星座にどう応えたか"></textarea></label></p>
      <p><button>フォローアップを登録</button></p>
    </form>
    <h2>登録済みフォローアップ</h2>
    {''.join(cards) or '<div class="card">まだフォローアップは登録されていません。</div>'}
    """
    return layout("フォローアップ登録", body, admin=True)


def obsidian_vault_page(message: str = "", manifest: dict | None = None) -> bytes:
    default_path = str(export_obsidian.DEFAULT_PUBLIC_VAULT_PATH)
    notice = f'<div class="card notice">{esc(message)}</div>' if message else ""
    manifest_html = ""
    if manifest:
        manifest_html = f"""
        <section class="card">
          <h2>書き出し結果</h2>
          <p><b>Vault:</b><br><code>{esc(manifest.get('vault_path'))}</code></p>
          <ul>
            <li>星: {manifest.get('stars', 0)}件</li>
            <li>星座: {manifest.get('constellations', 0)}件</li>
            <li>タグ: {manifest.get('tags', 0)}件</li>
          </ul>
          <p class="small">Obsidianでこのフォルダを「別の保管庫」として開けば、Noby個人のSecond Brainとは混ざりません。</p>
        </section>
        """
    body = f"""
    <h1>公開用Obsidian Vault</h1>
    <div class="card">
      <p>ここは、参加者に見せる前提の独立したObsidian保管庫を書き出すMVPです。</p>
      <p>Noby個人のSecond Brainには書き込みません。未承認・本人のみ・非公開の星も書き出しません。</p>
    </div>
    {notice}
    <form class="card" method="post" action="/admin/obsidian-vault/export">
      <p><label>公開用Vaultの保存先<br><input name="vault_path" value="{esc(default_path)}"></label></p>
      <p><button>承認済みの星と星座を書き出す</button></p>
    </form>
    {manifest_html}
    <section class="card">
      <h2>想定する使い方</h2>
      <ol>
        <li>LINE公式から届いた声を同意・承認する</li>
        <li>星座化する</li>
        <li>このページから独立Vaultへ書き出す</li>
        <li>Obsidianで <code>{esc(default_path)}</code> を別Vaultとして開く</li>
        <li>必要なら Obsidian Publish / Quartz / Cloudflare Pages で公開する</li>
      </ol>
    </section>
    """
    return layout("公開用Obsidian Vault", body, admin=True)


def extract_line_text_messages(payload: dict) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue
        messages.append(
            {
                "external_user_id": event.get("source", {}).get("userId", ""),
                "body": message.get("text", ""),
                "reply_token": event.get("replyToken", ""),
            }
        )
    return messages


def build_line_reply_payload(reply_token: str, text: str) -> dict:
    return {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}


def parse_line_reply_command(text: str) -> dict[str, str] | None:
    stripped = text.strip()
    for prefix in ("返信:", "返信：", "reply:", "Reply:"):
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            if not rest or " " not in rest:
                return None
            parent_id, body = rest.split(None, 1)
            body = body.strip()
            if not parent_id or not body:
                return None
            return {"parent_id": parent_id, "body": body}
    return None


def reflection_label(rid: str | None) -> str | None:
    if not rid:
        return None
    with db() as conn:
        row = conn.execute("SELECT display_name FROM reflections WHERE id=?", (rid,)).fetchone()
    if not row:
        return None
    return f"{row['display_name']}の気づき"


def build_consent_reply_payload(reply_token: str, parent_title: str | None = None) -> dict:
    prompt = pipeline_common.worldview_message("consent_prompt", "この気づきを宇宙に掲載してもよいですか？")
    target_text = f"{parent_title}への返信です。" if parent_title else ""
    return {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": f"ありがとうございます。{target_text}{prompt}",
                "quickReply": {
                    "items": [
                        {"type": "action", "action": {"type": "postback", "label": "名前ありでOK", "data": "consent=name", "displayText": "名前ありで掲載OK"}},
                        {"type": "action", "action": {"type": "postback", "label": "匿名ならOK", "data": "consent=anonymous", "displayText": "匿名なら掲載OK"}},
                        {"type": "action", "action": {"type": "postback", "label": "掲載しない", "data": "consent=reject", "displayText": "掲載しない"}},
                    ]
                },
            }
        ],
    }


def extract_line_postbacks(payload: dict) -> list[dict[str, str]]:
    postbacks: list[dict[str, str]] = []
    for event in payload.get("events", []):
        if event.get("type") != "postback":
            continue
        postbacks.append(
            {
                "external_user_id": event.get("source", {}).get("userId", ""),
                "reply_token": event.get("replyToken", ""),
                "data": event.get("postback", {}).get("data", ""),
            }
        )
    return postbacks


def line_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that works on macOS Python installs.

    The framework python on this machine can have an empty OpenSSL cafile,
    which makes urllib fail with CERTIFICATE_VERIFY_FAILED when calling LINE.
    Prefer certifi when available, then common macOS/Homebrew CA bundles.
    """
    cafile = os.environ.get("SSL_CERT_FILE", "")
    if cafile and Path(cafile).exists():
        return ssl.create_default_context(cafile=cafile)

    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

    for candidate in (
        "/etc/ssl/cert.pem",
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
    ):
        if Path(candidate).exists():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def send_line_payload(payload: dict) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/reply",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5, context=line_ssl_context()) as resp:
            resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        # Do not fail webhook intake if LINE reply fails. The reflection is more important.
        print(f"LINE reply failed: {exc}")


def reply_to_line(reply_token: str, text: str) -> None:
    if not reply_token:
        return
    send_line_payload(build_line_reply_payload(reply_token, text))


def ask_line_consent(reply_token: str, parent_title: str | None = None) -> None:
    if not reply_token:
        return
    send_line_payload(build_consent_reply_payload(reply_token, parent_title=parent_title))


LINE_RECEIVED_TEXT = "ありがとうございます。感想を受け取りました。掲載OKを選ぶと、公開サイトの気づきの宇宙に反映されます。"
LINE_CONSENT_ACCEPTED_TEXT = "ありがとうございます。公開サイトの気づきの宇宙に反映しました。宇宙がまた少し進化しました。"
LINE_CONSENT_REJECTED_TEXT = "了解しました。この感想は公開ページには掲載しません。送ってくださってありがとうございます。"
LINE_CONSENT_NOT_FOUND_TEXT = "確認できる直近の感想が見つかりませんでした。もう一度感想を送ってください。"


class Handler(BaseHTTPRequestHandler):
    def send_html(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path: str) -> None:
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def read_form_or_json_from_raw(self, raw_bytes: bytes) -> dict[str, str]:
        raw = raw_bytes.decode("utf-8") if raw_bytes else ""
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            data = json.loads(raw or "{}")
            return {k: str(v) for k, v in data.items() if v is not None}
        parsed = parse_qs(raw)
        return {k: v[0] for k, v in parsed.items() if v}

    def read_form_or_json(self) -> dict[str, str]:
        return self.read_form_or_json_from_raw(self.read_raw())

    def verify_line_signature(self, raw_bytes: bytes) -> bool:
        channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "")
        if not channel_secret:
            # Local demo mode: allow requests when no secret is configured.
            return True
        signature = self.headers.get("X-Line-Signature", "")
        digest = hmac.new(channel_secret.encode("utf-8"), raw_bytes, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(signature, expected)

    def handle_line_webhook(self, raw_bytes: bytes) -> None:
        if not self.verify_line_signature(raw_bytes):
            self.send_html(layout("403", "<h1>LINE署名検証エラー</h1>"), 403)
            return
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            payload = json.loads(raw_bytes.decode("utf-8") or "{}")
            # Actual LINE Messaging API webhook shape.
            if "events" in payload:
                for message in extract_line_text_messages(payload):
                    reply_command = parse_line_reply_command(message["body"])
                    parent_id = reply_command["parent_id"] if reply_command else None
                    body = reply_command["body"] if reply_command else message["body"]
                    insert_reflection(
                        source="line",
                        display_name="LINE参加者",
                        body=body,
                        parent_id=parent_id,
                        external_user_id=message["external_user_id"],
                        status="awaiting_consent",
                    )
                    ask_line_consent(message["reply_token"], parent_title=reflection_label(parent_id))
                for postback in extract_line_postbacks(payload):
                    data = postback["data"]
                    if data.startswith("consent="):
                        consent = data.split("=", 1)[1]
                        rid = apply_consent(postback["external_user_id"], consent)
                        if not rid:
                            reply_to_line(postback["reply_token"], LINE_CONSENT_NOT_FOUND_TEXT)
                        elif consent == "reject":
                            reply_to_line(postback["reply_token"], LINE_CONSENT_REJECTED_TEXT)
                        else:
                            publish_static_site_safely("Auto publish LINE kizuki")
                            reply_to_line(postback["reply_token"], LINE_CONSENT_ACCEPTED_TEXT)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
                return
        # Demo/adapter shape: form or simple JSON.
        data = self.read_form_or_json_from_raw(raw_bytes)
        insert_reflection(
            source=data.get("source", "line-demo"),
            display_name=data.get("display_name", "参加者"),
            body=data.get("body", ""),
            parent_id=data.get("parent_id") or None,
            external_user_id=data.get("external_user_id"),
        )
        self.redirect("/admin")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/":
            self.send_html(public_page())
        elif parsed.path == "/cosmos":
            self.send_html(cosmos_page())
        elif parsed.path == "/questions":
            self.send_html(questions_page())
        elif parsed.path == "/submit":
            self.send_html(submit_page(qs.get("parent_id", [""])[0]))
        elif parsed.path == "/admin":
            self.send_html(admin_page())
        elif parsed.path == "/admin/recordings":
            message = ""
            if qs.get("created"):
                message = f"原液を登録しました。ID: {qs['created'][0]}"
            elif qs.get("pipeline"):
                message = f"ローカルパイプラインを実行しました。ID: {qs['pipeline'][0]}"
            elif qs.get("error") == ["empty"]:
                message = "タイトルまたはパスを入力してください。"
            self.send_html(recordings_page(message))
        elif parsed.path == "/admin/followup-suggestions":
            self.send_html(followup_suggestions_page())
        elif parsed.path == "/admin/followups":
            message = ""
            if qs.get("created"):
                message = f"フォローアップを登録しました。ID: {qs['created'][0]}"
            elif qs.get("error") == ["missing"]:
                message = "星座とメモを入力してください。"
            self.send_html(followups_page(message))
        elif parsed.path == "/admin/obsidian-vault":
            message = ""
            if qs.get("exported"):
                message = f"公開用Vaultへ書き出しました: {qs['exported'][0]}"
            elif qs.get("error") == ["second-brain"]:
                message = "個人Second Brainには書き出せません。別保管庫を指定してください。"
            self.send_html(obsidian_vault_page(message))
        elif parsed.path == "/api/constellations":
            self.send_json(api_constellations_payload(qs.get("week", [None])[0]))
        elif parsed.path in ("/factory", "/studio"):
            message = ""
            if qs.get("created"):
                message = f"原液を保存し、教材化メモを生成しました。ID: {qs['created'][0]}"
            elif qs.get("error") == ["empty"]:
                message = "原液テキストが空だったため、保存しませんでした。"
            self.send_html(factory_page(message))
        elif parsed.path in ("/weekly", "/editor"):
            self.send_html(weekly_page())
        elif parsed.path == "/health":
            self.send_html(layout("OK", "<h1>OK</h1>"))
        else:
            self.send_html(layout("404", "<h1>404</h1>"), 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path in ("/api/line-webhook", "/webhook/line"):
            self.handle_line_webhook(self.read_raw())
        elif path == "/api/admin/approve":
            data = self.read_form_or_json()
            approve(data.get("id", ""))
            publish_static_site_safely("Auto publish approved kizuki")
            self.redirect("/admin")
        elif path == "/api/admin/hide":
            data = self.read_form_or_json()
            hide_reflection(data.get("id", ""))
            publish_static_site_safely("Hide public kizuki")
            self.redirect("/admin")
        elif path == "/api/factory/create":
            data = self.read_form_or_json()
            raw_text = data.get("raw_text", "")
            if not raw_text.strip():
                self.redirect("/factory?error=empty")
                return
            mid = insert_media_material(data.get("title", ""), data.get("course", ""), raw_text)
            self.redirect(f"/factory?created={mid}")
        elif path == "/admin/recordings":
            data = self.read_form_or_json()
            title = data.get("title", "").strip()
            audio_path = data.get("audio_path", "").strip()
            if not title and not audio_path:
                self.redirect("/admin/recordings?error=empty")
                return
            with db() as conn:
                rid = pipeline_common.create_source_recording(
                    conn,
                    title=title or Path(audio_path).stem or "無題の原液",
                    kind=data.get("kind", "voice_memo"),
                    audio_path=audio_path,
                    recorded_at=data.get("recorded_at", ""),
                )
            self.redirect(f"/admin/recordings?created={rid}")
        elif path.startswith("/admin/recordings/") and path.endswith("/pipeline"):
            recording_id = path.removeprefix("/admin/recordings/").removesuffix("/pipeline").strip("/")
            try:
                with db() as conn:
                    pipeline_common.run_recording_pipeline(conn, recording_id)
            except Exception as exc:
                self.send_html(layout("Pipeline Error", f"<h1>Pipeline Error</h1><pre>{esc(str(exc))}</pre>"), 400)
                return
            self.redirect(f"/admin/recordings?pipeline={recording_id}")
        elif path == "/admin/followups":
            data = self.read_form_or_json()
            constellation_id = data.get("constellation_id", "").strip()
            note_md = data.get("note_md", "").strip()
            if not constellation_id or not note_md:
                self.redirect("/admin/followups?error=missing")
                return
            title = data.get("title", "").strip()
            audio_path = data.get("audio_path", "").strip()
            with db() as conn:
                source_recording_id = None
                if audio_path:
                    source_recording_id = pipeline_common.create_source_recording(
                        conn,
                        title=title or "星座フォローアップ",
                        kind="followup",
                        audio_path=audio_path,
                        recorded_at=now_iso(),
                    )
                fid = pipeline_common.create_followup(conn, constellation_id, note_md, source_recording_id=source_recording_id)
            self.redirect(f"/admin/followups?created={fid}")
        elif path == "/admin/obsidian-vault/export":
            data = self.read_form_or_json()
            vault_path = data.get("vault_path", "").strip() or str(export_obsidian.DEFAULT_PUBLIC_VAULT_PATH)
            try:
                with db() as conn:
                    manifest = export_obsidian.export_public_vault(conn, vault_path)
            except ValueError:
                self.redirect("/admin/obsidian-vault?error=second-brain")
                return
            self.send_html(obsidian_vault_page("公開用Vaultへ書き出しました。", manifest=manifest))
        else:
            self.send_html(layout("404", "<h1>404</h1>"), 404)


def main() -> None:
    load_dotenv()
    init_db()
    seed_if_empty()
    port = int(os.environ.get("PORT", 8787))
    host = "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI気づきツリー MVP running: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
