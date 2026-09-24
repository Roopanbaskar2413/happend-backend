#!/usr/bin/env python3
"""Per-stay referral numbers: how many people selected a stay while
planning, and how many of those became real (or completed) trips.

This is the data to bring to a hotel when pitching a direct partnership --
"N travelers on our platform picked your property last month" is a much
stronger opener than a cold email.

Usage: python3 backend/scripts/stay_performance.py [city_id] [--days N]
Defaults to pondicherry, all-time.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.engine.catalog import load_catalog  # noqa: E402
from app.orm import SavedPlan, StaySelection  # noqa: E402


def _parse_args(argv: list[str]) -> tuple[str, int | None]:
    city = "pondicherry"
    days = None
    args = list(argv)
    if "--days" in args:
        i = args.index("--days")
        days = int(args[i + 1])
        del args[i : i + 2]
    if args:
        city = args[0]
    return city, days


def main() -> None:
    city, days = _parse_args(sys.argv[1:])
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    catalog = load_catalog(city)
    names_by_id = {s.id: s.name for s in catalog.stays}

    db = SessionLocal()
    try:
        selections = db.query(StaySelection).filter(StaySelection.city == city).all()
        if since:
            selections = [s for s in selections if s.created_at >= since]
        selection_counts = Counter(s.stay_id for s in selections)

        plans = db.query(SavedPlan).filter(SavedPlan.city == city).all()
        if since:
            plans = [p for p in plans if p.created_at >= since]

        saved_counts: Counter[str] = Counter()
        completed_counts: Counter[str] = Counter()
        for plan in plans:
            stay_id = (plan.plan_request_json or {}).get("stay_id")
            if not stay_id:
                continue
            saved_counts[stay_id] += 1
            if plan.status == "completed":
                completed_counts[stay_id] += 1
    finally:
        db.close()

    all_ids = set(selection_counts) | set(saved_counts) | set(names_by_id)
    rows = sorted(all_ids, key=lambda sid: -selection_counts.get(sid, 0))

    window = f"last {days} days" if days else "all-time"
    print(f"Stay performance for {city!r} ({window})\n")
    print(f"{'Stay':40s} {'Selected':>9s} {'Saved plans':>12s} {'Completed':>10s}")
    for sid in rows:
        name = names_by_id.get(sid, f"(unknown stay: {sid})")
        print(
            f"{name[:40]:40s} {selection_counts.get(sid, 0):>9d} "
            f"{saved_counts.get(sid, 0):>12d} {completed_counts.get(sid, 0):>10d}"
        )


if __name__ == "__main__":
    main()
