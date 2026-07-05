#!/usr/bin/env python3
"""気づきのテーマを1日1回まとめて分類するバッチ（ローカルMac + Codex CLI 想定）。

設計:
- 投稿の瞬間は app.py がキーワードで「暫定タグ」を付ける（即時・無料）。
- このバッチが未処理（themed_at IS NULL）の気づきをまとめて codex exec に渡し、
  既存テーマを優先しつつ合わなければ新テーマを1つ生み、本格分類で上書きする。
- 分類した行は themed_at を立てて、次回以降の二重処理を防ぐ。

使い方:
  # 本番DB(Supabase)を対象にするには .env に DATABASE_URL を入れる（未設定ならローカルSQLite）
  python3 batch_classify.py            # 既定40件まで
  python3 batch_classify.py 100        # 一度に処理する件数を指定

cron 例（macOS, 毎日 03:30 に実行 / ログ追記）:
  30 3 * * * cd /Users/noby/product/ai-kizuki-tree-mvp && /usr/bin/python3 batch_classify.py >> outputs/batch_classify.log 2>&1
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent


def _load_dotenv() -> None:
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


# DATABASE_URL を pipeline.common の import 前に読み込む（Postgres分岐は import 時に決まるため）
_load_dotenv()

from pipeline import common as pc  # noqa: E402

BATCH_LIMIT = int(os.environ.get("KIZUKI_BATCH_LIMIT", "40"))

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "themes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "themes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def pending_rows(conn, limit: int = BATCH_LIMIT) -> list:
    """まだLLM分類していない、公開対象の気づきを取得する。"""
    return list(
        conn.execute(
            "SELECT id, body FROM reflections "
            "WHERE themed_at IS NULL AND status IN ('approved','awaiting_consent') "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
    )


def build_prompt(theme_names: list[str], rows: list) -> str:
    theme_list = "、".join(theme_names) if theme_names else "（まだありません）"
    lines = []
    for r in rows:
        body = (r["body"] or "").strip().replace("\n", " ")[:500]
        lines.append(f'- id: {r["id"]}\n  本文: {body}')
    posts = "\n".join(lines)
    return (
        "あなたは「気づきの宇宙」という学びの場の気づき投稿に、短い日本語のテーマ名を付ける分類器です。\n"
        "観点: 笑い・感情・身体感覚・人間関係・自己理解 など。\n"
        "ルール:\n"
        "- 本文に本当に当てはまるテーマだけを選ぶ\n"
        "- できるだけ既存のテーマを再利用する\n"
        "- どれも合わない時だけ新しいテーマを1つまで作る（短い名詞句、2〜8文字目安）\n"
        "- 各投稿に1〜3個\n"
        f"既存のテーマ: {theme_list}\n\n"
        "以下の各投稿にテーマを付け、指定スキーマのJSONだけを返してください:\n"
        f"{posts}"
    )


def _parse_result_json(text: str) -> dict:
    """codex の最終メッセージから results を持つJSONを取り出す。"""
    if not text:
        return {"results": []}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {"results": []}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"results": []}
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        return {"results": []}
    return value


def classify_with_codex(prompt: str, schema: dict = OUTPUT_SCHEMA, timeout: int = 300) -> dict:
    """codex exec を非対話・read-onlyで実行し、構造化された最終メッセージを返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = os.path.join(tmp, "schema.json")
        out_path = os.path.join(tmp, "out.json")
        Path(schema_path).write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C", tmp,
            "-c", 'sandbox_mode="read-only"',
            "--output-schema", schema_path,
            "--output-last-message", out_path,
            prompt,
        ]
        model = os.environ.get("KIZUKI_CODEX_MODEL")
        if model:
            cmd[2:2] = ["-c", f'model="{model}"']
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
        text = Path(out_path).read_text(encoding="utf-8") if Path(out_path).exists() else ""
    return _parse_result_json(text)


def clean_themes(themes) -> list[str]:
    clean: list[str] = []
    for t in themes or []:
        label = str(t).strip()
        if label and label != "未分類" and label not in clean:
            clean.append(label)
        if len(clean) >= 3:
            break
    return clean or ["未分類"]


def apply_classification(conn, results: list) -> int:
    """分類結果を reflections.tags へ反映し、新テーマを語彙表に追加、themed_at を立てる。
    テーマはその気づきが属するスペースに帰属させる（テーマのper-space分離）。"""
    updated = 0
    for item in results:
        rid = str((item or {}).get("id", "")).strip()
        if not rid:
            continue
        themes = clean_themes((item or {}).get("themes"))
        conn.execute(
            "UPDATE reflections SET tags=?, themed_at=? WHERE id=?",
            (json.dumps(themes, ensure_ascii=False), pc.now_iso(), rid),
        )
        row = conn.execute("SELECT space_id FROM reflections WHERE id=?", (rid,)).fetchone()
        row_space = (row["space_id"] if row else None) or pc.DEFAULT_SPACE_ID
        for theme in themes:
            pc.ensure_theme(conn, theme, space_id=row_space)
        updated += 1
    return updated


def weave_all_spaces(conn) -> dict:
    """全スペースで星座を編み直し、光のリレーの出来事（誕生・成長）を記録する。

    all_time=True で週をまたいで古い星も繋ぎ直すため、過去の気づきが
    後から新しい星座に「生かされる」動きが relay_events に残る。
    """
    try:
        space_ids = [s["id"] for s in pc.list_spaces(conn)] or [pc.DEFAULT_SPACE_ID]
    except Exception:
        space_ids = [pc.DEFAULT_SPACE_ID]
    total = 0
    for sid in space_ids:
        created = pc.constellate_stars(conn, space_id=sid, all_time=True)
        total += len(created)
    return {"spaces": len(space_ids), "constellations": total}


def run(limit: int = BATCH_LIMIT) -> int:
    with pc.connect(pc.DEFAULT_DB_PATH) as conn:
        rows = pending_rows(conn, limit)
        updated = 0
        if rows:
            # 分類対象は全スペース混在のため、語彙ヒントも全スペースから合算する
            theme_names: list[str] = []
            try:
                space_ids = [s["id"] for s in pc.list_spaces(conn)] or [pc.DEFAULT_SPACE_ID]
            except Exception:
                space_ids = [pc.DEFAULT_SPACE_ID]
            for sid in space_ids:
                for name in pc.active_theme_names(conn, space_id=sid):
                    if name not in theme_names:
                        theme_names.append(name)
            prompt = build_prompt(theme_names, rows)
            result = classify_with_codex(prompt)
            updated = apply_classification(conn, result.get("results", []))
            print(f"分類完了: {updated}/{len(rows)} 件を更新しました。")
        else:
            print("分類対象の気づきはありません。")
        weave = weave_all_spaces(conn)
        print(f"星座を編みました: {weave['spaces']}スペース / {weave['constellations']}星座（光のリレー更新）")
        # 光の糸（星同士の意味リンク）と、星々から生まれた問いを編む
        from pipeline import weave as weave_mod

        resonance = weave_mod.weave_all(conn)
        print(
            f"光の糸を編みました: {resonance.get('spaces', 0)}スペース / "
            f"糸{resonance.get('new_links', 0)}本 / 問い{resonance.get('new_questions', 0)}件"
        )
        return updated


def main() -> None:
    limit = BATCH_LIMIT
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            print(f"件数の指定が不正です: {sys.argv[1]}")
            sys.exit(2)
    db_mode = "PostgreSQL (Supabase)" if pc.DATABASE_URL else f"SQLite ({pc.DEFAULT_DB_PATH})"
    print(f"[DB] {db_mode}")
    try:
        run(limit)
    except subprocess.TimeoutExpired:
        print("codex がタイムアウトしました。件数を減らして再実行してください。")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"codex 実行に失敗しました: {exc.stderr or exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
