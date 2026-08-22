# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-AUDIT-COVERAGE-GAPS Part E2 (2026-08-17): miohttp/miomail/miofile left no trace when
data crossed the app boundary, while mioconnect already records every crossing
(boundary_send/boundary_response, _exec_MioconnectCall). Fixed via a shared `_audit_egress`
helper (mohio_interpreter.py, next to `_audit_data_access`), used by all three -- same
'boundary_send' event shape mioconnect already writes to `data_audit_log`, destination/method/
status only, never the payload/credentials/file contents.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD). miohttp's
network layer is mocked (`_http_open`, module-level, same seam mioconnect and the SSRF-redirect
guard share) so this runs offline and deterministically.

Run: `python tests/test_audit_egress_e2.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
import mohio_interpreter as MI
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    it.run(prog)
    return it


def egress_entries(it):
    return [e for e in it._audit_logs.get('data_audit_log', []) if e.get('event') == 'boundary_send']


# ── miohttp: mock the network layer, same seam mioconnect/the SSRF guard already share ─────────
class _FakeResp:
    status = 200
    headers = {}
    def read(self): return b'{"ok": true}'
    def __enter__(self): return self
    def __exit__(self, *a): return False

class _FakeCM:
    def __enter__(self): return _FakeResp()
    def __exit__(self, *a): return False

_orig_http_open = MI._http_open
MI._http_open = lambda req, timeout, method, url: _FakeCM()
try:
    it = run_real('miohttp.get "https://example.com/api" as resp\nshow resp.status\n')
    ent = egress_entries(it)
    check("miohttp.get writes a boundary_send audit entry",
          len(ent) == 1 and ent[0]['channel'] == 'miohttp', ent)
    check("the entry names the destination and method, not any header/body value",
          ent[0].get('destination') == 'https://example.com/api'
          and ent[0].get('method') == 'GET', ent)
finally:
    MI._http_open = _orig_http_open


# ── miomail: dev-mode mock path (no provider env vars set) ─────────────────────────────────────
for _k in ('SENDGRID_API_KEY', 'BREVO_API_KEY', 'SMTP_HOST'):
    os.environ.pop(_k, None)
it = run_real('miomail.send to "a@b.com" subject "Hi" body "Yo"\n')
ent = egress_entries(it)
check("miomail.send writes a boundary_send audit entry", len(ent) == 1 and ent[0]['channel'] == 'miomail', ent)
check("the entry names the provider channel, never the recipient address (PII, not a destination id)",
      ent[0].get('destination') == 'mock' and 'a@b.com' not in str(ent), ent)


# ── miofile: read + write, real filesystem under a temp MIOFILE_ROOT ───────────────────────────
import tempfile
_tmp_root = tempfile.mkdtemp(prefix='mohio_miofile_audit_')
os.environ['MIOFILE_ROOT'] = _tmp_root
try:
    it = run_real('miofile.write "note.txt" "hello"\nmiofile.read "note.txt" as content\n')
    ent = egress_entries(it)
    check("miofile.write and miofile.read each write a boundary_send audit entry",
          len(ent) == 2 and {e['op'] for e in ent} == {'write', 'read'}, ent)
    check("the entry names the relative path, never the resolved absolute filesystem path "
          "(would leak MIOFILE_ROOT layout) or the file content",
          all(e.get('destination') == 'note.txt' for e in ent)
          and _tmp_root not in str(ent) and 'hello' not in str(ent), ent)
finally:
    os.environ.pop('MIOFILE_ROOT', None)
    import shutil as _sh
    _sh.rmtree(_tmp_root, ignore_errors=True)


# Test-strength check (content-safety review, 2026-08-19): all three egress writes must go
# through _audit_event, not a hand-rolled call straight to _audit_chained_save (the M2/M3
# bypass pattern the architectural rule forbids repeating). Spy on the real bound method
# across all three real channels in one pass.
_calls = []
_orig_audit_event = MI.MohioInterpreter._audit_event
def _spy_audit_event(self, log_name, entry, ctx):
    _calls.append((log_name, entry.get('event'), entry.get('channel')))
    return _orig_audit_event(self, log_name, entry, ctx)
MI.MohioInterpreter._audit_event = _spy_audit_event
_orig_http_open2 = MI._http_open
MI._http_open = lambda req, timeout, method, url: _FakeCM()
_tmp_root2 = tempfile.mkdtemp(prefix='mohio_miofile_audit_spy_')
os.environ['MIOFILE_ROOT'] = _tmp_root2
for _k in ('SENDGRID_API_KEY', 'BREVO_API_KEY', 'SMTP_HOST'):
    os.environ.pop(_k, None)
try:
    run_real('miohttp.get "https://example.com/api" as resp\nshow resp.status\n')
    run_real('miomail.send to "a@b.com" subject "Hi" body "Yo"\n')
    run_real('miofile.write "note.txt" "hello"\n')
finally:
    MI.MohioInterpreter._audit_event = _orig_audit_event
    MI._http_open = _orig_http_open2
    os.environ.pop('MIOFILE_ROOT', None)
    import shutil as _sh2
    _sh2.rmtree(_tmp_root2, ignore_errors=True)
check("miohttp egress audit goes through _audit_event (not a bypass)",
      ('data_audit_log', 'boundary_send', 'miohttp') in _calls, _calls)
check("miomail egress audit goes through _audit_event (not a bypass)",
      ('data_audit_log', 'boundary_send', 'miomail') in _calls, _calls)
check("miofile egress audit goes through _audit_event (not a bypass)",
      ('data_audit_log', 'boundary_send', 'miofile') in _calls, _calls)


print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
