# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""What a test reads when a served failure answers only a reference (RULED by Ron 2026-10-08).

A 5xx from the runtime tells the client one plain sentence and a reference; the detail goes to the
server log under that reference. A battery that asserts WHAT the failure said reads it here: the
client body, followed by the detail the server logged for its reference. In-process servers
(TestClient, `MohioServer.dispatch`) are read from `mohio_server.failure_detail`; a served subprocess
is read from the log text the test captured, passed as `log`.
"""
import json
import re


def reference_of(body):
    """The reference a failure body carries, or None."""
    if isinstance(body, dict):
        inner = body.get('body', body)
        if isinstance(inner, dict) and inner.get('reference'):
            return str(inner['reference'])
        if isinstance(inner, str):
            body = inner
        else:
            return None
    text = str(body or '')
    if text.strip().startswith('{'):
        try:
            got = json.loads(text)
            if isinstance(got, dict) and got.get('reference'):
                return str(got['reference'])
        except ValueError:
            pass
    m = re.search(r'Reference: (\w+)', text)
    return m.group(1) if m else None


def seen(body, log=None):
    """The client body plus the server's own detail for its reference, as one text to assert on."""
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    ref = reference_of(body)
    if not ref:
        return text
    detail = ''
    if log is not None:
        for line in str(log).splitlines():
            if ('reference=' + ref) in line or ('trace=' + ref) in line:
                detail += line + '\n'
    else:
        from mohio_server import failure_detail
        detail = failure_detail(ref)
    return text + '\n' + detail
