# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""A connect source must be env.X or secret.X, never a literal string. A literal
hardcodes a credential (e.g. a postgres URL with user:pass) and the string form also
silently mis-parses into junk assignments so the connection never opens.
HARDCODED_CREDENTIAL must fire as a hard, non-suppressible error. Found by the testing
chat (connect ... from "..." passed clean and the credential was never flagged)."""
import os
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer import validate
_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
def _fires(src):
    ctx = validate(_P.parse(src), src)
    return any('HARDCODED_CREDENTIAL' in str(e) for e in (ctx.errors or []))
def test_credential_string_connect_fires():
    assert _fires('connect db as postgres from "postgresql://admin:secret@db/prod"\n')
def test_block_form_credential_fires():
    assert _fires('connect db as postgres\n    from "postgresql://admin:secret@db/prod"\nconnect: done\n')
def test_env_connect_does_not_fire():
    assert not _fires('connect db as sqlite from env.DATABASE_URL\n')
def test_secret_block_connect_does_not_fire():
    assert not _fires('connect email as sender\n    from env.MAIL_PROVIDER\n    key secret.MAIL_API_KEY\nconnect: done\n')
if __name__ == '__main__':
    for t in (test_credential_string_connect_fires, test_block_form_credential_fires,
              test_env_connect_does_not_fire, test_secret_block_connect_does_not_fire):
        t()
    print("test_connect_credentials: 4/4 OK")
