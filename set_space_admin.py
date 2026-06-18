#!/usr/bin/env python3
"""スペース別の管理者パスワードを設定する（手動オンボード用）。

使い方:
    python3 set_space_admin.py <space_slug> <password>   # 設定/変更
    python3 set_space_admin.py <space_slug> ""           # 解除（本番では管理画面が遮断される）

.env に DATABASE_URL があれば本番Postgres、無ければローカルSQLiteに対して実行する。
パスワードはSHA-256ハッシュで保存され、平文はDBに残らない。
"""
import os
import sys
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


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python3 set_space_admin.py <space_slug> <password>")
        sys.exit(2)
    slug, password = sys.argv[1].strip(), sys.argv[2]
    db_mode = "PostgreSQL" if pc.DATABASE_URL else f"SQLite ({pc.DEFAULT_DB_PATH})"
    print(f"[DB] {db_mode}")
    with pc.connect(pc.DEFAULT_DB_PATH) as conn:
        if not pc.space_exists(conn, slug):
            print(f"スペース '{slug}' が存在しません。先に作成してください。")
            sys.exit(1)
        pc.set_space_admin_token(conn, slug, password)
    print(f"スペース '{slug}' の管理者パスワードを{'解除しました' if not password else '設定しました'}。")


if __name__ == "__main__":
    main()
