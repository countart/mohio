# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Behaviours that look like defects and are not, in a form a verification pass can read.

WHY THIS EXISTS. `[pii]` leaving in plaintext has been re-filed as a defect three times. It is
not one: it was decided, for a reason, and the reason is written down in a docstring next to the
code. A docstring is invisible to the thing that keeps re-filing it, so the reasoning kept being
rediscovered and re-argued instead of read.

A comment answers "why is this like this" for a person reading that line. It cannot answer
"is this intentional" for a pass that is looking at BEHAVIOUR, because that pass never gets to
the line. So the answer lives here as data, keyed by the behaviour, and both a person and a
program can reach it.

WHAT BELONGS HERE, and the bar is deliberately high: a behaviour that a reasonable reviewer
would flag, that was CONSIDERED and KEPT, with a reason that is not "we ran out of time". A
known gap is not a deliberate decision, and putting one here to quiet a warning would turn this
file into a place bugs go to be forgotten. Each entry names what would be reported, what was
decided, why, and what would reopen it.

    from mohio_decisions import DELIBERATE, is_deliberate
    is_deliberate('pii_plaintext_egress')     -> True
    DELIBERATE['pii_plaintext_egress']['why'] -> the reasoning, in full
"""

from __future__ import annotations

from typing import Dict, Optional

DELIBERATE: Dict[str, Dict[str, str]] = {
    'pii_plaintext_egress': {
        'behaviour': (
            "A field tagged [pii] is returned in full on every egress path: a direct give back, "
            "a copy into a variable, a hold, a string it was concatenated into, and the whole "
            "object. It is not masked, and unlike [phi] and [pci] it carries no display class."
        ),
        'decided': 'keep',
        'why': (
            "Masking [pii] on output would break the ordinary case it is most often used for. A "
            "contact form serves the visitor their own email back on the confirmation page, and "
            "a great deal of everyday code is that shape: showing a person their own data. "
            "[phi] and [pci] are different because the everyday case for those is NOT showing "
            "the value, so a mask costs nothing there and protects a great deal. Safest is not "
            "the same as narrowest, and the narrow closure was chosen: [phi] and [pci] mask, "
            "[pii] does not."
        ),
        'not_this': (
            "This is NOT a statement that [pii] is unprotected. A [pii] field is still encrypted "
            "at rest, still classified in the audit trail, and still subject to purpose "
            "limitation, which refuses a use outside the purpose it was collected for and now "
            "records the refusal. What was decided is only that it is not MASKED on the way out."
        ),
        'would_reopen': (
            "A sector or compliance profile that requires masking personally identifying data on "
            "output. That is a sector-layer decision, not a change to the universal default."
        ),
        'reasoning_lives_at': "mohio_interpreter.py, _egress_masked_fields",
        'decided_on': '2026-09-07',
    },
}


def is_deliberate(behaviour_id: str) -> bool:
    """Is this behaviour a decision rather than a defect?"""
    return str(behaviour_id) in DELIBERATE


def explain(behaviour_id: str) -> Optional[Dict[str, str]]:
    """The full record for a behaviour, or None if it was never decided.

    None is the right answer for anything not listed, and it is important that it stays that
    way: a behaviour is a decision because someone decided it, not because a lookup was lenient.
    """
    return DELIBERATE.get(str(behaviour_id))
