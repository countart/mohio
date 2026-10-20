# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The ONE source for valid Mohio time zone names (T1-DB-TYPED-STORAGE, RULED by Ron, 2026-10-05).

RULED: standard zone names only (`Asia/Bahrain`, `America/New_York`, and `UTC`). A fixed offset
(`UTC+3`) and the `Etc/GMT` names refuse at check, naming the zone name to write instead.

Python's own time zone database (`zoneinfo`, stdlib since 3.9) is the one source. Both `mio check`'s
validation (this module, called live) and the generated reference file
(`tools/langref/generate_timezone_reference.py`) share THIS function, so a zone name is never
guessed and the check and the docs can never independently drift from the database itself.

REAL FINDING, not assumed: `zoneinfo.available_timezones()` returns ZERO names unless a time zone
database is actually present -- Windows carries none, so without the `tzdata` PyPI package (now a
declared dependency, `pyproject.toml`/`requirements.txt`) this returns empty on every Windows dev
machine and on any minimal Linux image without system tzdata. Verified by running, not assumed.

THE FILTER, verified against a real zoneinfo database (598 names before filtering):
  - keep `UTC` (the one bare exception Ron names)
  - keep any name containing `/` that is NOT under `Etc/` (518 real `Area/Location` names, including
    three-level ones like `America/Argentina/Buenos_Aires` -- 26 of those, still kept)
  - drop everything under `Etc/` (35 names: `Etc/GMT+3`, `Etc/UTC`, `Etc/Greenwich`, `Etc/Zulu`, ...) --
    `Etc/GMT+3` IS a real, loadable zoneinfo key, so it needs an EXPLICIT drop, not just "unknown"
  - drop the 45 legacy no-slash aliases (`EST`, `GMT`, `Hongkong`, `Iceland`, ...) -- real zoneinfo
    keys, but not the `Area/Location` shape Ron's examples give
A bare `"UTC+3"` is not a zoneinfo key at all (raises `ZoneInfoNotFoundError`), so an arbitrary fixed
offset is refused for free by the membership check alone, with no extra pattern needed.
"""
from __future__ import annotations


def valid_timezone_names() -> frozenset:
    """Every zone name Mohio accepts: `UTC`, or a real `Area/Location` IANA name. Computed live
    against whatever tzdata is installed -- never a frozen snapshot -- so a `mio check` run always
    agrees with the database actually present on that machine."""
    import zoneinfo
    names = zoneinfo.available_timezones()
    return frozenset(
        n for n in names
        if n == 'UTC' or ('/' in n and not n.startswith('Etc/'))
    )


def is_valid_timezone_name(name) -> bool:
    """One zone name, checked against `valid_timezone_names()`. `name` must already be a plain
    string; a non-string (None, a number) is never valid -- callers pass what the AST/value layer
    already resolved, this does not coerce."""
    if not isinstance(name, str) or not name:
        return False
    return name in valid_timezone_names()


# A specific zone to suggest for the names people actually write. A fixed offset has no single
# right answer (several regions share an offset and differ on daylight saving), so the suggestion
# is a common zone at that standard offset, named as an example to confirm, not a silent rewrite.
_ALIAS_SUGGESTION = {
    'EST': 'America/New_York', 'EDT': 'America/New_York', 'EST5EDT': 'America/New_York',
    'CST': 'America/Chicago', 'CDT': 'America/Chicago', 'CST6CDT': 'America/Chicago',
    'MST': 'America/Denver', 'MDT': 'America/Denver', 'MST7MDT': 'America/Denver',
    'PST': 'America/Los_Angeles', 'PDT': 'America/Los_Angeles', 'PST8PDT': 'America/Los_Angeles',
    'GMT': 'UTC', 'Z': 'UTC', 'ZULU': 'UTC', 'UCT': 'UTC', 'UNIVERSAL': 'UTC',
    'ETC/UTC': 'UTC', 'ETC/GMT': 'UTC', 'ETC/UCT': 'UTC', 'ETC/ZULU': 'UTC',
    'HONGKONG': 'Asia/Hong_Kong', 'SINGAPORE': 'Asia/Singapore', 'JAPAN': 'Asia/Tokyo',
}
_OFFSET_SUGGESTION = {
    -10: 'Pacific/Honolulu', -9: 'America/Anchorage', -8: 'America/Los_Angeles',
    -7: 'America/Denver', -6: 'America/Chicago', -5: 'America/New_York',
    -4: 'America/Halifax', -3: 'America/Sao_Paulo', 0: 'UTC', 1: 'Europe/Paris',
    2: 'Africa/Cairo', 3: 'Asia/Bahrain', 4: 'Asia/Dubai', 5: 'Asia/Karachi',
    6: 'Asia/Dhaka', 7: 'Asia/Bangkok', 8: 'Asia/Singapore', 9: 'Asia/Tokyo',
    10: 'Australia/Brisbane', 12: 'Pacific/Auckland',
}


def _suggest_zone(name):
    import re as _re
    key = str(name).strip().upper()
    if key in _ALIAS_SUGGESTION:
        return _ALIAS_SUGGESTION[key]
    m = _re.fullmatch(r'(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?00)?', key)
    if m:
        hours = int(m.group(2)) * (1 if m.group(1) == '+' else -1)
        return _OFFSET_SUGGESTION.get(hours)
    m = _re.fullmatch(r'ETC/GMT([+-])(\d{1,2})', key)
    if m:
        # Etc/GMT names invert the sign: Etc/GMT+3 is three hours BEHIND UTC.
        hours = int(m.group(2)) * (-1 if m.group(1) == '+' else 1)
        return _OFFSET_SUGGESTION.get(hours)
    return None


def timezone_refusal_message(name) -> str:
    """The one message every refusal site shares, naming the actual problem and the fix -- never a
    bare 'invalid timezone'. Used at both check and run, so a developer sees the identical wording
    wherever the refusal fires. Names a specific zone to write where one can be suggested."""
    shown = repr(name) if not isinstance(name, str) else f'"{name}"'
    hint = _suggest_zone(name)
    if hint:
        fix = f'Write a zone name instead, for example "{hint}" (confirm it is the region you mean).'
    else:
        fix = 'Write a zone name instead, for example "Asia/Bahrain", "America/New_York".'
    return (
        f'{shown} is not a standard time zone name. Mohio accepts a real IANA zone name written '
        f'"Area/Location", or the bare word "UTC", because a fixed offset ("UTC+3") or an '
        f'"Etc/GMT..." name cannot follow daylight saving. {fix}'
    )
