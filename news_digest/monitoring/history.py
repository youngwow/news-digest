"""RunHistory: append-only per-run pipeline summaries with a tabular view."""

from __future__ import annotations

import json
import os


class RunHistory:
    def __init__(self, path: str):
        self.path = path

    def append(self, record: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read(self, limit: int = 0) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        records: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records[-limit:] if limit > 0 else records

    @staticmethod
    def format_table(records: list[dict]) -> str:
        if not records:
            return "(no history yet)"
        fmt = "{:<24}  {:<7}  {:>5}  {:>6}  {:>10}  {}"
        lines = [fmt.format("run_at", "overall", "steps", "failed", "duration_s", "failed_steps"),
                 fmt.format("-" * 24, "-" * 7, "-" * 5, "-" * 6, "-" * 10, "-" * 12)]
        for r in records:
            duration = r.get("duration_s")
            lines.append(fmt.format(
                r.get("run_at", "?"), r.get("overall", "?"), r.get("total_steps", "?"),
                r.get("failed_count", 0), f"{duration}" if duration is not None else "?",
                ",".join(r.get("failed_steps", [])) or "-"))
        return "\n".join(lines)
