# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The runtime's closed vocabularies and the pure helpers over them, in a module light enough for
`mio check` to read without loading the interpreter.

Each name here was defined in mohio_interpreter.py and is still reachable there under its old
name, so the runtime and the check-time scans read ONE definition and cannot drift apart. Nothing
here touches a database, a session or a program's values; it is words, sets and patterns.
"""
import re
from decimal import Decimal


_RENDER_EACH_OPEN = re.compile(
    r'^[ \t]*\{\{[ \t]*each[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+in[ \t]+'
    r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)'
    r'(?:[ \t]+take[ \t]+(\d+))?[ \t]*\}\}[ \t\r]*$', re.MULTILINE)
_RENDER_EACH_CLOSE = re.compile(
    r'^[ \t]*\{\{[ \t]*each[ \t]*:[ \t]*done[ \t]*\}\}[ \t\r]*$', re.MULTILINE)


PAGE_FIELDS = frozenset({'current', 'total', 'more', 'count', 'size', 'next', 'prev', 'cursor',
                        # JOINS UNIT 6 (ruled by Ron, 2026-10-03): with.all's paged shape only.
                        # `unmatched` is the one NESTED field (`page.unmatched.count` /
                        # `page.unmatched.start`), resolved by Context.get_dotted's own
                        # one-level-deeper check right after the PAGE_FIELDS membership test
                        # below -- registered here so the membership test itself accepts it.
                        'unmatched', 'kind'})
PAGE_FIELDS_UNMATCHED = frozenset({'count', 'start'})
# THE PROPERTIES THE RUNTIME PUTS ON A LIST A LOOKUP HANDS BACK (`find`, `retrieve.all`), answered
# by the dotted reader (`Context.get_dotted`) from the list itself, never a column of the table:
# `items.count` is a number on every result, 0 on an empty one. One list, so the check-time scans
# and the runtime cannot disagree about what is a built-in and what is a field
# (tests/test_battery_builtin_result_properties_warning.py holds the two together).
LIST_RESULT_PROPERTIES = frozenset({'first', 'last', 'count', 'distinct', 'position', 'pos',
                                    'page', 'unmatched'})
# The retired snake_case spelling -> its replacement, so a program written before the rename
# gets a teaching refusal naming the exact fix, not a silent None or a generic did-you-mean.
PAGE_FIELDS_RETIRED = {
    'has_more': 'more',
    'per_page': 'size',
    'next_cursor': 'cursor',
}


def _page_field_error(sub_key, outer_name):
    """The one message both the runtime intercept (Context.get_dotted) and the check-time
    mirror (mohio_reachability.scan_executor_branch_deferrals) raise for an unrecognized
    `<result>.page.<sub_key>` read -- kept as one function so the two can never drift apart
    on wording. Returns the message string; the caller decides how to raise it (a
    MohioRuntimeError at run, a check-time `err(...)` at check)."""
    if sub_key in PAGE_FIELDS_RETIRED:
        new = PAGE_FIELDS_RETIRED[sub_key]
        return (f"'{outer_name}.page.{sub_key}' is retired -- it was renamed to "
                f"'{outer_name}.page.{new}'. Read '{outer_name}.page.{new}' instead.")
    import difflib as _difflib
    _close = _difflib.get_close_matches(sub_key, sorted(PAGE_FIELDS), n=1)
    _hint = f" Did you mean '{outer_name}.page.{_close[0]}'?" if _close else ""
    return (f"'{sub_key}' is not a known page field on '{outer_name}.page'. Known fields: "
            f"{', '.join(sorted(PAGE_FIELDS))}.{_hint}")


AI_DECIDE_RETURN_TYPES = ('boolean', 'text', 'result',
                          'number', 'decimal', 'integer', 'dec', 'int')
AI_DECIDE_NUMERIC_TYPES = ('number', 'decimal', 'integer', 'dec', 'int')


def _ai_return_type_message(return_type):
    """Why an ai.decide return type is refused, and what to write instead."""
    shown = ', '.join(AI_DECIDE_RETURN_TYPES)
    if str(return_type).startswith('sh.'):
        return (f"ai.decide cannot return a shape yet: `returns {return_type}` is declared but "
                f"the shape-return path is not built.\n"
                f"    It does not fail -- it silently answers `not confident` on every call, "
                f"whatever the inputs, so the decision never actually runs.\n"
                f"    Supported today: {shown}. Return one of those and read the fields you "
                f"need from it.")
    return (f"ai.decide cannot return `{return_type}`.\n"
            f"    Supported today: {shown}.\n"
            f"    An unsupported type used to answer `not confident` on every call rather "
            f"than saying so.")


class Currencies:
    """The currencies a money type may name, with how each displays (symbol, minor-unit places,
    separators). A class, as it was inside the interpreter, so a proof that guards one entry is
    tied to it and goes stale when the table changes."""
    TABLE = {
        'USD': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'EUR': {'symbol': '€', 'places': 2, 'thousands': '.', 'decimal': ',', 'symbol_before': True},
        'GBP': {'symbol': '£', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'JPY': {'symbol': '¥', 'places': 0, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'CHF': {'symbol': 'CHF ', 'places': 2, 'thousands': "'", 'decimal': '.', 'symbol_before': True},
        'CAD': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'AUD': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'CNY': {'symbol': '¥', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'HKD': {'symbol': 'HK$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'SGD': {'symbol': 'S$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'INR': {'symbol': '₹', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'NZD': {'symbol': 'NZ$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'SEK': {'symbol': ' kr', 'places': 2, 'thousands': '\xa0', 'decimal': ',', 'symbol_before': False},
        'NOK': {'symbol': ' kr', 'places': 2, 'thousands': '\xa0', 'decimal': ',', 'symbol_before': False},
        'MXN': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'BRL': {'symbol': 'R$', 'places': 2, 'thousands': '.', 'decimal': ',', 'symbol_before': True},
        'ZAR': {'symbol': 'R', 'places': 2, 'thousands': '\xa0', 'decimal': '.', 'symbol_before': True},
        'KRW': {'symbol': '₩', 'places': 0, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        # THREE-DECIMAL CURRENCIES (ISO 4217 minor unit 3). The Gulf and North-African dinars are
        # divided into 1000, not 100, so a two-place quantize would silently drop the third digit
        # of every amount (0.001 -> 0.00). places=3 keeps them exact; the symbols use the common
        # ASCII currency abbreviations, before the amount, with the international ',' thousands and
        # '.' decimal that these codes are quoted with in cross-border finance.
        'BHD': {'symbol': 'BD ',  'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'KWD': {'symbol': 'KD ',  'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'OMR': {'symbol': 'OMR ', 'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'JOD': {'symbol': 'JD ',  'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'TND': {'symbol': 'DT ',  'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'IQD': {'symbol': 'IQD ', 'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'LYD': {'symbol': 'LD ',  'places': 3, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
    }


CURRENCIES = Currencies.TABLE


# Upload extensions refused outright.
DANGEROUS_UPLOAD_EXT = {
    # Windows executables and installers
    'exe', 'com', 'scr', 'msi', 'msp', 'dll', 'cpl', 'msc', 'appx', 'msix',
    'gadget', 'pif', 'lnk', 'scf', 'inf', 'chm', 'jnlp',
    # Windows shell and scripting
    'bat', 'cmd', 'ps1', 'ps2', 'psc1', 'psc2', 'vbs', 'vbe', 'js', 'jse',
    'wsf', 'wsh', 'sct', 'hta', 'reg',
    # Macro-enabled Office documents and add-ins. The plain formats
    # (docx, xlsx, pptx) cannot carry macros and stay allowed.
    'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'xlsb', 'pptm', 'potm', 'ppam',
    'ppsm', 'sldm', 'xll', 'wll',
    # Java
    'jar', 'class', 'war',
    # Server-side scripts -- an uploaded one becomes remote code execution
    # the moment anything serves the upload directory.
    'php', 'phtml', 'php3', 'php4', 'php5', 'phar', 'asp', 'aspx', 'jsp',
    'jspx', 'cgi', 'pl',
    # macOS, Linux, mobile executables and packages
    'app', 'dmg', 'pkg', 'command', 'deb', 'rpm', 'run', 'out', 'elf',
    'so', 'dylib', 'apk', 'ipa', 'sh', 'bash', 'zsh', 'csh', 'ksh',
    # Disk images
    'iso', 'img', 'vhd', 'vhdx',
    # Ruled permanently blocked (catalog, 2026-07-22).
    # svg carries script and becomes stored cross-site scripting the moment it is
    # served inline. doc/xls/ppt are the legacy Office formats and can carry
    # macros; the modern docx/xlsx/pptx cannot, and stay allowed.
    'svg', 'doc', 'xls', 'ppt',
    # py/rb are paid-tier only and are not available in the free compiler. An
    # uploaded script is remote code execution the moment anything runs it, so
    # the free tier refuses rather than warns.
    'py', 'rb',
}


# The connection word a dotted source may lead with (`db.users`).
SOURCE_QUALIFIERS = ('db',)


def split_table_ref_parts(raw):
    """(table, column) from a dotted source reference, at any depth: strip a leading connection
    qualifier, then the table is the FIRST remaining segment and anything after it is a column or
    sub-selector (`db.users.email` -> ('users', 'email')). The runtime's `_split_table_ref` adds
    the SQL name check on top; a check-time scan reads a parsed NAME, which already satisfies it.
    Returns (None, None) when nothing resolvable is left."""
    text = str(raw if raw is not None else '').strip()
    marker = '__USERVAR__'
    if text.startswith(marker):
        text = text[len(marker):]
    segs = [s for s in text.split('.') if s]
    if segs and segs[0] in SOURCE_QUALIFIERS:
        segs = segs[1:]
    if not segs:
        return None, None
    return segs[0], ('.'.join(segs[1:]) or None)


def value_matches_type(value_py, type_name):
    """True if a Python value satisfies a declared scalar type (`x as int`). A numeric-looking text
    value does NOT satisfy a number type; None (an unassigned typed value) always satisfies its
    type; an unknown type (a shape reference and so on) is not enforced here and passes."""
    if value_py is None:
        return True
    t = str(type_name).lower().split('.')[0]      # dec.2 -> dec
    if str(type_name).upper() in CURRENCIES:
        return (isinstance(value_py, (int, float, Decimal))
                and not isinstance(value_py, bool))
    if t in ('int', 'integer'):
        return isinstance(value_py, int) and not isinstance(value_py, bool)
    if t in ('dec', 'decimal'):
        return isinstance(value_py, (int, float, Decimal)) and not isinstance(value_py, bool)
    if t in ('text', 'string'):
        return isinstance(value_py, str)
    if t in ('boolean', 'bool'):
        return isinstance(value_py, bool)
    return True      # non-scalar / unknown type: not enforced here


# Built-in provider routing table (shipped as data, not hardcoded): name -> {api_format, base_url,
# env_key}, read once from mohio_data/providers.json. A coder-declared provider overrides a built-in.
_BUILTIN_PROVIDERS_CACHE = None


def load_builtin_providers():
    """The shipped provider table, name -> {api_format, base_url, env_key}. Cached per process."""
    global _BUILTIN_PROVIDERS_CACHE
    if _BUILTIN_PROVIDERS_CACHE is None:
        import json as _json
        import mohio_data
        raw = _json.loads(mohio_data.PROVIDERS_PATH.read_text(encoding="utf-8"))
        _BUILTIN_PROVIDERS_CACHE = {k: v for k, v in raw.items() if not k.startswith("_")}
    return dict(_BUILTIN_PROVIDERS_CACHE)


def builtin_provider_names():
    """The set of built-in provider names, for the check-time unknown-provider refusal."""
    return set(load_builtin_providers().keys())
