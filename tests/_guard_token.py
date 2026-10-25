# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A form guard token for a test client's own session. Not a test on its own.

A guard token is bound to the session it was issued to (Q266, ruled 2026-10-06), so a test that
posts a form needs a token for ITS client's session, the way a browser gets one from the page it
loaded. When the client has no session yet, it is given a fresh session id, which the server adopts
as a new session on the client's next request (an id it has never seen, and never invalidated).

This used to open the session with one deliberately refused form post and read the cookie off the
refusal. A refusal sends no session cookie now (RULED by Ron 2026-10-22: a session commits, and its
cookie is sent, only on a success), so that shortcut no longer works, and it never modelled a
browser, which gets its session from the successful page load that carried the form.
"""
import os
import uuid

SESSION_COOKIE = os.environ.get("MOHIO_SESSION_COOKIE", "mio_session")


def _cookie_domain(client):
    """The domain the test client stores a server's cookie under (httpx adds `.local` to a host with
    no dot: testserver -> testserver.local), so the id set here is the one the server's later
    Set-Cookie replaces, never a second cookie of the same name beside it."""
    host = client.base_url.host
    return host if "." in host else host + ".local"


def guard_token(client, interp, path):
    held = [c.value for c in client.cookies.jar if c.name == SESSION_COOKIE]
    sid = held[-1] if held else None
    if not sid:
        sid = uuid.uuid4().hex
        client.cookies.set(SESSION_COOKIE, sid, domain=_cookie_domain(client), path="/")
    return interp._issue_csrf(sid)
