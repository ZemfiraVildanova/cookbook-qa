"""One structured JSON line per request on stdout (SPEC 5.8, R-70).

Written with `sys.stdout` looked up at call time so test capture and container log
collection both see it. Never contains keys or secrets: the record is built from request
data, retrieval scores and token counts only.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


def emit(record: dict[str, Any]) -> None:
    status = int(record.get("http_status") or 200)
    if status >= 500:
        level = "ERROR"
    elif record.get("retrieval_mode") == "bm25_only":
        level = "WARNING"
    else:
        level = "INFO"
    line = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "level": level, **record}
    sys.stdout.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
