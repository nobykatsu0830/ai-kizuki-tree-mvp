#!/usr/bin/env python3
"""スペースに独自ドメインを設定する（完全分離: Hostヘッダ→スペース解決）。

使い方:
    python3 set_space_domain.py <space_slug> <domain>   # 設定/変更（例: goma.example.jp）
    python3 set_space_domain.py <space_slug> ""         # 解除（パス方式 /s/<slug>/ のみに戻す）

.env に DATABASE_URL があれば本番Postgres、無ければローカルSQLiteに対して実行する。
- 冪等: 同じ値を何度設定してもよい
- ドメイン形式の軽い検証あり（英数字とハイフン、ドット区切り）
- 別スペースに設定済みのドメインは拒否する
- 設定後も /s/<slug>/ の従来URLはそのまま使える（互換維持）
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
        print("usage: python3 set_space_domain.py <space_slug> <domain>")
        print('       python3 set_space_domain.py <space_slug> ""   # 解除')
        sys.exit(2)
    slug, domain = sys.argv[1].strip(), sys.argv[2]
    db_mode = "PostgreSQL" if pc.DATABASE_URL else f"SQLite ({pc.DEFAULT_DB_PATH})"
    print(f"[DB] {db_mode}")
    pc.init_db(pc.DEFAULT_DB_PATH)  # custom_domain 列を確実に用意（additive・冪等）
    with pc.connect(pc.DEFAULT_DB_PATH) as conn:
        if not pc.space_exists(conn, slug):
            print(f"スペース '{slug}' が存在しません。先に作成してください。")
            sys.exit(1)
        try:
            pc.set_space_custom_domain(conn, slug, domain)
        except ValueError as exc:
            print(f"エラー: {exc}")
            sys.exit(1)
    if domain.strip():
        print(f"スペース '{slug}' の独自ドメインを '{pc.normalize_host(domain)}' に設定しました。")
        print("従来の /s/<slug>/ URL もそのまま使えます（互換維持）。")
    else:
        print(f"スペース '{slug}' の独自ドメインを解除しました（パス方式のみ）。")


if __name__ == "__main__":
    main()
