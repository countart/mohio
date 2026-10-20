# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The database engines Mohio knows, in ONE place.

The runtime factory (`_make_db_runtime`) and the check-time refusal (`scan_unknown_db_driver`) both
read this, so an unknown engine is refused the SAME way at `mio check` and `mio run`, and adding an
engine later updates both at once. Kept in a tiny dependency-free module so the check pass can import
it without pulling in the interpreter.
"""

# Every accepted driver spelling, including the aliases each family answers to and the empty string,
# which `connect db` with no engine word means (sqlite). Lowercase; callers lowercase before testing.
SUPPORTED_DB_DRIVERS = frozenset({
    'sqlite', 'sqlite3', '',
    'postgres', 'postgresql',
    'mysql', 'mariadb',
    'mongodb', 'mongo',
    'oracle',
})

# The families the refusal NAMES: one canonical spelling each, no aliases and no empty string, so the
# message reads as a short menu rather than a list of synonyms.
SUPPORTED_DB_DRIVERS_DISPLAY = 'sqlite, postgres, mysql, mongodb or oracle'


def is_supported_driver(driver):
    """True when `driver` is an engine Mohio knows (case-insensitive)."""
    return (driver or '').strip().lower() in SUPPORTED_DB_DRIVERS


def unknown_driver_message(driver):
    """The one refusal for an engine word Mohio does not know. `connect db as csv` reaches it through
    the check pass; `connect db as json` never parses (json is a Mohio word), so the parse-error path
    gives the same text rather than a bare character (RULED by Ron 2026-10-11)."""
    return ("'%s' is not a database Mohio knows. To proceed: use %s."
            % (driver, SUPPORTED_DB_DRIVERS_DISPLAY))


def sqlite_path_from_address(address):
    """The file a `sqlite:` address names, or the address unchanged when it has no `sqlite:` scheme.

    ACCEPTED AS SQLITE (RULED by Ron, 2026-10-07). Other engines are addressed by URL, so a deployment
    reasonably writes SQLite the same way, `sqlite:///app.db`. It was passed to SQLite whole, as a
    file NAMED `sqlite:///app.db`: on Windows the open failed, elsewhere it made a stray file in a
    directory called `sqlite:`, and the app looked connected to a database it was not reading.

        sqlite:///app.db         app.db, relative to where the app runs
        sqlite:////srv/app.db    /srv/app.db (four slashes: an absolute path)
        sqlite:///C:/data/app.db C:/data/app.db
        sqlite://  or  sqlite:///:memory:   in memory

    A host (`sqlite://server/app.db`) or a query (`?mode=ro`) has no meaning Mohio applies here, and
    is refused rather than dropped."""
    s = str(address or '')
    if not s.lower().startswith('sqlite:'):
        return address
    rest = s[len('sqlite:'):]
    if rest in ('', '//', '///:memory:', '//:memory:'):
        return ':memory:'
    if rest.startswith('///') and '?' not in rest and len(rest) > 3:
        return rest[3:]
    raise RuntimeError(
        f"`{s}` is not a SQLite address Mohio can open. A SQLite address names a file: "
        f"`sqlite:///app.db` (relative), `sqlite:////srv/app.db` (absolute), or `sqlite:///:memory:`. "
        f"A server name or a `?` option has no meaning for a SQLite file.\n"
        f"    To proceed: write the file's path after `sqlite:///`, or give the path on its own.")
