#!/usr/bin/env python3
"""List every catalog row marked verified=false, as a work queue.

Usage: python3 backend/scripts/audit_data.py [city_id ...]
With no arguments, audits every plannable (non coming-soon) city.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.catalog import load_catalog, load_cities  # noqa: E402


def audit_city(city_id: str) -> int:
    catalog = load_catalog(city_id)
    unverified = 0
    for section_name, rows in (
        ("place", catalog.places),
        ("food", catalog.food),
        ("stay", catalog.stays),
    ):
        for row in rows:
            if not row.verified:
                unverified += 1
                print(f"[{city_id}] {section_name:5s} {row.id:28s} {row.name}")
    return unverified


def main() -> None:
    requested = sys.argv[1:]
    cities = [c for c in load_cities() if not c.coming_soon]
    if requested:
        cities = [c for c in cities if c.id in requested]

    total = 0
    for city in cities:
        total += audit_city(city.id)

    print(f"\n{total} unverified row(s) across {len(cities)} city/cities.")


if __name__ == "__main__":
    main()
