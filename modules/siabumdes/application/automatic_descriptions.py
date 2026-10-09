"""Presentation labels only; accounting identifiers and periods remain unchanged."""


def automatic_description(action, entity, period, *, detail=None, quarterly=False):
    year, month = period.split("-")
    month_number = int(month)
    period_label = f"{month_number:02}/{year}"
    if quarterly:
        start = ((month_number - 1) // 3) * 3 + 1
        period_label = f"{start:02}–{month_number:02}/{year}"
    parts = [action, entity, period_label]
    if detail:
        parts.append("BUMDes" if detail == "BUMDES" else detail)
    return " · ".join(parts)
