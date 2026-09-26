"""Number/date formatting used consistently by facts, drafts and the validator.

Every number the bot prints is produced here, so the validator can re-derive exactly
which numeric tokens are "grounded" in the contexts.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..timeutil import parse_ts

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}


def indian_int(n: float | int) -> str:
    """12400 -> '12,400'; 150000 -> '1,50,000' (Indian digit grouping)."""
    n = int(round(n))
    neg, s = n < 0, str(abs(int(round(n))))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def pct(x: float, signed: bool = False, decimals: int | None = None) -> str:
    """0.38 -> '38%'; 0.021 -> '2.1%'. Values with |x| > 1.5 are treated as already-percent."""
    v = x * 100 if abs(x) <= 1.5 else x
    if decimals is None:
        decimals = 0 if abs(v) >= 10 or float(v).is_integer() else 1
    txt = f"{abs(v):.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{abs(round(v))}"
    sign = ("+" if v > 0 else "-" if v < 0 else "") if signed else ("-" if v < 0 else "")
    return f"{sign}{txt}%"


def pabs(x: float) -> str:
    """Magnitude only — for phrasing like 'down 50%'."""
    return pct(abs(x))


def money(v) -> str:
    try:
        return "₹" + indian_int(float(str(v).replace(",", "")))
    except ValueError:
        return f"₹{v}"


def date_h(value, with_year: bool = False) -> str | None:
    """'2026-12-15' -> '15 Dec' (or '15 Dec 2026'). Keeps the value's own UTC offset (local date)."""
    dt = value if isinstance(value, datetime) else None
    if dt is None and isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = parse_ts(value)
    if not dt:
        return None
    return f"{dt.day} {MONTHS[dt.month - 1]}" + (f" {dt.year}" if with_year else "")


def weekday_time(value) -> str | None:
    """ISO datetime (keeps its own offset) -> 'Sat 3 May, 8am'."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    hour = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    minute = f":{dt.minute:02d}" if dt.minute else ""
    return f"{dt.strftime('%a')} {dt.day} {MONTHS[dt.month - 1]}, {hour}{minute}{ampm}"


def humanize(token: str) -> str:
    """'delivery_late' -> 'delivery late'; 'kids_yoga_post' -> 'kids yoga post'."""
    return re.sub(r"\s+", " ", str(token).replace("_", " ").replace("-", " ")).strip()


def month_set(month_range: str) -> set[int]:
    """'Nov-Feb' -> {11,12,1,2}; 'Jan' -> {1}; 'Feb 14' -> {2}."""
    names = re.findall(r"[A-Za-z]{3}", month_range or "")
    idx = [_MONTH_IDX[n.lower()] for n in names if n.lower() in _MONTH_IDX]
    if not idx:
        return set()
    if len(idx) == 1:
        return {idx[0]}
    a, b = idx[0], idx[1]
    out, m = set(), a
    while True:
        out.add(m)
        if m == b:
            break
        m = m % 12 + 1
    return out


def short_source(source: str) -> str:
    """'JIDA Oct 2026, p.14' -> 'JIDA'; 'Dental Council of India circular 2026-11-04' -> 'Dental Council of India'."""
    s = re.split(r"[,(]| \d{4}| circular| release| alert| launch| partner| calendar", source or "")[0].strip()
    return s or source
