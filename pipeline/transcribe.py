#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

try:
    from . import common
except ImportError:  # pragma: no cover - direct script execution
    import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Create transcript_raw for a source recording without external APIs.")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--db", default=str(common.DEFAULT_DB_PATH))
    args = parser.parse_args()

    common.init_db(args.db)
    with common.connect(args.db) as conn:
        result = common.transcribe_recording(conn, args.recording_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
