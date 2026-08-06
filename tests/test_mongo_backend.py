# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""MongoDB backend behavior, verified against mongomock (no real Mongo server needed).

Covers the three bugs fixed 2026-07-06:
  - find with a where-condition (find_many needed the `offset` param)
  - modify / update / remove (needed Mohio string `id` -> Mongo ObjectId `_id`)
  - remove.all (needed a real per-backend method, not raw conn.execute)

Skips cleanly if pymongo/mongomock aren't installed, so CI without Mongo stays green.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
try:
    import mongomock, pymongo
    pymongo.MongoClient = mongomock.MongoClient    # patch the driver for an in-memory Mongo
    _HAVE = True
except Exception:
    _HAVE = False
os.environ['MONGO_URL'] = 'mongodb://localhost/mohio_test'
os.environ.pop('DATABASE_URL', None)

CONNECT = 'connect db as mongodb from env.MONGO_URL\n'


def _run(src):
    from lark import Lark
    from mohio_transformer_ast import transform
    from mohio_interpreter import MohioInterpreter
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
    g = '\n'.join(l for l in raw.splitlines() if not l.strip().startswith('//'))
    P = Lark(g, parser='earley', ambiguity='resolve', propagate_positions=True)
    interp = MohioInterpreter()
    interp.run(transform(P.parse(src), src))
    return interp.shown


def test_save_and_find_where():
    shown = _run(CONNECT + 'save to db.mtest_a\n    name "Alice"\nsave: done\n'
                 'find r in db.mtest_a\n    where name is "Alice"\nfind: done\nshow r.first.id\nshow r.count\n')
    assert shown[-1] == 1, f"find-where count expected 1: {shown}"
    assert isinstance(shown[-2], str) and len(shown[-2]) > 8, f"find id should be an ObjectId string: {shown}"


def test_save_as_captures_id():
    shown = _run(CONNECT + 'save to db.mtest_b as ord\n    total 5\nsave: done\nshow ord.id\n')
    assert isinstance(shown[-1], str) and len(shown[-1]) > 8, f"save-as id: {shown}"


def test_modify_by_row():
    shown = _run(CONNECT + 'save to db.mtest_c\n    name "Bob"\nsave: done\n'
                 'modify every r in db.mtest_c\n    where name is "Bob"\n    apply r\n        name "Bob2"\n    apply: done\nmodify: done\n'
                 'find m in db.mtest_c\n    where name is "Bob2"\nfind: done\nshow m.count\n')
    assert shown[-1] == 1, f"modify+reread count expected 1 (id->_id translation): {shown}"


def test_remove_all_clears():
    shown = _run(CONNECT + 'save to db.mtest_d\n    x "1"\nsave: done\nsave to db.mtest_d\n    x "2"\nsave: done\n'
                 'remove.all from db.mtest_d\nfind r in db.mtest_d\nfind: done\nshow r.count\n')
    assert shown[-1] == 0, f"remove.all should empty the collection: {shown}"


def test_check_count():
    shown = _run(CONNECT + 'save to db.mtest_e\n    n "a"\nsave: done\nsave to db.mtest_e\n    n "b"\nsave: done\n'
                 'check count as total in db.mtest_e\n    on.success\n        show total\ncheck: done\n')
    assert shown[-1] == 2, f"check count expected 2 (Mongo count method): {shown}"


if __name__ == '__main__':
    if not _HAVE:
        print("  SKIP: pymongo/mongomock not installed")
        print("\nRESULTS: 0/0 passed (skipped)")
        sys.exit(0)
    passed = failed = 0
    for fn, label in [(test_save_and_find_where, "save + find-where + id (offset fix)"),
                      (test_save_as_captures_id, "save-as captures ObjectId"),
                      (test_modify_by_row, "modify by row (id->_id fix)"),
                      (test_remove_all_clears, "remove.all clears collection"),
                      (test_check_count, "check count")]:
        try:
            fn(); print(f"  [PASS] {label}"); passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {label}: {e}"); failed += 1
    print(f"\nRESULTS: {passed}/{passed + failed} passed")
