# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Universal record.id, save-as capture, and the dedupe guard — standalone.

Three backend-ergonomics features:
  1. Every table has an auto-increment `id`; find/retrieve return it; it is addressable.
  2. `save to db.X as name` binds the new record (with id) for immediate reuse.
  3. `save to db.X unless <field> exists` skips the insert if a row already matches,
     and returns the existing record.

Verified on SQLite (the dev backend). The Postgres/MySQL/Mongo port needs a live db.
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


CASES = [
    # 1. record.id
    ('find returns id',
     DB + 'save to db.users\n    name "Bo"\nsave: done\n'
          'find r in db.users\nfind: done\ngive back 200 r.first.id\n', 1),
    ('id auto-increments',
     DB + 'save to db.users\n    name "A"\nsave: done\nsave to db.users\n    name "B"\nsave: done\n'
          'find r in db.users\n    where name is "B"\nfind: done\ngive back 200 r.first.id\n', 2),
    ('retrieve returns id',
     DB + 'save to db.users\n    name "Bo"\nsave: done\n'
          'retrieve u from db.users\n    match name to "Bo"\nretrieve: done\ngive back 200 u.id\n', 1),
    ('id is addressable (modify where id)',
     DB + 'save to db.users\n    name "Bo"\n    role "guest"\nsave: done\n'
          'modify every u in db.users\n    where id is 1\n    apply u\n        role "admin"\n'
          '    apply: done\nmodify: done\n'
          'retrieve x from db.users\n    match name to "Bo"\nretrieve: done\ngive back 200 x.role\n', 'admin'),
    # 2. save ... as
    ('save as -> new id',
     DB + 'save to db.users as saved\n    name "Bo"\nsave: done\ngive back 200 saved.id\n', 1),
    ('save as -> full record',
     DB + 'save to db.users as saved\n    name "Bo"\nsave: done\ngive back 200 saved.name\n', 'Bo'),
    ('captured id flows into next write',
     DB + 'save to db.orders as ord\n    total 500\nsave: done\n'
          'save to db.log\n    order_id ord.id\nsave: done\n'
          'find l in db.log\nfind: done\ngive back 200 l.first.order_id\n', 1),
    # 3. dedupe
    ('dedupe blocks double-submit',
     DB + 'save to db.users unless email exists\n    email "a@b.com"\nsave: done\n'
          'save to db.users unless email exists\n    email "a@b.com"\nsave: done\n'
          'find r in db.users\nfind: done\ngive back 200 r.count\n', 1),
    ('dedupe allows different value',
     DB + 'save to db.users unless email exists\n    email "a@b.com"\nsave: done\n'
          'save to db.users unless email exists\n    email "c@d.com"\nsave: done\n'
          'find r in db.users\nfind: done\ngive back 200 r.count\n', 2),
    ('dedupe returns existing record',
     DB + 'save to db.users\n    email "a@b.com"\nsave: done\n'
          'save to db.users as dup unless email exists\n    email "a@b.com"\nsave: done\n'
          'give back 200 dup.id\n', 1),
    ('dedupe preserves original (no overwrite)',
     DB + 'save to db.users unless email exists\n    email "a@b.com"\n    name "Original"\nsave: done\n'
          'save to db.users unless email exists\n    email "a@b.com"\n    name "Changed"\nsave: done\n'
          'retrieve u from db.users\n    match email to "a@b.com"\nretrieve: done\ngive back 200 u.name\n', 'Original'),
]


def test_record_id_and_save_ergonomics():
    failures = []
    for label, src, expected in CASES:
        try:
            got = run(src)
            got_py = got.to_python() if hasattr(got, 'to_python') else got
            if str(got_py) != str(expected):
                failures.append(f"{label}: got {got_py!r} want {expected!r}")
        except Exception as e:
            failures.append(f"{label}: ERROR {str(e).splitlines()[-1][:50]}")
    assert not failures, "failures:\n  " + "\n  ".join(failures)


if __name__ == '__main__':
    fails = []
    for label, src, expected in CASES:
        try:
            got = run(src)
            got_py = got.to_python() if hasattr(got, 'to_python') else got
            ok = str(got_py) == str(expected)
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
                  + ("" if ok else f" (got {got_py!r} want {expected!r})"))
            if not ok:
                fails.append(label)
        except Exception as e:
            print(f"  [ERR ] {label}: {str(e).splitlines()[-1][:50]}")
            fails.append(label)
    print(f"\nRESULTS: {len(CASES) - len(fails)}/{len(CASES)} passed")
    import sys; sys.exit(0 if not fails else 1)
