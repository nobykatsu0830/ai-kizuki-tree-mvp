from __future__ import annotations

"""光の糸と、星々から生まれた問いを編む夜のバッチ。

設計:
- batch_classify.py が通常のエントリポイントで、分類のあとに全宇宙を静かに編み直す。
- このモジュールは、星同士の意味リンク（光の糸）と、複数の星からにじむ問いを扱う。
- codex が使えない夜も止まらないよう、意味リンクはタグ重なりの安全な規則で必ずフォールバックする。

使い方:
  # ふだんは batch_classify.py から呼ばれる
  python3 -m pipeline.weave
  python3 -m pipeline.weave 10

注記:
- この standalone CLI はローカルSQLiteでの補助実行を想定している。
- DATABASE_URL の分岐は pipeline.common の import 時に決まるため、main() 冒頭でも ROOT/.env を手読みする。
  本番のDB切り替えを含む通常運用は batch_classify.py 側を使う。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline import common as pc

ROOT = Path(__file__).parent.parent

WEAVE_LIMIT = int(os.environ.get("KIZUKI_WEAVE_LIMIT", "20"))
MAX_LINKS_PER_STAR = 3
MAX_NEW_QUESTIONS = 2
QUESTION_STAR_POOL = 40

_CLI_LIMIT_OVERRIDE: int | None = None


def visible_stars(conn, space_id) -> list:
    """公開中の星を古い順に返す。"""
    return list(
        conn.execute(
            f"""
            SELECT id, display_name, body, tags, created_at
            FROM reflections
            WHERE space_id=? AND {pc.VISIBLE_STAR_WHERE}
            ORDER BY created_at ASC
            """,
            (space_id,),
        )
    )


def unwoven_stars(conn, space_id, limit=WEAVE_LIMIT) -> list:
    """まだ光の糸を編んでいない公開中の星を返す。"""
    return list(
        conn.execute(
            f"""
            SELECT id, display_name, body, tags, created_at
            FROM reflections
            WHERE space_id=? AND {pc.VISIBLE_STAR_WHERE} AND woven_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (space_id, limit),
        )
    )


def _parse_codex_json(text: str) -> dict:
    """codex の最終メッセージから辞書JSONを取り出す。"""
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _run_codex(prompt, schema, timeout=300) -> dict:
    """codex exec を read-only で実行し、構造化結果を返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = os.path.join(tmp, "schema.json")
        out_path = os.path.join(tmp, "out.json")
        Path(schema_path).write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C",
            tmp,
            "-c",
            'sandbox_mode="read-only"',
            "--output-schema",
            schema_path,
            "--output-last-message",
            out_path,
            prompt,
        ]
        model = os.environ.get("KIZUKI_CODEX_MODEL")
        if model:
            cmd[2:2] = ["-c", f'model="{model}"']
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
        text = Path(out_path).read_text(encoding="utf-8") if Path(out_path).exists() else ""
    return _parse_codex_json(text)


def codex_available() -> bool:
    """codex CLI が使える夜かどうか。"""
    return shutil.which("codex") is not None and os.environ.get("KIZUKI_WEAVE_NO_CODEX") != "1"


def link_count(conn, space_id, star_id) -> int:
    """その星に現在つながっている糸の本数を返す。"""
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM star_links
        WHERE space_id=? AND (star_a=? OR star_b=?)
        """,
        (space_id, star_id, star_id),
    ).fetchone()
    return int(row["count"] if row else 0)


def _clip_text(text: str, limit: int) -> str:
    body = " ".join((text or "").strip().split())
    return body[:limit]


def _tag_list(row) -> list[str]:
    return pc.parse_json_list(row["tags"], default=())


def _load_existing_link_counts(conn, space_id: str, star_ids: list[str]) -> dict[str, int]:
    return {star_id: link_count(conn, space_id, star_id) for star_id in star_ids}


def _under_link_cap(existing_counts: dict[str, int], added_counts: dict[str, int], star_a: str, star_b: str) -> bool:
    return (
        existing_counts.get(star_a, 0) + added_counts.get(star_a, 0) < MAX_LINKS_PER_STAR
        and existing_counts.get(star_b, 0) + added_counts.get(star_b, 0) < MAX_LINKS_PER_STAR
    )


def _mark_woven(conn, space_id: str, star_ids: list[str]) -> None:
    stamped_at = pc.now_iso()
    for star_id in star_ids:
        conn.execute(
            "UPDATE reflections SET woven_at=? WHERE id=? AND space_id=?",
            (stamped_at, star_id, space_id),
        )


def _fallback_partner(target, others: list, tags_by_id: dict[str, list[str]]):
    target_tags = tags_by_id[target["id"]]
    ranked = []
    for other in others:
        if other["id"] == target["id"]:
            continue
        other_tags = tags_by_id[other["id"]]
        shared = [tag for tag in target_tags if tag in other_tags]
        if not shared:
            continue
        ranked.append((len(shared), other["created_at"] or "", other["id"], other, shared[0]))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    _, _, _, partner, shared_tag = ranked[0]
    return partner, shared_tag


def _link_prompt(visible: list, unwoven_ids: set[str]) -> str:
    lines = []
    for row in visible:
        marker = "新しい星" if row["id"] in unwoven_ids else "既存の星"
        tags = "、".join(_tag_list(row)) or "なし"
        body = _clip_text(row["body"] or "", 150)
        lines.append(f"- {marker} id={row['id']} / tags={tags}\n  本文: {body}")
    stars_text = "\n".join(lines)
    return (
        "あなたは「気づきの宇宙」で、星と星のあいだに静かな光の糸を見つける編み手です。\n"
        "ルール:\n"
        "- 意味のある響き合いだけを選ぶ\n"
        "- 各リンクは、少なくとも片側が「新しい星」であること\n"
        "- reason は60字以内、日本語の静かな一行\n"
        "- 夜の宇宙・照らし合いの世界観で、説教くさくしない\n"
        "- 指定スキーマのJSONだけを返す\n\n"
        f"{stars_text}"
    )


def _question_prompt(pool: list, existing_questions: list[dict]) -> str:
    star_lines = []
    for row in pool:
        body = _clip_text(row["body"] or "", 150)
        star_lines.append(f"- id={row['id']} / {row['display_name'] or '名前なし'}\n  本文: {body}")
    existing_text = "\n".join(f"- {item['question']}" for item in existing_questions) or "（まだありません）"
    return (
        "実際の星の本文を読み、複数の星に共通して流れている「みんなの問い」を最大2つ生成してください。\n"
        "ルール:\n"
        "- 既存の問い一覧と同旨のものは作らない\n"
        "- 各問いは40〜90字の日本語\n"
        "- 開かれた問いにする（答えを一つに決めつけない・説教くさくない・体感に触れる）\n"
        "- source star_ids は実在IDのみ、2〜6個\n"
        "- context は一行の補足として短くまとめる\n"
        "- 指定スキーマのJSONだけを返す\n\n"
        "既存の問い:\n"
        f"{existing_text}\n\n"
        "星の候補:\n"
        f"{'\n'.join(star_lines)}"
    )


def _link_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["a", "b", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["links"],
        "additionalProperties": False,
    }


def _question_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "context": {"type": "string"},
                        "star_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["question", "context", "star_ids"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }


def weave_links(conn, space_id) -> dict:
    """新しい星を起点に、意味のある光の糸を編む。"""
    limit = _CLI_LIMIT_OVERRIDE if _CLI_LIMIT_OVERRIDE is not None else WEAVE_LIMIT
    unwoven = unwoven_stars(conn, space_id, limit=limit)
    if not unwoven:
        return {"new_links": 0, "processed": 0, "mode": "none"}

    visible = visible_stars(conn, space_id)
    visible_ids = {row["id"] for row in visible}
    tags_by_id = {row["id"]: _tag_list(row) for row in visible}
    existing_counts = _load_existing_link_counts(conn, space_id, list(visible_ids))
    added_counts: dict[str, int] = {}
    inserted_pairs: list[list[str]] = []
    new_links = 0
    mode = "fallback"

    if codex_available():
        try:
            prompt = _link_prompt(visible, {row["id"] for row in unwoven})
            result = _run_codex(prompt, _link_schema())
            raw_links = result.get("links")
            if isinstance(raw_links, list) and raw_links:
                mode = "codex"
                for item in raw_links:
                    a = str((item or {}).get("a", "")).strip()
                    b = str((item or {}).get("b", "")).strip()
                    reason = str((item or {}).get("reason", "")).strip()
                    if a not in visible_ids or b not in visible_ids or a == b:
                        continue
                    if not _under_link_cap(existing_counts, added_counts, a, b):
                        continue
                    if pc.upsert_star_link(conn, a, b, reason, space_id=space_id):
                        new_links += 1
                        added_counts[a] = added_counts.get(a, 0) + 1
                        added_counts[b] = added_counts.get(b, 0) + 1
                        pair = list(sorted((a, b)))
                        inserted_pairs.append(pair)
            else:
                mode = "fallback"
        except Exception:
            mode = "fallback"

    if mode == "fallback":
        for row in unwoven:
            partner_info = _fallback_partner(row, visible, tags_by_id)
            if not partner_info:
                continue
            partner, shared_tag = partner_info
            a = row["id"]
            b = partner["id"]
            if not _under_link_cap(existing_counts, added_counts, a, b):
                continue
            reason = f"「{shared_tag}」というテーマで響き合っています"
            if pc.upsert_star_link(conn, a, b, reason, space_id=space_id):
                new_links += 1
                added_counts[a] = added_counts.get(a, 0) + 1
                added_counts[b] = added_counts.get(b, 0) + 1
                pair = list(sorted((a, b)))
                inserted_pairs.append(pair)

    # codexが吟味した星だけを「編み込み済み」にする。フォールバックの夜に相棒が
    # 見つからなかった星は未編みのまま残し、後日codexが再挑戦できるようにする
    # （無条件刻印は糸の成長上限になる—Cato監査 2026-07-04）。
    if mode == "codex":
        _mark_woven(conn, space_id, [row["id"] for row in unwoven])
    else:
        linked_ids = {sid for pair in inserted_pairs for sid in pair}
        _mark_woven(conn, space_id, [row["id"] for row in unwoven if row["id"] in linked_ids])
    if new_links > 0:
        pc.record_relay(
            conn,
            space_id,
            "link_woven",
            {"link_count": new_links, "pairs": inserted_pairs[:6]},
        )
    return {"new_links": new_links, "processed": len(unwoven), "mode": mode}


def emerge_questions(conn, space_id) -> dict:
    """複数の星から立ち上がる問いを生む。"""
    if not codex_available():
        return {"new_questions": 0, "skipped": "no-codex"}

    pool = list(
        conn.execute(
            f"""
            SELECT id, display_name, body, created_at
            FROM reflections
            WHERE space_id=? AND {pc.VISIBLE_STAR_WHERE}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (space_id, QUESTION_STAR_POOL),
        )
    )
    existing = pc.active_questions(conn, space_id=space_id, limit=20)
    try:
        result = _run_codex(_question_prompt(pool, existing), _question_schema())
    except Exception:
        return {"new_questions": 0}

    pool_by_id = {row["id"]: row for row in pool}
    seen_questions = {str(item["question"]).strip() for item in existing}
    inserted = 0
    for item in result.get("questions", []) if isinstance(result.get("questions"), list) else []:
        if inserted >= MAX_NEW_QUESTIONS:
            break
        question = str((item or {}).get("question", "")).strip()
        if not question or question in seen_questions:
            continue
        raw_ids = (item or {}).get("star_ids")
        if not isinstance(raw_ids, list):
            continue
        valid_ids: list[str] = []
        for star_id in raw_ids:
            sid = str(star_id).strip()
            if sid in pool_by_id and sid not in valid_ids:
                valid_ids.append(sid)
        if len(valid_ids) < 2:
            continue
        context = str((item or {}).get("context", "")).strip()
        pc.create_emergent_question(conn, question, context, valid_ids, space_id=space_id)
        names = [str(pool_by_id[sid]["display_name"] or sid) for sid in valid_ids]
        pc.record_relay(
            conn,
            space_id,
            "question_born",
            {"question": question[:80], "source_whos": names},
            star_who="・".join(names),
        )
        seen_questions.add(question)
        inserted += 1
    return {"new_questions": inserted}


def weave_space(conn, space_id) -> dict:
    """ひとつの宇宙で糸と問いをまとめて編む。"""
    link_result = weave_links(conn, space_id)
    question_result = emerge_questions(conn, space_id)
    merged = {"space_id": space_id}
    merged.update(link_result)
    merged.update(question_result)
    return merged


def weave_all(conn) -> dict:
    """全スペースを順番に編み、合計だけ返す。"""
    try:
        space_ids = [row["id"] for row in pc.list_spaces(conn)] or [pc.DEFAULT_SPACE_ID]
    except Exception:
        space_ids = [pc.DEFAULT_SPACE_ID]
    summaries = [weave_space(conn, space_id) for space_id in space_ids]
    return {
        "spaces": len(summaries),
        "new_links": sum(int(item.get("new_links", 0)) for item in summaries),
        "new_questions": sum(int(item.get("new_questions", 0)) for item in summaries),
    }


def _load_dotenv_for_main() -> None:
    if os.environ.get("AI_KIZUKI_SKIP_DOTENV") == "1":
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv_for_main()
    limit = WEAVE_LIMIT
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            print(f"件数の指定が不正です: {sys.argv[1]}")
            sys.exit(2)
    global _CLI_LIMIT_OVERRIDE
    _CLI_LIMIT_OVERRIDE = limit
    with pc.connect(pc.DEFAULT_DB_PATH) as conn:
        try:
            space_ids = [row["id"] for row in pc.list_spaces(conn)] or [pc.DEFAULT_SPACE_ID]
        except Exception:
            space_ids = [pc.DEFAULT_SPACE_ID]
        total_links = 0
        total_questions = 0
        for space_id in space_ids:
            result = weave_space(conn, space_id)
            total_links += int(result.get("new_links", 0))
            total_questions += int(result.get("new_questions", 0))
            print(
                f"[{space_id}] links={result.get('new_links', 0)} "
                f"questions={result.get('new_questions', 0)} "
                f"processed={result.get('processed', 0)} mode={result.get('mode', 'none')}"
            )
        print(f"total spaces={len(space_ids)} links={total_links} questions={total_questions}")


if __name__ == "__main__":
    main()
