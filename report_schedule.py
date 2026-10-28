"""Gate month-end reports before allocating the macOS bank-query runner."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def should_run_report(event_name, *, now=None):
    if event_name == "workflow_dispatch":
        return True
    if event_name != "schedule":
        return False
    now = now if now is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("排程時間必須包含時區")
    today = now.astimezone(ZoneInfo("Asia/Taipei")).date()
    return today.day in (5, 15) or (today + timedelta(days=1)).month != today.month


if __name__ == "__main__":
    result = str(should_run_report(os.getenv("GITHUB_EVENT_NAME", ""))).lower()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"run_report={result}\n")
    print(f"run_report={result}")
