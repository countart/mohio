# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Sanitize uploaded HTML before it is stored.

An uploaded .html file is the one upload type that can attack the people who later
view it. `accept html` is allowed on the free tier, so the file is cleaned at upload
time and only the cleaned version is kept -- the original never reaches disk.

WHY NOT bleach: the handoff named it, but bleach announced on 2026-06-05 that it is no
longer maintained and will get no further releases, including for security issues. A
sanitizer that stops receiving security fixes is the wrong dependency for the one job
where being current is the whole point. nh3 (Python bindings to the Ammonia Rust crate)
is maintained and is allowlist-based in the same way.

WHY ALLOWLIST: a blocklist of dangerous tags always has a gap -- a new attribute, a new
scheme, a malformed tag the parser recovers differently. An allowlist keeps only what is
known safe and drops everything else, so an unknown construct fails closed.

IF THE SANITIZER IS MISSING: uploading HTML fails loud. Storing it unsanitized because a
library is not installed would be the worst outcome -- the deployment would look like it
was protecting people while serving whatever was uploaded.
"""

SANITIZED_EXTENSIONS = frozenset({'html', 'htm', 'xhtml'})

# Structural and text markup only. No script, no frames, no embedding, no forms, and
# nothing that navigates or refreshes on its own.
_ALLOWED_TAGS = frozenset({
    'a', 'abbr', 'address', 'article', 'aside', 'b', 'blockquote', 'br', 'caption',
    'cite', 'code', 'col', 'colgroup', 'dd', 'del', 'details', 'div', 'dl', 'dt',
    'em', 'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'hr', 'i', 'img', 'ins', 'kbd', 'li', 'main', 'mark', 'nav', 'ol', 'p',
    'pre', 'q', 's', 'samp', 'section', 'small', 'span', 'strong', 'sub', 'summary',
    'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'time', 'tr', 'u', 'ul',
    'var', 'wbr',
})

# No `style` anywhere: CSS carries expression() and url(javascript:) in enough engines
# that keeping it costs more than it gives. No event handlers -- they are attributes,
# and only the ones named here survive.
_ALLOWED_ATTRIBUTES = {
    '*': {'class', 'dir', 'id', 'lang', 'title'},
    'a': {'href', 'hreflang', 'name', 'target'},
    'img': {'alt', 'height', 'src', 'width'},
    'col': {'span'}, 'colgroup': {'span'},
    'td': {'colspan', 'rowspan'}, 'th': {'colspan', 'rowspan', 'scope'},
    'ol': {'reversed', 'start', 'type'},
    'time': {'datetime'}, 'del': {'datetime'}, 'ins': {'datetime'},
    'blockquote': {'cite'}, 'q': {'cite'},
    'details': {'open'},
}

# javascript: and data: are absent on purpose -- they are the two schemes that turn a
# link or an image into script execution.
_ALLOWED_SCHEMES = frozenset({'http', 'https', 'mailto', 'tel'})


class SanitizerUnavailable(RuntimeError):
    """The sanitizer is not installed, so HTML cannot be accepted safely."""


def available():
    """True when HTML uploads can be sanitized."""
    try:
        import nh3  # noqa: F401
        return True
    except ImportError:
        return False


def sanitize_html(markup):
    """Return `markup` with everything outside the allowlist removed.

    Raises SanitizerUnavailable when the library is missing, so a caller fails loud
    rather than storing the original.
    """
    try:
        import nh3
    except ImportError as e:
        raise SanitizerUnavailable(
            "HTML uploads need the nh3 sanitizer, which is not installed.\n\n"
            "  Run:  pip install nh3\n\n"
            "Storing the file unsanitized is not an option -- an uploaded page can "
            "carry script that runs against whoever opens it. Remove html from "
            "`accept`, or install nh3."
        ) from e

    if isinstance(markup, bytes):
        markup = markup.decode('utf-8', errors='replace')

    return nh3.clean(
        markup,
        tags=set(_ALLOWED_TAGS),
        attributes={k: set(v) for k, v in _ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(_ALLOWED_SCHEMES),
        link_rel='noopener noreferrer',
        strip_comments=True,
    )
