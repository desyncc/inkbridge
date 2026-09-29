"""
Reads the vault's own Obsidian settings, so daily notes are found and created
exactly where Obsidian's Daily notes core plugin puts them instead of in a
layout InkBridge has to be told about separately.

Obsidian stores that plugin's settings in `<vault>/.obsidian/daily-notes.json`:

    {"folder": "Journal", "format": "YYYY/MM/YYYY-MM-DD", "template": "Templates/Daily"}

Every key is optional; a missing one means Obsidian's default (vault root,
`YYYY-MM-DD`, no template). Formats use Moment.js tokens, rendered here by a
small formatter covering the tokens that make sense in a daily note name.
Names are English: Obsidian localizes month and weekday names to the app's
language, which InkBridge can't see.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_FORMAT = "YYYY-MM-DD"

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


@dataclass
class DailyNoteSettings:
    folder: str = ""
    format: str = DEFAULT_FORMAT
    template: str = ""


def read_daily_note_settings(vault_dir: Path) -> Optional[DailyNoteSettings]:
    """The vault's Daily notes settings, or None if it has none (or they're unreadable)."""
    path = Path(vault_dir) / ".obsidian" / "daily-notes.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    def text(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    return DailyNoteSettings(
        folder=text("folder").strip("/"),
        format=text("format") or DEFAULT_FORMAT,
        template=text("template"),
    )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _locale_week(d: date):
    """Moment's default (en) locale week: weeks start Sunday, week 1 holds Jan 1."""
    jan1 = date(d.year, 1, 1)
    start = jan1 - timedelta(days=(jan1.weekday() + 1) % 7)  # Sunday on/before Jan 1
    week = (d - start).days // 7 + 1
    next_jan1 = date(d.year + 1, 1, 1)
    if d >= next_jan1 - timedelta(days=(next_jan1.weekday() + 1) % 7):
        return d.year + 1, 1
    return d.year, week


# Longest tokens first, so "MMMM" wins over "MM" and "M".
_TOKEN_RE = re.compile(
    r"\[[^\]]*\]|YYYY|YY|Q|MMMM|MMM|Mo|MM|M|DDDD|DDDo|DDD|Do|DD|D"
    r"|dddd|ddd|dd|do|d|E|e|GGGG|GG|gggg|gg|WW|Wo|W|ww|wo|w|HH|H|mm|m|ss|s"
)


def format_moment(value: datetime, fmt: str) -> str:
    """Renders `value` with a Moment.js format string."""
    d = value.date() if isinstance(value, datetime) else value
    iso_year, iso_week, iso_weekday = d.isocalendar()
    week_year, week = _locale_week(d)
    weekday = (d.weekday() + 1) % 7  # Moment: Sunday = 0
    day_of_year = d.timetuple().tm_yday
    hour = getattr(value, "hour", 0)
    minute = getattr(value, "minute", 0)
    second = getattr(value, "second", 0)

    def render(match: re.Match) -> str:
        token = match.group(0)
        if token.startswith("["):
            return token[1:-1]
        return {
            "YYYY": f"{d.year:04d}", "YY": f"{d.year % 100:02d}",
            "Q": str((d.month - 1) // 3 + 1),
            "MMMM": MONTHS[d.month - 1], "MMM": MONTHS[d.month - 1][:3],
            "Mo": _ordinal(d.month), "MM": f"{d.month:02d}", "M": str(d.month),
            "DDDD": f"{day_of_year:03d}", "DDDo": _ordinal(day_of_year), "DDD": str(day_of_year),
            "Do": _ordinal(d.day), "DD": f"{d.day:02d}", "D": str(d.day),
            "dddd": WEEKDAYS[weekday], "ddd": WEEKDAYS[weekday][:3], "dd": WEEKDAYS[weekday][:2],
            "do": _ordinal(weekday), "d": str(weekday),
            "E": str(iso_weekday), "e": str(weekday),
            "GGGG": f"{iso_year:04d}", "GG": f"{iso_year % 100:02d}",
            "gggg": f"{week_year:04d}", "gg": f"{week_year % 100:02d}",
            "WW": f"{iso_week:02d}", "Wo": _ordinal(iso_week), "W": str(iso_week),
            "ww": f"{week:02d}", "wo": _ordinal(week), "w": str(week),
            "HH": f"{hour:02d}", "H": str(hour), "mm": f"{minute:02d}", "m": str(minute),
            "ss": f"{second:02d}", "s": str(second),
        }[token]

    return _TOKEN_RE.sub(render, fmt)


_OFFSET_UNITS = {"d": "days", "w": "weeks", "h": "hours", "m": "minutes", "s": "seconds"}
_VARIABLE_RE = re.compile(
    r"{{\s*(date|time)\s*(?:([+-]\d+)([yqmwdhs]))?\s*(?::(.*?))?\s*}}", re.IGNORECASE
)


def render_template(template: str, note_date: datetime, date_format: str,
                    now: Optional[datetime] = None) -> str:
    """
    Fills in the variables Obsidian's Daily notes plugin supports in a
    template: {{date}}, {{title}}, {{time}}, {{date:FORMAT}}, {{time:FORMAT}},
    an optional offset like {{date+1d:FORMAT}}, {{yesterday}}, {{tomorrow}}.
    Templater syntax (<% %>) is left untouched: only Templater can run it.
    """
    now = now or datetime.now()
    title = format_moment(note_date, date_format).split("/")[-1]

    def variable(match: re.Match) -> str:
        kind, amount, unit, fmt = match.groups()
        base = note_date.replace(hour=now.hour, minute=now.minute, second=now.second)
        if amount:
            n = int(amount)
            if unit in _OFFSET_UNITS:
                base += timedelta(**{_OFFSET_UNITS[unit]: n})
            elif unit in ("y", "q"):
                months = n * (12 if unit == "y" else 3)
                year, month = divmod(base.month - 1 + months, 12)
                base = base.replace(year=base.year + year, month=month + 1)
        if fmt:
            return format_moment(base, fmt.strip())
        if kind.lower() == "time":
            return format_moment(base, "HH:mm")
        return title if not amount else format_moment(base, date_format).split("/")[-1]

    text = _VARIABLE_RE.sub(variable, template)
    text = re.sub(r"{{\s*title\s*}}", title, text, flags=re.IGNORECASE)
    for word, days in (("yesterday", -1), ("tomorrow", 1)):
        other = format_moment(note_date + timedelta(days=days), date_format).split("/")[-1]
        text = re.sub(r"{{\s*" + word + r"\s*}}", other, text, flags=re.IGNORECASE)
    return text
