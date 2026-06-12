#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from . import common
except ImportError:  # pragma: no cover
    import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Suggest top 3 constellations for human follow-up. No auto-response.")
    parser.add_argument("--week", help="Any date in the target week, YYYY-MM-DD. Defaults to all weeks.")
    parser.add_argument("--write", help="Optional Markdown output path.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    parser.add_argument("--db", default=str(common.DEFAULT_DB_PATH))
    args = parser.parse_args()

    common.init_db(args.db)
    with common.connect(args.db) as conn:
        suggestions = common.suggest_followups(conn, week=args.week, limit=3)
    if args.json:
        output = json.dumps(suggestions, ensure_ascii=False, indent=2)
    else:
        output = common.suggestions_to_markdown(suggestions)
    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
