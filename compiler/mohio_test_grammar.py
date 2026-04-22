#!/usr/bin/env python3
"""
mohio_test_grammar.py
Phase 1 grammar validation harness.
Tests the fraud detection demo + individual construct snippets.
"""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from lark import Lark, UnexpectedInput, UnexpectedToken, UnexpectedCharacters

GRAMMAR_FILE = "mohio.lark"

# ─────────────────────────────────────────────
# Load grammar
# ─────────────────────────────────────────────

def load_grammar():
    with open(GRAMMAR_FILE) as f:
        raw = f.read()
    lines = []
    for line in raw.splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(line)
    return "\n".join(lines)

# ─────────────────────────────────────────────
# Parser factory
# ─────────────────────────────────────────────

def make_parser(grammar: str):
    return Lark(
        grammar,
        parser="earley",
        ambiguity="resolve",
        propagate_positions=True,
    )

# ─────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────

FRAUD_DEMO = open("tests/fraud_demo.mho").read()

SNIPPETS = {
    # ── Variables & assignment ────────────────────────────────
    "hold_decl":        'hold FRAUD_THRESHOLD 0.85',
    "hold_eq":          'hold FRAUD_THRESHOLD = 0.85',
    "lock_decl":        'lock MAX_RETRIES 3',
    "assignment_bare":  'username "Ronnie"',
    "assignment_typed": 'amount as decimal 0.00',
    "set_retired":      'set name "Ronnie"',
    "env_ref":          'db_url env.DATABASE_URL',
    "bool_true":        'flag true',
    "bool_false":       'active false',
    "dotted_assign":    'name user.name',

    # ── Sector / compliance ───────────────────────────────────
    "sector_financial":  'sector: financial',
    "sector_healthcare": 'sector: healthcare',
    "compliance_hipaa":  'compliance: HIPAA',
    "compliance_pci":    'compliance: PCI_DSS',

    # ── Connect ───────────────────────────────────────────────
    "connect_decl": 'connect db as postgres from env.DATABASE_URL',

    # ── Shape ─────────────────────────────────────────────────
    "shape_basic": """\
shape Transaction
    id          as text
    amount      as decimal
shape: done
""",
    "shape_with_mods": """\
shape Payment
    card_cvv    as text never store
    card_number as text format "####"
shape: done
""",

    # ── Flow control ──────────────────────────────────────────
    # Phase 1: all flow control blocks require explicit closers.
    # The whitespace-agnostic parser cannot determine block boundaries
    # from indentation alone. Phase 1.5 (indentation preprocessor) lifts this.
    "if_block": """\
if score > 90
    give back 200 "excellent"
otherwise
    give back 200 "needs review"
if: done
""",
    "if_is_true": """\
if isFraudulent is true
    give back 422 "blocked"
if: done
""",
    "if_or_if": """\
if score > 90
    give back 200 "excellent"
or if score > 70
    give back 200 "good"
otherwise
    give back 200 "ok"
if: done
""",
    "check_block": """\
check status
    when "active"
        give back 200 "ok"
    when "pending"
        give back 202 "pending"
    otherwise
        give back 400 "unknown"
check: done
""",
    "each_block": """\
each user in users
    give back 200 user
each: done
""",
    "repeat_block": """\
repeat 3 times
    give back 200 "retry"
repeat: done
""",
    "while_block": """\
while count > 0
    give back 200 "processing"
while: done
""",

    # ── Data ops (always required closers) ────────────────────
    "retrieve_block": """\
retrieve member from db.members
    match id is transaction.member_id
retrieve: done
""",
    "retrieve_on_failure": """\
retrieve member from db.members
    match id is user.id
    on.failure
        give back 404 "Not found"
retrieve: done
""",
    "find_block": """\
find recent in db.transactions
    where member_id is transaction.member_id
find: done
""",
    "find_and_time": """\
find recent in db.transactions
    where member_id is transaction.member_id
    and timestamp since now() - 24 hours
find: done
""",
    "save_block": """\
save to db.cleared_transactions
    id          transaction.id
    cleared_at  now()
save: done
""",
    "update_block": """\
update db.orders
    match id is order.id
    status "shipped"
update: done
""",
    "remove_block": """\
remove from db.sessions
    match user_id is user.id
remove: done
""",
    "transaction_block": """\
transaction
    save to db.orders
        amount cart.total
    save: done
transaction: done
""",

    # ── AI primitives ─────────────────────────────────────────
    # NOTE: ai.audit must appear BEFORE not confident.
    # not_confident uses statement* which otherwise swallows ai.audit.
    "ai_decide": """\
ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh transaction.amount, member.history
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
ai.decide: done
""",
    "ai_audit_stmt": 'ai.audit to fraud_audit_log',
    "ai_chain": """\
ai.chain fraud_chain
    try claude
        quality above 0.90
ai.chain: done
""",

    # ── Actions ───────────────────────────────────────────────
    "give_back_200":   'give back 200 "OK"',
    "give_back_value": 'give back member',
    "give_back_422":   'give back 422 "Transaction blocked pending review"',
    "give_back_202":   'give back 202 "Referred to manual review"',
    "halt_stmt":       'halt',
    "stop_stmt":       'stop',
    "skip_stmt":       'skip',
    "jump_to":         'jump to /dashboard',

    # ── Service calls (mio* builtins) ─────────────────────────
    "service_2part": 'miolog.alert "High-risk transaction flagged"',
    "service_3part": 'miolog.app_log.info "message"',
    "service_param":  'miomail.send to user.email',

    # ── Listen / routing ──────────────────────────────────────
    "listen_new": """\
listen for
    new sh.Transaction
        require role "screener"
        give back 200 "ok"
    new: done
listen: done
""",

    # ── Require role ──────────────────────────────────────────
    "require_role_or":     'require role "admin" or "screener"',
    "require_role_single": 'require role "admin"',

    # ── Closers ───────────────────────────────────────────────
    "named_closer_retrieve": 'retrieve: done',
    "named_closer_ai":       'ai.decide: done',
    "bare_closer":           'done',

    # ── Try / catch ───────────────────────────────────────────
    "try_block": """\
try
    retrieve data from db.records
        match id is record.id
    retrieve: done
catch timeout
    give back 503 "Service unavailable"
always
    miolog.info "Attempt complete"
""",

    # ── Task ──────────────────────────────────────────────────
    "task_decl": """\
task clearTransaction(transaction) returns text
    give back "done"
clearTransaction: done
""",

    # ── Math ──────────────────────────────────────────────────
    "math_mul": 'total ((price * quantity))',
    "math_div": 'rate ((amount / 100))',
    "math_mod": 'rem ((count % 5))',
    "math_add": 'total ((subtotal + tax))',

    # ── Time ──────────────────────────────────────────────────
    "time_now":       'cutoff now()',
    "time_now_minus": 'cutoff now() - 24 hours',
    "timespan_decl": """\
timespan last_quarter
    start 2026-01-01
    end 2026-03-31
timespan: done
""",

    # ── Comments & whitespace ─────────────────────────────────
    "line_comment": '// this is a comment\nhalt',
    "blank_lines":  '\n\nhalt\n\n',
}

# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────

def run_tests(parser):
    results = []
    passed = 0
    failed = 0

    # Full fraud demo first
    print("\n" + "="*60)
    print("TARGET: Fraud Detection Demo (full program)")
    print("="*60)
    try:
        tree = parser.parse(FRAUD_DEMO)
        depth = tree_depth(tree)
        print(f"  ✅  PASS — tree depth {depth}")
        passed += 1
        results.append(("FRAUD DEMO", "PASS", None))
    except Exception as e:
        print(f"  ❌  FAIL")
        print(f"      {type(e).__name__}: {e}")
        failed += 1
        results.append(("FRAUD DEMO", "FAIL", str(e)))

    # Individual snippets
    print("\n" + "="*60)
    print("UNIT SNIPPETS")
    print("="*60)
    for name, snippet in SNIPPETS.items():
        try:
            parser.parse(snippet.strip() + "\n")
            print(f"  ✅  {name}")
            passed += 1
            results.append((name, "PASS", None))
        except Exception as e:
            short = str(e).split("\n")[0][:100]
            print(f"  ❌  {name}")
            print(f"      {type(e).__name__}: {short}")
            failed += 1
            results.append((name, "FAIL", str(e)))

    # Summary
    total = passed + failed
    print("\n" + "="*60)
    print(f"RESULTS: {passed}/{total} passed")
    if failed:
        print(f"\nFailed:")
        for name, status, err in results:
            if status == "FAIL":
                print(f"  - {name}")
    print("="*60)
    return failed == 0


def tree_depth(tree, depth=0):
    if not hasattr(tree, "children"):
        return depth
    if not tree.children:
        return depth
    return max(tree_depth(c, depth + 1) for c in tree.children)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Loading grammar...")
    try:
        grammar = load_grammar()
        parser = make_parser(grammar)
        print("Grammar loaded ✅")
    except Exception as e:
        print(f"Grammar load FAILED: {e}")
        sys.exit(1)

    ok = run_tests(parser)
    sys.exit(0 if ok else 1)
