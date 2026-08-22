#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_test_grammar.py
Mohio Language — Grammar + Transformer Test Harness
Version: 3.8.0 | August 2026 | Particular LLC

Three test suites:
  1. PARSE TESTS    — snippets that must parse cleanly
  2. REJECT TESTS   — snippets the grammar must reject (retired/invalid syntax)
  3. TRANSFORMER    — compile errors and warnings
  4. DEMO FILES     — four full programs end-to-end

Run:
  python3 mohio_test_grammar.py
  python3 mohio_test_grammar.py --verbose
  python3 mohio_test_grammar.py --suite parse
  python3 mohio_test_grammar.py --suite transformer
  python3 mohio_test_grammar.py --suite demos
"""


# ================================================================================
#   THE GATE. It runs the SAME path `mio check` runs: mohio_enforce.enforce().
# ================================================================================
#   enforce() = validate() (parse tree) + transform() (AST) + scan_*() (whole program).
#
#   This gate used to call validate() ONLY. It was therefore blind to 25 transformer guards and
#   7 whole-program scanners, and reported a confident 154/154 while real bugs sat in main.
#
#   So: a green gate now means the compiler is green. Keep it that way -- do not "fix" a red
#   test by calling a single layer. If a snippet here is not a valid PROGRAM (a lone closer, a
#   call to an undeclared task), mark it expect="parse" -- do not weaken the door.
# ================================================================================

import os
import os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from lark import Lark, UnexpectedInput

import mohio_data
GRAMMAR_FILE = str(mohio_data.GRAMMAR_PATH)
VERBOSE = "--verbose" in sys.argv
SUITE   = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--suite"), "all")


# ══════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════

def load_grammar():
    with open(GRAMMAR_FILE, encoding="utf-8") as f:
        raw = f.read()
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))

def make_parser(grammar):
    return Lark(grammar, parser="earley", ambiguity="resolve", propagate_positions=True)

grammar = load_grammar()
parser  = make_parser(grammar)

# Import transformer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mohio_transformer import validate
from mohio_enforce import enforce   # the single door: validate + transform + scanners


# ══════════════════════════════════════════════════════════════
# TEST RUNNER
# ══════════════════════════════════════════════════════════════

passed = 0
failed = 0
results = []

def run(name, src, expect="pass", error_contains=None, warn_contains=None):
    """
    expect: "pass"    — must parse and validate with no errors
            "parse"   — must parse (grammar only, no transformer)
            "reject"  — grammar must reject (parse error expected)
            "error"   — must parse but transformer must produce error
            "warn"    — must parse, transformer must produce warning, no errors
    """
    global passed, failed
    src = src.strip() + "\n"
    try:
        tree = parser.parse(src)
        parse_ok = True
    except Exception as e:
        parse_ok = False
        parse_err = str(e).split("\n")[0][:80]

    if expect == "reject":
        if not parse_ok:
            passed += 1
            results.append((name, "PASS", None))
            if VERBOSE:
                print(f"  ✅  {name}")
        else:
            failed += 1
            results.append((name, "FAIL", "Expected parse failure but it parsed"))
            print(f"  ❌  {name} — should have been rejected by grammar")
        return

    if not parse_ok:
        failed += 1
        results.append((name, "FAIL", parse_err))
        print(f"  ❌  {name}")
        print(f"       Parse error: {parse_err}")
        return

    if expect == "parse":
        passed += 1
        results.append((name, "PASS", None))
        if VERBOSE:
            print(f"  ✅  {name}")
        return

    ctx, _program = enforce(tree, source=src)   # all three layers, one door

    if expect == "pass":
        if ctx.errors:
            failed += 1
            results.append((name, "FAIL", ctx.errors[0].message))
            print(f"  ❌  {name} — unexpected compile error:")
            for e in ctx.errors:
                print(f"       [{e.line}] {e.message}")
        else:
            passed += 1
            results.append((name, "PASS", None))
            if VERBOSE:
                w = f" ({len(ctx.warnings)} warn)" if ctx.warnings else ""
                print(f"  ✅  {name}{w}")

    elif expect == "error":
        if not ctx.errors:
            failed += 1
            results.append((name, "FAIL", "Expected compile error — none produced"))
            print(f"  ❌  {name} — MISSED — should have produced a compile error")
        else:
            if error_contains and not any(error_contains.lower() in e.message.lower() for e in ctx.errors):
                failed += 1
                results.append((name, "FAIL", f"Error missing expected text: {error_contains}"))
                print(f"  ❌  {name} — error produced but wrong message:")
                for e in ctx.errors:
                    print(f"       [{e.line}] {e.message}")
            else:
                passed += 1
                results.append((name, "PASS", None))
                if VERBOSE:
                    print(f"  ✅  {name} — correctly caught: {ctx.errors[0].message[:60]}")

    elif expect == "warn":
        if ctx.errors:
            failed += 1
            results.append((name, "FAIL", "Got errors, expected only warnings"))
            print(f"  ❌  {name} — got errors (wanted warnings only):")
            for e in ctx.errors:
                print(f"       [{e.line}] {e.message}")
        elif not ctx.warnings:
            failed += 1
            results.append((name, "FAIL", "No warning produced"))
            print(f"  ❌  {name} — MISSED — should have produced a warning")
        else:
            if warn_contains and not any(warn_contains.lower() in w.message.lower() for w in ctx.warnings):
                failed += 1
                results.append((name, "FAIL", f"Warning missing expected text: {warn_contains}"))
                print(f"  ❌  {name} — warning produced but wrong message")
            else:
                passed += 1
                results.append((name, "PASS", None))
                if VERBOSE:
                    print(f"  ✅  {name} — correctly warned: {ctx.warnings[0].message[:60]}")


def section(title):
    print(f"\n{'='*55}")
    print(title)
    print('='*55)


# ══════════════════════════════════════════════════════════════
# SUITE 1 — PARSE TESTS
# ══════════════════════════════════════════════════════════════

if SUITE in ("all", "parse"):
    section("SUITE 1 — PARSE TESTS")

    # ── Declarations ──────────────────────────────────────────
    run("sector_financial",     "sector: financial")
    run("sector_healthcare",    "sector: healthcare")
    run("compliance_hipaa",     "compliance: HIPAA")
    run("compliance_pci",       "compliance: PCI_DSS")
    run("connect_db",           "connect db as postgres from env.DATABASE_URL")
    run("hold_simple",          "hold THRESHOLD 0.85")
    run("hold_eq",              "hold THRESHOLD = 0.85")
    run("lock_simple",          "lock MAX_RETRIES 3")
    run("include_file",         'include "config/app.mho"')
    run("load_pack",            "load pack miogscreen")
    run("load_pack_version",    "load pack miogscreen version 1.2")

    # ── Shapes ────────────────────────────────────────────────
    run("shape_basic", """
shape Transaction
    id          as text
    amount      as decimal
shape: done
""")
    run("shape_mods", """
shape Payment
    card_cvv    as text     never store
    status      as text     default active
    amount      as decimal  required
shape: done
""")
    run("shape_retain", """
shape Order
    id      as uuid     required    default uuid()
    total   as decimal  required
    retain for 7 years
shape: done
""")
    run("shape_list_type", """
shape Member
    roles   as list text
    tags    as list NAME
shape: done
""")
    run("shape_dotted_field", """
shape Member
    status.allowed active, inactive, suspended
    mfa.verified as boolean default false
shape: done
""")

    # ── Task — new no-parens form ──────────────────────────────
    run("task_no_params", """
task runReport
    returns text
    give back "done"
task: done
""")
    run("task_one_param", """
task greet
    take name as text
    returns text
    give back "Hello"
task: done
""")
    run("task_keyword_param", """
task clearTransaction
    take transaction as sh.Transaction
    returns text
    save to db.cleared
        id transaction.id
    save: done
    give back "Cleared"
task: done
""")
    run("task_step_param", """
task processStep
    take step as sh.WorkflowStep
    returns text
    give back "done"
task: done
""")

    # ── call verb — task invocation (call linked to task; run is async-only) ──
    run("call_named_block", """
task fulfillOrder
    take order, customer as text
    returns text

    give back "ok"
task: done

call fulfillOrder
    order    "o1"
    customer "c1"
call: done
""")
    run("call_with", 'task sendWelcome\n    take member as text\n    returns text\n\n    give back "ok"\ntask: done\n\ncall sendWelcome with "m1"')
    # Bare `call NAME` (no closer, no `with`) now fails loud: it degrades to an
    # assignment to a variable named "call" and silently no-ops the task call.
    # Bare `call NAME` is a PROCEDURE CALL (no args, no closer). It used to be rejected
    # because it mis-parsed into an assignment to a variable named 'call' and silently did
    # nothing; call_procedure now parses it properly, so the old rejection is obsolete.
    run("call_bare_procedure",
        'task generateReport\n    show "ok"\ntask: done\n\ncall generateReport')

    # ── MioQL — retrieve ───────────────────────────────────────
    run("retrieve_basic", """
retrieve member from db.members
    match id to request.id
retrieve: done
""")
    run("retrieve_as_alias", """
retrieve member as username from db.members
    match id to request.id
retrieve: done
""")
    run("retrieve_one", """
retrieve.one member from db.members
    match email to request.email
retrieve.one: done
""")
    run("retrieve_on_failure", """
retrieve member from db.members
    match id to request.id
    on.failure
        give back 404 "Not found"
retrieve: done
""")

    # ── MioQL — find ───────────────────────────────────────────
    run("find_basic", """
find members in db.members
    where status is active
find: done
""")
    run("find_above", """
find flagged in db.transactions
    where amount is above 10000
find: done
""")
    run("find_below", """
find small in db.transactions
    where amount is below 100
find: done
""")
    run("find_between", """
find mid in db.members
    where score is between 700 and 850
find: done
""")
    run("find_contains", """
find smiths in db.members
    where last_name contains "Smith"
find: done
""")
    run("find_starts", """
find info in db.members
    where email starts "info@"
find: done
""")
    run("find_is_in_days", """
find recent in db.transactions
    where created_at is.in last 30 days
find: done
""")
    run("find_is_in_hours", """
find recent in db.transactions
    where created_at is.in last 24 hours
find: done
""")
    run("find_and_is_in", """
find recent in db.transactions
    where member_id is request.member_id
    and created_at is.in last 24 hours
find: done
""")
    run("find_older_than", """
find old in db.sessions
    where created_at is older than 90 days
find: done
""")
    run("find_is_empty", """
find pending in db.orders
    where deleted_at is empty
find: done
""")
    run("find_in_list", """
find active in db.members
    where status active, pending
find: done
""")
    run("find_not_in_list", """
find allowed in db.members
    where status is.not banned, deleted
find: done
""")
    run("find_order_up", """
find members in db.members
    order.up by name
find: done
""")
    run("find_order_down", """
find tx in db.transactions
    order.down by created_at
find: done
""")
    run("find_up_to", """
find members in db.members
    up to 25
find: done
""")
    run("find_skip", """
find members in db.members
    skip 40
find: done
""")
    run("find_paginate", """
find members in db.members
    paginate by 25
    cursor from request.cursor
find: done
""")
    run("find_by_group", """
find summary by merchant in db.transactions
    up to 10
find: done
""")
    run("find_cache", """
find members in db.members
    where status is active
    cache for 10 minutes
find: done
""")
    run("find_match_to", """
find active in db.members
    match status to "active"
find: done
""")

    # ── MioQL — save / update / remove ─────────────────────────
    run("save_basic", """
save to db.members
    name    request.name
    email   request.email
save: done
""")
    run("save_or_update", """
save or update db.members
    match email to request.email
    name request.name
save: done
""")
    run("update_basic", """
update db.members
    match id to member.id
    status "active"
update: done
""")
    run("remove_basic", """
remove from db.sessions
    match user_id to user.id
remove: done
""")
    run("remove_all", "remove.all from db.temp_imports\nremove.all: done")

    # ── Flow control ──────────────────────────────────────────
    run("check_when", """
check status
    when "active"  -> give back 200 "ok"
    when "pending" -> give back 202 "pending"
    otherwise        give back 400 "unknown"
check: done
""")
    run("check_above", """
check score
    above 90  -> give back "excellent"
    above 70  -> give back "good"
    otherwise -> give back "needs review"
check: done
""")
    run("each_basic", """
each user in users
    give back 200 user
each: done
""")
    run("repeat_basic", """
repeat 3 times
    give back 200 "retry"
repeat: done
""")
    run("while_basic", """
while queue.size > 0
    give back 200 "processing"
while: done
""")

    # ── if — trailing qualifier ONLY ──────────────────────────
    run("halt_if",          "halt if maintenance_mode is true")
    # was `is empty` (two words) -- that form silently misparses as `member IS empty`
    # (bareword equality, not an emptiness predicate); the test was passing on the misparse,
    # not on real if-guard behavior. Fixed to the canonical dot form, 2026-08-10 --
    # T1-SPACED-MISPARSE-GUARDS.
    run("give_back_if",     'give back 404 "not found" if member is.empty')
    run("halt_plain",       "halt")
    run("stop_stmt",        "stop")

    # ── Listen for ────────────────────────────────────────────
    run("listen_new", """
shape Transaction
    method POST
shape: done
listen for
    new sh.Transaction
        require role "screener"
        give back 200 "ok"
    new: done
listen: done
""")
    run("listen_request_inbound", """
shape InvoiceDownload
    method GET
shape: done
listen for
    request for sh.InvoiceDownload
        require role "member"
    request: done
listen: done
""")
    run("listen_from_connector", """
listen for
    from Stripe
        when payment.succeeded
            update db.orders
                status "paid"
                match id to event.order_id
            update: done
        otherwise
            miolog.info "Unhandled event"
listen: done
""")

    # ── AI primitives ─────────────────────────────────────────
    run("ai_decide_full", """
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount, member.history
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
    on.failure
        give back 503 "Unavailable"
ai.decide: done
""")
    # `ai.chain` is retired; `ai.connect` with an `order` block is canonical, and `try` inside a
    # provider block is retired with it. This test asserted the retired form and only survived
    # because the gate never built an AST.
    run("ai_connect", """
ai.connect fraud_chain
    order
        anthropic model "claude-haiku-4-5-20251001"
        anthropic model "claude-sonnet-4-6"
    order: done
ai.connect: done
""")
    run("ai_audit_stmt",    "ai.audit to fraud_audit_log")

    # ── New verbs ─────────────────────────────────────────────
    run("load_pack",        "load pack miogscreen")
    run("apply_single", """
apply miogscreen.remove_background as clean_image
    target_color #00FF00
apply: done
""")
    run("apply_collection", """
apply miogscreen to every portrait in portrait_file
    method mask
    tolerance 5%
apply: done
""")
    run("modify_every", """
modify every portrait in portrait_file
    apply miogscreen.remove_background
    apply: done
    modify.in greenscreen/output
modify: done
""")
    run("copy_basic",       "copy source_file to destination\ncopy: done")
    run("copy_unquoted",    "copy removed_image to img/removals\n    rename output.png\ncopy: done")
    run("get_block", """
get config from cache.settings
    match key to "app_config"
get: done
""")
    run("pull_bounded", """
pull up to 100 from db.transactions
    where status is pending
pull: done
""")
    run("request_outbound", """
request ocr_result from GoogleVision.ocr
    with vision_request
    on.failure give back 503 "unavailable"
request: done
""")

    # ── mioconnect ────────────────────────────────────────────
    run("mioconnect_full", """
shape ChargeRequest
    method POST
shape: done
shape ChargeResult
    method POST
shape: done
mioconnect Stripe
    address "https://api.stripe.com/v1"
    auth bearer secret.STRIPE_KEY
    timeout 30 seconds
    operation charge
        path "/charges"
        sends sh.ChargeRequest
        returns sh.ChargeResult
    operation: done
mioconnect: done
""")
    run("mioconnect_alias", """
mioconnect Stripe as StripeUS
    address "https://api.stripe.com/v1"
    auth bearer secret.STRIPE_US_KEY
mioconnect: done
""")
    run("mioconnect_from",  "mioconnect QuickBooks from env.QB_CONNECTION\nmioconnect: done")

    # ── miovalidate ───────────────────────────────────────────
    run("miovalidate_decl", """
miovalidate subscriber_rules
    check name as text length 2 to 100
    check email as email
    check age as integer between 18 and 120
miovalidate: done
""")
    run("validate_using", """
validate using subscriber_rules
    on.failure give back 422 errors
validate: done
""")

    # ── Saga / journey ────────────────────────────────────────
    run("saga_basic", """
saga fulfill_order
    step reserve_inventory
        save to db.holds
            id order.id
        save: done
        undo
            remove from db.holds
                match id to order.id
            remove: done
    step: done
saga: done
""")

    # ── Actions ───────────────────────────────────────────────
    run("give_back_200",    'give back 200 "OK"')
    run("give_back_value",  "give back member")
    run("give_back_422",    'give back 422 "Transaction blocked"')
    run("jump_to",          "jump to /dashboard")
    run("require_single",   'require role "admin"')
    run("require_or",       'require role "admin" or "screener"')

    # ── Expressions ───────────────────────────────────────────
    run("math_basic",       "total (price * quantity)")
    run("math_date_cast",   "age_days (today - member.created_at) as.days")
    run("math_round",       "total (price * quantity) round.up")
    run("math_round_to",    "total (price * quantity) round.to 2")
    run("bool_expr",        "is_large (amount > 1000)")
    run("math_equality",    "is_match (result = expected)")
    run("as_decimal",       "price amount as.decimal")

    # ── String ops ────────────────────────────────────────────
    run("truncate_to",      "short_name truncate.to 35")
    run("mask_all",         'hold masked = "4111111111111234" mask.all except last 4')
    run("remove_special",   "remove.special raw_input")
    run("remove_html",      "remove.html raw_input")
    run("prepend_stmt",     'prepend "TXN-" to reference_number')
    run("append_stmt",      'append ".pdf" to filename')

    # ── Rerun ─────────────────────────────────────────────────
    run("rerun_simple",     "rerun sendAlert with event")
    run("rerun_n",          "rerun.3 sendAlert with event")
    run("rerun_after",      "rerun.after 5 seconds sendAlert with event")

    # ── Sign / verify ─────────────────────────────────────────
    run("sign_url", """
sign url for cloud_storage
    expires in 30 minutes
    named download_url
sign: done
""")
    run("verify_token", """
verify token from request.header "Authorization"
    scope "read:members"
    on.failure give back 401 "Unauthorized"
verify: done
""")

    # ── miomap — field direction arrows ──────────────────────
    run("miomap_arrow_canonical", """
miomap ACHToCanonical
    from sh.ACH
    to sh.Canonical
    fields
        account_number -> account
        routing_number -> routing
        full_name      -> display_name
    fields: done
miomap: done
""")
    run("miomap_to_alternative", """
miomap ACHToCanonical
    from sh.ACH
    to sh.Canonical
    fields
        account_number to account
        routing_number to routing
    fields: done
miomap: done
""")
    run("miomap_with_transform", """
miomap ACHToCanonical
    from sh.ACH
    to sh.Canonical
    fields
        amount -> total
            divide.by 100
        full_name -> display_name
            as.uc
    fields: done
miomap: done
""")

    # ── miomap — rejected forms ───────────────────────────────
    run("left_arrow_rejected",  "name ← source", expect="reject")
    run("left_arrow_ascii_rejected", "name <- source", expect="reject")
    run("cm_purge_with_reason", """
cm.purge member.id
    reason "GDPR Article 17"
    includes "user_records"
cm.purge: done
""")

    # ── Time expressions ──────────────────────────────────────
    run("time_now",         "cutoff now()")
    run("time_now_minus",   "cutoff now() - 24 hours")
    run("time_today",       "cutoff today")
    run("time_last_month",  "cutoff last_month")
    run("uuid_call",        "id uuid()")
    run("uuid_default", """
shape X
    id as uuid default uuid()
shape: done
""")

    # ── Closers ───────────────────────────────────────────────
    # A lone closer must PARSE (that is what powers the "unmatched closer" error),
    # but it is not a valid program -- so grammar-only.
    run("named_closer",     "retrieve: done",  expect="parse")
    run("ai_closer",        "ai.decide: done", expect="parse")
    run("bare_done",        "done",            expect="parse")

    # ── Error handling ────────────────────────────────────────
    run("try_block", """
try
    give back 200 "ok"
on.failure
    give back 503 "error"
always
    miolog.info "attempt complete"
try: done
""")
    run("transaction_block", """
transaction
    save to db.orders
        amount cart.total
    save: done
transaction: done
""")

    # ── Service calls ─────────────────────────────────────────
    run("service_2part",    'miolog.alert "High-risk transaction flagged"')
    run("miomail_send",     "miomail.send to user.email")

    # ── Comments / whitespace ─────────────────────────────────
    run("comment",          "// this is a comment\nhalt")
    run("blank_lines",      "\n\nhalt\n\n")


# ══════════════════════════════════════════════════════════════
# SUITE 2 — REJECT TESTS (grammar must refuse)
# ══════════════════════════════════════════════════════════════

if SUITE in ("all", "reject"):
    section("SUITE 2 — REJECT TESTS (grammar must refuse)")

    # if as block opener — retired
    # `if` as a block opener now PARSES on purpose (retired_if_block), so the transformer can
    # raise the directional "if is a trailing guard" message instead of the user getting a bare
    # "Syntax error". A compile error is the stronger assertion: it proves the MESSAGE fires.
    run("if_block_opener_rejected", """
if score > 90
    give back "excellent"
if: done
""", expect="error")

    # parens on task — retired
    run("task_parens_rejected",
        'task greet(name text) returns text\n    give back "Hello"\ntask: done',
        expect="reject")

    # parens on ai.decide — retired
    run("ai_decide_parens_rejected",
        "ai.decide isFraudulent(transaction) returns boolean\n    confidence above 0.85\n    not confident\n        give back false\nai.decide: done",
        expect="reject")

    # match is (old form) inside retrieve — retired, match TO is canonical
    # Note: 'match id is value' is now just parsed as assignment in some contexts
    # This tests that the retrieve block with wrong match form fails structurally
    # (Skipping — Earley may still parse it as assignment noise, grammar doesn't hard-reject)

    # order by ... ascending/descending — retired SQL form
    # Grammar parses these as assignment noise; transformer source scan warns
    run("order_by_ascending_retired", """
find members in db.members
    order by name ascending
find: done
""", expect="parse")  # grammar accepts (noise) — mio fmt converts

    run("order_by_descending_retired", """
find tx in db.transactions
    order by created_at descending
find: done
""", expect="parse")  # grammar accepts (noise) — mio fmt converts


# ══════════════════════════════════════════════════════════════
# SUITE 3 — TRANSFORMER TESTS
# ══════════════════════════════════════════════════════════════

if SUITE in ("all", "transformer"):
    section("SUITE 3 — TRANSFORMER TESTS")

    # ── Hard errors ───────────────────────────────────────────

    run("error_ai_decide_missing_not_confident", """
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount
    ai.audit to fraud_audit_log
ai.decide: done
""", expect="error", error_contains="not confident")

    run("error_ai_audit_after_not_confident", """
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount
    not confident
        give back 202 "Referred"
    ai.audit to fraud_audit_log
ai.decide: done
""", expect="error", error_contains="before")

    run("error_cm_purge_no_reason", """
cm.purge member.id
    includes "user_records"
cm.purge: done
""", expect="error", error_contains="reason")

    run("error_invoke_phase3", """
invoke TransactionMonitor
    with transaction
invoke: done
""", expect="error", error_contains="Phase 3")

    # call is retired — must compile error
    # Note: call generates error in _scan_source before grammar parsing
    # So it appears in transformer errors, not grammar reject tests.
    # Test is in the transformer test suite instead.

    run("error_recall_phase3", """
recall from agent.memory
    similar to "fraud patterns"
    limit 5
recall: done
""", expect="error", error_contains="Phase 3")

    run("error_modify_every_no_noun", """
modify every in portrait_file
    apply miogscreen.remove_background
    apply: done
modify: done
""", expect="error", error_contains="noun")

    run("error_bidir_arrow_reserved", """
miomap SyncMap
    from sh.Source
    to sh.Dest
    fields
        account <-> account_number
    fields: done
miomap: done
""", expect="error", error_contains="bidirectional")

    run("error_closer_mismatch", """
retrieve member from db.members
    match id to request.id
find: done
""", expect="error", error_contains="mismatch")

    run("error_pci_save_cvv", """
sector: financial
save to db.cards
    card_cvv request.card_cvv
save: done
""", expect="error", error_contains="PCI")

    # ── Warnings ──────────────────────────────────────────────

    # `set` is RETIRED, not merely warned about. It used to be accepted as noise and silently
    # discarded, which is how a dead keyword survives in docs and comes back as canon.
    run("set_keyword_rejected",
        'set name "Ronnie"',
        expect="error")

    run("warn_task_named_closer", """
task greet
    take name as text
    returns text
    give back "Hello"
greet: done
""", expect="warn", warn_contains="task: done")

    # `check confidence above N` is CANONICAL -- locked Apr 3 (LDD v2.0), never overturned.
    # This test used to assert it warned "retired", which CEMENTED a drift a compiler chat
    # introduced off a stale marker. A test written against the implementation makes the
    # implementation true by definition. It must assert the DESIGN.
    run("check_confidence_above_is_canonical", """
ai.decide isFraudulent returns boolean
    check confidence above 0.85
    weigh transaction.amount
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred"
ai.decide: done
""", expect="pass")

    run("warn_ai_decide_no_audit", """
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount
    not confident
        give back 202 "Referred"
ai.decide: done
""", expect="warn", warn_contains="audit")

    # ── Clean pass — no errors, no warnings ───────────────────

    run("pass_ai_decide_full", """
sector: financial
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount, member.history
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
ai.decide: done
""")

    run("pass_cm_purge_with_reason", """
cm.purge member.id
    reason "GDPR Article 17"
    includes "user_records"
cm.purge: done
""")

    run("pass_task_canonical", """
task greet
    take name as text
    returns text
    give back "Hello"
task: done
""")

    run("pass_halt_if", "halt if maintenance_mode is true")


# ══════════════════════════════════════════════════════════════
# SUITE 4 — DEMO FILES
# ══════════════════════════════════════════════════════════════

if SUITE in ("all", "demos"):
    section("SUITE 4 — DEMO FILES (parse + transform)")

    demo_files = {
        "fraud_demo":       "tests/fraud_demo.mho",
        "invoice_saga":     "tests/invoice_saga.mho",
        "member_dashboard": "tests/member_dashboard.mho",
        "patient_intake":   "tests/patient_intake.mho",
    }

    for name, path in demo_files.items():
        try:
            with open(path, encoding='utf-8') as f:
                src = f.read()
            run(name, src)
        except FileNotFoundError:
            failed += 1
            print(f"  ❌  {name} — file not found: {path}")


# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════

# ── silent no-op lint ─────────────────────────────────────────────────────────────────
# Gated deliberately. This bug class -- a construct that parses cleanly and then discards the
# value the developer wrote -- survived review after review, because every layer looks correct in
# isolation and nothing ever errors. A lint that is not gated drifts back up; one that is gated
# cannot. Findings must stay at zero.
_lint_findings = 0
try:
    import subprocess as _sp
    _lint = _sp.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  'tools', 'silent_noop_lint.py')],
                    capture_output=True, text=True, timeout=180,
                    env=dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(__file__))))
    _lint_findings = 0 if _lint.returncode == 0 else 1
    if _lint_findings:
        failed += 1
        results.append(("silent no-op lint", "FAIL", "constructs discarding their values"))
        print("\n" + _lint.stdout.strip()[-1200:])
    else:
        passed += 1
        results.append(("silent no-op lint", "PASS", ""))
except Exception as _e:
    # A lint that cannot run must not silently pass -- that would be the same failure mode it
    # exists to catch.
    failed += 1
    results.append(("silent no-op lint", "FAIL", f"lint could not run: {_e}"))

total = passed + failed
print(f"\n{'='*55}")
print(f"RESULTS: {passed}/{total} passed", "✅" if failed == 0 else f"— {failed} FAILED ❌")
if failed:
    print("\nFailed tests:")
    for name, status, err in results:
        if status == "FAIL":
            print(f"  - {name}" + (f": {err}" if err else ""))
print('='*55)
sys.exit(0 if failed == 0 else 1)
