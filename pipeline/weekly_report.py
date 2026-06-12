#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

try:
    from . import common
except ImportError:  # pragma: no cover
    import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a local weekly constellation report Markdown file.")
    parser.add_argument("--week", help="Any date in the target week, YYYY-MM-DD. Defaults to current week.")
    parser.add_argument("--output-dir", help="Directory for Markdown reports. Defaults to outputs/weekly_reports.")
    parser.add_argument("--db", default=str(common.DEFAULT_DB_PATH))
    args = parser.parse_args()

    common.init_db(args.db)
    with common.connect(args.db) as conn:
        result = common.generate_weekly_report(conn, week=args.week, output_dir=args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
