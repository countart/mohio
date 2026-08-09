# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""STRUCTURAL INVARIANTS -- the rules, enforced against the SOURCE, not against behaviour.

WHY THIS FILE EXISTS
--------------------
Every bug in this session was one of a small number of repeating shapes. Behaviour tests
catch a bug once. They do not stop the same shape reappearing three files over, six weeks
later, in a patch nobody reviewed closely. Docs rot. Review misses. The only thing that
does not rot is a test that reads the source and refuses.

So: these are not tests of what the compiler DOES. They are tests of how it is WRITTEN.
Each one names a real bug that shipped.

If one of these fails, do not work around it. The invariant is the point.
"""
import os, sys, re, ast

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import mohio_data

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        for line in str(detail).splitlines()[:8]:
            print(f"           {line}")
    _p += bool(cond); _f += (not cond)

def src(name):
    return open(os.path.join(_ROOT, name), encoding='utf-8').read()

def code(name):
    """Source with comments stripped.

    These invariants describe bugs, and the fixes DOCUMENT those bugs in comments. A
    comment that quotes the old broken line is not the old broken line. Scanning raw text
    flags the cure as the disease -- which this file did, on its first run.
    """
    out = []
    for line in src(name).splitlines():
        # crude but sufficient: drop a #-comment that is not inside a string literal
        if line.lstrip().startswith('#'):
            continue
        out.append(re.sub(r'\s#(?=(?:[^"\']*["\'][^"\']*["\'])*[^"\']*$).*$', '', line))
    return "\n".join(out)

INTERP  = code('mohio_interpreter.py')
SERVER  = code('mohio_server.py')
GRAMMAR = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')  # grammar comments are //, and the rules we scan are not in them

print("structural invariants (rules enforced against the source)")

# ---------------------------------------------------------------------------
# 1. A DATABASE VERB NEVER DEGRADES QUIETLY.
#
# BUG: 12 of 15 db verbs did `if not db: return None`. save/update/remove DISCARDED the
# write and reported success. pull returned an empty list -- it LIED and said the table
# was empty. transaction ran its body with no transaction around it. mio check: no errors.
#
# THE INVARIANT: every _exec_ that needs a connection goes through the ONE door,
# _db_or_fail. No verb may reach for get_connection('db') and decide for itself.
# ---------------------------------------------------------------------------
_ALLOWED_RAW = {
    # These raise directly, loudly, and predate the door. They are correct; they are
    # simply not routed through it. If you touch one, route it through the door.
    '_exec_SaveAllBlock', '_exec_RemoveAllBlock', '_exec_CheckMioqlBlock', '_exec_CmPurgeBlock',
}
offenders = []
for m in re.finditer(r'\n    def (_exec_\w+)\(self, node, ctx\):(.*?)(?=\n    def |\Z)', INTERP, re.S):
    name, body = m.group(1), m.group(2)
    if "get_connection('db')" not in body:
        continue
    if '_db_or_fail' in body or name in _ALLOWED_RAW:
        continue
    offenders.append(name)
check("every db verb goes through the single door (_db_or_fail)",
      not offenders,
      "verbs reaching for get_connection('db') on their own:\n  " + "\n  ".join(offenders))

# The exact line that caused the data loss. It must never come back, anywhere.
bad = [f"line {i+1}: {l.strip()}" for i, l in enumerate(INTERP.splitlines())
       if re.search(r'if not db\s*:\s*return None\s*$', l)]
check("no `if not db: return None` anywhere (the silent-write-discard line)",
      not bad, "\n".join(bad))

# A transaction that cannot be atomic must refuse, not run the body unprotected.
check("transaction never runs its body without a transaction",
      not re.search(r'if db:\s*db\.begin_transaction\(\)', INTERP),
      "`if db: db.begin_transaction()` means a missing connection ran the body ANYWAY.")

# ---------------------------------------------------------------------------
# 2. A KEY IS DECLARED, NEVER INFERRED.
#
# BUG: `pk = pk_map.get(table, cols[0])` made the FIRST COLUMN the primary key of any
# table nobody had thought about. `exits` is (room_id, direction, dest); a room has many
# exits; 229 of 342 exits were destroyed on every seed. ON CONFLICT DO UPDATE succeeds,
# so nothing raised and the endpoint reported "342 rows seeded".
#
# THE INVARIANT: uniqueness cannot be inferred. No default, ever, on a key lookup.
# ---------------------------------------------------------------------------
check("the seed never falls back to a guessed key",
      not re.search(r'(pk_map|SEED_KEYS)\.get\(\s*table\s*,', SERVER),
      "A default on the key lookup means a column becomes a PRIMARY KEY by accident of "
      "column ORDER. Uniqueness is a claim about the world; it cannot be inferred.")

# The seed machinery no longer lives in the general server. Seeding is an app/control-plane
# concern: there is no generic seeder, because uniqueness is a per-app claim about the world and
# cannot be inferred (keying `exits` on room_id once destroyed 229 of 342 rows, silently, because
# the upsert "succeeded"). The invariant now is that the shared runtime carries NO app's seed map.
check("general server carries no app seed map (seeding is not a runtime concern)",
      re.search(r'^SEED_KEYS\s*=\s*\{', SERVER, re.M) is None)

# ---------------------------------------------------------------------------
# 3. THE SERVICE NAMESPACE HAS EXACTLY ONE SOURCE.
#
# BUG: the service list lived in four places that had drifted apart in BOTH directions --
# three names reserved that were not services, five real services not reserved.
# ---------------------------------------------------------------------------
from mohio_services import SERVICE_ROOTS
from mohio_transformer import MOHIO_RESERVED_EXACT
_name_ex  = set(re.search(r'NAME: /\(\?!__USERVAR__\)\(\?!\(\?:([^)]*)\)', GRAMMAR).group(1).split('|'))
_svc_root = set(re.search(r'MIO_SERVICE_ROOT\.1: /\(\?:([^)]*)\)', GRAMMAR).group(1).split('|'))
check("grammar NAME-exclusion == the canonical service list",
      _name_ex == set(SERVICE_ROOTS),
      f"drift: {sorted(_name_ex ^ set(SERVICE_ROOTS))}")
check("grammar MIO_SERVICE_ROOT == the canonical service list",
      _svc_root == set(SERVICE_ROOTS),
      f"drift: {sorted(_svc_root ^ set(SERVICE_ROOTS))}")
_phantom = {n for n in MOHIO_RESERVED_EXACT if n.startswith('mio')} - set(SERVICE_ROOTS)
check("the transformer reserves no mio* name that is not a service root",
      not _phantom, f"phantoms: {sorted(_phantom)}")

# ---------------------------------------------------------------------------
# 4. AN EXPRESSION ALLOWLIST DOES NOT LOSE THE GENERATORS.
#
# BUG: `random_expr` was reachable from value_expr but NOT from concat_term. Inside a `&`
# the only surviving parse for `unique.id` was dotted_name -- a VARIABLE lookup -- so the
# generator silently produced empty string. `hold s ("x" & unique.id)` printed "x".
#
# THE INVARIANT: every place a value can appear, a generator can appear.
# ---------------------------------------------------------------------------
_concat = re.search(r'\nconcat_term:(.*?)(?=\n\n|\n[a-z_]+:)', GRAMMAR, re.S)
check("concat_term accepts random_expr (unique.id / random.uuid in a `&`)",
      _concat is not None and 'random_expr' in _concat.group(1),
      "Without it, a generator inside a concat degrades to a variable lookup and is EMPTY.")

# ---------------------------------------------------------------------------
# 5. NO SCHEMA FAILURE IS SWALLOWED.
#
# BUG: PostgresRuntime.ensure_table ended in `except Exception: self.conn.rollback()` with
# no re-raise. Every schema failure -- a table that could not be created, a column that
# could not be added -- passed in total silence.
# ---------------------------------------------------------------------------
_ensure = re.search(r'def ensure_table\(self, table, columns, id_value=None\):(.*?)(?=\n    def )',
                    INTERP[INTERP.find('class PostgresRuntime'):], re.S)
check("PostgresRuntime.ensure_table does not swallow schema failures",
      _ensure is not None and 'raise' in _ensure.group(1),
      "A schema the runtime cannot build is not a warning.")

# ---------------------------------------------------------------------------
# 6. THE VERSION HAS ONE SOURCE.
#
# BUG: `mio serve` bannered v0.3.8 while /mio/health reported "version": "0.4.4". Two
# hardcoded strings, two files, one binary, already disagreeing. A version that disagrees
# with itself makes a deploy unprovable.
# ---------------------------------------------------------------------------
_vers = []
for f in ('mio.py', 'mohio_server.py'):
    for i, line in enumerate(code(f).splitlines()):
        if re.search(r'''["']\d+\.\d+\.\d+["']''', line) and 'version' in line.lower():
            _vers.append(f"{f}:{i+1}: {line.strip()}")
check("no hardcoded version string outside mohio_version.py",
      not _vers, "\n".join(_vers))

from mohio_version import VERSION as _V
import mohio_server as _srv
check("mohio_server takes its version from the single source",
      getattr(_srv, 'VERSION', None) == _V)

# RH-4: pyproject.toml is a real THIRD source of the version string (setuptools reads it
# directly to build the wheel/sdist metadata; mohio_version.py has no way to enforce it at
# import time the way it enforces mio.py/mohio_server.py above). They agreed only because both
# were hand-edited together at the same past point -- exactly how the published 0.4.8 wheel
# shipped correctly-labeled while the source tree had already moved on, and how a FUTURE bump
# could silently do the same in the other direction if this file only bumps one of the two.
import tomllib as _tomllib
with open(os.path.join(_ROOT, 'pyproject.toml'), 'rb') as _pf:
    _pyproject_version = _tomllib.load(_pf)['project']['version']
check("pyproject.toml's version matches mohio_version.VERSION (the one real source)",
      _pyproject_version == _V,
      f"pyproject.toml: {_pyproject_version!r}  vs  mohio_version.VERSION: {_V!r}")

# The quoted-string scan above (check 6) looks for `"X.Y.Z"` next to the word "version" --
# it never catches an UNQUOTED docstring banner like "Version: 0.3.8 | Language: v3.8 |
# May 2026". That exact shape is how mio.py, mohio_interpreter.py, and mohio_ast.py drifted
# two releases behind mohio_version.VERSION: nothing imports a docstring, so nothing forced
# it to update when RH-1 bumped the real source. Scanned with src(), not code(), because
# mohio_ast.py's banner is a full-line `#` comment that code() strips outright.
#
# Deliberately NOT a tree-wide scan. mohio_test_grammar.py/mohio_transformer.py carry a
# "Version: 3.8.0" banner that tracks LANGUAGE_VERSION ("v3.8"), not VERSION -- a correct,
# different axis, matching mio.py's own line which pairs "Version:" against a separate
# "Language:" field on purpose. mohio_langmap.py/mohio_transformer_ast.py carry a
# "Version: 0.1.0" banner that maps to neither axis -- an unresolved third number, flagged
# for Ronnie, not silently fixed here. A blanket "any Version: X.Y.Z anywhere" scan would
# false-positive on both and needed to be declined, not forced -- see PRODUCTION-BUILD-PLAN.md.
_banner_files = ('mio.py', 'mohio_interpreter.py', 'mohio_ast.py')
_stale_banners = []
for f in _banner_files:
    for i, line in enumerate(src(f).splitlines()):
        m = re.search(r'Version:\s*(\d+\.\d+\.\d+)', line)
        if m and m.group(1) != _V:
            _stale_banners.append(f"{f}:{i+1}: {line.strip()}")
check("no stale hardcoded 'Version:' docstring banner in the known release-axis files",
      not _stale_banners, "\n".join(_stale_banners))

# ---------------------------------------------------------------------------
# 7. THE PARSER CACHE NEVER LEAVES A PARTIAL FILE.
#
# BUG: pickle.dump wrote INCREMENTALLY straight to the final path, hit an unpicklable
# module object, raised halfway, and left a TRUNCATED .pkl -- which `except Exception:
# pass` swallowed. Every subsequent start: "cache load failed: Ran out of input", delete,
# recompile, write another truncated file. The grammar recompiled on every single boot.
#
# THE INVARIANT: write to a temp file and rename. A partial file must never land.
# ---------------------------------------------------------------------------
MIO = code('mio.py')
_cache_fn = re.search(r'def _make_parser_cached\(grammar\):(.*?)(?=\ndef )', MIO, re.S)
check("the parser cache writes atomically (temp file + os.replace)",
      _cache_fn is not None and 'os.replace' in _cache_fn.group(1)
      and 'mkstemp' in _cache_fn.group(1),
      "pickle.dump straight to the final path leaves a truncated file on any failure.")
check("the parser cache does not pickle.dump directly to the final path",
      _cache_fn is not None
      and not re.search(r'open\(\s*cache_file\s*,\s*["\']wb["\']\s*\)', _cache_fn.group(1)))
check("one process compiles the grammar at most once (memoized)",
      '_PARSER_MEMO' in MIO and _cache_fn is not None and '_PARSER_MEMO' in _cache_fn.group(1))
check("no hardcoded /app path in a diagnostic",
      'listing /app' not in MIO,
      "It printed 'listing /app:' on Windows while listing the cwd.")

# ---------------------------------------------------------------------------
# 8. THE SOURCE COMPILES. (Cheap, and it has caught a truncated str_replace.)
# ---------------------------------------------------------------------------
for f in ('mohio_interpreter.py', 'mohio_server.py', 'mohio_transformer.py',
          'mohio_transformer_ast.py', 'mio.py', 'mohio_services.py'):
    try:
        ast.parse(src(f)); ok, why = True, ""
    except SyntaxError as e:
        ok, why = False, f"{e.lineno}: {e.msg}"
    check(f"{f} parses", ok, why)



# ── ONE SCANNER LIST ──────────────────────────────────────────────────────────
# There were TWO. `mohio_enforce.enforce()` had one, `mio.py check` kept a private copy,
# and `scan_block_opener_as_variable` -- added to enforce() -- silently did not exist in
# mio.py. `mio check` went on saying "no errors" about the exact program the scanner was
# written to catch.
#
# It is the same disease as every other bug this week, living inside the enforcement
# itself: A LIST THAT DOES NOT NAME A THING DOES NOT FAIL. IT SILENTLY DOES NOTHING.
print()
print("one scanner list: a rule added once must run everywhere")

_enf = open(os.path.join(_ROOT, 'mohio_enforce.py'), encoding='utf-8').read()
_mio = open(os.path.join(_ROOT, 'mio.py'), encoding='utf-8').read()
_rch = open(os.path.join(_ROOT, 'mohio_reachability.py'), encoding='utf-8').read()

check("mohio_reachability defines the canonical ERROR_SCANS",
      re.search(r'^ERROR_SCANS\s*=\s*\(', _rch, re.M) is not None)
check("mohio_reachability defines the canonical WARNING_SCANS",
      re.search(r'^WARNING_SCANS\s*=\s*\(', _rch, re.M) is not None)
check("enforce() runs the canonical list (run_scans)", 'run_scans(' in _enf)
# mio.py check now reaches the scanners THROUGH THE DOOR (enforce / enforce_scans), not by
# calling run_scans() by hand. Going through the door IS the single-door invariant: the CLI must
# not hand-roll Layer 3, or it can drift from the canonical scanner list again. So the correct
# assertion is that the check path references the enforce door, not that mio.py contains a raw
# run_scans( call (which would be the OLD, hand-rolled pattern this refactor removed).
check("mio.py check reaches the scanners through the enforce door",
      'enforce_scans' in _mio or 'from mohio_enforce import' in _mio)

# The real rule: NOBODY calls a scanner by hand. A hand-rolled call is a list of one, and
# a list of one is how the second list started.
for _fname, _src in (('mohio_enforce.py', _enf), ('mio.py', _mio)):
    _hand = re.findall(r'\bscan_[a-z_]+\(', _src)
    check(f"{_fname} does not call any scanner by hand ({_hand or 'none'})", not _hand)

# enforce() used to IMPORT the three warning scanners and never call them, so every caller
# of enforce() -- the tests, the server -- silently got zero warnings.
check("the warning scanners are actually RUN, not just imported",
      'WARNING_SCANS' in _rch and 'warnings.extend' in _rch or 'run_scans' in _enf)



# ── THE AST CACHE MUST SEE THE WHOLE COMPILER ─────────────────────────────────
# The cache invalidates on a fingerprint of the compiler's own source. That fingerprint
# used to be a HAND-WRITTEN LIST of six files -- and the list did not name
# `mohio_reachability.py`, which is where EVERY SCANNER LIVES.
#
# So changing a scanner did not invalidate the cache. `mio check` replayed a cached "clean"
# result and the new rule silently never ran. It also missed `mohio_enforce.py` and
# `mohio_sector_loader.py`, which means tightening a SECTOR CONFIDENCE FLOOR would not take
# effect on an already-checked file: `mio check` would report "no errors" on a program that
# violates a compliance floor. That is check-time silence on a compliance control.
#
# Observed live: a floor-violating program checked CLEAN, and the identical program under a
# fresh filename reported the violation.
#
# A list that does not name a thing does not fail. It silently does nothing. So the
# fingerprint no longer keeps a list.
print()
print("ast cache: the fingerprint covers the WHOLE compiler, not a hand-picked list")

_mio_src = open(os.path.join(_ROOT, 'mio.py'), encoding='utf-8').read()
_fp = re.search(r'def _compiler_fingerprint\(\).*?(?=\ndef )', _mio_src, re.S)
_fp_src = _fp.group(0) if _fp else ''

check("the fingerprint globs the compiler's .py files rather than listing them",
      "glob.glob" in _fp_src)
check("the fingerprint does NOT hand-list transformer/interpreter/ast",
      "'mohio_transformer.py'," not in _fp_src)
check("the grammar is still fingerprinted", "GRAMMAR_PATH" in _fp_src)

# The property, not the implementation: touching ANY compiler module must move the hash.
import importlib
_mio_mod = importlib.import_module('mio')
_before = _mio_mod._compiler_fingerprint()
for _probe in ('mohio_reachability.py', 'mohio_enforce.py', 'mohio_sector_loader.py'):
    _path = os.path.join(_ROOT, _probe)
    if not os.path.exists(_path):
        continue
    _orig = open(_path, 'rb').read()
    try:
        with open(_path, 'ab') as _fh:
            _fh.write(b'\n# fingerprint probe\n')
        _mio_mod._COMPILER_FINGERPRINT = None
        _after = _mio_mod._compiler_fingerprint()
    finally:
        with open(_path, 'wb') as _fh:
            _fh.write(_orig)
        _mio_mod._COMPILER_FINGERPRINT = None
    check(f"editing {_probe} invalidates the AST cache", _after != _before)

check("restoring the files restores the original fingerprint",
      _mio_mod._compiler_fingerprint() == _before)

# INVARIANT: audit events are written durably AS THEY HAPPEN, never batched to session end.
# Batching audit writes to the end of a run reintroduces the ephemeral-loss hole: a sleep or
# crash mid-run would lose every unbatched audit entry, and the compliance guarantee ("every
# access is logged") would be silently false for the whole session. Each _audit_event must
# persist its record at the moment of the call. This guards against a future change that turns
# per-event writes into a session-end flush.
try:
    from mohio_interpreter import MohioInterpreter as _MI
    class _OneShotDB:
        # Declared through the provider-verified channel: a test double has no inspectable
        # backend, and an unclassifiable sink grades `none` rather than being assumed adequate.
        _mohio_grade_verified = 'durable'

        def __init__(self): self.rows = []
        def ensure_table(self, *a): pass
        def save(self, t, r): self.rows.append((t, r))
    class _AuditCtx:
        def __init__(self, db): self._sector_compliance = ['gdpr']; self._db = db
        def get_connection(self, n): return self._db
        def get(self, k): return None
    _adb = _OneShotDB()
    _actx = _AuditCtx(_adb)
    _mi = _MI()
    _mi._audit_event('data_audit_log', {'event': 'a1'}, _actx)
    _one = len(_adb.rows)
    _mi._audit_event('data_audit_log', {'event': 'a2'}, _actx)
    _two = len(_adb.rows)
    check("audit writes are durable as-they-happen (not batched to session end)",
          _one == 1 and _two == 2,
          f"after 1 event: {_one} durable, after 2: {_two} -- if not 1 then 2, writes are "
          f"batched and would be lost on sleep/crash")
except Exception as _e:
    check("audit write-as-you-go invariant runs", False, str(_e))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
