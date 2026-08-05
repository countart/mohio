# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Endpoints that end in a `render` block serve the page as a proper HTTP response
({status, body:html-string, content_type:text/html}), not a raw MohioValue. API
endpoints that `give back` keep returning {status, body}. Guards the page half of
'real working apps'."""
import os
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'
def run(body_app, req):
    prog = transform(_P.parse(H + body_app), H + body_app)
    return MohioInterpreter().run(prog, req)
def test_render_endpoint_returns_html_response():
    app = ('shape Home\n    method GET\nshape: done\n'
           'listen for\n    request for sh.Home at /home\n'
           '        render\n            <h1>Hi</h1>\n        render: done\n'
           '    request: done\nlisten: done\n')
    resp = run(app, {'_method': 'GET', '_path': '/home'})
    assert isinstance(resp, dict), f"expected dict response, got {type(resp).__name__}"
    assert resp['status'] == 200, resp
    assert resp.get('content_type') == 'text/html', resp
    assert isinstance(resp['body'], str), "body must be a plain string, not a wrapper"
    assert resp['body'].lstrip().lower().startswith('<!doctype'), resp['body'][:40]
def test_give_back_api_endpoint_still_works():
    app = ('shape Ping\nshape: done\n'
           'listen for\n    new sh.Ping at /ping\n'
           '        give back 201 "pong"\n    new: done\nlisten: done\n')
    resp = run(app, {'_method': 'POST', '_path': '/ping', 'ping': {}})
    assert isinstance(resp, dict) and resp['status'] == 201 and resp['body'] == 'pong', resp
if __name__ == '__main__':
    test_render_endpoint_returns_html_response()
    test_give_back_api_endpoint_still_works()
    print("test_page_serving: 2/2 OK")
