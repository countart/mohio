# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_server.py
Mohio Language — HTTP Server Runtime
Particular LLC. Version comes from mohio_version (single source of truth).

Pure Starlette routing — bypasses FastAPI parameter injection entirely.
All routes use raw Starlette Request objects via add_route().

GENERAL RUNTIME. This server knows nothing about any specific app: no app's schema, no app's
seed data, no app's front end. It serves any app directory — static files, the app's own
index.html, and routed .mho pages — and nothing more.

Deliberately NOT here:
  - database admin (seed / stats / reset). Per-tenant DB management is a CONTROL-PLANE concern:
    the platform connects to the tenant's database with credentials it already holds, behind its
    own auth. A served app carries no database-admin surface, which keeps every tenant machine's
    secret list minimal and removes admin attack surface from every microVM.
  - seeding. There is no generic seeder: uniqueness is a per-app claim about the world and cannot
    be inferred. Seeding belongs with the app, or with the control plane.
  - any bundled demo front end. An app with no root route and no index.html gets a neutral page.

CONCURRENCY MODEL: SINGLE WORKER (deliberate, not incidental).
Sessions live in process memory, so a second uvicorn worker would hold a SEPARATE session dict
and a user's identity would flap between requests depending on which worker answered. Multi-worker
is therefore actively wrong until sessions are durable and shared. Once a durable session store
lands, multi-worker becomes safe and is worth revisiting as a scaling lever -- durable sessions are
the precondition, not an optimisation.

REQUEST LIMITS: body and upload sizes are capped and requests are bounded by a wall-clock timeout
(see _MAX_BODY_BYTES / _MAX_UPLOAD_BYTES / _REQUEST_TIMEOUT). All are env-configurable.
"""

from __future__ import annotations
import collections, json, os, sys, datetime, threading, time as _pooltime
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))

# Static file directories searched in order for GET requests
# Files that must NEVER be served as static, whatever their extension would map to. A general
# host serves arbitrary asset types, so an allowlist is wrong here -- but it must never hand out
# source, config, credentials, or a database. Dotfiles and dot-directories (.git/, .env) are
# rejected separately by name.
_DENY_STATIC_EXT = {
    # A build artifact derived from source IS source. `.mho` was denied while
    # `index.mho.cache` -- a pickled parse tree holding every literal in the file,
    # keys included -- was served on request. Denying the source and shipping its
    # serialized form beside it is the same leak through a different name.
    ".cache", ".pkl", ".pickle",                          # build artifacts
    # SQLite writes its live data to sidecar files. `.db` was refused while the
    # write-ahead log sitting next to it -- holding everything written recently --
    # was served on request.
    ".db-wal", ".db-shm", ".db-journal",
    ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
    ".old", ".save", ".tmp", ".temp", ".dump", ".core",   # leftovers and dumps
    ".mho", ".py", ".pyc", ".lark",                       # source
    ".env", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml",   # config
    ".db", ".sqlite", ".sqlite3", ".sql",                 # data
    ".pem", ".key", ".crt", ".p12", ".pfx",               # credentials
    ".p7b", ".p7c", ".p8", ".der", ".jks", ".keystore",   # more key material
    ".ppk", ".asc", ".gpg", ".kdbx",
    ".log", ".bak", ".swp", ".orig",                      # incidental
}


# Files that carry no extension at all but must never be served. `id_rsa` is a
# private key whether or not anything in its name says so.
_DENY_STATIC_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys", "known_hosts",
    "htpasswd", "passwd", "shadow", "credentials", "core",
}


def _is_denied_static_name(name: str) -> bool:
    """True when this filename must never be served, whatever directory it sits in.

    Checks EVERY suffix, not just the last one. `splitext` sees `backup.sql.gz` as a
    `.gz` file and served it, which meant compressing a database dump was enough to
    get it past the list -- so was renaming a log to `app.log.1`. A denied type stays
    denied under a wrapper.
    """
    low = name.lower()
    if low in _DENY_STATIC_NAMES:
        return True
    if low.endswith("~"):          # editor backup of whatever it copied
        return True
    if "." in low:
        segs = low.split(".")
        for i in range(1, len(segs)):
            if ("." + segs[i]) in _DENY_STATIC_EXT:
                return True
            # `.p8`, `.p12` and friends still read as key material.
            if ("." + ".".join(segs[i:])) in _DENY_STATIC_EXT:
                return True
    return False


def _xml_body(body):
    """Serialize a value as XML for `give back ... as xml`.

    Lived inside the POST responder only, so a GET -- which is how every feed and
    sitemap is actually fetched -- set an `application/xml` content type and then sent
    `str(body)`, i.e. a Python dict repr under an XML header. One copy, used by both
    responders, so the two cannot disagree again.
    """
    import xml.sax.saxutils as _sx

    def _el(tag, val):
        tag = str(tag)
        if isinstance(val, dict):
            return "<%s>%s</%s>" % (tag, "".join(_el(k, v) for k, v in val.items()), tag)
        if isinstance(val, (list, tuple)):
            it = tag[:-1] if (tag.endswith("s") and len(tag) > 1) else "item"
            return "<%s>%s</%s>" % (tag, "".join(_el(it, v) for v in val), tag)
        return "<%s>%s</%s>" % (tag, _sx.escape("" if val is None else str(val)), tag)

    # A string that already looks like markup is passed through untouched. That is how
    # a feed or a sitemap is assembled today -- the program built the XML itself, and
    # escaping it would turn a working feed into a page of visible angle brackets.
    # Structured data is serialized; a plain string is wrapped.
    if isinstance(body, str) and body.lstrip()[:1] == "<":
        return body
    return '<?xml version="1.0" encoding="UTF-8"?>' + _el("response", body)


def _static_roots():
    """Directories static files may be served from, most specific first.

    Deliberately does NOT include the compiler's own directory. Rooting static serving where
    mohio_server.py lives meant `GET /mohio_interpreter.py` returned the runtime's source from
    every tenant app, and meant the app's real assets were never searched at all.
    """
    roots = []
    explicit = os.environ.get("MOHIO_STATIC_DIR")
    if explicit:
        roots.append(Path(explicit))
    if _APP_DIR[0] is not None:
        roots.append(_APP_DIR[0])
    if not roots:
        roots.append(Path.cwd())          # local `mio serve` with no app dir supplied
    return roots


# Set by create_app from the server's app_dir; a module-level cell so the closure can read it.
_APP_DIR = [None]


# ══════════════════════════════════════════════════════════════
# ADMIN GATE
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# SERVER STATE
# ══════════════════════════════════════════════════════════════

def _declared_status_response(server, status):
    """Answer a status from the app's `map route` declaration, or None to keep the default.

    THE THREE STATES, and the third one is the reason this returns None rather than inventing
    something: a declared PAGE renders, a declared MESSAGE is the body, and NO DECLARATION falls
    through to the platform or server default. A route need not declare an error response, and
    often should not -- a good default page beats a bare message.

    The message form is deliberately naked text. On an HTML route that is a bare string in the
    browser, and that is the coder's call: the compiler does not guess HTML-versus-API context
    from the shape of a route, because guessing it wrong is worse than answering exactly what
    was asked for.
    """
    from starlette.responses import Response
    declared = getattr(server, "status_responses", None) or {}
    entry = declared.get(int(status))
    if not entry:
        return None
    kind, target = entry
    if kind == "message":
        return Response(status_code=int(status), content=str(target),
                        media_type="text/plain", headers=CORS_HEADERS)

    # A PAGE. Render the mounted program for that path, and serve what it produced AT the
    # declared status -- the page is an ordinary page, the status is the route's business.
    programs = getattr(server, "sibling_programs", None) or {}
    interps = getattr(server, "sibling_interps", None) or {}
    key = "/" + str(target).strip().lstrip("/").rstrip("/")
    prog = programs.get(key)
    interp = interps.get(key)
    if prog is None or interp is None:
        return None                       # nothing mounted there: keep the default 404
    previous = list(getattr(interp, "shown", []) or [])
    try:
        interp.shown = []
        ran = interp.run(prog)
        produced = [str(x) for x in (interp.shown or []) if str(x).strip()]
        body = None
        if isinstance(ran, dict) and ran.get("body"):
            body = ran["body"]
        elif produced:
            body = "\n".join(produced)
        if body is None:
            return None
        return Response(status_code=int(status), content=str(body),
                        media_type="text/html", headers=CORS_HEADERS)
    except Exception as _page_err:
        # SILENT-SHAPE ANNOTATION, and the ratchet was right to ask. A custom error page that
        # itself fails must never replace the error the visitor came here for -- they asked for
        # a missing page and would get a 500 about the 404 page instead, which is worse than the
        # 404. So the fallback IS correct here. What is not correct is being quiet about it: the
        # operator needs to know their error page is broken, so it says so on stderr and the
        # visitor still gets the status they came for.
        import sys as _sys
        print(f"  [route] the custom [{status}] page at {target!r} failed to render: "
              f"{type(_page_err).__name__}: {_page_err}. Answering with the plain status "
              f"instead.", file=_sys.stderr)
        return None
    finally:
        interp.shown = previous


def _convention_body(server, url_path):
    """Run a convention-mapped file and serve whatever it produced, or None to keep the 404.

    T1-FRAMEWORK-FOUNDATION Phase 2. This is the whole no-boilerplate promise: a file in a
    served folder answers at its convention URL with no `page` block and no route registration.
    Rendering already worked -- `mio run about.mho` emits a full HTML document -- so the only
    gap was the serve layer, which had no way to turn that output into a response.

    A `render` block compiles to a ShowBlock, and its HTML lands in `interp.shown`, which is
    also where `show` output goes. That is deliberate and correct here: in a convention-served
    framework the page IS whatever the file emits.

    Returns None when this framework does not serve by convention, so the caller keeps its 404.
    Raises loudly when the framework is declared but not built -- silently handing back a web
    page to someone who wrote `framework: mobile` would be the worst possible answer.
    """
    from starlette.responses import Response
    from mohio_interpreter import _GiveBack
    from mohio_framework import (SERVES_BY_CONVENTION, DECLARED_NOT_BUILT, not_built_message)
    fw = getattr(server, "framework", None)
    if fw in DECLARED_NOT_BUILT:
        return Response(status_code=501,
                        content=not_built_message(fw),
                        media_type="text/plain",
                        headers=CORS_HEADERS)
    if fw not in SERVES_BY_CONVENTION:
        return None                      # `api`: endpoints only, the 404 is correct

    # Serve ONLY this app's own mapped URL. create_multi_app sends every unmatched path to the
    # index app as a catch-all, so without this an absent URL would render the home page at 200.
    own = getattr(server, "route_path", None)
    expected = own if own is not None else "/"
    if (url_path or "/").rstrip("/") != expected.rstrip("/"):
        return None                      # not this file's URL -- a real 404

    # A convention-served GET is READ-ONLY (ruled 2026-08-24). Serve-by-default answers a
    # bare GET by RUNNING the file, so a top-level `save` would fire on every page view --
    # verified before this guard: four GETs on a page with a top-level save wrote four rows.
    # Refuse BEFORE running, never after: the whole point is that nothing mutates.
    from mohio_framework import unsafe_on_get, read_only_violation_message
    offenders = unsafe_on_get(server.program)
    if offenders:
        src = getattr(server.program, "source_path", None) or url_path
        return Response(status_code=500,
                        content=read_only_violation_message(src, url_path, offenders),
                        media_type="text/plain", headers=CORS_HEADERS)

    interp = server.interp
    previous = list(getattr(interp, "shown", []) or [])
    try:
        interp.shown = []
        _ran = interp.run(server.program)
        produced = [str(x) for x in (interp.shown or []) if str(x).strip()]
        # A top-level `give back` IS the page's answer -- the file producing output, which is
        # exactly what convention serving serves. `run()` catches the give-back internally and
        # RETURNS it as {status, body} rather than raising, so it has to be read off the return
        # value; watching for an exception here caught nothing. Before this the program answered
        # and nobody heard it: `shown` was empty, the convention path reported "produced no
        # output", and the request fell through to the neutral "no home page yet" placeholder.
        # Found while removing `page`, because a great many files used `page at /` for no reason
        # other than to give a `give back` somewhere to live.
        if isinstance(_ran, dict) and 'status' in _ran and not produced:
            interp.shown = previous
            _body = _ran.get('body')
            _text = '' if _body is None else str(_body)
            _html = _text.lstrip()[:1] == '<'
            _resp = Response(status_code=int(_ran.get('status') or 200), content=_text,
                             media_type="text/html" if _html else "text/plain",
                             headers=CORS_HEADERS)
            _resp.mohio_answered = True
            return _resp
    except _GiveBack as gb:
        # A top-level `give back` IS the page's answer -- it is the file producing output, which
        # is exactly what convention serving serves. Before this it escaped as an exception, the
        # convention path reported a failure, and the request fell through to the neutral
        # "no home page yet" placeholder: the program answered and nobody heard it. Found while
        # removing `page`, because a great many files used `page at /` for no reason other than
        # to give a `give back` somewhere to live.
        interp.shown = previous
        _status = getattr(gb, 'status', None) or 200
        _value = getattr(gb, 'value', None)
        _body = '' if _value is None else (
            _value.to_python() if hasattr(_value, 'to_python') else _value)
        _text = '' if _body is None else str(_body)
        _html = _text.lstrip()[:1] == '<'
        _resp = Response(status_code=int(_status), content=_text,
                         media_type="text/html" if _html else "text/plain",
                         headers=CORS_HEADERS)
        _resp.mohio_answered = True
        return _resp
    except Exception as exc:
        # Running the page is the response here, so a failure IS the response -- surface it
        # rather than letting it read as "no such page".
        interp.shown = previous
        return Response(status_code=500,
                        content=f"{url_path} failed while running: {exc}",
                        media_type="text/plain",
                        headers=CORS_HEADERS)
    finally:
        pass

    if not produced:
        # FAIL LOUD. A convention-mapped file that answers a direct GET with nothing is a real
        # mistake -- an empty body or a bare 404 would send the author hunting for a routing
        # problem that does not exist. Name the file, the method and the path.
        source = getattr(server.program, "source_path", None) or url_path
        interp.shown = previous
        # Two different situations reach here and they must not share one message. A file
        # with NO routes at all simply forgot to render. A file that DOES declare routes
        # answers other requests fine and just has nothing for this GET -- telling its author
        # to "add a listen for route" would send them looking for something already there.
        from mohio_ast import ListenBlock as _Listen
        _has_routes = any(isinstance(st, _Listen)
                          for st in (getattr(server.program, "statements", None) or []))
        if _has_routes:
            hint = (f"{source} declares routes but none of them answered GET {url_path}, and "
                    f"the file rendered nothing either, so there is no response to send. If "
                    f"this endpoint is only meant for another method, that is fine and this "
                    f"GET simply has no page; add a `render` block if it should also answer "
                    f"in a browser.")
        else:
            hint = (f"{source} produced no output for GET {url_path}. A file served by "
                    f"convention answers with whatever it renders or shows, and this one "
                    f"emitted nothing. Add a `render` block (or a `show`), or give it a "
                    f"`listen for` route if it was never meant to answer a GET.")
        return Response(status_code=500, content=hint, media_type="text/plain",
                        headers=CORS_HEADERS)

    body = "\n".join(produced)
    interp.shown = previous
    looks_html = body.lstrip()[:1] == "<"
    return Response(status_code=200, content=body,
                    media_type="text/html" if looks_html else "text/plain",
                    headers=CORS_HEADERS)


class MohioServer:
    def __init__(self, program, interp, verbose=False, app_dir=None, route_path=None):
        self.program       = program
        self.interp        = interp
        self.verbose       = verbose
        self._session_store = self._build_session_store()
        self.start_time    = datetime.datetime.utcnow()
        self.request_count = 0
        # Where this app's own static files live. Static serving is rooted HERE, never at the
        # compiler's directory -- serving from the compiler directory handed out the runtime's
        # own source (mohio_server.py, mohio.lark, ...) over HTTP from every tenant app, and
        # meant the app's real assets were never on the search path at all.
        self.app_dir       = Path(app_dir).resolve() if app_dir else None

        # The URL the CLI mapped THIS file to (about.mho -> /about). Convention serving is
        # gated on it. Without that gate the catch-all in create_multi_app -- which routes
        # every unmatched path to the index app so assets and health still resolve -- made
        # `GET /nope` render the HOME PAGE at 200 instead of 404ing. Serving a real page for
        # a URL that does not exist is worse than the gap this phase closed. None means
        # single-file serve, where the file IS the app and answers at "/".
        self.route_path    = route_path

        # T1-FRAMEWORK-FOUNDATION Phase 2. The framework is resolved ONCE, here, from the
        # ASSEMBLED program -- after includes and the journey spine were merged by the CLI --
        # and held for the life of the app. It cannot be read per-request or per-statement:
        # the framework decides WHETHER and HOW a program is invoked at all, so it has to be
        # known before anything runs. This is what "the app runs under its framework" means.
        # A bad value raises here, at startup, rather than on the first request.
        from mohio_framework import resolve as _resolve_framework
        self.framework = _resolve_framework(self.program, where="this app")

    def _build_session_store(self):
        """Choose the session store: an explicitly registered provider always wins (same
        explicit-instruction-outranks-env-default precedent as the 2026-08-04 AI
        model-resolution ruling); otherwise MOHIO_SESSION_STORE=postgres selects the
        built-in Postgres-backed store (reusing DATABASE_URL, no new secret); otherwise
        the in-memory default, unchanged from before this seam existed.
        """
        from mohio_interpreter import MohioInterpreter, _InMemorySessionStore, _PostgresSessionStore
        provider = MohioInterpreter._session_store_provider
        if provider is not None:
            return provider()
        backend = os.environ.get("MOHIO_SESSION_STORE", "memory").strip().lower()
        if backend == "postgres":
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise RuntimeError(
                    "MOHIO_SESSION_STORE=postgres requires DATABASE_URL to be set. "
                    "Sessions cannot be made durable without a database to store them in."
                )
            return _PostgresSessionStore(database_url)
        return _InMemorySessionStore()

    def stats(self):
        uptime = datetime.datetime.utcnow() - self.start_time
        return {
            "status":         "running",
            "version":        VERSION,
            "build":          BUILD_SHA,
            "uptime_seconds": int(uptime.total_seconds()),
            "requests":       self.request_count,
            "sessions":       self._session_store.count(),
        }

    def clear_session(self, session_id):
        self._session_store.delete(session_id)

    def dispatch(self, payload, session_id=None):
        self.request_count += 1
        # MINT a session when the request does not carry one. Without this, a first-time
        # visitor took the plain run() path, which never sets `session` in ctx -- so
        # `session.id` was EMPTY, and a program could not bootstrap a session at all.
        # Zork writes `miocookie.set "mio_session" to session.id`, so it was setting the
        # cookie to "", and miocookie.exists reads an empty cookie as absent (correctly).
        # Chicken and egg: session.id came from the cookie, the cookie was written from
        # session.id, and neither could ever start. Every request re-ran the otherwise branch.
        if not session_id:
            import uuid as _uuid
            session_id = _uuid.uuid4().hex
        try:
            result = self.interp.run_with_session(
                self.program,
                request=payload,
                session_id=session_id,
                store=self._session_store,
            )
        except Exception as e:
            if self.verbose:
                import traceback
                traceback.print_exc()
            from mohio_interpreter import format_runtime_error, log_runtime_error
            # Use THIS REQUEST's id rather than minting a fresh one per error. Before, an error
            # got its own trace id, so it could be correlated with itself and with nothing else
            # -- not with the audit row written moments earlier in the same request.
            info = format_runtime_error(e, trace_id=getattr(self.interp, '_request_id', None))
            log_runtime_error(info, verbose=self.verbose)
            return {"status": info["status"], "body": info}

        if result is None:
            return {"status": 204, "body": None}
        if isinstance(result, dict):
            return result
        # A bare (non-dict) result is a give-back value, not a {status, body}
        # envelope. Unwrap any MohioValue so the body is the clean value rather
        # than its internal repr. NOTE: the 200 here is a fallback only -- a status
        # carried by a give-back *inside a task* is currently dropped at the task
        # boundary (see give-back-semantics design item); that is not fixed here.
        return {"status": 200, "body": _unwrap_mohio(result)}


# ══════════════════════════════════════════════════════════════
# CORS HEADERS
# ══════════════════════════════════════════════════════════════

from mohio_version import VERSION, BUILD_SHA

CORS_HEADERS = {
    # No wildcard by default on a multi-tenant runtime -- see _cors_origins().
    # A deployment opts in with MOHIO_CORS_ORIGINS.
    **({"Access-Control-Allow-Origin": os.environ.get("MOHIO_CORS_ORIGINS", "").strip()}
       if os.environ.get("MOHIO_CORS_ORIGINS", "").strip() else {}),
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    # Stateful apps: a request may depend on session state, so a cached
    # response is always wrong. Never let a browser/CDN serve a stale reply.
    "Cache-Control": "no-store, no-cache, must-revalidate",
}


def _unwrap_mohio(v):
    """Recursively unwrap MohioValue wrappers to plain Python.

    Without this, a give-back value that is still wrapped reaches json.dumps and
    serializes as its internal repr (e.g. "MohioValue('[COMMITTED]', 'string')")
    instead of the clean value the client should see. Duck-typed to avoid a hard
    import cycle with the interpreter.
    """
    if type(v).__name__ == "MohioValue" and hasattr(v, "value"):
        return _unwrap_mohio(v.value)
    if isinstance(v, dict):
        return {k: _unwrap_mohio(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_unwrap_mohio(x) for x in v]
    return v


def _safe_json_bytes(content) -> bytes:
    """Serialize any response content to JSON bytes that a client can parse.

    MohioValue wrappers are unwrapped to their plain values first. Strings
    (newlines, quotes, unicode) are escaped by json.dumps as usual; anything json
    can't handle natively (datetime, bytes, ...) is coerced via str() rather than
    raising. Module-level so it's importable and unit-testable without pulling in
    starlette.
    """
    return json.dumps(
        _unwrap_mohio(content),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _response_payload(body):
    """Map a give-back body to a JSON-serializable payload for the client.

    A dict passes through, EXCEPT that an empty/blank "message" is replaced with
    a clear marker. Anything else becomes {"message": ...}. An empty/None body
    never serializes to a blank message: a blank response is always a bug (it
    looks like a connection error to the client), so we surface it explicitly
    with a visible message and an "_empty" flag for debugging, rather than hide
    it behind "".
    """
    EMPTY_MARKER = "(no response was generated)"

    body = _unwrap_mohio(body)

    def _is_blank(v):
        return v is None or (isinstance(v, str) and v.strip() == "")

    if isinstance(body, dict):
        if _is_blank(body.get("message")):
            return {**body, "message": EMPTY_MARKER, "_empty": True}
        return body
    # A list/tuple is a collection response (e.g. `give back rows as json` from a
    # retrieve). It must serialize as a JSON array, not a stringified Python repr
    # wrapped in {"message": ...}. Items were already MohioValue-unwrapped above.
    if isinstance(body, (list, tuple)):
        return list(body)
    if _is_blank(body):
        return {"message": EMPTY_MARKER, "_empty": True}
    # A boolean or number keeps its JSON type: `give back ok found` where found is
    # boolean serializes as {"message": true}, not the Python-style {"message":
    # "True"}. Everything else becomes text.
    if isinstance(body, (bool, int, float)):
        return {"message": body}
    return {"message": str(body)}


# ══════════════════════════════════════════════════════════════
# FRONTEND / ADMIN LOADERS
# ══════════════════════════════════════════════════════════════

# The general runtime serves NO bundled demo front end. If an app provides neither a root route
# (`request for ... at /`) nor an index.html, this neutral page is shown. It names nothing
# app-specific -- the app is expected to bring its own front end.
_NEUTRAL_INDEX = (
    f"<!doctype html><html><head><meta charset='utf-8'>"
    f"<title>Mohio</title></head><body style='font-family:system-ui;"
    f"max-width:40rem;margin:4rem auto;padding:0 1rem;color:#20344a'>"
    f"<h1>Your Mohio app is running.</h1>"
    f"<p>This app has no home page yet. Add an <code>index.html</code>, or a "
    f"<code>request for ... at /</code> route in your <code>.mho</code> file, "
    f"and it will appear here.</p>"
    f"<p style='color:#5f7186;font-size:.9rem'>Served by the Mohio runtime "
    f"v{VERSION}.</p></body></html>"
)

# Session cookie name. Sensible default; an app or deployment may override via env without the
# server hardcoding any one app's choice.
_SESSION_COOKIE = os.environ.get("MOHIO_SESSION_COOKIE", "mio_session")

# The app's home page file. An explicit MOHIO_INDEX_HTML wins outright. Otherwise the server tries
# the web conventions in order. A Mohio home page (index.mho / home.mho) is not listed here because
# it is not a static file -- it is EXECUTED, and directory mode already maps `index.mho -> /`, so it
# is served by the route dispatch above this fallback. The chain is therefore:
#   1. the app's root route  (index.mho in directory mode, or `request for ... at /`)
#   2. MOHIO_INDEX_HTML      (explicit override, if set)
#   3. index.html, home.html (static conventions)
#   4. neutral page
_INDEX_FILE = os.environ.get("MOHIO_INDEX_HTML", "")

# Session identity from the X-Session-ID *header* is OFF by default. A header-supplied session id
# is attacker-suppliable: anyone who learns an id becomes that user. Browser sessions must come
# from the HttpOnly cookie, which script on another origin cannot read. Programmatic/API clients
# that genuinely need a header can turn it on per deployment, at which point the ids should be
# signed rather than accepted raw.
_ALLOW_SESSION_HEADER = os.environ.get(
    "MOHIO_ALLOW_SESSION_HEADER", "").lower() in ("1", "true", "yes")


def _session_header(request):
    """The X-Session-ID header, only when the deployment has opted in."""
    return request.headers.get("X-Session-ID") if _ALLOW_SESSION_HEADER else None
_INDEX_CANDIDATES = ("index.html", "home.html")


# ══════════════════════════════════════════════════════════════
# APP FACTORY
# ══════════════════════════════════════════════════════════════

def _secure_default(request):
    """Default for the cookie `Secure` flag: on for https, off for plain http.

    Hardcoding True meant sessions silently did not work on http://localhost -- browsers reject
    Secure cookies over plain http -- so local `mio serve` development had broken logins while
    production (https on Fly) looked fine.
    """
    try:
        if os.environ.get("MOHIO_COOKIE_SECURE", "").lower() in ("1", "true", "yes"):
            return True
        if os.environ.get("MOHIO_COOKIE_SECURE", "").lower() in ("0", "false", "no"):
            return False
        return request.url.scheme == "https"
    except Exception:
        return True


def _with_all_cookies(response, parts):
    """Attach every Set-Cookie value to the response (dict headers can only hold one)."""
    for extra in parts[1:]:
        response.raw_headers.append((b"set-cookie", extra.encode("latin-1")))
    return response



# ── Request limits (M3/M4) ────────────────────────────────────────────────────────────
# The machine is small and bodies/uploads are read fully into memory, so these are real OOM
# guards rather than theory. A cap is a guard, not a solution: the real fix for large uploads is
# streaming to disk instead of reading into memory, which is a separate follow-up.
_MAX_BODY_BYTES   = int(os.environ.get("MOHIO_MAX_BODY_BYTES",   2 * 1024 * 1024))   # 2 MB
_MAX_UPLOAD_BYTES = int(os.environ.get("MOHIO_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))  # 10 MB
_REQUEST_TIMEOUT  = float(os.environ.get("MOHIO_REQUEST_TIMEOUT", 30))               # seconds

# ══════════════════════════════════════════════════════════════
# PER-IP REQUEST BACKSTOP  (T0-RATE-LIMIT-BACKSTOP, 2026-09-01)
# ══════════════════════════════════════════════════════════════
# WHAT THIS IS: a crude, in-memory, per-IP ceiling for a bare `mio serve` with nothing in
# front of it -- Colab, a laptop demo, a naive self-host. Its whole job is to stop ONE address
# trivially hammering an unprotected instance.
#
# WHAT THIS IS NOT, and must never be sold as: production rate limiting. There is no
# cross-process coordination and no persistence, so N instances behind a load balancer each
# count separately. Scale is horizontal (more instances) plus rate limiting at the EDGE
# (reverse proxy / CDN); that is the scale story, not a bigger number here. This is the
# per-instance abuser floor underneath it.
#
# 2000/sec/IP by default, and the number is deliberately middle-high: per-IP counting sees a
# NAT'd classroom, office or coffee shop as ONE address, so the default has to leave a shared
# IP ample headroom while still tripping on a real single-address flood.
_RATE_LIMIT_DEFAULT = 2000
_RATE_LIMIT_ENV = "MOHIO_MAX_REQUESTS_PER_SECOND"
# How many addresses to track at once. The counter itself must not become the attack: an
# attacker spraying millions of source addresses would otherwise grow this map without bound.
# Each entry is three small numbers, so the cap is about memory, not accuracy.
_RATE_LIMIT_MAX_IPS = int(os.environ.get("MOHIO_RATE_LIMIT_MAX_IPS", 20000))


class _RateLimiter:
    """Sliding-window counter, O(1) memory per address.

    A true sliding window would keep every request's timestamp -- at 2000/sec that is 2000
    floats per address, which turns the counter into the memory footgun it exists to avoid.
    This keeps the CURRENT one-second window's count and the PREVIOUS one's, and weights the
    previous by how much of it is still in view. Standard technique, bounded, and accurate
    enough for a backstop whose job is "one address is hammering us", not billing.
    """

    def __init__(self, limit_per_second, max_ips=_RATE_LIMIT_MAX_IPS):
        self.limit = int(limit_per_second)
        self.max_ips = int(max_ips)
        self._buckets = collections.OrderedDict()   # ip -> [window, current, previous]
        self._lock = threading.Lock()

    def allow(self, ip, now=None):
        """True if this request is under the ceiling. A limit of 0 or less disables it."""
        if self.limit <= 0:
            return True
        now = _pooltime.time() if now is None else now
        window = int(now)
        with self._lock:
            entry = self._buckets.get(ip)
            if entry is None:
                entry = [window, 0, 0]
                self._buckets[ip] = entry
            elif entry[0] == window - 1:
                entry[0], entry[1], entry[2] = window, 0, entry[1]
            elif entry[0] != window:
                entry[0], entry[1], entry[2] = window, 0, 0
            # Weight the previous window by the part of it still inside the last second.
            overlap = 1.0 - (now - window)
            estimated = entry[1] + entry[2] * overlap
            if estimated >= self.limit:
                self._buckets.move_to_end(ip)
                return False
            entry[1] += 1
            self._buckets.move_to_end(ip)
            self._evict(window)
            return True

    def _evict(self, window):
        """Bound the map. Stale entries go first; past the cap, least-recently-seen go too."""
        while len(self._buckets) > self.max_ips:
            oldest_ip, oldest = next(iter(self._buckets.items()))
            # An entry two windows old cannot influence any future decision, so dropping it
            # loses nothing. Past the cap we drop the least-recently-seen regardless, which is
            # the right trade: a spraying attacker's one-off addresses are exactly the entries
            # that never come back, so they are the ones evicted.
            del self._buckets[oldest_ip]
            if oldest[0] >= window - 1:
                break


def _resolve_rate_limit(program):
    """The per-IP ceiling for this app: a journey `limits` block, else env, else the default.

    Same resolution shape as the framework and the session store -- an explicit declaration
    outranks an env default, which outranks the built-in. Resolved ONCE at startup from the
    assembled program, not per request.
    """
    declared = 0
    for node in _walk_nodes(program):
        if type(node).__name__ == 'LimitsBlock':
            n = int(getattr(node, 'max_requests_per_second', 0) or 0)
            if n > 0:
                declared = n
                break
    if declared > 0:
        return declared
    env = os.environ.get(_RATE_LIMIT_ENV, "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            raise RuntimeError(
                f"{_RATE_LIMIT_ENV}={env!r} is not a whole number. It sets the per-IP requests "
                f"per second ceiling; use a number, or unset it for the "
                f"{_RATE_LIMIT_DEFAULT} default.")
    return _RATE_LIMIT_DEFAULT


def _walk_nodes(node, _seen=None):
    """Every AST node reachable from a program, so a `limits` block is found whether it sits
    at the top level or inside a journey."""
    if _seen is None:
        _seen = set()
    if id(node) in _seen:
        return
    _seen.add(id(node))
    yield node
    for attr in ('statements', 'body', 'journey_body'):
        for child in (getattr(node, attr, None) or []):
            if hasattr(child, '__dict__'):
                for sub in _walk_nodes(child, _seen):
                    yield sub


class _RateLimitMiddleware:
    """Runtime-internal. The coder's surface is the `limits` block and nothing else.

    Deliberately NOT an Express-style middleware the developer writes and wires: they declare
    the ceiling they want and the runtime hides how it is enforced. It runs before routing so
    an over-limit request is rejected before any parsing, session lookup or program execution.
    """

    def __init__(self, app, limiter=None, trusted_proxy=False):
        self.app = app
        self.limiter = limiter
        self.trusted_proxy = trusted_proxy

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or self.limiter is None:
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request
        request = Request(scope)
        if not self.limiter.allow(_client_ip(request, self.trusted_proxy)):
            await _rate_limited_response(self.limiter.limit)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _rate_limited_response(limit):
    """The same shape as the oversize-payload refusal: an error name a caller can branch on,
    and a sentence that says what happened without a stack trace."""
    from starlette.responses import JSONResponse
    return JSONResponse(
        {"error": "rate_limit_exceeded",
         "message": (f"Too many requests from this address -- this app accepts {limit} per "
                     f"second. Try again shortly.")},
        status_code=429, headers={"Retry-After": "1"})


def _client_ip(request, trusted_proxy):
    """The address to count against.

    SOCKET PEER BY DEFAULT. `X-Forwarded-For` is set by whoever is in front, and when nothing
    is in front that is the CLIENT -- so honouring it unconditionally lets an attacker send a
    different value on every request and never hit the limit at all. Ignoring it when a proxy
    IS in front is the opposite failure: every request arrives from the proxy's single address,
    one bucket for the whole site, and a busy afternoon throttles everyone.
    Neither default is safe in both deployments, so the deployment says which it is:
    MOHIO_TRUSTED_PROXY=1 when a proxy really does sit in front.
    """
    if trusted_proxy:
        fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if fwd:
            return fwd
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


class _TooLarge(Exception):
    """Raised when a request body or upload exceeds its configured cap."""
    def __init__(self, what, limit):
        self.what, self.limit = what, limit
        super().__init__(f"{what} exceeds the {limit} byte limit")


def _oversize_response(exc):
    from starlette.responses import JSONResponse
    mb = exc.limit / (1024 * 1024)
    return JSONResponse(
        {"error": "payload_too_large",
         "message": f"{exc.what} is larger than the {mb:.0f}MB limit for this app."},
        status_code=413)


def _check_content_length(request, limit, what):
    """Reject on the declared Content-Length before reading anything into memory."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared and declared > limit:
        raise _TooLarge(what, limit)


class _TimedOut(Exception):
    """A request exceeded _REQUEST_TIMEOUT."""


async def _with_timeout(fn, *args, **kwargs):
    """Run a synchronous handler in a worker thread under a wall-clock timeout.

    A runaway loop in a .mho handler otherwise occupies the worker forever, and enough of them
    stop the app responding while still reporting healthy. The thread is not killable, so this
    bounds the REQUEST, not the work -- the runaway is logged and abandoned, and the caller gets
    a legible 504 instead of hanging.
    """
    import asyncio
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs),
                                      timeout=_REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        raise _TimedOut()

def _cors_origins():
    """Allowed CORS origins. Same-origin by default.

    A wildcard on a multi-tenant runtime let any site on the internet script requests against any
    hosted app, and is self-defeating with cookie sessions anyway (browsers refuse credentialed
    requests to a wildcard origin). A deployment opts in explicitly.
    """
    raw = os.environ.get("MOHIO_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return ["*"] if raw == "*" else [o.strip() for o in raw.split(",") if o.strip()]


def _etag_for(path):
    """A weak ETag from size + mtime -- cheap, and changes whenever a deploy rewrites the file."""
    try:
        st = path.stat()
        return f'W/"{st.st_size:x}-{int(st.st_mtime):x}"'
    except Exception:
        return None


def create_app(server: MohioServer):
    # Root static serving at THIS app's directory (see _static_roots).
    if getattr(server, 'app_dir', None) is not None:
        _APP_DIR[0] = server.app_dir
    try:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, HTMLResponse, Response
        from starlette.routing import Route
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware
    except ImportError:
        print("\n  Starlette not installed. Run: pip install fastapi uvicorn\n")
        sys.exit(1)

    class SafeJSONResponse(JSONResponse):
        """JSONResponse that never crashes on a stray non-serializable value.

        Strings (incl. newlines/quotes/unicode) are escaped by json.dumps as
        usual; anything json can't handle natively (datetime, MohioValue, bytes)
        is coerced via str() instead of raising. This closes the whole class of
        'a handler returned something odd -> 500 -> Connection error' failures.
        """
        def render(self, content) -> bytes:
            return _safe_json_bytes(content)

    # ── Shared POST dispatcher ────────────────────────────────

    async def _dispatch_post(request: Request) -> Response:
        ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        is_form = False
        try:
            if ctype == "application/x-www-form-urlencoded":
                # A real browser <form> submit. Parse with the standard library so
                # we need no extra dependency. A repeated field name keeps its list
                # (multi-checkbox); a single value collapses to a string.
                from urllib.parse import parse_qs
                _check_content_length(request, _MAX_BODY_BYTES, "Request body")
                raw = await request.body()
                if len(raw) > _MAX_BODY_BYTES:
                    raise _TooLarge("Request body", _MAX_BODY_BYTES)
                parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                payload = {k: (v[-1] if len(v) == 1 else v) for k, v in parsed.items()}
                is_form = True
            elif ctype == "multipart/form-data":
                # File uploads. Starlette parses multipart via python-multipart.
                # Text fields go into payload as usual; each uploaded file becomes
                # a descriptor dict the interpreter validates and stores.
                import os as _os
                _check_content_length(request, _MAX_UPLOAD_BYTES, "Upload")
                form = await request.form()
                payload = {}
                _upload_total = 0
                for key in form:
                    vals = form.getlist(key)
                    descs = []
                    for v in vals:
                        if hasattr(v, "filename") and getattr(v, "filename", None) is not None:
                            content = await v.read()
                            _upload_total += len(content)
                            if _upload_total > _MAX_UPLOAD_BYTES:
                                raise _TooLarge("Upload", _MAX_UPLOAD_BYTES)
                            ext = _os.path.splitext(v.filename)[1].lstrip(".").lower()
                            descs.append({"_mohio_file": True, "filename": v.filename,
                                          "ext": ext, "size": len(content),
                                          "content": content})
                        else:
                            descs.append(v)
                    payload[key] = descs[0] if len(descs) == 1 else descs
                is_form = True
            else:
                _check_content_length(request, _MAX_BODY_BYTES, "Request body")
                raw = await request.body()
                if len(raw) > _MAX_BODY_BYTES:
                    raise _TooLarge("Request body", _MAX_BODY_BYTES)
                payload = json.loads(raw) if raw else {}
        except _TooLarge as _tl:
            return _oversize_response(_tl)
        except Exception:
            return JSONResponse(
                {"error": "Request body must be valid JSON or form data"},
                status_code=400,
                headers=CORS_HEADERS,
            )

        path = request.url.path or "/"

        cookie_header = request.headers.get("Cookie", "")
        request_cookies = {}
        if cookie_header:
            for part in cookie_header.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    request_cookies[k.strip()] = v.strip()

        # The cookie is read AFTER this on purpose -- it used to be read after session_id was
        # already decided, so POST never saw it. The GET path did. Result: the SAME caller got a
        # different session.id on every POST, which is exactly what Zork uses.
        # DECISION (session.id / unique.id): session.id is an IDENTITY. Same caller, same value.
        session_id = (
            _session_header(request) or
            payload.get("_session") or
            request_cookies.get(_SESSION_COOKIE)
        )

        req = {
            "_method":             payload.get("_method") or request.method,
            "_path":               path,
            # Auth rebuild Item 1 removed every consumer of a client-supplied `_roles`; roles are
            # server-derived via `grant role`. The field is no longer forwarded so a future change
            # cannot accidentally start reading this dead, client-controlled input again.
            "__request_cookies__": request_cookies,
            **{k: v for k, v in payload.items() if not k.startswith("_")}
        }
        if "_shape" in payload:
            req["_shape"] = payload["_shape"]
        # Guard fields are underscore-prefixed, so the spread above drops them.
        # Carry them through explicitly so the interpreter can verify them.
        for gk in ("_csrf", "_trap"):
            if gk in payload:
                req[gk] = payload[gk]
        if is_form:
            req["_form_post"] = True
        if session_id:
            req["_session"] = session_id

        for k, v in request.query_params.items():
            if k not in req:
                req[k] = v

        if server.verbose:
            print(f"  [POST] {path}  shape={req.get('_shape')}  "
                  f"session={session_id or 'none'}")

        result = server.dispatch(req, session_id=session_id)

        status = result.get("status", 200)
        body   = result.get("body")

        # Only a genuine 204 should have an empty body (e.g. preflight). A
        # None/empty give-back must still return parseable JSON — otherwise a
        # client that calls response.json() throws on the empty body and the
        # browser reports it as "Connection error — is the server running?".
        if status == 204:
            return Response(status_code=204, headers=CORS_HEADERS)

        response_headers = dict(CORS_HEADERS)
        pending_cookies = result.get("__pending_cookies__") or {}
        if not isinstance(pending_cookies, dict):
            pending_cookies = {}
        set_cookie_parts = []
        for cookie_name, cookie_opts in pending_cookies.items():
            parts = [f"{cookie_name}={cookie_opts.get('value', '')}"]
            if cookie_opts.get('expires') is not None:
                expires_s = int(cookie_opts['expires'])
                if expires_s < 0:
                    parts.append("Max-Age=0")
                else:
                    parts.append(f"Max-Age={expires_s}")
            parts.append(f"Path={cookie_opts.get('path', '/')}")
            if cookie_opts.get('domain'):
                parts.append(f"Domain={cookie_opts['domain']}")
            if cookie_opts.get('secure', _secure_default(request)):
                parts.append("Secure")
            if cookie_opts.get('http_only', True):
                parts.append("HttpOnly")
            same_site = cookie_opts.get('same_site', 'Lax')
            parts.append(f"SameSite={same_site}")
            set_cookie_parts.append("; ".join(parts))

        # Every cookie must go out. A plain dict cannot hold duplicate Set-Cookie headers, so
        # the first one used to win and the rest were silently dropped -- an app setting a
        # session cookie plus a preference cookie lost one of them with no error.
        if set_cookie_parts:
            response_headers["Set-Cookie"] = set_cookie_parts[0]

        # A redirect (e.g. `jump to "/path"`): set Location and return a bodyless
        # redirect response so the browser actually navigates.
        redirect_to = result.get("redirect_to")
        if redirect_to:
            response_headers["Location"] = str(redirect_to)
            return _with_all_cookies(Response(status_code=status, headers=response_headers), set_cookie_parts)

        # `give ... as download`: the browser must save this, not display it. Serving a
        # file inline runs it inside this site's own origin, which is how an uploaded
        # page or PDF gets to read the viewer's session. The filename is quoted and
        # stripped of anything that could break out of the header.
        _download = result.get("download")
        if _download:
            _safe = str(_download).replace("\\", "/").rsplit("/", 1)[-1]
            _safe = "".join(ch for ch in _safe if ch.isprintable() and ch not in '"\r\n')
            _safe = _safe or "download"
            response_headers["Content-Disposition"] = f'attachment; filename="{_safe}"'
            _body = body
            if _body is None:
                _body = b""
            elif isinstance(_body, str):
                _body = _body.encode("utf-8")
            elif not isinstance(_body, (bytes, bytearray)):
                _body = str(_body).encode("utf-8")
            return _with_all_cookies(
                Response(_body, status_code=status,
                         media_type=result.get("content_type") or "application/octet-stream",
                         headers=response_headers), set_cookie_parts)

        # An HTML body (a rendered page or a form re-render after a failed submit)
        # must reach the browser as markup, not wrapped in a JSON envelope.
        if result.get("content_type") == "text/html":
            html_body = "" if body is None else (body if isinstance(body, str) else str(body))
            return _with_all_cookies(HTMLResponse(html_body, status_code=status, headers=response_headers), set_cookie_parts)

        # An explicit non-HTML/JSON content-type (application/xml, text/plain, ...) from a
        # `give back ... as xml|text` serves the body raw under that type, never JSON-wrapped.
        _ct = result.get("content_type")
        if _ct and _ct not in ("text/html", "application/json"):
            if "xml" in _ct:
                raw_body = _xml_body(body)
            else:
                raw_body = "" if body is None else (body if isinstance(body, str) else str(body))
            return _with_all_cookies(
                Response(raw_body, status_code=status, media_type=_ct,
                         headers=response_headers), set_cookie_parts)

        if isinstance(body, dict):
            return _with_all_cookies(
                SafeJSONResponse(body, status_code=status,
                                 headers=response_headers), set_cookie_parts)

        return _with_all_cookies(
            SafeJSONResponse(
                _response_payload(body),
                status_code=status,
                headers=response_headers,
            ), set_cookie_parts)

    # ── Route handlers ────────────────────────────────────────

    async def serve_frontend(request: Request) -> Response:
        # Try the program's own root route first. If it defines a
        # `request for ... at /` (or a root page), serve that. Only when there is
        # no root route do we fall back to the bundled frontend, so single-purpose
        # deploys like the Zork demo are untouched.
        cookie_header = request.headers.get("Cookie", "")
        request_cookies = {}
        if cookie_header:
            for part in cookie_header.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    request_cookies[k.strip()] = v.strip()
        session_id = (_session_header(request)
                      or request_cookies.get(_SESSION_COOKIE))
        req = {"_method": "GET", "_path": "/", "_roles": [],
               "__request_cookies__": request_cookies}
        for k, v in request.query_params.items():
            if k not in req:
                req[k] = v
        try:
            result = await _with_timeout(server.dispatch, req, session_id=session_id)
        except _TimedOut:
            return JSONResponse(
                {"error": "request_timeout",
                 "message": f"This request took longer than {_REQUEST_TIMEOUT:.0f}s and was "
                            f"stopped. Long-running work belongs in a background job."},
                status_code=504)
        except Exception as e:
            # FIX-B9-6 (T1-SILENT-SWEEP-BATCH9, finding #17): `MohioServer.dispatch` already
            # catches every exception from running the program itself and returns a real
            # {status, body} error dict via format_runtime_error/log_runtime_error (see
            # dispatch() above) -- it never raises. So reaching this except block means
            # something failed OUTSIDE that -- most likely _with_timeout's own wrapping
            # machinery -- which is rarer and MORE surprising than an ordinary program crash,
            # not less. The old code set `result = None` and fell through to the SAME
            # "no root route" placeholder logic used for a genuinely undefined `/`, at HTTP
            # 200 -- indistinguishable from a healthy app with no root route, which is exactly
            # what made a real crash here look like "GET works" from the outside (the
            # comment two lines up already said "Surface it" -- it only ever reached server
            # logs, never the client). A crash is not the same outcome as no route; it must
            # not share a branch. Return a real error response instead of falling through.
            if server.verbose:
                import traceback as _tb; _tb.print_exc()
            from mohio_interpreter import format_runtime_error, log_runtime_error
            info = format_runtime_error(e)
            log_runtime_error(info, verbose=server.verbose)
            print(f"  [serve /] root route raised: {e}")
            return JSONResponse(info, status_code=info["status"])
        if isinstance(result, dict):
            status = result.get("status", 200)
            body = result.get("body")
            # Fall through to index.html / the neutral placeholder ONLY for a genuine
            # "no root route" (the interpreter marks it `_no_route`) or a bodyless 204.
            # An EXPLICIT give-back -- give back 404, give back 500, give back ok,
            # give back 200 -- is served with the status the program asked for, even
            # when the body is empty. Previously the `status not in (204,404) and body`
            # test swallowed every empty-body and 404 give-back into the placeholder at
            # 200, silently dropping the status.
            if not result.get("_no_route") and status != 204:
                return HTMLResponse("" if body is None else str(body),
                                    status_code=status)
        # T1-FRAMEWORK-FOUNDATION Phase 2 -- serve by default at the ROOT too.
        # `/` has its own handler (declared home page, then index.html, then the neutral
        # placeholder), so the seam further down never saw it. A render-only index.mho
        # therefore still answered with the "no home page yet" placeholder even after
        # /about started working -- the same gap, one route later. Convention serving is
        # tried FIRST here: an index.mho that renders something IS the home page, and it
        # must win over the placeholder that exists precisely because there was none.
        # A convention answer is used whenever the page actually produced one. The status is
        # NOT filtered to 200: a file whose whole content is `give back 404 "gone"` is answering,
        # and answering with 404 is the answer -- treating that as "no page" would replace a
        # deliberate response with a placeholder. `give back 500` is that same deliberate answer,
        # so the two kinds of 500 must be told apart by WHO produced them, never by the number:
        # `mohio_answered` marks a give-back the program made on purpose, while an unmarked 500
        # is the convention path reporting its own failure and still falls through.
        _conv_root = _convention_body(server, "/")
        if _conv_root is not None and (getattr(_conv_root, "mohio_answered", False)
                                       or _conv_root.status_code != 500):
            return _conv_root

        # No root route. Try the app's declared home page, then the web conventions. A Mohio home
        # page (index.mho) is handled by the dispatch above, not here -- .mho files are executed,
        # not served as static files.
        for _name in ((_INDEX_FILE,) if _INDEX_FILE else _INDEX_CANDIDATES):
            idx = _static_file_response(_name)
            if idx is not None:
                return idx
        # Nothing to show: the general runtime makes NO assumption (no bundled demo front end).
        # The app is expected to provide a root route, an index.mho, or an index.html.
        return HTMLResponse(_NEUTRAL_INDEX, status_code=200)

    async def handle_post_root(request: Request) -> Response:
        return await _dispatch_post(request)

    async def health(request: Request) -> Response:
        return JSONResponse(server.stats())

    async def ping(request: Request) -> Response:
        # `build` is the COMMIT, and it is the field a deploy question actually needs: VERSION
        # moves per release, so it cannot tell a production container apart from a checkout six
        # commits ahead that reports the same number (2026-08-20 -- that ambiguity cost a live
        # investigation a clean answer). "unknown" means this build did not record its commit,
        # which is itself worth seeing, and is never a fabricated value.
        return JSONResponse({"pong": True, "version": VERSION, "build": BUILD_SHA})

    def _static_file_response(path: str, _if_none_match: str = ""):
        """Return a Response for a real static file under the APP's static root, or None.

        Three things this must not do, all of which it used to:
          - serve from the compiler's own directory (handed out mohio_server.py, mohio.lark, ...)
          - follow a symlink out of the static root (a tenant supplies the repo, so a link to
            /etc/passwd or /proc/self/environ was attacker-controlled input)
          - serve source or config (`.mho`, `.py`, `.env`, dotfiles, databases) as a download
        """
        if not path or ".." in path or path.startswith("/"):
            return None
        # C4 -- never serve code, config, data files, or dotfiles, whatever the extension map says.
        _low = path.lower()
        _name = _low.rsplit("/", 1)[-1]
        if _name.startswith(".") or any(part.startswith(".") for part in _low.split("/")):
            return None
        # A leading underscore means "private, never routed". It applies to FILES here,
        # not to folders: `_next/` is where every Next.js asset lives, and denying a
        # whole directory tree by name would break that silently for anyone serving a
        # built front end through Mohio. The denylist above already refuses source,
        # config and data wherever they sit, so the folder rule bought little.
        # Revisit after the conversion work has had time to settle.
        _parts = [p for p in _low.split("/") if p]
        if _parts and _parts[-1].startswith("_"):
            return None
        if _is_denied_static_name(_parts[-1] if _parts else _low):
            return None
        for static_dir in _static_roots():
            root = Path(static_dir).resolve()
            candidate = Path(static_dir) / path
            try:
                candidate = candidate.resolve()
                # C3 -- containment: resolve() follows symlinks, so the result must still be
                # inside the root it was resolved from.
                try:
                    inside = candidate.is_relative_to(root)
                except AttributeError:                      # Python < 3.9
                    inside = os.path.commonpath([str(candidate), str(root)]) == str(root)
                if not inside:
                    continue
                if candidate.exists() and candidate.is_file():
                    suffix = candidate.suffix.lower()
                    mime_types = {
                        ".ico":  "image/x-icon",
                        ".png":  "image/png",
                        ".jpg":  "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".gif":  "image/gif",
                        ".svg":  "image/svg+xml",
                        ".webp": "image/webp",
                        ".avif": "image/avif",
                        ".css":  "text/css",
                        ".js":   "application/javascript",
                        ".mjs":  "application/javascript",
                        ".map":  "application/json",
                        ".json": "application/json",
                        ".xml":  "application/xml",
                        ".txt":  "text/plain",
                        ".md":   "text/markdown",
                        ".csv":  "text/csv",
                        ".html": "text/html",
                        # Fonts: a wrong MIME here can make a browser refuse the font outright,
                        # so these matter more than most.
                        ".woff":  "font/woff",
                        ".woff2": "font/woff2",
                        ".ttf":   "font/ttf",
                        ".otf":   "font/otf",
                        ".eot":   "application/vnd.ms-fontobject",
                        # Media
                        ".mp4":  "video/mp4",
                        ".webm": "video/webm",
                        ".ogg":  "audio/ogg",
                        ".mp3":  "audio/mpeg",
                        ".wav":  "audio/wav",
                        # Documents / binaries
                        ".pdf":  "application/pdf",
                        ".zip":  "application/zip",
                        # WebAssembly must be served as application/wasm or the browser will
                        # refuse to instantiate it (matters for in-browser Mohio via Pyodide).
                        ".wasm": "application/wasm",
                    }
                    content_type = mime_types.get(suffix,
                                                   "application/octet-stream")
                    data = candidate.read_bytes()
                    # A 24h immutable cache meant a tenant could fix their CSS, redeploy in
                    # seconds, and returning visitors kept the old file for a day. Revalidate
                    # cheaply instead: a repeat request becomes a 304, and a redeploy changes
                    # the tag so the new file is picked up at once.
                    headers = {**CORS_HEADERS,
                               "Cache-Control": "public, max-age=60, must-revalidate"}
                    etag = _etag_for(candidate)
                    if etag:
                        headers["ETag"] = etag
                        if _if_none_match and etag in _if_none_match:
                            return Response(status_code=304, headers=headers)
                    return Response(content=data, media_type=content_type, headers=headers)
            except Exception:
                continue
        return None

    async def serve_page_or_static(request: Request) -> Response:
        """GET catch-all. A real static file wins (predictable: explicit assets
        shadow routes, and assets cost no interpreter run). Otherwise the path is
        forwarded to the interpreter as a GET so journey pages and `request for`
        endpoints render. The interpreter's clean 404 (no route) or 204 (no content)
        falls through to a final static 404 -- never a silent blank page."""
        raw_path = request.path_params.get("path", "")

        # 1. Real static file first.
        static_resp = _static_file_response(
            raw_path, request.headers.get("If-None-Match", ""))
        if static_resp is not None:
            return static_resp

        # 2. Forward to the interpreter as a GET request.
        full_path = request.url.path or "/"
        cookie_header = request.headers.get("Cookie", "")
        request_cookies = {}
        if cookie_header:
            for part in cookie_header.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    request_cookies[k.strip()] = v.strip()
        session_id = (_session_header(request)
                      or request_cookies.get(_SESSION_COOKIE))

        req = {
            "_method":             "GET",
            "_path":               full_path,
            # Client `_roles` is no longer consumed anywhere (Auth rebuild Item 1); not forwarded.
            "__request_cookies__": request_cookies,
        }
        for k, v in request.query_params.items():
            if k not in req:
                req[k] = v

        if server.verbose:
            print(f"  [GET] {full_path}  session={session_id or 'none'}")

        result = server.dispatch(req, session_id=session_id)
        status = result.get("status") if isinstance(result, dict) else 200
        body   = result.get("body") if isinstance(result, dict) else result

        # A redirect from a GET handler (`jump to "/path"`): set Location, return bodyless.
        if isinstance(result, dict) and result.get("redirect_to"):
            return Response(status_code=status,
                            headers={**CORS_HEADERS, "Location": str(result["redirect_to"])})

        # Fall through to a static 404 only for a genuine no-route 404 (marked by the
        # interpreter) or an empty 204/None. An intentional `give back 404 "..." [as xml]`
        # from a handler carries a body/content-type and is served normally below.
        _no_route = isinstance(result, dict) and result.get('_no_route')
        if status in (204, None) or (status == 404 and (_no_route or not body)):
            # T1-FRAMEWORK-FOUNDATION Phase 2 -- SERVE BY DEFAULT.
            #
            # A file that only renders has no route to match, so dispatch marks `_no_route`
            # and this used to be a bare 404 -- which is why `page` was secretly doing the
            # framework's routing job. The CLI already mapped this file to this URL; the
            # program simply never registered a route, because in a convention-served
            # framework it should not have to. Run it and serve what it produces.
            #
            # Only for a framework that serves BY CONVENTION (`web`, `game`). `api` keeps the
            # 404 on purpose: answering endpoint calls with no page routing is the entire
            # point of that value, so inventing a page for it would be wrong. A declared but
            # not-yet-built framework fails LOUD rather than quietly serving a web page.
            _conv = _convention_body(server, full_path)
            if _conv is not None:
                return _conv
            _declared = _declared_status_response(server, 404)
            if _declared is not None:
                return _declared
            return Response(status_code=404, headers=CORS_HEADERS)

        response_headers = dict(CORS_HEADERS)
        # Honor any cookies the page set (e.g. a session cookie).
        get_cookie_parts = []
        pending_cookies = result.get("__pending_cookies__") if isinstance(result, dict) else None
        if isinstance(pending_cookies, dict):
            for cookie_name, cookie_opts in pending_cookies.items():
                parts = [f"{cookie_name}={cookie_opts.get('value', '')}"]
                if cookie_opts.get('expires') is not None:
                    expires_s = int(cookie_opts['expires'])
                    parts.append("Max-Age=0" if expires_s < 0 else f"Max-Age={expires_s}")
                parts.append(f"Path={cookie_opts.get('path', '/')}")
                if cookie_opts.get('domain'):
                    parts.append(f"Domain={cookie_opts['domain']}")
                if cookie_opts.get('secure', _secure_default(request)):
                    parts.append("Secure")
                if cookie_opts.get('http_only', True):
                    parts.append("HttpOnly")
                parts.append(f"SameSite={cookie_opts.get('same_site', 'Lax')}")
                get_cookie_parts.append("; ".join(parts))
        # EVERY cookie, not the first one. This loop used to assign the header and then `break`,
        # so a page setting two cookies delivered one and dropped the other with no error --
        # measured over real HTTP: `miocookie.set "alpha"` + `miocookie.set "beta"` on a GET
        # handler produced exactly one Set-Cookie header, `alpha`. The POST dispatcher already
        # did this correctly with `_with_all_cookies`, which is the helper that exists precisely
        # because a plain dict cannot hold two headers of the same name; this path never called
        # it. Same bug, same building, one path fixed and one not.
        if get_cookie_parts:
            response_headers["Set-Cookie"] = get_cookie_parts[0]

        content_type = result.get("content_type") if isinstance(result, dict) else None
        # `give ... as download`: the browser saves the file instead of displaying it.
        # This is the GET path; _dispatch_post carries the same branch. Both are needed,
        # since a download can be the answer to either.
        _download = result.get("download") if isinstance(result, dict) else None
        if _download:
            _safe = str(_download).replace("\\", "/").rsplit("/", 1)[-1]
            _safe = "".join(ch for ch in _safe if ch.isprintable() and ch not in '"\r\n')
            _safe = _safe or "download"
            response_headers["Content-Disposition"] = f'attachment; filename="{_safe}"'
            _b = body
            if _b is None:
                _b = b""
            elif isinstance(_b, str):
                _b = _b.encode("utf-8")
            elif not isinstance(_b, (bytes, bytearray)):
                _b = str(_b).encode("utf-8")
            return _with_all_cookies(Response(_b, status_code=status,
                            media_type=content_type or "application/octet-stream",
                            headers=response_headers), get_cookie_parts)

        # An explicit non-HTML content-type (application/xml, text/plain, ...) from a
        # `give back ... as xml|text` serves the body raw under that type, ahead of the
        # markup heuristic below (so an XML sitemap is not sniffed as HTML).
        if content_type and content_type not in ("text/html", "application/json"):
            if "xml" in content_type:
                raw = _xml_body(body)
            else:
                raw = "" if body is None else (body if isinstance(body, str) else str(body))
            return _with_all_cookies(Response(raw, status_code=status, media_type=content_type,
                            headers=response_headers), get_cookie_parts)
        if content_type == "text/html" or (isinstance(body, str) and "<" in body[:64]):
            return _with_all_cookies(HTMLResponse(body if isinstance(body, str) else str(body),
                                status_code=status, headers=response_headers), get_cookie_parts)
        if isinstance(body, dict):
            return _with_all_cookies(SafeJSONResponse(body, status_code=status, headers=response_headers), get_cookie_parts)
        return _with_all_cookies(SafeJSONResponse(_response_payload(body),
                                status_code=status, headers=response_headers), get_cookie_parts)

    async def options_handler(request: Request) -> Response:
        return Response(status_code=200, headers=CORS_HEADERS)

    # ── Route table ───────────────────────────────────────────

    routes = [
        Route("/",                          serve_frontend,   methods=["GET", "HEAD"]),
        Route("/",                          handle_post_root, methods=["POST"]),
        Route("/ping",                      ping,             methods=["GET", "HEAD"]),
        Route("/health",                    health,           methods=["GET", "HEAD"]),
        Route("/mio/health",                health,           methods=["GET", "HEAD"]),
        # NOTE: no /mio/sessions routes. Listing live session IDs to an unauthenticated caller
        # handed out session identity to anyone on the internet, and a DELETE let anyone log out
        # any user. Session inspection is a control-plane / debug concern, not a public route on
        # a runtime that serves other people's apps.
        # NOTE: no schema-coupled admin routes on the general runtime. Per-tenant DB management
        # (seed / stats / reset) is a CONTROL-PLANE concern -- it connects to the tenant's database
        # directly with credentials the platform already holds, gated behind platform auth. The
        # served app carries no database-admin surface, which keeps the tenant machine's secret list
        # minimal and removes admin attack surface from every microVM. Seeding is likewise an
        # app/dev concern, never a runtime one (there is no generic seeder -- uniqueness is a
        # per-app claim that cannot be inferred). Zork keeps its own seed/admin on its own side.
        # Static files. Favicon/touch-icon paths used to get their own dedicated Route()
        # entries pointed at serve_static -- confirmed live bug (2026-08-15): serve_static
        # reads the filename from request.path_params["path"], a param only the {path:path}
        # catch-all route below ever populates. The four dedicated routes matched a fixed
        # literal path with no {path:path} placeholder, so path_params["path"] was always ""
        # for them, _static_file_response("") always returns None, and they intercepted
        # ahead of the catch-all -- every tenant app's favicon 404'd unconditionally, real
        # file or not. Fixed: removed, so these filenames fall through to the catch-all
        # (serve_page_or_static) immediately below, which already resolves an arbitrary
        # static filename correctly. serve_static itself is now unreferenced (it existed
        # only to back these four routes) -- removed alongside them rather than left as dead,
        # broken code.
        Route("/{path:path}",               serve_page_or_static, methods=["GET", "HEAD"]),
        Route("/{path:path}",               _dispatch_post,   methods=["POST", "PUT", "DELETE"]),
        Route("/{path:path}",               options_handler,  methods=["OPTIONS"]),
    ]

    # The per-IP backstop resolves ONCE here, from the assembled program: a journey `limits`
    # block outranks MOHIO_MAX_REQUESTS_PER_SECOND, which outranks the 2000 default -- the same
    # explicit-beats-env-beats-default order the framework and session store already use.
    _limiter = _RateLimiter(_resolve_rate_limit(server.program))
    _trusted_proxy = os.environ.get("MOHIO_TRUSTED_PROXY", "").strip().lower() in (
        "1", "true", "yes")

    middleware = [
        # First in the list, so it runs OUTERMOST: an address over its ceiling is turned away
        # before CORS, routing, body parsing or any program code.
        Middleware(_RateLimitMiddleware, limiter=_limiter, trusted_proxy=_trusted_proxy),
        Middleware(CORSMiddleware,
                   allow_origins=_cors_origins(),
                   allow_methods=["*"],
                   allow_headers=["*"])
    ]

    app = Starlette(routes=routes, middleware=middleware)
    return app


# ══════════════════════════════════════════════════════════════
# DIRECTORY MODE  --  one .mho file per page, each at its own URL
# ══════════════════════════════════════════════════════════════

def create_multi_app(programs, interps, verbose=False, app_dir=None, redirects=None,
                     status_responses=None):
    """Serve a folder of .mho files (mio serve myapp/). Each file was mapped to a
    URL by the CLI (index.mho -> /, contact.mho -> /contact). Here we build a full
    single-file app per program (reusing create_app, so form parsing, guard verify,
    cookies, and the root behaviour are identical) and route each request to the
    app whose URL best matches the path. No request handling is duplicated.

    `app_dir` is the served directory. It roots static serving there, so assets
    that live beside the .mho files (style.css, images, JS) are found -- without it
    every per-file MohioServer had app_dir=None and static resolved against the
    process cwd, so `GET /style.css` returned empty in directory mode."""
    apps = {}
    for url, program in programs.items():
        _srv = MohioServer(program, interps[url], verbose=verbose,
                           app_dir=app_dir, route_path=url)
        # Every app carries the declared responses AND the means to render one: a `[404] /page`
        # answer is a real page, rendered per request the way any other page is, not a string
        # captured once at boot.
        _srv.status_responses = dict(status_responses or {})
        _srv.sibling_programs = programs
        _srv.sibling_interps = interps
        apps[url] = create_app(_srv)
    if not apps:
        raise ValueError("create_multi_app: no programs to serve")

    def _match(path):
        # Exact page wins; then the longest URL prefix (nested pages); then the
        # index app ('/') as the catch-all for assets, health, and unknown paths.
        if path in apps:
            return apps[path]
        best, best_len = None, -1
        for url, a in apps.items():
            if url == "/":
                continue
            base = url.rstrip("/")
            if path == base or path.startswith(base + "/"):
                if len(base) > best_len:
                    best, best_len = a, len(base)
        if best is not None:
            return best
        return apps.get("/") or next(iter(apps.values()))

    # T1-MAP-EXTRACTION: `map`'s redirects answer INSTEAD of any file, so they are checked
    # before route matching. A redirect that fell through to the route table would be shadowed
    # by whatever happened to be mounted there, which is the opposite of what it says.
    _redirects = dict(redirects or {})

    async def _router(scope, receive, send):
        if scope.get("type") != "http":
            # lifespan/websocket: hand to any app so startup/shutdown still runs
            await next(iter(apps.values()))(scope, receive, send)
            return
        _path = scope.get("path", "/")
        _key = _path if _path == "/" else _path.rstrip("/")
        _hit = _redirects.get(_key)
        if _hit is not None:
            _dst, _code = _hit
            await send({"type": "http.response.start", "status": _code,
                        "headers": [(b"location", str(_dst).encode()),
                                    (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await _match(_path)(scope, receive, send)

    return _router
