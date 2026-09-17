# mio serve — security defaults are compiler-level (verified 2026-08-27)
For the manual / security docs. Records what raw `mio serve` protects BY DEFAULT, so this doesn't get re-
investigated. Verified by running against the dev checkout's mohio_server.py (the same code path pip install
mohio ships), not the getmohio platform.

## The finding
The static-file denylist is COMPILER-LEVEL, not platform-only. Anyone running `mio serve` directly — self-
hoster, student in a Colab/Jupyter notebook, an enterprise backend — gets this protection with no platform and
no configuration. getmohio adds hardening ON TOP; it is not the only copy of the protection.

## What raw `mio serve` blocks by default (run-verified, real HTTP)
All returned 404 with no body; control paths (/ and /style.css) served normally, proving it's targeted, not a
blanket failure:
- .env, config.yaml, .git/config, .somedotfile, id_rsa, backup.sql.gz
- literal traversal (/../etc/hostname) and encoded traversal (/%2e%2e/.../etc/passwd)
- directory listing (/.git/)
- the app's own source (/index.mho) — .mho source is not served

## How it's built (mohio_server.py — the general runtime mio serve loads)
Three independent layers (defense in depth):
1. Extension denylist (~30: source, config, credentials, key material, db/wal/journal sidecars, dumps/backups)
   + name denylist (id_rsa, passwd, shadow, credentials). Suffix-walks every dot-segment, so compound
   extensions like backup.sql.gz are caught even though .gz alone is not denied.
2. Dotfile / dot-directory rule: any path segment starting with `.` is refused outright, independent of the
   extension table (catches .git/ and dotfiles).
3. Traversal / containment: `..` and leading `/` rejected up front; after resolve() the candidate must be
   is_relative_to(root), which defeats symlink escapes. Static roots also exclude the compiler's own directory.

## History (why this is mature, not a fresh default)
The code comments record real bypasses already found and closed: .gz-wrapped dumps, .pkl cache-of-source, WAL/
journal sidecars, symlink escape, and a past bug where the compiler's own files (mohio_server.py, mohio.lark)
were served from every tenant app. This is a maintained defense with regression history, not an untested
assumption.

## Practical consequence for notebooks / self-hosting
Serving a directory with `mio serve` will NOT expose .env, .git, keys, dumps, or source in that directory. This
is safe by default everywhere Mohio runs, not just on getmohio. (Note: this was NOT known during the first
Colab serve test, which is why that test was inconclusive on file exposure — it is now confirmed safe.)

## Per-IP request backstop (added 2026-09-01, T0-RATE-LIMIT-BACKSTOP)
`mio serve` now carries a crude per-IP request ceiling, default 2000/sec, settable in the app and by env:

```
journey App
    limits
        max requests 2000 per second
    limits: done
journey: done
```

Resolution order is the same one the framework and session store already use: a declared `limits` block beats
`MOHIO_MAX_REQUESTS_PER_SECOND`, which beats the 2000 default. Over the ceiling is a clean 429 with a
`Retry-After`, refused before routing or any program code runs. The enforcing middleware is runtime-internal
and never appears in a program — the coder declares the ceiling, the runtime hides the mechanism.

Two defaults worth knowing, because each is wrong in the other deployment:
- The counted address is the SOCKET PEER. `X-Forwarded-For` is honoured only when `MOHIO_TRUSTED_PROXY` says a
  proxy really is in front. Honouring it unconditionally would let an attacker send a fresh value per request
  and never hit the limit; ignoring it behind a real proxy would put the whole site in one bucket.
- 2000 is deliberately middle-high. A NAT'd classroom, office or cafe is ONE address to a per-IP counter, so
  the default has to leave a shared address ample headroom while still tripping on a single-address flood.

**This is NOT production rate limiting, and must not be sold as such.** It is in-memory with no cross-process
coordination, so N instances behind a load balancer each count separately. Scale is horizontal (more
instances) plus rate limiting at the EDGE — reverse proxy or CDN — which remains the real answer. The backstop
is the per-instance abuser floor underneath that, defense for the unproxied case, never a replacement for the
edge. Raising the number is not the scale story.

## Still open (see the broader security-floor audit)
This confirms the STATIC-FILE denylist is compiler-level, and rate limiting now has a compiler-level backstop
(above). Whether the remaining security defaults (auth enforcement, tenant isolation) are also compiler-level
vs platform-only is being tested separately — see the security-floor audit. The principle to hold: the
baseline security floor belongs in the compiler so every Mohio deployment is safe by default; the platform
adds hardening on top, never the only copy of a baseline.
