"""Cross-dialect divergence sweep (overnight run, Stage 1, 2026-08-03).

One Mohio program body, exercised identically against SQLite and Postgres (both real, not
mocked -- Postgres via a locally reachable instance). Diffs the `show` output between dialects.
A divergence here means the SAME Mohio source produces different observable behavior depending
on which database backend is connected -- a portability bug users would hit silently.

DRAFT / investigative script for the overnight run. Not part of the regression gate.
Run: `python tests/test_cross_dialect_sweep.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_RAW = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark')).read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

POSTGRES_URL = os.environ.get('MOHIO_SWEEP_POSTGRES_URL',
                              'postgresql://postgres:postgres@localhost:5432/postgres')

# Body reused verbatim for both dialects -- only the `connect` line's dialect word differs.
BODY = '''
// Every row gets an explicit TEXT id from the first write, so the auto-created id column is
// TEXT PRIMARY KEY (not SERIAL) from the start -- required for the id-matched upsert below to
// be well-typed on both backends. See the Stage 1 finding: id is the only column Mohio's
// auto-schema gives a real uniqueness guarantee to, so it's the fair column to upsert on.
save to db.sweep_items
    id "widget-1"
    grp "a"
    name "widget"
    qty 3
save: done

save to db.sweep_items
    id "gadget-1"
    grp "a"
    name "gadget"
    qty 1
save: done

save to db.sweep_items
    id "gizmo-1"
    grp "b"
    name "gizmo"
    qty 7
save: done

// find + where
find grp_a in db.sweep_items
    where grp is "a"
find: done
show "find where grp=a count {{ grp_a.count }}"

// retrieve + match (single field)
retrieve one from db.sweep_items
    match name to "widget"
    on.failure show "retrieve MISS"
retrieve: done
show "retrieve match name=widget -> {{ one.name }} qty {{ one.qty }}"

// update
update db.sweep_items
    match name to "widget"
    qty 99
update: done
retrieve after_update from db.sweep_items
    match name to "widget"
retrieve: done
show "after update widget qty {{ after_update.qty }}"

// upsert -- insert new. Matched on `id`, the ONLY column Mohio's auto-schema gives a real
// uniqueness guarantee to (see Stage 1 finding: no other column gets a UNIQUE constraint, so
// upsert on a non-id field fails on Postgres -- ON CONFLICT with no matching constraint --
// unless the table's uniqueness was set up manually outside Mohio, as Zork's items table was).
upsert db.sweep_items
    match id to "newitem-1"
    name "newitem"
    grp "c"
    qty 5
upsert: done
retrieve up1 from db.sweep_items
    match id to "newitem-1"
retrieve: done
show "upsert insert -> {{ up1.name }} qty {{ up1.qty }}"

// upsert -- update existing
upsert db.sweep_items
    match id to "newitem-1"
    name "newitem"
    grp "c"
    qty 50
upsert: done
retrieve up2 from db.sweep_items
    match id to "newitem-1"
retrieve: done
show "upsert update -> {{ up2.name }} qty {{ up2.qty }}"
find allrows_after_upsert in db.sweep_items
    where name is "newitem"
find: done
show "upsert did not duplicate: count {{ allrows_after_upsert.count }}"

// remove
save to db.sweep_items
    id "doomed-1"
    grp "z"
    name "doomed"
    qty 1
save: done
remove from db.sweep_items
    where name is "doomed"
remove: done
find gone in db.sweep_items
    where name is "doomed"
find: done
show "after remove count {{ gone.count }}"

// ordering
find ordered_up in db.sweep_items
    where grp is "a"
    order.up by qty
find: done
show "order.up by qty first {{ ordered_up.first.name }}"

find ordered_down in db.sweep_items
    where grp is "a"
    order.down by qty
find: done
show "order.down by qty first {{ ordered_down.first.name }}"

// pagination -- limit / up to
find limited in db.sweep_items
    where grp is "a"
    up to 1
find: done
show "up to 1 count {{ limited.count }}"

// count
check count as n in db.sweep_items
    where grp is "a"
check: done
show "check count grp=a -> {{ n }}"

// transaction
transaction
    save to db.sweep_items
        id "txitem-1"
        grp "tx"
        name "txitem"
        qty 1
    save: done
transaction: done
retrieve txcheck from db.sweep_items
    match name to "txitem"
retrieve: done
show "after transaction -> {{ txcheck.name }}"
'''

def run_dialect(dialect, extra_prelude=""):
    src = f'connect db as {dialect} from env.DATABASE_URL\n{extra_prelude}{BODY}'
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    it.shown = []
    it.run(prog)
    return list(it.shown)

def cleanup_postgres():
    try:
        import psycopg2
        c = psycopg2.connect(POSTGRES_URL, connect_timeout=3)
        cur = c.cursor()
        cur.execute('DROP TABLE IF EXISTS sweep_items')
        c.commit()
        c.close()
    except Exception as e:
        print(f"  (postgres cleanup skipped: {e})")

def main():
    print("=== Cross-dialect divergence sweep ===\n")

    os.environ['DATABASE_URL'] = ':memory:'
    print("--- SQLite (in-memory) ---")
    try:
        sqlite_out = run_dialect('sqlite')
        for line in sqlite_out:
            print(f"  {line}")
    except Exception as e:
        print(f"  SQLITE RUN FAILED: {type(e).__name__}: {e}")
        sqlite_out = None

    print("\n--- Postgres (local, reachable check) ---")
    pg_reachable = False
    try:
        import psycopg2
        c = psycopg2.connect(POSTGRES_URL, connect_timeout=3)
        c.close()
        pg_reachable = True
    except Exception as e:
        print(f"  Postgres NOT reachable at {POSTGRES_URL}: {type(e).__name__}: {e}")
        print("  To run this stage against Postgres: set MOHIO_SWEEP_POSTGRES_URL to a reachable")
        print("  instance (or ensure the default localhost:5432 postgres/postgres is up).")

    postgres_out = None
    if pg_reachable:
        os.environ['DATABASE_URL'] = POSTGRES_URL
        # Drop the sweep table first in case a prior run left it behind.
        cleanup_postgres()
        try:
            postgres_out = run_dialect('postgres')
            for line in postgres_out:
                print(f"  {line}")
        except Exception as e:
            print(f"  POSTGRES RUN FAILED: {type(e).__name__}: {e}")
        finally:
            cleanup_postgres()

    print("\n=== DIFF ===")
    if sqlite_out is None:
        print("  Cannot diff -- SQLite run itself failed (see above).")
    elif postgres_out is None:
        print("  Cannot diff -- Postgres was not reachable or its run failed.")
        print("  SQLite-only results are reported above; Postgres parity is UNVERIFIED this run.")
    elif sqlite_out == postgres_out:
        print("  NO DIVERGENCE -- SQLite and Postgres produced identical output for every verb exercised.")
    else:
        print("  DIVERGENCE FOUND:")
        for i, (s, p) in enumerate(zip(sqlite_out, postgres_out)):
            if s != p:
                print(f"    line {i}: sqlite={s!r}  postgres={p!r}")
        if len(sqlite_out) != len(postgres_out):
            print(f"    output line COUNT differs: sqlite={len(sqlite_out)} postgres={len(postgres_out)}")

    return sqlite_out, postgres_out, pg_reachable

if __name__ == '__main__':
    main()
