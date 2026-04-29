---

## The Mohio Arc — Three-Stage Canonical Pattern

Every significant Mohio program follows the same three-stage pattern. This is the architecture of the language.

```
GATHER              UNDERSTAND              ACT
retrieve / find   ai.decide / ai.create   give back / export
```

MoQL feeds the arc. AI works on what MoQL returned. They are always sequential — never nested. A `find` block never contains an `ai.decide`. An `ai.decide` block never contains a `find`. The architecture of the program is readable in the structure of the code.

**Foundation Blocks set the world. Lock Blocks build the arc.**

---

## The Readability Test — Five Steps

The Walk-By Test asks: can a non-developer read this in three seconds? The Readability Test asks: does the code match the English description of what it does?

Five steps — runs alongside the Walk-By Test:

1. Describe the steps in conversational English
2. Write the code
3. Check how closely the verbs, nouns, and modifiers match
4. Not close — rewrite without adding words
5. Close — run the Walk-By Test. Passes — ship it

Both tests are required before locking any canonical example. Neither replaces the other.

---

## cm.purge — reason Required

`cm.purge` is the native Right to Be Forgotten verb. It cascades a deletion across all linked stores simultaneously — database, cache, audit logs — and produces an immutable record.

`reason` is a required modifier. The compiler refuses to build without it. A GDPR purge without a documented reason is an incomplete audit record. Same philosophy as `ai.decide` without `not confident`.

```mohio
cm.purge member.id
    reason "GDPR right to erasure — {{ request.ticket_id }}"
    includes "user_records" "session_data" "audit_logs"
    preserve "legal_hold_records"
cm.purge: done
```

**Compiler error without reason:**
```
Compile error — cm.purge requires a reason modifier.
A purge without a documented reason is not legally defensible.
Add: reason "..." before includes.
```

---

## Zero-Drift Multilingual Syntax

`mio translate` converts Mohio keywords to another language. Zero-Drift means the prefix system is the structural guarantee — the dot connector between prefix and word never changes. Swap the vocabulary word. The parser, runtime, and compiled output are identical.

The prefix is grammar. The word is vocabulary. They are separated by the dot.

A file translated to Portuguese and back to English loses zero architectural intent. This is what makes the multilingual claim defensible as a professional language first — Scratch is educational, Inform 7 is experimental fiction, Mohio is production.

---

## `{ }` Single Braces — Retired

Single braces `{ }` are retired as an inline object syntax. Collision risk with `{{ }}` template injection — developers will mix them up.

```mohio
// DRIFT — never locked, now explicitly retired
give back 200 with { id = new_user.id }

// CORRECT — bare with pairs
give back 200
    with id  new_user.id
```

When four or more `with` pairs appear, the compiler suggests declaring a shape.

---

## `notify via email` — Retired

`miomail` is the only email primitive in Mohio. Any email delivery — internal ops, member-facing, compliance alerts — always goes through `miomail`. `notify via email` is not valid. `notify` routes to channels (slack, webhook, push) only.

```mohio
// DRIFT
notify ops_team
    via email
    message "Tests failed"

// CORRECT
miomail
    send
        to      env.OPS_EMAIL
        subject "Tests failed — {{ commit.hash }}"
    send: done
miomail: done
```

---

## `run async` capture — `as` is the form modifier

Capturing the result of `run async` uses `as`, consistent with how Mohio names results everywhere.

```mohio
// DRIFT — = outside math context
run async task_a = process_payments(batch)

// CORRECT — as is the form modifier
run async process_payments(batch) as task_a
run async update_inventory(batch) as task_b

wait for task_a, task_b
```

---

## `retrieve X in db.Y` — Retired

`in` is not valid in retrieve blocks. The correct form is `retrieve from db.Y as X`.

```mohio
// DRIFT
retrieve profile in db.member_profiles

// CORRECT
retrieve from db.member_profiles as profile
```

Note: `find X in db.Y` IS valid — `in` connects the result name to the source in find blocks. Only retrieve uses `from ... as`.

---

## `find` nested inside `retrieve` — Not Valid

`find` nested inside a `retrieve` block is not established syntax. The correct form is two separate sequential blocks. Same rule as AI and MoQL never nesting.

```mohio
// DRIFT — find nested inside retrieve
retrieve from GoogleDrive
    find file
        name contains "{{ episode.title }}"

// CORRECT — sequential blocks
find episode_file from GoogleDrive
    where name contains "{{ episode.title }}"
find: done
read GoogleDrive/{{ episode_file.id }} as audio
```

`find [result] from [connector] where [conditions]` is valid. `find` nested inside another block is not.

---

## `includes` — Reserved

`includes` is reserved for future use. It appeared in early examples as a list-membership condition modifier and was explicitly retired before shipping. The correct form for list membership testing in `check` blocks is `when contains`:

```mohio
check current.user.roles
    when contains admin
        // admin logic
    when contains editor
        // editor logic
    otherwise
        require role viewer
check: done
```

---

## ai.explain — Locked Form

The standalone `ai.explain` form uses `as` to name the result, matching how Mohio names results everywhere. Output destination is left to the developer — the language delivers the explanation, the developer declares what to do with it.

```mohio
ai.explain fraud_check as explanation
    audience "compliance officer"
    format "bullet list"
ai.explain: done
give back explanation
```

Valid output destinations: `give back`, `miomail`, `miolog`, `save to db`. The inline one-liner form is also valid when immediately returning: `give back ai.explain fraud_check audience "..." format "..."`.

---

## Error Message Philosophy — Four Questions

Every Mohio error message — compiler, runtime, or audit — must answer four questions:

1. **What went wrong?** Plain English description
2. **Why is it a problem?** Consequences of the failure
3. **What to do about it?** The specific fix
4. **Where did it happen?** File, line, block name

Not a code. Not a line number. Four answers that let the developer act immediately. Tone: calm, specific, actionable. Never sarcastic. Never vague.

---

## Additional Retired Keywords (post April 2026 sessions)

| Keyword / Pattern | Replacement |
|-------------------|------------|
| `{ }` single braces | `with [key] [value]` pairs or shapes |
| `notify via email` | `miomail send` — miomail is the only email primitive |
| `run async [name] = task()` | `run async task() as [name]` |
| `retrieve X in db.Y` | `retrieve from db.Y as X` |
| `find` nested inside `retrieve` | Separate blocks — `find from [connector] where` |
| `retrieve explanation from ai.explain` | `ai.explain [decision] as [name]` |
| `includes` (as condition modifier) | `when contains` — `includes` RESERVED |
| `or if` | `check / when / otherwise` |
| `consider` | RESERVED — use `check` |
| `divide by N` | `divide.by N` (dot connector required) |
| `order by X ascending` | `order.up by X` |
| `order by X descending` | `order.down by X` |
| `where` in update blocks | `match [field] to [value]` |
| `status to X` in update blocks | Bare `status X` |
| `timeout N seconds` | `wait up to N seconds` |

---

*Mohio Design Decisions — Particular LLC — Apache 2.0*
*This document is the public record of locked language decisions.*
*It is derived from the Language Design Document (LDD), which is the internal authoritative specification.*
*LDD v3.6 is the authoritative source. When this document and the LDD conflict, the LDD wins.*
