# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Full test battery for the raw sql escape hatch — standalone (runs without pytest).

Covers the whole surface a developer would reach into raw SQL for:
  reads (joins, subqueries, CTEs, aggregates, ordering, paging),
  writes (insert/update/delete with count + id),
  multi-statement scripts and the quote-aware splitter,
  safety (parameterized {{ }}, fail-loud, rollback, row-factory isolation),
  guardrails (blocked in certified sectors).

Hand this to the test chat as the sql-block regression battery.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

DB = 'connect db as sqlite from env.DATABASE_URL\n'


def run(src):
    return MohioInterpreter().run(transform(_P.parse(src), src)).get('body')


# Shared fixture: two related tables with a few rows, built via raw sql.
FIX = (DB +
       'sql\n    CREATE TABLE authors (id INTEGER, name TEXT)\nsql: done\n'
       'sql\n    CREATE TABLE books (id INTEGER, author_id INTEGER, title TEXT, price INTEGER)\nsql: done\n'
       'sql\n    INSERT INTO authors (id, name) VALUES (1, "Ada");\n'
       '    INSERT INTO authors (id, name) VALUES (2, "Bao");\n'
       '    INSERT INTO authors (id, name) VALUES (3, "Cy")\nsql: done\n'
       'sql\n    INSERT INTO books (id, author_id, title, price) VALUES (1, 1, "Loops", 30);\n'
       '    INSERT INTO books (id, author_id, title, price) VALUES (2, 1, "Types", 40);\n'
       '    INSERT INTO books (id, author_id, title, price) VALUES (3, 2, "Sagas", 50)\nsql: done\n')

# ---- value cases: (label, src, expected) --------------------------------------
CASES = [
    # --- reads ---
    ('simple SELECT',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT count(*) as c FROM authors\n    sql: done\nretrieve: done\ngive back 200 r.first.c\n', 3),
    ('inner JOIN',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT a.name, b.title FROM books b JOIN authors a ON a.id = b.author_id '
           'WHERE b.title = "Sagas"\n    sql: done\nretrieve: done\ngive back 200 r.first.name\n', 'Bao'),
    ('LEFT JOIN keeps unmatched',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT a.name FROM authors a LEFT JOIN books b ON a.id = b.author_id '
           'WHERE b.id IS NULL\n    sql: done\nretrieve: done\ngive back 200 r.first.name\n', 'Cy'),
    ('aggregate GROUP BY',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT author_id, count(*) as n FROM books GROUP BY author_id '
           'ORDER BY n DESC\n    sql: done\nretrieve: done\ngive back 200 r.first.n\n', 2),
    ('SUM aggregate',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT sum(price) as total FROM books\n    sql: done\nretrieve: done\ngive back 200 r.first.total\n', 120),
    ('subquery',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT title FROM books WHERE author_id = '
           '(SELECT id FROM authors WHERE name = "Bao")\n    sql: done\nretrieve: done\ngive back 200 r.first.title\n', 'Sagas'),
    ('CTE (WITH)',
     FIX + 'retrieve r from db.*\n    sql\n        WITH pricey AS (SELECT * FROM books WHERE price >= 40) '
           'SELECT count(*) as c FROM pricey\n    sql: done\nretrieve: done\ngive back 200 r.first.c\n', 2),
    ('ORDER BY + LIMIT',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT title FROM books ORDER BY price DESC LIMIT 1\n    sql: done\nretrieve: done\n'
           'give back 200 r.first.title\n', 'Sagas'),
    ('row count of a SELECT',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT * FROM books\n    sql: done\nretrieve: done\ngive back 200 r.count\n', 3),
    # --- writes: count + id ---
    ('INSERT result.id (new auto id)',
     DB + 'sql\n    CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)\nsql: done\n'
          'retrieve r from db.*\n    sql\n        INSERT INTO t (name) VALUES ("Zed")\n    sql: done\nretrieve: done\ngive back 200 r.id\n', 1),
    ('UPDATE result.count',
     FIX + 'retrieve r from db.*\n    sql\n        UPDATE books SET price = 99 WHERE author_id = 1\n    sql: done\nretrieve: done\n'
           'give back 200 r.count\n', 2),
    ('DELETE result.count',
     FIX + 'retrieve r from db.*\n    sql\n        DELETE FROM books WHERE price = 50\n    sql: done\nretrieve: done\ngive back 200 r.count\n', 1),
    # --- multi-statement + splitter ---
    ('multi-statement script',
     DB + 'sql\n    CREATE TABLE m (n INTEGER);\n    INSERT INTO m VALUES (1);\n'
          '    INSERT INTO m VALUES (2)\nsql: done\n'
          'retrieve r from db.*\n    sql\n        SELECT count(*) as c FROM m\n    sql: done\nretrieve: done\ngive back 200 r.first.c\n', 2),
    ('semicolon inside string literal not split',
     DB + 'sql\n    CREATE TABLE notes (txt TEXT)\nsql: done\n'
          'sql\n    INSERT INTO notes (txt) VALUES (\'a;b;c\')\nsql: done\n'
          'retrieve r from db.*\n    sql\n        SELECT txt FROM notes\n    sql: done\nretrieve: done\ngive back 200 r.first.txt\n', 'a;b;c'),
    ("doubled-quote escape inside string",
     DB + "sql\n    CREATE TABLE q (txt TEXT)\nsql: done\n"
          "sql\n    INSERT INTO q (txt) VALUES ('it''s; ok')\nsql: done\n"
          "retrieve r from db.*\n    sql\n        SELECT txt FROM q\n    sql: done\nretrieve: done\ngive back 200 r.first.txt\n", "it's; ok"),
    # --- safety: parameterized interpolation ---
    ('{{ }} bound parameter matches',
     FIX + 'hold who "Ada"\nretrieve r from db.*\n    sql\n'
           '        SELECT count(*) as c FROM authors WHERE name = {{ who }}\n'
           '    sql: done\nretrieve: done\ngive back 200 r.first.c\n', 1),
    ('{{ }} injection attempt is data, not sql',
     FIX + 'hold evil "Ada\\"; DROP TABLE authors; --"\n'
           'retrieve r from db.*\n    sql\n        SELECT count(*) as c FROM authors WHERE name = {{ evil }}\n    sql: done\nretrieve: done\n'
           'retrieve r2 from db.*\n    sql\n        SELECT count(*) as still FROM authors\n    sql: done\nretrieve: done\ngive back 200 r2.first.still\n', 3),
    # --- interop: sql must not corrupt later ORM ops (row_factory) ---
    ('sql then find works',
     FIX + 'retrieve r from db.*\n    sql\n        SELECT * FROM authors\n    sql: done\nretrieve: done\n'
           'save to db.things\n    tag "x"\nsave: done\nfind t in db.things\nfind: done\n'
           'give back 200 t.count\n', 1),
    ('sql then modify works',
     DB + 'save to db.items\n    tag "a"\nsave: done\n'
          'retrieve r from db.*\n    sql\n        SELECT 1 as one\n    sql: done\nretrieve: done\n'
          'modify every it in db.items\n    where tag is "a"\n    apply it\n        tag "b"\n'
          '    apply: done\nmodify: done\nfind b in db.items\n    where tag is "b"\nfind: done\n'
          'give back 200 b.count\n', 1),
]

# ---- error / guardrail cases: (label, src, keyword-in-error) ------------------
ERROR_CASES = [
    ('bad SQL fails loud',
     DB + 'retrieve r from db.*\n    sql\n        SELECT * FROM ghost_table\n    sql: done\nretrieve: done\ngive back 200 "ran"\n', 'sql.error'),
    ('syntax error fails loud',
     DB + 'retrieve r from db.*\n    sql\n        SELECT FROM WHERE\n    sql: done\nretrieve: done\ngive back 200 "ran"\n', 'sql.error'),
    ('blocked in financial sector',
     'sector: financial\n' + DB + 'retrieve r from db.*\n    sql\n        SELECT 1\n    sql: done\nretrieve: done\ngive back 200 "ran"\n',
     'blocked_in_certified_sector'),
    ('blocked in healthcare sector',
     'sector: healthcare\n' + DB + 'retrieve r from db.*\n    sql\n        SELECT 1\n    sql: done\nretrieve: done\ngive back 200 "ran"\n',
     'blocked_in_certified_sector'),
    ('blocked in hierarchical financial sector',
     'sector: financial.banking.retail\n' + DB + 'retrieve r from db.*\n    sql\n        SELECT 1\n    sql: done\nretrieve: done\ngive back 200 "ran"\n',
     'blocked_in_certified_sector'),
]

# ---- allowed case: raw sql IS allowed outside certified sectors ---------------
ALLOWED_CASES = [
    ('allowed in education sector',
     'sector: education\n' + DB + 'save to db.n\n    v 5\nsave: done\n'
     'retrieve r from db.*\n    sql\n        SELECT v FROM n\n    sql: done\nretrieve: done\ngive back 200 r.first.v\n', 5),
]


def _err(src):
    try:
        got = run(src)
        return str(got.to_python() if hasattr(got, 'to_python') else got)
    except Exception as e:
        return str(e)


def _check(label, src, expected):
    try:
        got = run(src)
        got_py = got.to_python() if hasattr(got, 'to_python') else got
        return (str(got_py) == str(expected), got_py)
    except Exception as e:
        return (False, 'EXC ' + str(e).splitlines()[-1][:44])


def test_sql_battery():
    fails = []
    for label, src, expected in CASES + ALLOWED_CASES:
        ok, got = _check(label, src, expected)
        if not ok:
            fails.append(f"{label}: got {got!r} want {expected!r}")
    for label, src, keyword in ERROR_CASES:
        if keyword not in _err(src):
            fails.append(f"{label}: expected error {keyword!r}")
    assert not fails, "sql battery failures:\n  " + "\n  ".join(fails)


if __name__ == '__main__':
    fails = []
    print("-- value + allowed --")
    for label, src, expected in CASES + ALLOWED_CASES:
        ok, got = _check(label, src, expected)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f" (got {got!r} want {expected!r})"))
        if not ok:
            fails.append(label)
    print("-- guardrails --")
    for label, src, keyword in ERROR_CASES:
        ok = keyword in _err(src)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f" (no {keyword!r})"))
        if not ok:
            fails.append(label)
    total = len(CASES) + len(ALLOWED_CASES) + len(ERROR_CASES)
    print(f"\nRESULTS: {total - len(fails)}/{total} passed")
    import sys; sys.exit(0 if not fails else 1)
