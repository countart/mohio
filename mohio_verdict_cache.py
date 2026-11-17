# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The serve-startup verdict cache (Lever 2 Part B, design 2026-11-01).

WHAT IT KEEPS. Starting `mio serve` re-runs the whole verdict for every page: compile-time validation, the
AST build, the include and journey assembly, and every whole-program scanner. With the parse already
cached that verdict is nearly all of a warm start. This keeps a CLEAN verdict (no errors) beside the page,
as `<page>.mho.verdict`, and the next start reuses it when nothing it was built from has changed.

WHAT IT IS KEYED ON, because a stale clean verdict is a program that would now be refused going live:
  - the compiler: every compiler module, the grammar, every file under mohio_data (the bundled langmap
    packs, the bundled sector profiles, the diagnostics registry) and the Python and Lark versions;
  - the transitive source set: every `.mho` under the application root, and the `journey.mho` (or its
    absence) in the page's folder and in every folder above it, up to the drive root;
  - every sector profile on the search path (the certified location included) and every langmap pack in
    the folder a page's `// language:` header is resolved against;
  - and, recorded while the verdict was built, every other file the compiler read (an include outside the
    application root, a file a `{{ include }}` names) and every environment variable it read, compared
    when the verdict is reused. A value read from the environment is kept only as a hash.

WHAT IS NEVER REUSED. Two scanners read the moment or the deployment rather than the source, so identical
source can change their answer: the webhook secret rotation check (it reads the rotation date and today's
date) and the certified-sector requirement (it reads MOHIO_REQUIRE_CERTIFIED_SECTORS). Both run again on
every start, cached verdict or not. Declarations always run again too: they open the database.

MOHIO_VERDICT_CACHE=off turns the cache off; nothing is read or written.
"""
import hashlib
import os
import pickle
import sys

CACHE_VERSION = 1
# The scanners whose answer depends on more than the source. Named here, beside the cache, and checked
# by name against the canonical scanner list, so a rename refuses rather than quietly caching one.
ALWAYS_RERUN = ("scan_webhook_secret_rotation", "scan_required_certified_sector")

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "mohio_data")
_SKIP_DIRS = {"node_modules", "__pycache__"}

# ── recording what a verdict read ───────────────────────────────────────────────────────────────

_recording = None
_hook_installed = False


def _derived_or_compiler(path):
    """True for a file whose content is already in the key by another route: a compiler module or a
    file under mohio_data (both fingerprinted), the interpreter's own library (the Python and Lark
    versions are in the key), and the parse caches, which are derived from sources that are."""
    p = os.path.normcase(os.path.abspath(path))
    if p.endswith((".cache", ".pkl", ".pyc", ".mho.verdict")):
        return True
    here = os.path.normcase(_HERE)
    if os.path.dirname(p) == here and p.endswith(".py"):
        return True
    if p.startswith(os.path.normcase(_DATA) + os.sep):
        return True
    for prefix in {sys.prefix, sys.base_prefix, sys.exec_prefix}:
        if prefix and p.startswith(os.path.normcase(os.path.abspath(prefix)) + os.sep):
            return True
    return False


def _audit(event, args):
    rec = _recording
    if rec is None:
        return
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if isinstance(path, int) or path is None:
            return
        if mode is not None:
            if any(c in str(mode) for c in "wax+"):
                return
        elif isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR):
            return
        try:
            path = os.path.abspath(os.fsdecode(path))
        except (TypeError, ValueError):  # SILENT-OK: not a filesystem path; nothing was read from disk
            return
        if not _derived_or_compiler(path):
            rec["files"].add(path)


class _RecordingEnviron(type(os.environ)):
    """os.environ itself, sharing its storage, that notes the name of every variable read."""

    def __init__(self, real, seen):
        super().__init__(real._data, real.encodekey, real.decodekey, real.encodevalue, real.decodevalue)
        self._seen = seen

    def __getitem__(self, key):
        self._seen.add(key)
        return super().__getitem__(key)


class Recording:
    """While open, every file the compiler reads and every environment variable it asks for is noted."""

    def __init__(self):
        self.files = set()
        self.env = set()
        self._real_env = None

    def __enter__(self):
        global _recording, _hook_installed
        if not _hook_installed:
            sys.addaudithook(_audit)
            _hook_installed = True
        _recording = {"files": self.files}
        self._real_env = os.environ
        os.environ = _RecordingEnviron(self._real_env, self.env)
        return self

    def __exit__(self, *exc):
        global _recording
        _recording = None
        os.environ = self._real_env
        return False


# ── the key ─────────────────────────────────────────────────────────────────────────────────────

def _sha(path):
    """The SHA-256 of a file's bytes, or the word for its state when there are no bytes to hash. Absence
    is a state the key records, not a failure: a file that appears where none was changes the key."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:  # SILENT-OK: absence is recorded as its own state, and a file appearing changes it
        return "absent"
    except IsADirectoryError:
        return "dir"
    except PermissionError:
        return "unreadable"


# Each digest is taken once per process. A start's lookup takes them before the build, so the key a
# kept verdict carries is the state the build began from, and a directory of many pages walks its tree
# once rather than once per page.
_DIGESTS = {}


def _tree_digest(root, want, recursive=True, kind=""):
    """One digest over every file under `root` (only its own folder when not `recursive`) that
    `want(name)` accepts, by relative path and content. `kind` names `want` for the per-process memo."""
    memo_key = (os.path.normcase(os.path.abspath(root)), recursive, kind)
    if kind and memo_key in _DIGESTS:
        return _DIGESTS[memo_key]
    digest = _tree_digest_walk(root, want, recursive)
    if kind:
        _DIGESTS[memo_key] = digest
    return digest


def _tree_digest_walk(root, want, recursive):
    h = hashlib.sha256()
    if not os.path.isdir(root):
        h.update(b"absent")
        return h.hexdigest()
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in _SKIP_DIRS) if recursive else []
        for f in sorted(files):
            if want(f):
                p = os.path.join(d, f)
                h.update(os.path.relpath(p, root).replace(os.sep, "/").encode() + b"\0")
                h.update(_sha(p).encode() + b"\n")
    return h.hexdigest()


def _compiler_digest():
    if "compiler" in _DIGESTS:
        return _DIGESTS["compiler"]
    _DIGESTS["compiler"] = _compiler_digest_build()
    return _DIGESTS["compiler"]


def _compiler_digest_build():
    import lark
    h = hashlib.sha256()
    h.update(f"verdict-v{CACHE_VERSION}|py{sys.version_info[0]}.{sys.version_info[1]}|lark{lark.__version__}|".encode())
    for f in sorted(os.listdir(_HERE)):
        if f.endswith((".py", ".lark")):
            h.update(f.encode() + b"\0" + _sha(os.path.join(_HERE, f)).encode())
    h.update(_tree_digest(_DATA, lambda f: not f.startswith(".") and not f.endswith((".pkl", ".tmp"))).encode())
    return h.hexdigest()


def _ancestor_journeys(page_dir):
    """The journey.mho (or its absence) in the page's folder and every folder above it."""
    h = hashlib.sha256()
    cur = os.path.abspath(page_dir)
    while True:
        p = os.path.join(cur, "journey.mho")
        h.update(p.encode() + b"\0" + _sha(p).encode() + b"\n")
        parent = os.path.dirname(cur)
        if parent == cur:
            return h.hexdigest()
        cur = parent


def _sector_profiles():
    """Every sector profile on the search path, in search order, the certified location included."""
    import mohio_sector_loader as sl
    h = hashlib.sha256()
    for sp in sl._default_search_paths():
        h.update(os.path.abspath(sp).encode() + b"\0")
        h.update(_tree_digest(sp, lambda f: f.startswith("sector-")).encode() + b"\n")
    return h.hexdigest()


def _langmap_packs(page_dir):
    """The packs in the folder a page's `// language:` header is resolved against (bundled packs are
    under mohio_data, in the compiler digest)."""
    return _tree_digest(os.path.join(page_dir, "..", "maps"), lambda f: f.endswith(".langmap"))


def key_parts(page_path, app_root):
    """Each part of the key by name, so a rebuilt verdict can say which part changed."""
    page_path = os.path.abspath(page_path)
    page_dir = os.path.dirname(page_path)
    # A served directory is the application, and its whole tree is in the key. A single file is keyed on
    # its own folder only: it may sit in a folder holding anything at all (a temporary directory, a home
    # folder), and walking all of that on every start cost more than the verdict it saves. What a single
    # file reads beyond its folder is recorded as it is read, and the journeys above it are in the key.
    root = os.path.abspath(app_root) if app_root else page_dir
    return {
        "the compiler": _compiler_digest(),
        "the page and its application root": hashlib.sha256(
            f"{os.path.normcase(page_path)}|{os.path.normcase(root)}|{os.path.normcase(os.getcwd())}".encode()).hexdigest(),
        "a journey.mho in a folder above the page": _ancestor_journeys(page_dir),
        "a .mho file in the application": _tree_digest(root, lambda f: f.endswith(".mho"),
                                                       recursive=bool(app_root), kind="mho"),
        "a sector profile": _sector_profiles(),
        "a langmap pack": _langmap_packs(page_dir),
    }


# ── keeping and reusing a verdict ───────────────────────────────────────────────────────────────

def enabled():
    return os.environ.get("MOHIO_VERDICT_CACHE", "").strip().lower() not in ("off", "0", "false", "no")


def _path_for(page_path):
    return os.path.splitext(os.path.abspath(page_path))[0] + ".mho.verdict"


def _env_hash(name):
    v = os.environ.get(name)
    return None if v is None else hashlib.sha256(f"{name}={v}".encode()).hexdigest()


def load(page_path, app_root):
    """(entry, None) for a verdict that still holds; (None, why) when it was rebuilt for a reason worth
    saying; (None, None) when there was none to reuse."""
    path = _path_for(page_path)
    try:
        with open(path, "rb") as fh:
            entry = pickle.load(fh)
    except FileNotFoundError:
        return None, None
    except Exception:  # SILENT-OK: an unreadable verdict is rebuilt, and the reason is said
        return None, f"{os.path.basename(path)} could not be read"
    if not isinstance(entry, dict) or entry.get("version") != CACHE_VERSION:
        return None, "the verdict was written by another build of the cache"
    parts = key_parts(page_path, app_root)
    for name, digest in parts.items():
        if entry["parts"].get(name) != digest:
            return None, f"{name} changed"
    for f, digest in entry["files"].items():
        if _sha(f) != digest:
            return None, f"{f} changed"
    for name, digest in entry["env"].items():
        if _env_hash(name) != digest:
            return None, f"the environment variable {name} changed"
    return entry, None


def store(page_path, app_root, recording, payload):
    """Keep a clean verdict. Returns None, or a sentence saying why it could not be kept."""
    entry = {"version": CACHE_VERSION, "parts": key_parts(page_path, app_root),
             "files": {f: _sha(f) for f in sorted(recording.files)},
             "env": {n: _env_hash(n) for n in sorted(recording.env)}}
    entry.update(payload)
    path = _path_for(page_path)
    tmp = path + f".{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as fh:  # EGRESS-EXEMPT: the verdict is kept on this machine, beside the page; it is never served (mohio_server denies it)
            pickle.dump(entry, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except (OSError, pickle.PicklingError, TypeError, AttributeError) as e:
        try:
            os.remove(tmp)
        except OSError:  # SILENT-OK: the temporary file was never written
            pass
        return f"the verdict could not be kept beside the page ({type(e).__name__}: {e})"
    return None
