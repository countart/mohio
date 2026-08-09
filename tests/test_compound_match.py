# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Compound-key match test — retrieve and update must AND all match clauses.

Regression guard for the bug where only the FIRST match clause was honored
(e.g. Zork "open mailbox" matched the first puzzle in the room instead of the
open-mailbox puzzle). Stacked `match` clauses = AND.
"""
import sqlite3
import sys
from pathlib import Path

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime, Context
import mohio_data

_passed = 0
_failed = 0


def check(label, got, expect):
    global _passed, _failed
    ok = got == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"
          + ("" if ok else f"  (expected {expect!r})"))
    _passed += ok
    _failed += (not ok)


def _db():
    rt = DbRuntime.__new__(DbRuntime)
    rt.conn = sqlite3.connect(":memory:")
    rt.conn.row_factory = sqlite3.Row
    rt._in_transaction = False
    rt.conn.execute("CREATE TABLE puzzles (id INTEGER, room TEXT, verb TEXT, solved INTEGER)")
    rt.conn.execute("INSERT INTO puzzles VALUES (1,'outside','say',0)")
    rt.conn.execute("INSERT INTO puzzles VALUES (2,'outside','open',0)")
    rt.conn.execute("INSERT INTO puzzles VALUES (3,'inside','open',0)")
    rt.conn.commit()
    return rt


def test_backend_compound():
    print("\n=== backend: retrieve_one_multi / update_multi (AND) ===")
    rt = _db()
    check("compound room+verb -> open mailbox row",
          rt.retrieve_one_multi("puzzles", {"room": "outside", "verb": "open"})["id"], 2)
    check("compound inside+open",
          rt.retrieve_one_multi("puzzles", {"room": "inside", "verb": "open"})["id"], 3)
    check("single match (backward compat) -> first row",
          rt.retrieve_one_multi("puzzles", {"room": "outside"})["id"], 1)
    n = rt.update_multi("puzzles", {"solved": 1}, {"room": "outside", "verb": "open"})
    check("compound update affects exactly one row", n, 1)
    check("only the matched row updated",
          rt.retrieve_one_multi("puzzles", {"id": 2})["solved"], 1)
    check("sibling row untouched",
          rt.retrieve_one_multi("puzzles", {"id": 1})["solved"], 0)


def test_exec_passes_all_matches():
    print("\n=== exec: retrieve block passes ALL match clauses ===")
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    parser = Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)

    class RecDB:
        def __init__(self): self.spec = None
        def retrieve_one_spec(self, table, spec):
            self.spec = spec
            return {"id": 2, "name": "open_mailbox"}

    rec = RecDB()
    _orig_get_conn = Context.get_connection
    Context.get_connection = lambda self, name='db': rec  # inject mock db
    try:
        src = ("retrieve puzzle from db.puzzles\n"
               "    match room to current_room\n"
               "    match verb to action\n"
               "retrieve: done\n")
        MohioInterpreter().run(transform(parser.parse(src), src),
                               request={"current_room": "outside", "action": "open"})
    finally:
        Context.get_connection = _orig_get_conn
    fields = {f for _, pairs in rec.spec for f, _ in pairs}
    check("both match clauses reach the backend (compound AND)",
          fields, {"room", "verb"})


def test_or_not_blocks():
    print("\n=== OR (match any) / NOT (no.match) blocks ===")
    rt = _db()
    rt.conn.execute("ALTER TABLE puzzles ADD COLUMN status TEXT DEFAULT 'open'")
    rt.conn.execute("UPDATE puzzles SET status='solved' WHERE id=1")
    rt.conn.commit()
    check("OR: outside AND (say|open) -> first match (id1)",
          rt.retrieve_one_spec("puzzles",
              [('and', [('room', 'outside')]),
               ('or',  [('verb', 'say'), ('verb', 'open')])])["id"], 1)
    check("OR: inside AND (say|open) -> id3",
          rt.retrieve_one_spec("puzzles",
              [('and', [('room', 'inside')]),
               ('or',  [('verb', 'say'), ('verb', 'open')])])["id"], 3)
    check("NOT: outside AND status<>solved -> id2",
          rt.retrieve_one_spec("puzzles",
              [('and', [('room', 'outside')]),
               ('not', [('status', 'solved')])])["id"], 2)


def test_or_not_exec():
    print("\n=== exec builds AND/OR/NOT spec from blocks ===")
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    parser = Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)

    class RecDB:
        def __init__(self): self.spec = None
        def retrieve_one_spec(self, table, spec):
            self.spec = spec
            return {"id": 1}

    rec = RecDB()
    _orig_get_conn = Context.get_connection
    Context.get_connection = lambda self, name='db': rec
    try:
        src = ("retrieve p from db.t\n"
               "    match room to current_room\n"
               "    match any\n        verb to \"open\"\n        verb to \"read\"\n    match any: done\n"
               "    no.match\n        status to \"solved\"\n    no.match: done\n"
               "retrieve: done\n")
        MohioInterpreter().run(transform(parser.parse(src), src),
                               request={"current_room": "outside"})
    finally:
        Context.get_connection = _orig_get_conn
    check("spec groups in order", [k for k, _ in rec.spec], ['and', 'or', 'not'])


def test_find_or_not_exec():
    """find (not just retrieve) must honor match (AND), match any (OR), and
    no.match (NOT) blocks — including combined — and filter at runtime.
    Regression for the four stacked bugs: optional-closer leak, inline-vs-block
    ambiguity under nesting, transformer _BODY drop, interpreter spec assembly."""
    print("\n=== find honors AND/OR/NOT blocks (runtime) ===")
    import os
    os.environ.setdefault("DATABASE_URL", ":memory:")
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    parser = Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)
    head = ("connect db as sqlite from env.DATABASE_URL\n"
            "shape S\n    method POST\nshape: done\n"
            "listen for\n    new sh.S at /x\n"
            '        save to db.issues\n            project "MOHIO"\n            label "bug"\n            status "Open"\n        save: done\n'
            '        save to db.issues\n            project "MOHIO"\n            label "blocker"\n            status "Done"\n        save: done\n'
            '        save to db.issues\n            project "OTHER"\n            label "bug"\n            status "Open"\n        save: done\n')
    foot = "    new: done\nlisten: done\n"

    def run(fb):
        src = head + fb + foot
        r = MohioInterpreter().run(transform(parser.parse(src), src),
                                   request={"_method": "POST", "_path": "/x"})
        v = getattr(r, "value", r)
        body = v.get("body") if hasattr(v, "get") else v
        return [(x.get("project"), x.get("label"), x.get("status")) for x in body]

    AND = ('        find r in db.issues\n            match\n                project to "MOHIO"\n            match: done\n        find: done\n        give back r\n')
    OR  = ('        find r in db.issues\n            match any\n                label to "bug"\n                label to "blocker"\n            match any: done\n        find: done\n        give back r\n')
    NOT = ('        find r in db.issues\n            no.match\n                status to "Done"\n            no.match: done\n        find: done\n        give back r\n')
    COMB = ('        find r in db.issues\n            match\n                project to "MOHIO"\n            match: done\n'
            '            match any\n                label to "bug"\n                label to "blocker"\n            match any: done\n'
            '            no.match\n                status to "Done"\n            no.match: done\n        find: done\n        give back r\n')
    check("AND project=MOHIO -> 2", len(run(AND)), 2)
    check("OR bug|blocker -> 3", len(run(OR)), 3)
    check("NOT status<>Done -> 2", len(run(NOT)), 2)
    comb = run(COMB)
    check("COMBINED -> 1", len(comb), 1)
    check("COMBINED row is MOHIO/bug/Open", comb[0] if comb else None, ("MOHIO", "bug", "Open"))


if __name__ == "__main__":
    test_backend_compound()
    test_exec_passes_all_matches()
    test_or_not_blocks()
    test_or_not_exec()
    test_find_or_not_exec()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)