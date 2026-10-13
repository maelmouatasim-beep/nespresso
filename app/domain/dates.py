"""Dates affichées en français, « 22 oct. 2026 » (D-054). Jamais de format ambigu à l'écran."""

from __future__ import annotations

from datetime import date, datetime

MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin",
          "juil.", "août", "sept.", "oct.", "nov.", "déc.")  # fmt: skip


def fr_date(d: date | datetime | None) -> str:
    """« 22 oct. 2026 » ; « — » si vide."""
    if d is None:
        return "—"
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
