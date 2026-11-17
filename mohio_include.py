# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""THE ONE INCLUDE RESOLVER (RULED by Ron 2026-11-09), for `include "file"` at top level and
`{{ include "file" }}` in a render alike, at check and at run alike.

A bounded search path: the including file's own folder first, then the application root. Both
lookups are bounded by the root, which is a ceiling: a path that resolves anywhere outside it is
refused, even when the file exists. `../` works inside the root. A nested include searches ITS own
file's folder first, so the base travels with the include and the root stays the same. The path is
a string literal written in the program; no runtime value ever picks it.

The root comes from the application, never from the working directory: the folder `mio serve`
was given, or for a single file the highest folder above it holding a `journey.mho` (its own folder
when none does). Before this, the render include and the runtime's own expansion resolved against
the working directory, so a served app found its fragments only when started from inside its own
folder, and a deploy that started it from anywhere else answered `include_not_found`."""
import os


class IncludeRefused(Exception):
    """An include that may not be loaded: it names nothing, or it leaves the application.
    `.reason` is 'outside' or 'missing'; the message is written for the developer."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def app_root_for_file(page_dir):
    """The application root for a file in `page_dir`: the HIGHEST ancestor folder (including its
    own) holding a `journey.mho`, or the folder itself when none does. The highest and not the
    nearest, because a subfolder journey tightens under the root, it is not the root."""
    page_dir = os.path.abspath(page_dir)
    highest, cur = None, page_dir
    while True:
        if os.path.isfile(os.path.join(cur, 'journey.mho')):
            highest = cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return highest or page_dir


def _inside(path, root):
    path, root = os.path.normcase(path), os.path.normcase(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:      # SILENT-OK: different drives cannot share a root, so it is outside
        return False


def resolve_include(path, including_file, app_root):
    """The file `include "path"` names, written in `including_file`, inside `app_root`.

    Raises IncludeRefused when the path leaves the root or names no file in either place."""
    if not app_root:
        raise IncludeRefused('missing', (
            f'include "{path}" has no application folder to look in: this program was not loaded '
            f'from a file. Run it with `mio run` or `mio serve` on the file or its folder.'))
    root = os.path.realpath(os.path.abspath(app_root))
    here = os.path.dirname(os.path.abspath(including_file)) if including_file else root
    looked = []
    for base in (here, root):
        cand = os.path.realpath(os.path.join(base, path))
        if not _inside(cand, root):
            raise IncludeRefused('outside', (
                f'include "{path}" leaves the application: it resolves to {cand}, outside the '
                f'application folder {root}. An include may reach any file inside the application, '
                f'`../` included, and nothing above it. Move the file into the application, or '
                f'correct the path.'))
        if cand not in looked:
            looked.append(cand)
        if os.path.isfile(cand):
            return cand
    where = ' or '.join(os.path.dirname(c) for c in looked)
    raise IncludeRefused('missing', (
        f'include cannot find "{path}" (looked in {where}). An include looks next to the file that '
        f'includes it, then in the application folder {root}.'))
