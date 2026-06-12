#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

try:
    from . import common
except ImportError:  # pragma: no cover
    import common

DEFAULT_PUBLIC_VAULT_PATH = common.ROOT.parent / "kizuki-universe-public-vault"


def safe_filename(value: str, fallback: str = "untitled") -> str:
    value = (value or "").strip() or fallback
    value = re.sub(r"[\\/:*?\"<>|#\[\]\n\r\t]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value[:80].strip("._ ") or fallback


def md_escape_frontmatter(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def tags_from_raw(raw: str | None) -> list[str]:
    return common.parse_json_list(raw, default=())


def public_star_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
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


def constellation_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM constellations ORDER BY week_of DESC, created_at DESC"))


def ensure_vault_dirs(vault_path: Path) -> None:
    for subdir in ("Stars", "Constellations", "Tags", "Weekly", "System"):
        (vault_path / subdir).mkdir(parents=True, exist_ok=True)


def write_index(vault_path: Path, star_count: int, constellation_count: int) -> Path:
    path = vault_path / "00_はじめに.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "type: index",
                "visibility: public",
                "---",
                "",
                "# 気づきの宇宙 公開保管庫",
                "",
                "この保管庫はNobyの個人Second Brainとは分離された、参加者に見せる前提の公開用Obsidian Vaultです。",
                "未承認・本人のみ・非公開の星は書き出しません。",
                "",
                f"- 公開星数: {star_count}",
                f"- 星座数: {constellation_count}",
                "",
                "## 入口",
                "",
                "- [[Stars]]: 公開された星",
                "- [[Constellations]]: 星座",
                "- [[Tags]]: テーマ",
                "- [[Weekly]]: 週次レポート素材",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def star_markdown(row: sqlite3.Row) -> str:
    tags = tags_from_raw(row["tags"])
    tag_links = "\n".join(f"- [[Tags/{tag}]]" for tag in tags)
    constellation = row["constellation_name"] or ""
    constellation_link = f"[[Constellations/{safe_filename(constellation)}]]" if constellation else "未星座化"
    return "\n".join(
        [
            "---",
            "type: star",
            f"id: {row['id']}",
            "status: approved",
            "visibility: public",
            f"source: {md_escape_frontmatter(row['source'])}",
            f"display_name: {md_escape_frontmatter(row['display_name'])}",
            f"star_kind: {row['star_kind'] or 'insight'}",
            f"created_at: {md_escape_frontmatter(row['created_at'])}",
            "tags:",
            *[f"  - {md_escape_frontmatter(tag)}" for tag in tags],
            "---",
            "",
            f"# {row['display_name']}の星",
            "",
            row["body"],
            "",
            "## 関連",
            "",
            f"- 星座: {constellation_link}",
            tag_links or "- [[Tags/未分類]]",
            "",
        ]
    )


def write_stars(conn: sqlite3.Connection, vault_path: Path) -> list[Path]:
    paths = []
    for row in public_star_rows(conn):
        path = vault_path / "Stars" / f"{safe_filename(row['id'])}.md"
        path.write_text(star_markdown(row), encoding="utf-8")
        paths.append(path)
    return paths


def constellation_markdown(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    stars = list(
        conn.execute(
            """
            SELECT r.* FROM constellation_stars cs
            JOIN reflections r ON r.id=cs.star_id
            WHERE cs.constellation_id=? AND r.status='approved' AND r.visibility='universe'
            ORDER BY r.created_at ASC
            """,
            (row["id"],),
        )
    )
    star_links = "\n".join(f"- [[Stars/{safe_filename(star['id'])}|{star['display_name']}の星]]" for star in stars)
    return "\n".join(
        [
            "---",
            "type: constellation",
            f"id: {row['id']}",
            "visibility: public",
            f"week_of: {md_escape_frontmatter(row['week_of'])}",
            "---",
            "",
            f"# {row['name']}",
            "",
            row["summary_md"],
            "",
            "## 生まれた問い",
            "",
            row["generated_question_md"],
            "",
            "## この星座に含まれる星",
            "",
            star_links or "まだ公開星はありません。",
            "",
        ]
    )


def write_constellations(conn: sqlite3.Connection, vault_path: Path) -> list[Path]:
    paths = []
    for row in constellation_rows(conn):
        path = vault_path / "Constellations" / f"{safe_filename(row['name'])}.md"
        path.write_text(constellation_markdown(conn, row), encoding="utf-8")
        paths.append(path)
    return paths


def write_tags(conn: sqlite3.Connection, vault_path: Path) -> list[Path]:
    tag_to_stars: dict[str, list[sqlite3.Row]] = {}
    for row in public_star_rows(conn):
        for tag in tags_from_raw(row["tags"]):
            tag_to_stars.setdefault(tag, []).append(row)
    paths = []
    for tag, stars in sorted(tag_to_stars.items()):
        path = vault_path / "Tags" / f"{safe_filename(tag)}.md"
        links = "\n".join(f"- [[Stars/{safe_filename(star['id'])}|{star['display_name']}の星]]" for star in stars)
        path.write_text(f"---\ntype: tag\nvisibility: public\n---\n\n# {tag}\n\n{links}\n", encoding="utf-8")
        paths.append(path)
    return paths


def export_public_vault(conn: sqlite3.Connection, vault_path: str | Path = DEFAULT_PUBLIC_VAULT_PATH) -> dict:
    vault = Path(vault_path).expanduser()
    if "2nd-Brain" in str(vault):
        raise ValueError("公開用Vaultの出力先に個人Second Brainは指定しないでください。別保管庫を指定してください。")
    ensure_vault_dirs(vault)
    star_paths = write_stars(conn, vault)
    constellation_paths = write_constellations(conn, vault)
    tag_paths = write_tags(conn, vault)
    index_path = write_index(vault, len(star_paths), len(constellation_paths))
    manifest = {
        "vault_path": str(vault),
        "index": str(index_path),
        "stars": len(star_paths),
        "constellations": len(constellation_paths),
        "tags": len(tag_paths),
        "note": "This is a separate public Obsidian vault. It intentionally excludes pending/self_only/non-public reflections.",
    }
    (vault / "System" / "export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export approved public stars/constellations to a separate Obsidian vault.")
    parser.add_argument("--vault", default=str(DEFAULT_PUBLIC_VAULT_PATH), help="Separate public vault path. Must not be Noby's personal 2nd-Brain.")
    parser.add_argument("--db", default=str(common.DEFAULT_DB_PATH))
    args = parser.parse_args()

    common.init_db(args.db)
    with common.connect(args.db) as conn:
        result = export_public_vault(conn, args.vault)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
