#!/usr/bin/env python3
"""
mohio_test_grammar.py
Phase 1 grammar validation harness.
Tests the fraud detection demo + individual construct snippets.
"""

import sys
from lark import Lark, UnexpectedInput, UnexpectedToken, UnexpectedCharacters

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

GRAMMAR_FILE = "mohio.lark"

# ─────────────────────────────────────────────
# Load grammar
# ─────────────────────────────────────────────

def load_grammar():
    with open(GRAMMAR_FILE) as f:
        raw = f.read()
    # Strip lark-style comments (// lines) — Lark doesn't support // comments
    # in the grammar file itself, only in the language being parsed
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        lines.append(line)
    grammar = "\n".join(lines)
    return grammar

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
    # Variables & assignment
    "hold_decl": 'hold FRAUD_THRESHOLD 0.85',
    "lock_decl": 'lock MAX_RETRIES 3',
    "assignment_bare": 'username "Ronnie"',
    "assignment_typed": 'amount as decimal 0.00',
    "env_ref": 'db_url env.DATABASE_URL',

    # Sector / compliance
    "sector_decl": 'sector: financial',
    "compliance_decl": 'compliance: HIPAA',

    # Connect
    "connect_decl": 'connect db as postgres from env.DATABASE_URL',

    # Shape
    "shape_decl": """
shape Transaction
    id          as text
    amount      as decimal
shape: done
""",

    # Flow control
    "if_block": """
if score > 90
    give back 200 "excellent"
otherwise
    give back 200 "needs review"
""",
    "check_block": """
check status
    when "active"
        give back 200 "ok"
    when "pending"
        give back 202 "pending"
    otherwise
        give back 400 "unknown"
""",
    "each_block": """
each user in users
    give back 200 user
""",
    "repeat_block": """
repeat 3 times
    give back 200 "retry"
""",

    # Data ops
    "retrieve_block": """
retrieve member from db.members
    match id is transaction.member_id
retrieve: done
""",
    "find_block": """
find recent in db.transactions
    where member_id is transaction.member_id
find: done
""",
    "save_block": """
save to db.cleared_transactions
    id          transaction.id
    cleared_at  now()
""",

    # AI
    "ai_decide": """
ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh transaction.amount, member.history
    not confident
        give back 202 "Referred to manual review"
    ai.audit to fraud_audit_log
ai.decide: done
""",

    # Output
    "give_back_200": 'give back 200 "OK"',
    "give_back_value": 'give back member',
    "give_back_status": 'give back 422 "Transaction blocked pending review"',
    "halt": 'halt',
    "stop": 'stop',
    "jump_to": 'jump to /dashboard',

    # Listen
    "listen_block": """
listen for
    new sh.Transaction
        require role "screener"
        give back 200 "ok"
    new: done
listen: done
""",

    # Require role
    "require_role": 'require role "admin" or "screener"',

    # Closers
    "named_closer": 'retrieve: done',
    "bare_closer": 'done',

    # Result handlers
    "on_failure": """
retrieve member from db.members
    match id is user.id
    on.failure
        give back 404 "Not found"
retrieve: done
""",

    # Try / catch
    "try_block": """
try
    retrieve data from db.records
        match id is record.id
    retrieve: done
catch timeout
    give back 503 "Service unavailable"
always
    miolog.info "Attempt complete"
""",

    # Timespan
    "timespan_decl": """
timespan last_quarter
    start 2026-01-01
    end 2026-03-31
timespan: done
""",
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
        print("✅  PASS — parsed successfully")
        print(f"    Tree depth: {tree_depth(tree)}")
        passed += 1
        results.append(("FRAUD DEMO", "PASS", None))
    except Exception as e:
        print(f"❌  FAIL")
        print(f"    {type(e).__name__}: {e}")
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
        print(f"\nFAILED:")
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
    return max(tree_depth(c, depth+1) for c in tree.children)


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
