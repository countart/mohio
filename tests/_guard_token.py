# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A form guard token for a test client's own session. Not a test on its own.

A guard token is bound to the session it was issued to (Q266, ruled 2026-10-06), so a test that
posts a form needs a token for ITS client's session, the way a browser gets one from the page it
loaded. When the client has no session yet, one refused form post opens it: the guard refuses it
before the handler runs, and the response carries the session cookie.
"""
import os

SESSION_COOKIE = os.environ.get("MOHIO_SESSION_COOKIE", "mio_session")


def guard_token(client, interp, path):
    sid = client.cookies.get(SESSION_COOKIE)
    if not sid:
        client.post(path, data={"_csrf": "", "_trap": ""})
        sid = client.cookies.get(SESSION_COOKIE)
    if not sid:
        raise AssertionError("the test client has no session after a form post to %s, so no "
                             "guard token can be issued for it" % path)
    return interp._issue_csrf(sid)
