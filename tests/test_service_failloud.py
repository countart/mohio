# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_service_failloud.py -- guards the service-call silent-no-op closure.

History: `_exec_ServiceCallStmt` silently no-opped miomail / miosms / mioai /
miohttp / miopdf / miofile / mioimage (the dotted-method forms), so
`miomail.send`, `miosms.send`, `mioai.generate` SILENTLY DID NOTHING -- even
though a real email sender exists (reachable only via the miomail block form).
Now the side-effecting dotted forms fail loud; miolog stays real; miocache stays a
graceful cache-miss (None).
"""
import os
os.environ['DATABASE_URL'] = ':memory:'
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_H = 'connect db as sqlite from env.DATABASE_URL\n'

def run(body):
    MohioInterpreter().run(transform(_P.parse(_H + body), _H + body), {})

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL: {name}")

def fails_loud(body):
    try:
        run(body); return False
    except Exception as e:
        m = str(e).lower()
        return any(s in m for s in
                   ('not yet', 'not wired', 'silently', 'not built', 'commercial',
                    'retired', 'not a wired'))

def runs_clean(body):
    try:
        run(body); return True
    except Exception:
        return False

# Side-effecting dotted forms must fail loud (no silent no-op)
# miomail.send is now wired to the real sender (miomail_stmt closer is optional,
# matching miohttp_stmt), so single-line miomail.send runs the real executor like
# miohttp.get -- it is no longer a fail-loud dotted stub. The remaining dotted
# forms below are still unwired and must fail loud.
check("miomail.send runs clean (wired to real sender, like miohttp)", runs_clean('miomail.send to "a@b.com" subject "Hi" body "Yo"\n'))
check("miosms.send fails loud",  fails_loud('miosms.send to "+15551234" body "Yo"\n'))
check("mioai.generate fails loud", fails_loud('mioai.generate "a poem"\n'))
check("miopdf.from fails loud",  fails_loud('miopdf.from "<h1>x</h1>"\n'))
# miolog stays real (no error); miocache statement form routes to the real cache executor
check("miolog.info stays real (no error)", runs_clean('miolog.info "hello"\n'))
check("miocache.set stays real (no error)", runs_clean('miocache.set "k" "v"\n'))

# Dedicated not-built service rules used to `return None` in the transformer, which
# dropped the statement and let it silently no-op. They now emit a NotBuiltService
# node and fail loud at the point of use. Commercial tier and plain not-built.
check("miovault.get fails loud (commercial)",      fails_loud('miovault.get "k"\n'))
check("miotranslate.text fails loud (commercial)", fails_loud('miotranslate.text "hi" to es\n'))
check("miosecurity.scan fails loud (commercial)",  fails_loud('miosecurity.scan\n'))
check("miograph.endpoint fails loud",              fails_loud('miograph.endpoint "u"\n'))
check("mioprint.send fails loud",                  fails_loud('mioprint.send "x"\n'))
check("miodata.xml fails loud",                    fails_loud('hold p "x"\nmiodata.xml p\n'))
check("mioresponse.header fails loud",             fails_loud('mioresponse.header "X" "Y"\n'))
check("miopush.send fails loud",                   fails_loud('miopush.send "x" to chan\n'))
check("miopublish.guaranteed fails loud",          fails_loud('miopublish.guaranteed "x"\n'))
check("mioimage.resize fails loud",                fails_loud('mioimage.resize "img" 100 x 100\n'))

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
