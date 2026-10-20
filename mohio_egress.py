# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""THE EGRESS GATE: every way a value leaves a Mohio program, and the one rule each applies
(RULED by Ron 2026-10-08, from the health app's HC-041, HC-043, HC-053 to HC-055 and HC-060).

Protected data kept leaving through whichever boundary had been patched last: a log file after the
log lines were fixed, a verbose metric after the logs, a shaped connector after the raw one. Each
was fixed path by path, and each fix left the next path open. So the rule lives HERE, once, and every
boundary asks it: `MohioInterpreter._egress(boundary, value, ...)` for a value, `egress_text` for a
line of text, `gated_print` for what a module prints. The boundary gate test
(tests/test_battery_egress_gate_boundaries.py) enumerates every outbound primitive in the compiler
and fails the build for one that does not go through here, so a new boundary cannot be added
without it.

The rules, per the rulings already made for display, AI and outside calls:

  display   a page, a JSON response, a give back: masked by the display rule ([phi] and [pci]
            hidden, [pii] shown in full, the standing ruling for showing someone their own data)
  log       every log level, log file, debug and journey log, diagnostic, metric and trace, in every
            verbosity: every tagged value masked, [pii] included, by classification and never by
            length
  export    a file written from data: every tagged value masked ([pii] included, the export ruling)
  refuse    an outside call (mioconnect raw and shaped, miohttp, AI, email), a file written by the
            program (a file has no tag), and a value placed where the browser keeps or forwards it
            (a cookie, a header, a redirect): a protected value is
            refused unless it is masked first or its record is sent `stripped`; an AI call to a
            loopback address is the one exception
"""
import builtins as _builtins
import sys as _sys

EGRESS_RULES = {
    'page': 'display', 'response': 'display', 'json': 'display',
    'log': 'log', 'log_file': 'log', 'diagnostic': 'log', 'metric': 'log', 'trace': 'log',
    'export': 'export',
    'cookie': 'refuse', 'header': 'refuse', 'redirect': 'refuse',
    'connector': 'refuse', 'http': 'refuse', 'ai': 'refuse', 'email': 'refuse',
    'file': 'refuse',
}


def rule_for(boundary):
    """The rule a boundary applies. An unknown boundary is a compiler bug and fails loud: a gate
    that let an unnamed boundary through would be the path-by-path patching again."""
    rule = EGRESS_RULES.get(boundary)
    if rule is None:
        raise RuntimeError(f"egress boundary {boundary!r} is not in EGRESS_RULES; every boundary "
                           f"names its rule there (mohio_egress.py)")
    return rule


def egress_text(boundary, text):
    """A line of text leaving through `boundary`, with every protected value this request has
    touched replaced by its mask. Text has lost its class by the time it is a line, so the line is
    checked against the values noted as they entered (mohio_interpreter._note_classified)."""
    rule_for(boundary)
    if not isinstance(text, str):
        return text
    mi = _sys.modules.get('mohio_interpreter')
    if mi is None:          # no program has run in this process, so nothing has been noted
        return text
    return mi._scrub_classified(text)


def gated_print(*args, **kwargs):
    """`print`, through the gate. Every compiler module that prints binds this as its `print`, so a
    diagnostic, a trace or a log line formatting a value cannot reach the screen or a captured log
    unmasked. Program output (`show`) is masked by the display rule before it gets here."""
    _builtins.print(*(egress_text('trace', a) if isinstance(a, str) else a for a in args), **kwargs)
