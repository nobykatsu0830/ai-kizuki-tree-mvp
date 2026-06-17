from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    class _DictRow(dict):
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

    class _PgCursor:
        def __init__(self, cur):
            self._cur = cur

        def fetchone(self):
            row = self._cur.fetchone()
            return _DictRow(row) if row else None

        def fetchall(self):
            return [_DictRow(r) for r in self._cur.fetchall()]

        def __iter__(self):
            for row in self._cur:
                yield _DictRow(row)

    class _PgConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            sql = sql.replace("?", "%s")
            sql = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
            sql = re.sub(r"INSERT OR REPLACE INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
            if re.search(r"^INSERT INTO", sql.strip(), re.IGNORECASE) and "ON CONFLICT" not in sql.upper():
                sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, tuple(params))
            return _PgCursor(cur)

        def executescript(self, script):
            cur = self._conn.cursor()
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            self._conn.close()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *_):
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            self._conn.close()
DEFAULT_DB_PATH = ROOT / "data" / "kizuki_tree.sqlite3"
DEFAULT_WORLDVIEW_PATH = ROOT / "worldview.yaml"
DEFAULT_SPACE_ID = "noby-universe"

DEFAULT_WORLDVIEW = {
    "space_id": DEFAULT_SPACE_ID,
    "terms": {
        "star": "星",
        "constellation": "星座",
        "universe": "気づきの宇宙",
        "nebula": "星雲",
        "owner": "推し",
        "content": "惑星",
    },
    "messages": {
        "consent_prompt": "この気づきを宇宙に掲載してもよいですか？",
        "star_born": "あなたの星が生まれました",
        "in_constellation": "あなたの星が「{constellation}」星座につながりました",
    },
    "cta": {
        "join_label": "",
        "join_url": "",
        "join_note": "",
    },
    "theme": {"primary_color": "#1a1a2e"},
}

THEME_KEYWORDS = {
    "待つ": ["待つ", "待て", "焦", "急"],
    "安心": ["安心", "ほっと", "落ち着"],
    "身体感覚": ["身体", "からだ", "呼吸", "胸", "腹", "緊張"],
    "相手を変えようとする": ["相手", "変え", "コントロール"],
    "笑い": ["笑", "ラフター", "でたらめ", "ジブリッシュ"],
    "関係性": ["親子", "夫婦", "仲間", "職場", "関係", "対話"],
    "自己理解": ["自分", "本音", "気づき", "感じ", "思い込み"],
    "問い": ["問い", "質問", "なぜ", "どうしたら", "何が"],
    "実践": ["練習", "実験", "やってみ", "習慣", "一週間"],
}

TAG_QUESTIONS = {
    "待つ": "待てなかった場面の奥に、どんな願いがありましたか。",
    "安心": "安心が戻った瞬間には、何が起きていましたか。",
    "身体感覚": "身体の反応は、どんな気づきを教えてくれていますか。",
    "相手を変えようとする": "相手を変えたい気持ちの奥に、どんな願いがありますか。",
    "笑い": "笑いが入ることで、場の見え方はどう変わりましたか。",
    "関係性": "その気づきは、誰との関係に一番つながっていますか。",
    "自己理解": "自分について、いま見え方が変わったことは何ですか。",
    "問い": "この問いを次に深めるなら、どこから見ていきますか。",
    "実践": "次の一週間で、どんな小さな実験をしますか。",
}

FILLER_RE = re.compile(r"(えーと|えっと|えー|あのー|あの|そのー|その|なんか|まあ|まー|うーん|ええと)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path = DEFAULT_DB_PATH):
    if DATABASE_URL:
        return _PgConn(psycopg2.connect(DATABASE_URL))
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_scalar(value: str):
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    return value


def load_simple_yaml(path: str | Path) -> dict:
    result: dict = {}
    stack: list[tuple[int, dict]] = [(-1, result)]
    for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise ValueError(f"Tabs are not supported in YAML at line {line_no}")
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        key, sep, value = stripped.partition(":")
        if not sep or not key:
            raise ValueError(f"Unsupported YAML line {line_no}: {raw}")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip():
            parent[key.strip()] = _parse_scalar(value)
        else:
            child: dict = {}
            parent[key.strip()] = child
            stack.append((indent, child))
    return result


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_worldview(path: str | Path = DEFAULT_WORLDVIEW_PATH) -> dict:
    """worldview.yaml を読む（デフォルト=noby宇宙の種・init/CLI/バッチ用）。"""
    if not Path(path).exists():
        return dict(DEFAULT_WORLDVIEW)
    loaded = load_simple_yaml(path)
    return _deep_merge(DEFAULT_WORLDVIEW, loaded)


# --- マルチテナント: リクエストごとの「現在の宇宙」をスレッドローカルで保持 ---
_request_ctx = threading.local()


def set_current_space(space_id: str, worldview: dict) -> None:
    """リクエスト処理の先頭で、対象スペースと世界観を束ねてセットする。"""
    _request_ctx.space_id = space_id
    _request_ctx.worldview = worldview


def clear_current_space() -> None:
    _request_ctx.space_id = None
    _request_ctx.worldview = None


def current_worldview() -> dict:
    """現在のリクエストの世界観。未設定なら yaml（後方互換: CLI/バッチ/テスト）。"""
    wv = getattr(_request_ctx, "worldview", None)
    return wv if wv else load_worldview()


def current_space_id() -> str:
    sid = getattr(_request_ctx, "space_id", None)
    if sid:
        return str(sid)
    return str(load_worldview().get("space_id") or DEFAULT_SPACE_ID)


def load_worldview_for_space(conn, space_id: str) -> dict:
    """スペースの世界観をDBから読む。無ければ noby はyaml、他はデフォルトにフォールバック。"""
    row = conn.execute("SELECT worldview_json FROM spaces WHERE id=?", (space_id,)).fetchone()
    if row and row["worldview_json"]:
        try:
            merged = _deep_merge(DEFAULT_WORLDVIEW, json.loads(row["worldview_json"]))
            merged["space_id"] = space_id
            return merged
        except (json.JSONDecodeError, TypeError):
            pass
    base = load_worldview() if space_id == DEFAULT_SPACE_ID else dict(DEFAULT_WORLDVIEW)
    base = dict(base)
    base["space_id"] = space_id
    return base


def list_spaces(conn) -> list[dict]:
    rows = conn.execute("SELECT id, name, created_at FROM spaces ORDER BY created_at ASC").fetchall()
    return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"]} for r in rows]


def space_exists(conn, space_id: str) -> bool:
    return conn.execute("SELECT 1 FROM spaces WHERE id=?", (space_id,)).fetchone() is not None


def create_space(conn, space_id: str, name: str, worldview: dict | None = None) -> None:
    """新しい宇宙を作る（手動オンボード用）。worldview は terms/messages/cta/theme の辞書。"""
    space_id = (space_id or "").strip()
    if not space_id:
        raise ValueError("space_id is required")
    wv_json = json.dumps(worldview or {}, ensure_ascii=False)
    conn.execute(
        "INSERT OR IGNORE INTO spaces (id, name, worldview_path, worldview_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (space_id, (name or space_id).strip(), "", wv_json, now_iso()),
    )
    conn.execute("UPDATE spaces SET name=?, worldview_json=? WHERE id=?", ((name or space_id).strip(), wv_json, space_id))


def default_space_id() -> str:
    return current_space_id()


def worldview_term(key: str, default: str = "") -> str:
    terms = current_worldview().get("terms", {})
    return str(terms.get(key) or default or key)


def worldview_message(key: str, default: str = "", **values) -> str:
    messages = current_worldview().get("messages", {})
    text = str(messages.get(key) or default or key)
    try:
        return text.format(**values)
    except KeyError:
        return text


def worldview_cta() -> dict:
    """公開ページの参加CTA。現在の宇宙の cta.* で差し替え可能。
    未設定なら内部の星投稿フォームへ安全にフォールバックする。"""
    cta = current_worldview().get("cta", {}) or {}
    return {
        "join_label": str(cta.get("join_label") or "この宇宙に星を送る"),
        "join_url": str(cta.get("join_url") or "/submit"),
        "join_note": str(
            cta.get("join_note")
            or "講座で生まれた気づき・感想・問いは、公式LINEに送るだけでこの宇宙の星になります。"
        ),
    }


def table_columns(conn, table: str) -> set[str]:
    if DATABASE_URL:
        cur = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ? AND table_schema = 'public'",
            (table,),
        )
        return {row["name"] for row in cur}
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    worldview = load_worldview()
    space_id = str(worldview.get("space_id") or DEFAULT_SPACE_ID)
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reflections (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                source TEXT NOT NULL,
                external_user_id TEXT,
                display_name TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                approved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reflections_status ON reflections(status);
            CREATE INDEX IF NOT EXISTS idx_reflections_parent ON reflections(parent_id);

            CREATE TABLE IF NOT EXISTS media_materials (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                course TEXT NOT NULL DEFAULT '',
                raw_text TEXT NOT NULL,
                summary TEXT NOT NULL,
                questions TEXT NOT NULL DEFAULT '[]',
                participant_text_draft TEXT NOT NULL,
                audio_text_draft TEXT NOT NULL,
                next_live_question TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_media_materials_created ON media_materials(created_at);

            CREATE TABLE IF NOT EXISTS spaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                worldview_path TEXT NOT NULL,
                worldview_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_recordings (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL DEFAULT 'noby-universe',
                title TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'raw',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_source_recordings_space_created ON source_recordings(space_id, created_at);

            CREATE TABLE IF NOT EXISTS derived_contents (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL DEFAULT 'noby-universe',
                source_recording_id TEXT NOT NULL,
                layer TEXT NOT NULL,
                content_type TEXT NOT NULL,
                body_md TEXT NOT NULL,
                audio_path TEXT,
                topic_tags_json TEXT NOT NULL DEFAULT '[]',
                visibility TEXT NOT NULL DEFAULT 'members',
                published_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_derived_recording_type ON derived_contents(source_recording_id, content_type);

            CREATE TABLE IF NOT EXISTS constellations (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL DEFAULT 'noby-universe',
                name TEXT NOT NULL,
                summary_md TEXT NOT NULL,
                generated_question_md TEXT NOT NULL,
                week_of TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_constellations_space_name_week
                ON constellations(space_id, name, week_of);

            CREATE TABLE IF NOT EXISTS constellation_stars (
                constellation_id TEXT NOT NULL,
                star_id TEXT NOT NULL,
                PRIMARY KEY (constellation_id, star_id)
            );
            CREATE INDEX IF NOT EXISTS idx_constellation_stars_star ON constellation_stars(star_id);

            CREATE TABLE IF NOT EXISTS followups (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL DEFAULT 'noby-universe',
                constellation_id TEXT NOT NULL,
                source_recording_id TEXT,
                note_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_followups_constellation ON followups(constellation_id);

            CREATE TABLE IF NOT EXISTS reflux_notifications (
                id TEXT PRIMARY KEY,
                star_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                notified_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reflux_star_kind ON reflux_notifications(star_id, kind);

            CREATE TABLE IF NOT EXISTS relay_events (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL DEFAULT 'noby-universe',
                kind TEXT NOT NULL,
                constellation_id TEXT,
                constellation_name TEXT,
                star_id TEXT,
                star_who TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_relay_events_space_created ON relay_events(space_id, created_at);

            CREATE TABLE IF NOT EXISTS themes (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'active',
                merged_into TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "reflections", "space_id", f"TEXT NOT NULL DEFAULT '{space_id}'")
        ensure_column(conn, "reflections", "star_kind", "TEXT NOT NULL DEFAULT 'insight'")
        ensure_column(conn, "reflections", "visibility", "TEXT NOT NULL DEFAULT 'universe'")
        ensure_column(conn, "reflections", "embedding_json", "TEXT")
        ensure_column(conn, "reflections", "constellation_id", "TEXT")
        ensure_column(conn, "reflections", "themed_at", "TEXT")  # LLMバッチ分類済みの印（NULL=未処理）
        ensure_column(conn, "spaces", "worldview_json", "TEXT")  # スペース別の世界観（マルチテナント）
        conn.execute(
            "INSERT OR IGNORE INTO spaces (id, name, worldview_path, created_at) VALUES (?, ?, ?, ?)",
            (space_id, str(worldview.get("terms", {}).get("universe", "気づきの宇宙")), "worldview.yaml", now_iso()),
        )
        conn.execute("UPDATE reflections SET space_id=? WHERE space_id IS NULL OR space_id=''", (space_id,))
        conn.execute("UPDATE reflections SET star_kind='insight' WHERE star_kind IS NULL OR star_kind=''", ())
        conn.execute("UPDATE reflections SET visibility='universe' WHERE visibility IS NULL OR visibility=''", ())
        # テーマ語彙が空なら、既存の気づきに付いているタグを初期語彙として取り込む（データから育てる）
        theme_count = conn.execute("SELECT COUNT(*) AS n FROM themes").fetchone()["n"]
        if not theme_count:
            seen: set[str] = set()
            for r in conn.execute("SELECT tags FROM reflections"):
                for tag in parse_json_list(r["tags"], default=()):
                    if tag and tag != "未分類":
                        seen.add(tag)
            for tag in seen:
                conn.execute(
                    "INSERT OR IGNORE INTO themes (name, status, created_at) VALUES (?, 'active', ?)",
                    (tag, now_iso()),
                )


def active_theme_names(conn) -> list[str]:
    """現在アクティブなテーマ名を生成順に返す（AI分類で再利用する語彙）。"""
    rows = conn.execute(
        "SELECT name FROM themes WHERE status='active' ORDER BY created_at ASC, name ASC"
    ).fetchall()
    return [row["name"] for row in rows]


def ensure_theme(conn, name: str) -> None:
    """テーマが無ければ追加する（自動創発の保存先）。既存ならそのまま。"""
    name = (name or "").strip()
    if not name or name == "未分類":
        return
    conn.execute(
        "INSERT OR IGNORE INTO themes (name, status, created_at) VALUES (?, 'active', ?)",
        (name, now_iso()),
    )


def hidden_theme_names(conn) -> set:
    """非表示テーマ名の集合（公開ページ・宇宙でのフィルタ用）。"""
    rows = conn.execute("SELECT name FROM themes WHERE status='hidden'").fetchall()
    return {row["name"] for row in rows}


def theme_overview(conn) -> list[dict]:
    """テーマ一覧 + 使用件数（承認済みの気づき基準）。管理画面の表示用。"""
    counts: dict[str, int] = {}
    for r in conn.execute("SELECT tags FROM reflections WHERE status='approved'").fetchall():
        for tag in parse_json_list(r["tags"], default=()):
            if tag and tag != "未分類":
                counts[tag] = counts.get(tag, 0) + 1
    rows = conn.execute(
        "SELECT name, status, merged_into FROM themes ORDER BY created_at ASC, name ASC"
    ).fetchall()
    known = {row["name"] for row in rows}
    overview = [
        {"name": row["name"], "status": row["status"], "merged_into": row["merged_into"], "count": counts.get(row["name"], 0)}
        for row in rows
    ]
    # themes表に未登録だが実データに付いているタグも拾う（保険）
    for name, count in counts.items():
        if name not in known:
            overview.append({"name": name, "status": "active", "merged_into": None, "count": count})
    return overview


def _replace_tag_everywhere(conn, old: str, new: str | None) -> None:
    """全reflectionsのtags JSON内の old を new に置換（new=None なら削除）。重複排除・順序維持。"""
    rows = conn.execute("SELECT id, tags FROM reflections").fetchall()
    for r in rows:
        tags = parse_json_list(r["tags"], default=())
        if old not in tags:
            continue
        out: list[str] = []
        for tag in tags:
            repl = new if tag == old else tag
            if repl and repl not in out:
                out.append(repl)
        conn.execute(
            "UPDATE reflections SET tags=? WHERE id=?",
            (json.dumps(out or ["未分類"], ensure_ascii=False), r["id"]),
        )


def rename_theme(conn, old: str, new: str) -> None:
    """テーマを改名する（実データのタグも一括置換）。"""
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new or old == new:
        return
    _replace_tag_everywhere(conn, old, new)
    ensure_theme(conn, new)
    conn.execute("DELETE FROM themes WHERE name=?", (old,))


def merge_theme(conn, src: str, dst: str) -> None:
    """テーマ src を dst に統合する（タグ置換 + src は履歴として hidden 化）。"""
    src = (src or "").strip()
    dst = (dst or "").strip()
    if not src or not dst or src == dst:
        return
    _replace_tag_everywhere(conn, src, dst)
    ensure_theme(conn, dst)
    ensure_theme(conn, src)
    conn.execute("UPDATE themes SET status='hidden', merged_into=? WHERE name=?", (dst, src))


def set_theme_status(conn, name: str, status: str) -> None:
    """テーマの表示/非表示を切り替える。"""
    name = (name or "").strip()
    if not name or status not in ("active", "hidden"):
        return
    ensure_theme(conn, name)
    conn.execute("UPDATE themes SET status=? WHERE name=?", (status, name))
    if status == "active":
        conn.execute("UPDATE themes SET merged_into=NULL WHERE name=?", (name,))


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
    return [part.strip() for part in parts if part.strip()]


def parse_json_list(raw: str | None, default: Iterable[str] = ("未分類",)) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return list(default)
    if not isinstance(value, list):
        return list(default)
    clean = [str(item).strip() for item in value if str(item).strip()]
    return clean or list(default)


def infer_tags(text: str) -> list[str]:
    tags = []
    for tag, words in THEME_KEYWORDS.items():
        if any(word in text for word in words):
            tags.append(tag)
    return tags[:5] or ["未分類"]


def infer_star_kind(text: str) -> str:
    body = text or ""
    if any(marker in body for marker in ("?", "？", "どう", "なぜ", "何", "でしょうか", "ですか", "教えて", "知りたい", "したら")):
        return "question"
    if any(marker in body for marker in ("職場", "家庭", "子ども", "夫", "妻", "親", "現場", "朝", "今日", "チーム", "上司", "状況")):
        return "situation"
    if any(marker in body for marker in ("感じ", "思いました", "印象", "響き", "残りました", "面白", "楽しかった", "よかった")):
        return "impression"
    return "insight"


def resolve_input_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


def read_recording_text(audio_path: str) -> tuple[str, str]:
    if not audio_path.strip():
        return "", ""
    path = resolve_input_path(audio_path)
    candidates = []
    if path.suffix.lower() in (".txt", ".md"):
        candidates.append(path)
    candidates.append(path.with_suffix(".txt"))
    candidates.append(path.parent / f"{path.name}.txt")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip(), str(candidate)
    return "", ""


def create_source_recording(
    conn: sqlite3.Connection,
    title: str,
    audio_path: str,
    recorded_at: str,
    kind: str = "voice_memo",
    space_id: str | None = None,
    status: str = "raw",
) -> str:
    rid = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO source_recordings
        (id, space_id, title, audio_path, recorded_at, kind, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rid,
            space_id or default_space_id(),
            (title or "無題の原液").strip(),
            (audio_path or "").strip(),
            recorded_at or now_iso(),
            kind or "voice_memo",
            status,
            now_iso(),
        ),
    )
    return rid


def get_recording(conn: sqlite3.Connection, recording_id: str):
    return conn.execute("SELECT * FROM source_recordings WHERE id=?", (recording_id,)).fetchone()


def upsert_derived_content(
    conn: sqlite3.Connection,
    recording_id: str,
    content_type: str,
    layer: str,
    body_md: str,
    topic_tags: list[str] | None = None,
    visibility: str = "members",
    audio_path: str = "",
    space_id: str | None = None,
) -> str:
    existing = conn.execute(
        """
        SELECT id FROM derived_contents
        WHERE source_recording_id=? AND content_type=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (recording_id, content_type),
    ).fetchone()
    topic_tags_json = json.dumps(topic_tags or [], ensure_ascii=False)
    if existing:
        conn.execute(
            """
            UPDATE derived_contents
            SET layer=?, body_md=?, audio_path=?, topic_tags_json=?, visibility=?
            WHERE id=?
            """,
            (layer, body_md, audio_path, topic_tags_json, visibility, existing["id"]),
        )
        return existing["id"]
    cid = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO derived_contents
        (id, space_id, source_recording_id, layer, content_type, body_md, audio_path, topic_tags_json, visibility, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cid, space_id or default_space_id(), recording_id, layer, content_type, body_md, audio_path, topic_tags_json, visibility, now_iso()),
    )
    return cid


def latest_derived(conn: sqlite3.Connection, recording_id: str, content_types: Iterable[str]):
    placeholders = ",".join("?" for _ in content_types)
    types = list(content_types)
    return conn.execute(
        f"""
        SELECT * FROM derived_contents
        WHERE source_recording_id=? AND content_type IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [recording_id, *types],
    ).fetchone()


def transcribe_recording(conn: sqlite3.Connection, recording_id: str) -> dict:
    recording = get_recording(conn, recording_id)
    if not recording:
        raise ValueError(f"source_recording not found: {recording_id}")
    text, source_path = read_recording_text(recording["audio_path"])
    if text:
        cid = upsert_derived_content(conn, recording_id, "transcript_raw", "raw_audio", text, visibility="private")
        conn.execute("UPDATE source_recordings SET status='transcribed' WHERE id=?", (recording_id,))
        return {"recording_id": recording_id, "derived_content_id": cid, "status": "transcribed", "source_path": source_path}
    placeholder = (
        "文字起こし未実行: このローカルPhase 1では外部音声認識サービスを呼びません。"
        "音声ファイルと同名の .txt を置くか、audio_path に .txt/.md を指定してください。"
    )
    cid = upsert_derived_content(conn, recording_id, "transcript_raw", "raw_audio", placeholder, visibility="private")
    return {"recording_id": recording_id, "derived_content_id": cid, "status": recording["status"], "placeholder": True}


def clean_transcript_text(text: str) -> str:
    text = FILLER_RE.sub("", text or "")
    text = re.sub(r"[ 　]+", " ", text)
    sentences = split_sentences(text)
    if not sentences:
        return normalize_text(text)
    paragraphs = []
    for i in range(0, len(sentences), 3):
        paragraphs.append("".join(sentences[i : i + 3]).strip())
    return "\n\n".join(part for part in paragraphs if part)


def clean_recording(conn: sqlite3.Connection, recording_id: str) -> dict:
    raw = latest_derived(conn, recording_id, ["transcript_raw"])
    if not raw:
        raise ValueError("transcript_raw is required before clean")
    body = clean_transcript_text(raw["body_md"])
    cid = upsert_derived_content(conn, recording_id, "transcript_clean", "cleaned", body, visibility="members")
    return {"recording_id": recording_id, "derived_content_id": cid, "chars": len(body)}


def digest_from_text(text: str) -> tuple[str, list[str]]:
    tags = infer_tags(text)
    sentences = split_sentences(text)
    lead = sentences[:4] if sentences else [clip_text(normalize_text(text), 180)]
    bullets = "\n".join(f"- {clip_text(sentence, 90)}" for sentence in lead if sentence)
    tag_text = "、".join(tags)
    digest = f"## トピック\n{tag_text}\n\n## ダイジェスト\n{bullets or '- 本文がまだありません。'}"
    return digest, tags


def digest_recording(conn: sqlite3.Connection, recording_id: str) -> dict:
    source = latest_derived(conn, recording_id, ["transcript_clean", "transcript_raw"])
    if not source:
        raise ValueError("transcript_clean or transcript_raw is required before digest")
    digest, tags = digest_from_text(source["body_md"])
    cid = upsert_derived_content(conn, recording_id, "digest", "digest", digest, topic_tags=tags, visibility="members")
    return {"recording_id": recording_id, "derived_content_id": cid, "topic_tags": tags}


def line_summary_from_text(text: str, max_chars: int = 300) -> str:
    sentences = split_sentences(text)
    base = " ".join(sentences[:3]) if sentences else normalize_text(text)
    if not base:
        base = "今週の原液から、次の気づきにつながる入口が生まれました。"
    if not base.startswith("【"):
        base = f"【今週の原液】{base}"
    return clip_text(base, max_chars)


def summarize_recording(conn: sqlite3.Connection, recording_id: str) -> dict:
    source = latest_derived(conn, recording_id, ["digest", "transcript_clean", "transcript_raw"])
    if not source:
        raise ValueError("digest, transcript_clean, or transcript_raw is required before summarize")
    summary = line_summary_from_text(source["body_md"], 300)
    tags = parse_json_list(source["topic_tags_json"], default=())
    cid = upsert_derived_content(conn, recording_id, "summary", "digest", summary, topic_tags=tags, visibility="members")
    return {"recording_id": recording_id, "derived_content_id": cid, "chars": len(summary)}


def run_recording_pipeline(conn: sqlite3.Connection, recording_id: str, steps: Iterable[str] = ("transcribe", "clean", "digest", "summarize")) -> list[dict]:
    results = []
    for step in steps:
        if step == "transcribe":
            results.append({"step": step, **transcribe_recording(conn, recording_id)})
        elif step == "clean":
            results.append({"step": step, **clean_recording(conn, recording_id)})
        elif step == "digest":
            results.append({"step": step, **digest_recording(conn, recording_id)})
        elif step == "summarize":
            results.append({"step": step, **summarize_recording(conn, recording_id)})
        else:
            raise ValueError(f"unknown pipeline step: {step}")
    return results


def week_start(value: str | None = None) -> str:
    if value:
        day = date.fromisoformat(value[:10])
    else:
        day = datetime.now(timezone.utc).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def approved_star_rows(conn: sqlite3.Connection, week_of: str | None = None, space_id: str | None = None) -> list[sqlite3.Row]:
    params: list[str] = [space_id or default_space_id()]
    where = "WHERE status='approved' AND space_id=? AND visibility IN ('universe', 'nebula')"
    if week_of:
        start = date.fromisoformat(week_start(week_of))
        end = start + timedelta(days=7)
        where += " AND created_at >= ? AND created_at < ?"
        params.extend([start.isoformat(), end.isoformat()])
    return list(conn.execute(f"SELECT * FROM reflections {where} ORDER BY created_at ASC", params))


def constellation_name(tag: str, kind: str) -> str:
    if kind == "question":
        return f"{tag}から生まれた問いの星座"
    if tag == "未分類":
        return f"{kind}の星座"
    return f"{tag}の星座"


def constellation_summary(tag: str, kind: str, stars: list[sqlite3.Row]) -> str:
    bodies = "\n".join(f"- {row['display_name']}: {clip_text(row['body'], 80)}" for row in stars[:5])
    kind_label = {"insight": "気づき", "impression": "感想", "question": "質問", "situation": "状況共有", "mixed": "気づき"}.get(kind, kind)
    return f"{tag} / {kind_label} に関する星が {len(stars)} 件つながりました。\n\n{bodies}"


def generated_question(tag: str, kind: str) -> str:
    if kind == "question":
        return f"この問いの星座に、{worldview_term('owner', '推し')}はどんな声で応えると循環が深まりますか。"
    return TAG_QUESTIONS.get(tag, f"「{tag}」というテーマは、日常のどこに現れていますか。")


def _group_stars(stars: list[sqlite3.Row]) -> list[tuple[str, str, list[sqlite3.Row]]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for star in stars:
        tags = parse_json_list(star["tags"]) or ["未分類"]
        for tag in dict.fromkeys(tags):
            grouped[tag].append(star)
    valid = [(tag, "mixed", rows) for tag, rows in grouped.items() if len(rows) >= 2]
    if valid:
        return sorted(valid, key=lambda item: (-len(item[2]), item[0]))
    if len(stars) >= 2:
        return [("今週の星", "mixed", stars)]
    return []


def upsert_constellation(conn: sqlite3.Connection, name: str, summary_md: str, question_md: str, week_of: str, space_id: str | None = None) -> str:
    sid = space_id or default_space_id()
    existing = conn.execute(
        "SELECT id FROM constellations WHERE space_id=? AND name=? AND week_of=?",
        (sid, name, week_of),
    ).fetchone()
    if existing:
        cid = existing["id"]
        conn.execute(
            "UPDATE constellations SET summary_md=?, generated_question_md=? WHERE id=?",
            (summary_md, question_md, cid),
        )
        conn.execute("DELETE FROM constellation_stars WHERE constellation_id=?", (cid,))
        return cid
    cid = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO constellations (id, space_id, name, summary_md, generated_question_md, week_of, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cid, sid, name, summary_md, question_md, week_of, now_iso()),
    )
    return cid


def embed_star_vector(text: str) -> list[float]:
    """Return a tiny deterministic local embedding placeholder.

    This is intentionally not a semantic embedding API. It gives Phase 1 a stable
    `embedding_json` field for local testing and can be replaced by a real model
    later without changing the schema.
    """
    body = text or ""
    length_bucket = min(len(body) / 280.0, 1.0)
    values = [length_bucket]
    for words in THEME_KEYWORDS.values():
        values.append(1.0 if any(word in body for word in words) else 0.0)
    return values


def embed_stars(conn: sqlite3.Connection, week: str | None = None, space_id: str | None = None) -> dict:
    stars = approved_star_rows(conn, week_of=week_start(week) if week else None, space_id=space_id)
    updated = 0
    for star in stars:
        vector = embed_star_vector(star["body"])
        conn.execute("UPDATE reflections SET embedding_json=? WHERE id=?", (json.dumps(vector), star["id"]))
        updated += 1
    return {"updated": updated, "space_id": space_id or default_space_id(), "week_of": week_start(week) if week else None}


def _star_who(star) -> str:
    if isinstance(star, dict):
        return (star.get("display_name") or "匿名")
    try:
        return star["display_name"] or "匿名"
    except Exception:
        return "匿名"


def _star_id(star) -> str | None:
    if isinstance(star, dict):
        return star.get("id")
    try:
        return star["id"]
    except Exception:
        return None


def record_relay_event(conn, space_id: str, kind: str, constellation_id: str, constellation_name: str, added_stars: list, total_count: int) -> str:
    """光のリレーの出来事を1件記録する（星座の誕生・成長など、アウトプットが生かされた動き）。"""
    whos = list(dict.fromkeys(_star_who(s) for s in added_stars))
    detail = {"star_count": total_count, "added_count": len(added_stars), "added_whos": whos}
    eid = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO relay_events
        (id, space_id, kind, constellation_id, constellation_name, star_id, star_who, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            space_id,
            kind,
            constellation_id,
            constellation_name,
            (_star_id(added_stars[0]) if added_stars else None),
            "・".join(whos),
            json.dumps(detail, ensure_ascii=False),
            now_iso(),
        ),
    )
    return eid


def relay_feed(conn, space_id: str | None = None, limit: int = 8) -> list[dict]:
    """公開ページ用：最近の「宇宙の動き」を新しい順で返す。"""
    sid = space_id or default_space_id()
    rows = conn.execute(
        "SELECT * FROM relay_events WHERE space_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (sid, limit),
    ).fetchall()
    feed = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        except Exception:
            detail = {}
        feed.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "constellation_id": r["constellation_id"],
                "constellation_name": r["constellation_name"],
                "star_who": r["star_who"],
                "detail": detail,
                "created_at": r["created_at"],
            }
        )
    return feed


def constellate_stars(conn: sqlite3.Connection, week: str | None = None, space_id: str | None = None, all_time: bool = False) -> list[dict]:
    week_of = week_start(week)
    sid = space_id or default_space_id()
    embed_week = None if all_time else week_of
    embed_stars(conn, week=embed_week, space_id=space_id)
    stars = approved_star_rows(conn, week_of=embed_week, space_id=space_id)
    groups = _group_stars(stars)
    created = []
    assigned: set[str] = set()
    for tag, kind, group_stars in groups:
        name = constellation_name(tag, kind)
        prior = conn.execute(
            "SELECT id FROM constellations WHERE space_id=? AND name=? AND week_of=?",
            (sid, name, week_of),
        ).fetchone()
        prior_star_ids: set[str] = set()
        if prior:
            prior_star_ids = {
                row["star_id"]
                for row in conn.execute(
                    "SELECT star_id FROM constellation_stars WHERE constellation_id=?", (prior["id"],)
                )
            }
        cid = upsert_constellation(conn, name, constellation_summary(tag, kind, group_stars), generated_question(tag, kind), week_of, space_id)
        for star in group_stars:
            conn.execute("INSERT OR IGNORE INTO constellation_stars (constellation_id, star_id) VALUES (?, ?)", (cid, star["id"]))
            if star["id"] not in assigned:
                conn.execute("UPDATE reflections SET constellation_id=? WHERE id=?", (cid, star["id"]))
                assigned.add(star["id"])
        added_stars = [s for s in group_stars if s["id"] not in prior_star_ids]
        if not prior:
            record_relay_event(conn, sid, "constellation_born", cid, name, group_stars, len(group_stars))
        elif added_stars:
            record_relay_event(conn, sid, "constellation_grew", cid, name, added_stars, len(group_stars))
        created.append({"id": cid, "name": name, "week_of": week_of, "star_count": len(group_stars)})
    return created


def constellations_payload(conn: sqlite3.Connection, week: str | None = None, space_id: str | None = None) -> dict:
    sid = space_id or default_space_id()
    params: list[str] = [sid]
    where = "WHERE c.space_id=?"
    week_of = None
    if week:
        week_of = week_start(week)
        where += " AND c.week_of=?"
        params.append(week_of)
    constellations = []
    for row in conn.execute(f"SELECT c.* FROM constellations c {where} ORDER BY c.week_of DESC, c.created_at DESC", params):
        stars = [
            {
                "id": star["id"],
                "display_name": star["display_name"],
                "body": star["body"],
                "tags": parse_json_list(star["tags"]),
                "star_kind": star["star_kind"],
            }
            for star in conn.execute(
                """
                SELECT r.* FROM constellation_stars cs
                JOIN reflections r ON r.id=cs.star_id
                WHERE cs.constellation_id=?
                ORDER BY r.created_at ASC
                """,
                (row["id"],),
            )
        ]
        constellations.append(
            {
                "id": row["id"],
                "space_id": row["space_id"],
                "name": row["name"],
                "summary_md": row["summary_md"],
                "generated_question_md": row["generated_question_md"],
                "week_of": row["week_of"],
                "created_at": row["created_at"],
                "stars": stars,
                "star_count": len(stars),
            }
        )
    return {"space_id": sid, "week_of": week_of, "constellations": constellations}


def generate_weekly_report(conn: sqlite3.Connection, week: str | None = None, output_dir: str | Path | None = None) -> dict:
    week_of = week_start(week)
    payload = constellations_payload(conn, week=week_of)
    if not payload["constellations"]:
        constellate_stars(conn, week=week_of)
        payload = constellations_payload(conn, week=week_of)
    out_dir = Path(output_dir) if output_dir else ROOT / "outputs" / "weekly_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{week_of}_weekly_constellation_report.md"
    lines = [f"# 週次星座レポート {week_of}", "", "LINE配信は未実装です。このMarkdownはローカル確認用です。", ""]
    notification_count = 0
    for constellation in payload["constellations"]:
        lines.extend(
            [
                f"## {constellation['name']}",
                "",
                constellation["summary_md"],
                "",
                f"**生まれた問い**: {constellation['generated_question_md']}",
                "",
                "### つながった星",
            ]
        )
        for star in constellation["stars"]:
            lines.append(f"- {star['display_name']} ({star['id']}): {clip_text(star['body'], 90)}")
            existing = conn.execute(
                "SELECT id FROM reflux_notifications WHERE star_id=? AND kind='in_constellation' LIMIT 1",
                (star["id"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO reflux_notifications (id, star_id, kind, notified_at) VALUES (?, ?, 'in_constellation', ?)",
                    (uuid.uuid4().hex[:12], star["id"], now_iso()),
                )
                notification_count += 1
        lines.append("")
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return {"path": str(report_path), "week_of": week_of, "constellation_count": len(payload["constellations"]), "reflux_notifications": notification_count}


def suggest_followups(conn: sqlite3.Connection, week: str | None = None, limit: int = 3) -> list[dict]:
    payload = constellations_payload(conn, week=week)
    suggestions = []
    for constellation in payload["constellations"]:
        question_count = sum(1 for star in constellation["stars"] if star["star_kind"] == "question")
        score = constellation["star_count"] + question_count * 2
        reason = f"{constellation['star_count']}件の星"
        if question_count:
            reason += f"、質問の星{question_count}件"
        suggestions.append(
            {
                "id": constellation["id"],
                "name": constellation["name"],
                "score": score,
                "reason": reason,
                "generated_question_md": constellation["generated_question_md"],
                "summary_md": constellation["summary_md"],
            }
        )
    return sorted(suggestions, key=lambda item: (-item["score"], item["name"]))[:limit]


def suggestions_to_markdown(suggestions: list[dict]) -> str:
    if not suggestions:
        return "応答候補はまだありません。まず星座化を実行してください。\n"
    lines = ["# フォローアップ候補トップ3", "", "AIは自動応答しません。推しが声で応えるための選定支援だけを行います。", ""]
    for i, item in enumerate(suggestions, start=1):
        lines.extend(
            [
                f"## {i}. {item['name']} (score={item['score']})",
                "",
                f"- 理由: {item['reason']}",
                f"- 生まれた問い: {item['generated_question_md']}",
                "",
                item["summary_md"],
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def create_followup(
    conn: sqlite3.Connection,
    constellation_id: str,
    note_md: str,
    source_recording_id: str | None = None,
    space_id: str | None = None,
) -> str:
    fid = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO followups (id, space_id, constellation_id, source_recording_id, note_md, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (fid, space_id or default_space_id(), constellation_id, source_recording_id, note_md or "", now_iso()),
    )
    return fid
