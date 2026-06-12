#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

try:
    from . import common
except ImportError:  # pragma: no cover
    import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic local embedding_json placeholders for approved stars.")
    parser.add_argument("--week", help="Any date in the target week, YYYY-MM-DD. Omit to embed all approved stars.")
    parser.add_argument("--db", default=str(common.DEFAULT_DB_PATH))
    args = parser.parse_args()

    common.init_db(args.db)
    with common.connect(args.db) as conn:
        result = common.embed_stars(conn, week=args.week)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
