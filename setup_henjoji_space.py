#!/usr/bin/env python3
"""遍照寺「気づきの御護摩」henjoji スペースを作成する（手動オンボード用・冪等）。

使い方:
    python3 setup_henjoji_space.py <join_password>            # 作成/更新 + 投稿パスワード設定
    python3 setup_henjoji_space.py <join_password> --seed     # ＋ 動作確認用サンプル気づきを投入（ローカル専用）

.env に DATABASE_URL があれば本番Postgres、無ければローカルSQLiteに対して実行する。
パスワードはSHA-256ハッシュで保存され、平文はDBに残らない。
既存の henjoji スペースがあれば worldview_json を正準値で更新する（冪等）。
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# common.py は import 時に DATABASE_URL を読むため、先に .env を読み込む
_envp = Path(__file__).resolve().parent / ".env"
if _envp.exists():
    for _line in _envp.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from pipeline import common as pc  # noqa: E402

HENJOJI_SLUG = "henjoji"
HENJOJI_NAME = "気づきの御護摩"

# 正準 goma worldview_json（設計仕様どおり。キー名・値の発明禁止）
HENJOJI_WORLDVIEW = {
    "scene": "goma",
    "terms": {
        "star": "護摩木",
        "constellation": "焔",
        "universe": "気づきの御護摩",
        "nebula": "薪",
        "owner": "導き手",
        "content": "巻物",
    },
    "messages": {
        "consent_prompt": "この気づきを御護摩にくべてもよいですか？",
        "star_born": "あなたの護摩木が、火に入りました",
        "in_constellation": "あなたの護摩木が「{constellation}」の焔につながりました",
    },
    "goma": {
        "temple": "遍照寺 朝のお勤め",
        "title": "気づきの御護摩",
        "tagline": "あなたの気づきが、護摩木となって、誰かの明日を照らす。",
        "cta": "今朝の気づきを、くべる",
        "cta_note": "お勤めのあと、心に残ったひとことを。",
        "feed_title": "炎のいま",
        "question_title": "今朝の問い",
        "question_note": "— 皆さんの護摩木から、炎が浮かびあがらせた問い",
        "stats_label": "今朝の灯",
        "okibi_title": "熾火 ── これまでにくべられた気づき",
        "new_label": "いま、火に入りました",
        "resonate_label": "この護摩木と響き合う",
        "footer": "遍照 ── あまねく照らす。ひとつの気づきが、みなの明日を照らしますように。",
    },
    "theme": {
        "primary_color": "#0d1230",
        "bg_top": "#070a18", "bg_mid": "#1c1c42", "bg_bottom": "#7c4038",
        "flame_core": "#fff3c8", "flame_mid": "#ffb43a", "flame_outer": "#ff7a1e",
        "gold": "#f3c76a", "gold_dim": "#b9a06a",
        "wood_light": "#a8845c", "wood_dark": "#6d5136",
        "ink": "#241505", "text": "#e8dfc8", "text_dim": "#9a90ad",
    },
}

# 動作確認用サンプル（明らかに架空とわかる内容・名前。--seed 指定時のみ）
SAMPLE_KIZUKI = [
    ("テスト花子", "これはテスト用の気づきです（架空）", 0),
    ("テスト太郎", "サンプル投稿：朝のお勤めのあとの一言（架空）", 0),
    ("テスト熾火", "八日前のサンプル気づき。熾火帯の表示確認用（架空）", 8),
]


def seed_samples(conn) -> int:
    """サンプル気づきを投入（既に同名サンプルがあればスキップ＝冪等）。"""
    inserted = 0
    for name, body, days_ago in SAMPLE_KIZUKI:
        exists = conn.execute(
            "SELECT 1 FROM reflections WHERE space_id=? AND display_name=? AND body=?",
            (HENJOJI_SLUG, name, body),
        ).fetchone()
        if exists:
            continue
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, approved_at, space_id, star_kind, visibility) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], "seed", name, body, json.dumps(["未分類"], ensure_ascii=False),
             "approved", ts, ts, HENJOJI_SLUG, "insight", "universe"),
        )
        inserted += 1
    return inserted


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python3 setup_henjoji_space.py <join_password> [--seed]")
        sys.exit(2)
    password = sys.argv[1]
    do_seed = "--seed" in sys.argv[2:]
    if not password:
        print("join_password は空にできません（護摩空間は合言葉保護が前提です）。")
        sys.exit(1)
    db_mode = "PostgreSQL" if pc.DATABASE_URL else f"SQLite ({pc.DEFAULT_DB_PATH})"
    print(f"[DB] {db_mode}")
    pc.init_db(pc.DEFAULT_DB_PATH)
    with pc.connect(pc.DEFAULT_DB_PATH) as conn:
        already = pc.space_exists(conn, HENJOJI_SLUG)
        pc.create_space(conn, HENJOJI_SLUG, HENJOJI_NAME, HENJOJI_WORLDVIEW)
        pc.set_space_join_password(conn, HENJOJI_SLUG, password)
        n = seed_samples(conn) if do_seed else 0
    print(f"スペース '{HENJOJI_SLUG}' を{'更新' if already else '作成'}しました（scene=goma・合言葉保護）。")
    if do_seed:
        print(f"サンプル気づきを {n} 件投入しました（既存分はスキップ）。")


if __name__ == "__main__":
    main()
