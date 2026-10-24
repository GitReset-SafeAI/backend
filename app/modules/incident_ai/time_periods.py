"""Convert supplied clock times to time periods without guessing AM/PM."""

import re


def normalize_reported_time(value: str) -> str:
    value = value.strip().lower()
    if value in {"morning", "afternoon", "evening", "night", "late_night", "not_sure"}:
        return value
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", value)
    if not match:
        raise ValueError("Use a time period or a clock time such as 17:00 or 5 PM")
    hour, minute, meridiem = match.groups()
    hour, minute = int(hour), int(minute or 0)
    if minute > 59 or hour > 23 or (meridiem and not 1 <= hour <= 12):
        raise ValueError("Invalid clock time")
    if meridiem:
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif 1 <= hour <= 12 and not value.startswith("0"):
        return "not_sure"
    if hour < 5:
        return "late_night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"
