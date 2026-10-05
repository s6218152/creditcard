import re


_PERIOD_PATTERNS = (
    re.compile(r"(?P<year>(?:19|20)\d{2}|1\d{2})年(?P<month>\d{1,2})月", re.IGNORECASE),
    re.compile(r"(?P<year>20\d{2})(?P<month>0[1-9]|1[0-2])(?=\.pdf$)", re.IGNORECASE),
    re.compile(r"(?P<year>1\d{2})(?P<month>0[1-9]|1[0-2])(?=\.pdf$)", re.IGNORECASE),
)
_MAIL_PREFIX = re.compile(
    r"^\d+_(?:(?:[a-z]{3}_)?\d{1,2}_[a-z]{3}_\d{4}_\d{3,6}_|\d{1,2}_[a-z]{3}_\d{4}_\d{3,6}_)+",
    re.IGNORECASE,
)


def _normalize_filename(filename: str) -> str:
    normalized = _MAIL_PREFIX.sub("", filename.casefold())
    return re.sub(r"(cbgc[cs]-(?:mthly|daily)stmt)_.*(?=\.pdf$)", r"\1_{reference}", normalized)


def _period_and_group(filename: str):
    normalized = _normalize_filename(filename)
    for pattern in _PERIOD_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        year, month = int(match.group("year")), int(match.group("month"))
        if year < 1911:
            year += 1911
        if 1 <= month <= 12:
            start, end = match.span()
            group = f"{normalized[:start]}{{period}}{normalized[end:]}"
            return (year, month), group
    return None, normalized


def latest_statement_flags(filenames: list[str], ranks: list[float]) -> list[bool]:
    if len(filenames) != len(ranks):
        raise ValueError("filenames 與 ranks 長度必須一致")
    groups = {}
    for index, filename in enumerate(filenames):
        period, group = _period_and_group(filename)
        rank = (1, *period) if period else (0, ranks[index])
        groups.setdefault(group, []).append((index, rank))

    keep = [True] * len(filenames)
    for candidates in groups.values():
        latest_rank = max(rank for _, rank in candidates)
        for index, rank in candidates:
            keep[index] = rank == latest_rank
    return keep
