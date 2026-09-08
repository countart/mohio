# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-FRAMEWORK-FOUNDATION (2026-08-24) -- the framework registry and resolution point.

`framework:` declares an app's TARGET type. It is orthogonal to `sector:`: sector enforces RULES,
framework scaffolds STRUCTURE. At most one of each, neither required -- a project with no
`framework:` declaration IS a `web` app, which is the default.

WHY THIS IS A MODULE AND NOT A FIELD ON THE CONTEXT -- the scaffold-vs-runtime finding.

`sector:` resolves at EXECUTION time: `_exec_SectorDecl` sets `ctx._sector` when the statement
runs. Framework cannot work that way, and the reason is structural rather than stylistic. The
serve layer has to know the framework BEFORE it runs anything, because the framework decides
WHETHER and HOW the program is invoked at all -- `web` serves a rendered file at its
convention URL, an api answers endpoint calls and does no page routing. By the time a statement
executes, that decision is already made. So framework resolves at ASSEMBLY time, from the
finished program, and is held by the serve layer for the life of the app.

That is exactly what "the app RUNS UNDER its framework" means in practice: not a value the
program sets as it goes, but an application-level context established once, before the first
statement, and consulted by the runtime on every request. `_apply_journey` merges the journey
spine into every page's program before any server is built, so a single `framework:` line in
`journey.mho` reaches every page in the app -- which is why the declaration is journey-level and
why the framework governs the APPLICATION rather than the page.

An unknown value is refused at TRANSFORM time (so `mio check` catches it, not the first request).
A declared-but-unbuilt framework is accepted by the grammar and refused LOUDLY at serve time --
never a silent fallthrough to `web` behaviour, because a developer who writes
`framework: mobile` today must find out where it stands rather than quietly get a website.
"""
from __future__ import annotations

# ── The registry ────────────────────────────────────────────────────────────────────────────
#
# FRAMEWORK IS THE CATEGORY, NOT THE STRUCTURE (ruled 2026-08-24). `web` is the framework;
# "app-ness" -- multi-page vs SPA vs PWA vs dashboard -- is the SCAFFOLD layer, a separate
# catalog effort with its own build lane (`scaffold-catalog-design.md`). An earlier draft of this
# file used `web-app` as the canonical value, which collapsed those two levels into one word.
# It does not: `framework: web` will pair with a `scaffold:` declaration later (default
# multi-page). `scaffold:` is deliberately NOT built here and nothing below precludes it -- the
# framework value is kept free of structure words so the second axis stays available.
#
# Three tiers, and the middle one is a real distinction rather than a hedge:
#
#   BUILT              -- the framework serves today.
#   DECLARED_NOT_BUILT -- a real, named, designed target whose serving behaviour does not exist
#                         yet. Declaring it is valid; serving it fails loud.
BUILT = {
    'web':  "files serve at their convention URL, no routing code",
    'api':  "endpoint calls only, no page routing -- the external-frontend case",
    'game': "web games: an existing scaffold plus the MioScript game primitives back this "
            "today, so it is a real framework and NOT a behaviour-built-later stub",
}

DECLARED_NOT_BUILT = {
    'mobile':       "native mobile; routing is screen/view navigation, not URLs",
    'desktop':      "native desktop; screen/window navigation",
    'infotainment': "in-car and embedded surfaces; signal/event and screen mapping",
}

# Frameworks whose serving model IS convention web serving (filename -> URL, render the file,
# serve the output). `game` is here because a Mohio game is a WEB game -- it is served over HTTP
# like any other web app; what makes it its own framework is the scaffold and the MioScript game
# primitives behind it, not a different transport. `api` is deliberately absent: it answers
# endpoint calls and does NO page routing, which is the whole point of the value.
SERVES_BY_CONVENTION = frozenset({'web', 'game'})

# No aliases. `web` and `api` are the canonical spellings and the only ones (ruled 2026-08-24 --
# `web-app` and `headless` were dropped rather than kept as second spellings). One word, one job.
ALIASES = {}

DEFAULT = 'web'


def canonical_base(value: str) -> str:
    """The framework a value names, ignoring any dotted sub-profile.

    A sub-profile REFINES a framework, it never changes which one you are in: `mobile.ios` is
    mobile and `api.rest` is api. Dispatch is on the base, always.
    """
    base = (value or '').split('.', 1)[0].strip()
    return ALIASES.get(base, base)


def is_known(value: str) -> bool:
    base = canonical_base(value)
    return base in BUILT or base in DECLARED_NOT_BUILT


def known_values() -> list:
    """Every accepted spelling, canonical first, for error messages."""
    return sorted(BUILT) + sorted(DECLARED_NOT_BUILT) + sorted(ALIASES)


def unknown_value_message(value: str) -> str:
    built = ', '.join(f'`{v}`' for v in sorted(BUILT))
    declared = ', '.join(f'`{v}`' for v in sorted(DECLARED_NOT_BUILT))
    return (f"`framework: {value}` is not a framework Mohio knows. "
            f"Built and ready to serve: {built}. "
            f"Declared but not built yet (they parse, serving one fails loud): {declared}. "
            f"A dotted sub-profile refines a framework rather than replacing it, so "
            f"`mobile.ios` is fine and `ios` on its own is not. Leave `framework:` out "
            f"entirely and the app is a `{DEFAULT}` app, which is the default.")


def not_built_message(value: str) -> str:
    base = canonical_base(value)
    what = DECLARED_NOT_BUILT.get(base, '')
    return (f"`framework: {value}` is declared but not built yet -- {what}. "
            f"Mohio will not quietly serve it as a `{DEFAULT}` app instead, because that would hand "
            f"you a website when you asked for something else. Built frameworks today: "
            f"{', '.join(f'`{v}`' for v in sorted(BUILT))}.")


# ── Resolution: read the framework off an assembled program ─────────────────────────────────

class FrameworkError(Exception):
    """A framework could not be resolved for this app. Always loud, never a fallback."""


def resolve(program, *, where: str = "this app") -> str:
    """The canonical framework base this program runs under.

    Reads the ASSEMBLED program -- after includes and the journey spine have been merged -- so a
    single `framework:` in `journey.mho` governs every page in the app. Returns the canonical
    base (`web`, `api`, `game`, ...), never a raw spelling, so callers dispatch on one vocabulary.

    Cardinality is enforced here rather than at parse time: the grammar cannot see that two
    SEPARATE files were merged into one program, and "at most one framework" is a property of
    the assembled app, not of any single file.
    """
    decls = _framework_decls(program)
    if not decls:
        return DEFAULT
    values = {d.framework for d in decls}
    if len(values) > 1:
        raise FrameworkError(
            f"{where} declares more than one framework ({', '.join(sorted(repr(v) for v in values))}). "
            f"An app runs under exactly one framework -- it is the application-level context "
            f"everything else executes inside, so it cannot differ between two files that are "
            f"served together. Declare `framework:` once, in the journey spine.")
    value = decls[0].framework
    if not is_known(value):
        raise FrameworkError(unknown_value_message(value))
    return canonical_base(value)


def declared_value(program) -> str:
    """The framework exactly as written, or '' when none was declared. For messages only."""
    decls = _framework_decls(program)
    return decls[0].framework if decls else ''


def _framework_decls(program) -> list:
    """Every FrameworkDecl in an assembled program, top level or inside a journey."""
    from mohio_ast import FrameworkDecl
    found = []

    def walk(node, depth=0):
        if depth > 6:                     # journeys nest shallowly; this is a cycle guard
            return
        if isinstance(node, FrameworkDecl):
            found.append(node)
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, depth + 1)
            return
        for attr in ('statements', 'body', 'declarations'):
            inner = getattr(node, attr, None)
            if isinstance(inner, (list, tuple)):
                walk(inner, depth + 1)

    walk(program)
    return found


# ── GET safety: a convention-served page is READ-ONLY ───────────────────────────────────────
#
# RULED 2026-08-24. A convention-served GET renders; it does not change things. This is HTTP's
# own safety rule (GET is safe and idempotent), it is what every major framework does, and it
# is already Mohio's own distinction one level up: `render` renders, `listen for` handles
# requests that change something. Serve-by-default made that distinction load-bearing, because
# it runs a whole file to answer a bare GET -- so a top-level `save` in a page fired on every
# page view. Verified before this guard existed: four bare GETs on a page with a top-level save
# wrote four rows (the counter climbed 2, 3, 4, 5).
#
# WHERE THE LINE SITS: statements that run on a bare GET are the page body. Statements inside a
# `listen for` / `request for` handler are NOT -- that handler is the explicit, opted-in place
# for a state change, and it only runs for the request it declares. So the scan walks the page
# body and stops at a handler boundary. Reads (`retrieve`, `find`, `check count`) and local
# variables are untouched: a page that reads data to render it is the normal case.
#
# `miohttp` is split by its own method rather than banned wholesale, which is the same rule
# applied one layer out: `miohttp.get` is a safe read a page may legitimately do to render;
# `miohttp.post`/`put`/`delete`/`patch` change something on the far end and belong in a handler.
_WRITES_ON_GET = {
    # persistence
    'SaveBlock':         'save',
    'SaveOrUpdateBlock': 'save or update',
    'SaveAllBlock':      'save all',
    'UpdateBlock':       'update',
    'RemoveBlock':       'remove',
    'RemoveAllBlock':    'remove all',
    'ModifyBlock':       'modify',
    'CmPurgeBlock':      'cm.purge',
    'SqlBlock':          'raw sql',
    # identity / client state
    'GrantRoleDecl':     'grant role',
    'MioCookieSet':      'miocookie.set',
    'MioCookieDelete':   'miocookie.delete',
    # irreversible outbound
    'MiomailStmt':       'miomail',
    'SendStmt':          'send',
}

# A handler is where a state change belongs, so the scan does not look inside one.
_HANDLER_NODES = ('ListenBlock', 'RequestInboundBlock', 'ConnectionBlock', 'RequestOutboundBlock')

_SAFE_HTTP_METHODS = frozenset({'get', 'head', 'options'})


def unsafe_on_get(program) -> list:
    """Every state-changing statement reachable on a bare convention GET.

    Returns [(verb, line), ...] in source order, empty when the page is read-only. Walks the
    page body -- including into check/repeat/try, because a `save` inside a `check` still fires
    on a page view -- and stops at a handler boundary, because a `listen for` body only runs for
    the request it declares.
    """
    found = []
    seen = set()

    def walk(node, depth=0):
        if depth > 40 or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, depth + 1)
            return
        name = type(node).__name__
        if name in _HANDLER_NODES:
            return                        # explicit handler: a state change is allowed there
        if name == 'MiohttpStmt':
            method = str(getattr(node, 'method', 'get') or 'get').lower()
            if method not in _SAFE_HTTP_METHODS:
                found.append((f'miohttp.{method}', getattr(node, 'line', 0)))
            return
        if name in _WRITES_ON_GET:
            found.append((_WRITES_ON_GET[name], getattr(node, 'line', 0)))
            return
        if not hasattr(node, '__dict__'):
            return
        for value in vars(node).values():
            if isinstance(value, (list, tuple)) or hasattr(value, '__dict__'):
                walk(value, depth + 1)

    walk(getattr(program, 'statements', None) or [])
    found.sort(key=lambda p: (p[1] or 0))
    return found


def read_only_violation_message(source, url_path, offenders) -> str:
    verbs = ', '.join(f'`{v}` (line {ln})' if ln else f'`{v}`' for v, ln in offenders)
    return (f"a convention-served page is read-only; move state changes into a listen-for "
            f"handler. {source} answers GET {url_path} by being run, and it changes state: "
            f"{verbs}. A GET is safe and repeatable -- a browser, a link preview or a crawler "
            f"may fetch this page any number of times, and each fetch would run that again. "
            f"`render` renders; `listen for` handles a request that changes something. Move "
            f"the state change into a `listen for ... request: done` handler and leave the "
            f"page to render what it reads.")


# ── AI on a convention-served page: ALLOWED, with a build-time warning ──────────────────────
#
# RULED 2026-08-24. An AI call on a convention GET is allowed. It is corruption-safe: it reasons
# and returns an answer, it does not change stored state, so re-running it on a repeat view
# cannot leave the app wrong. That is a different question from whether it is EXPENSIVE, and the
# expense is the coder's call to make, not Mohio's.
#
# So this WARNS at build time and never refuses. What the warning has to convey is the thing a
# developer does not picture when they put `ai.decide` on a page: convention serving runs the
# file on every single view, a public page may be crawled, and each view is a real, billed AI
# call.
#
# EXPLICITLY NOT DONE, and both were considered and ruled against:
#   * robots.txt is NOT touched. The coder may well want the page indexed -- that is their
#     decision about their own site, and a compiler quietly editing it would be taking it.
#   * NO bot detection. It is an arms race, it misclassifies real users, and it is not the
#     control that matters here.
# The real control is RATE LIMITING: it bounds cost against ALL high-volume GETs, whoever is
# making them, without touching crawlability. Recommended in the docs; a candidate feature
# later, not built now.
#
# The four constructs below are the ones that actually reach the AI runtime and bill per call
# (`self.ai.decide` / `self.ai.agent_turn`, traced in mohio_interpreter.py). `ai.connect`,
# `ai.create` and `ai.override` are setup, not per-view spend, so they are not warned about.
_AI_COSTS_PER_VIEW = {
    'AiDecideBlock':  'ai.decide',
    'AiDecideInvoke': 'ai.decide',
    'AiCompareBlock': 'ai.compare',
    'AiRespondBlock': 'ai.respond',
    'AiAgentBlock':   'ai.agent',
}


def ai_cost_on_get(program) -> list:
    """Every billed AI call reachable on a bare convention GET, as [(verb, line), ...].

    Same walk and same handler boundary as `unsafe_on_get`: a call inside a `listen for` handler
    runs only for the request it declares, so it is not a per-view cost and is not warned about.
    """
    found = []
    seen = set()

    def walk(node, depth=0):
        if depth > 40 or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, depth + 1)
            return
        name = type(node).__name__
        if name in _HANDLER_NODES:
            return
        if name in _AI_COSTS_PER_VIEW:
            found.append((_AI_COSTS_PER_VIEW[name], getattr(node, 'line', 0)))
            # keep walking: an agent block can legitimately contain further calls
        if not hasattr(node, '__dict__'):
            return
        for value in vars(node).values():
            if isinstance(value, (list, tuple)) or hasattr(value, '__dict__'):
                walk(value, depth + 1)

    walk(getattr(program, 'statements', None) or [])
    found.sort(key=lambda p: (p[1] or 0))
    return found


def ai_cost_warning(url_path, calls) -> str:
    verbs = ', '.join(f'{v} (line {ln})' if ln else v for v, ln in calls)
    return (f"{url_path} calls AI on a plain page view: {verbs}. This page runs on EVERY view, "
            f"including crawler hits if it is indexed, and every view is a real AI cost. Set "
            f"rate limits. Mohio does not touch robots.txt or try to spot bots -- whether this "
            f"page is crawled is your call.")


# ── `map` route resolution: mounts and redirects the serve layer applies ────────────────────
#
# T1-MAP-EXTRACTION (2026-08-24). `map` is where a developer declares the routing the
# framework's convention cannot carry. It is read the same way the framework is -- once, off the
# ASSEMBLED program, before any request -- because a mount CHANGES which file answers a URL and
# a redirect answers instead of a file. Both are routing decisions, and routing is settled before
# a request arrives, not during one.
#
# The `route` classifier is FRAMEWORK-SCOPED: it means "URL routing", which is a web idea. A
# framework that does not serve by convention has no use for it, and later frameworks will
# register their own classifiers (`screen` for mobile, `signal` for hardware) rather than
# overloading this one.

class MapError(Exception):
    """A `map` could not be resolved. Always loud, never a silent partial route table."""


def _map_decls(program) -> list:
    from mohio_ast import MapSectionsDecl
    found, seen = [], set()

    def walk(node, depth=0):
        if depth > 8 or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, MapSectionsDecl):
            found.append(node)
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, depth + 1)
            return
        for attr in ('statements', 'body', 'declarations'):
            inner = getattr(node, attr, None)
            if isinstance(inner, (list, tuple)):
                walk(inner, depth + 1)

    walk(program)
    return found


def resolve_map(program):
    """(mounts, redirects) declared by every `map` in this assembled program.

    mounts    -- {url_path: source_file}
    redirects -- {from_path: (to_path, status)}

    Several `map` blocks may coexist (a journey and a page can each declare one), so they are
    merged. A COLLISION is refused rather than resolved by ordering: two mounts claiming one URL,
    or two redirects leaving one path, is a genuine contradiction in the app's routing, and
    picking the last one silently would make which file answers depend on file-walk order.
    """
    mounts, redirects = {}, {}
    for decl in _map_decls(program):
        where = f"`map {decl.name}`" if decl.name else "`map`"
        for m in decl.mounts:
            path = _normalise_path(m.path)
            if path in mounts and mounts[path] != m.source:
                raise MapError(
                    f"{where} mounts two different files at {path!r} ({mounts[path]!r} and "
                    f"{m.source!r}). One address answers with one file. Remove one of the "
                    f"mounts, or give them different paths.")
            mounts[path] = m.source
        for r in decl.redirects:
            src = _normalise_path(r.source)
            if src in redirects and redirects[src] != (_normalise_path(r.target), r.status):
                raise MapError(
                    f"{where} redirects {src!r} to two different places. A path leads one way. "
                    f"Remove one of the redirects.")
            redirects[src] = (_normalise_path(r.target), r.status)
    both = set(mounts) & set(redirects)
    if both:
        p = sorted(both)[0]
        raise MapError(
            f"{p!r} is both mounted and redirected. It cannot answer with a file and send the "
            f"visitor somewhere else at the same time. Decide which one it is.")
    return mounts, redirects


def resolve_map_responses(program):
    """{status: (kind, target)} declared by every `map` route section in this program.

    A SEPARATE function rather than a third return value from `resolve_map`, because that one
    has callers and a silently-widened tuple is how a caller starts unpacking the wrong thing.

    A COLLISION is refused for the same reason a duplicate mount is: two declarations for one
    status is a contradiction about what the app answers, and picking the later one silently
    would make the answer depend on file-walk order.
    """
    out = {}
    for decl in _map_decls(program):
        where = f"`map {decl.name}`" if decl.name else "`map`"
        for r in (getattr(decl, 'responses', None) or []):
            key = int(getattr(r, 'status', 0) or 0)
            val = (getattr(r, 'kind', ''), getattr(r, 'target', ''))
            if key in out and out[key] != val:
                raise MapError(
                    f"{where} declares two different answers for [{key}]: {out[key][1]!r} and "
                    f"{val[1]!r}. One status answers one way. Remove one of them.")
            out[key] = val
    return out


def _normalise_path(path: str) -> str:
    """A leading slash, no trailing slash (except root). `/about/` and `about` are one address;
    letting them be two would make a mount silently miss the URL it was written for."""
    p = (path or '').strip()
    if not p:
        return '/'
    if not p.startswith('/'):
        p = '/' + p
    while len(p) > 1 and p.endswith('/'):
        p = p[:-1]
    return p
