<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# The mental model: Mohio has no classes

*For a Java, C#, PHP, or Python developer meeting Mohio for the first time. This is the one
thing to understand before anything else clicks.*

Every runnable example here is verified against the current compiler.

---

## The habit to unlearn

In a class-based language, data and behavior live together: a `Player` class owns a `health`
field AND a `heal()` method that changes it. One thing, two jobs.

Mohio splits those two jobs into two different kinds of declaration, and never puts them back
together:

- **`shape`** describes data. What a `Player` IS.
- **`task`** does something. What HAPPENS to a `Player`, or because of one.

**There is no `class` keyword, and there never will be.** Reaching for one, or writing a
`shape` that also carries behavior, is the single most common first mistake, and the compiler
tells you so directly:

```
a task returns a value only when it declares a return type: `task greet <param> as <type>
returns <type>`. Without `returns`, its `give back` is a response, not a value for the caller.
```

That message exists because a newcomer expects a method call to give back a value the way
`player.heal()` would. In Mohio, that expectation is correct -- you just write the value-return
explicitly with `returns`, on a `task`, not on the `shape`.

---

## `shape` describes, it never acts

```mohio
shape Player
    name as text
    health as int
shape: done
```

A `shape` declares the CONTRACT: what fields a `Player` has, and their types. It cannot contain
a verb, a calculation, or a decision -- those all belong on the other side. A `shape` can carry
validation (`required`, `min`, `max`, `format "email"` -- see `Docs/guides/form-field-types.md`), because
validation describes what a VALID `Player` looks like, which is still description, not action.

---

## `task` acts, it never describes

```mohio
task greet
    take name as text
    returns text
    give back ("Hello, " & name & "!")
task: done

call greet with "Aria" as greeting
show greeting                          // Hello, Aria!
```

A `task` is the callable unit of behavior -- Mohio's word for a function. It optionally takes
inputs (`take`), and optionally gives a value back to its caller (`returns` + `give back`).
Full mechanics, every calling form, and the exact return-type rule quoted above:
`Docs/guides/Mohio-Tasks-and-Call-Explainer.md`.

---

## The method equivalent: a task that takes a shape and returns one

A Java method that changes an object and hands it back -- `player.heal(25)` -- has a direct
Mohio equivalent: **a task that TAKES a shape instance and RETURNS a shape instance.** That
pairing is as close as Mohio gets to a method, and it is enough to build with.

```mohio
shape Player
    name as text
    health as int
shape: done

task heal
    take player as sh.Player
    take amount as int
    returns sh.Player
    create healed as sh.Player
        name player.name
        health (player.health + amount)
    create: done
    give back healed
task: done

create hero as sh.Player
    name "Aria"
    health 40
create: done

call heal as hero
    player hero
    amount 25
call: done

show ("hero: " & hero.name & " -- " & hero.health)
```

Run it:

```bash
mio run heal.mho
```

```
hero: Aria -- 65
```

**The one real difference from a method, and it matters:** there is no `this`, and a task
cannot reach into a shape instance and change one of its fields in place -- `player.health
(player.health + amount)` inside the task is not wired and fails loud rather than silently
doing nothing. `heal` builds a brand new `Player` (`create healed as sh.Player ...`) with the
updated value and hands that back; the caller captures it under whichever name it likes
(`call heal as hero` reuses the name `hero`, which is why the `show` line at the end sees the
healed value). Nothing is mutated behind your back. A task's only way to change what a caller
sees is to return a new value -- which is also why Mohio has no reference semantics to reason
about, and no `heal()` running somewhere that silently changes a `Player` you're still holding
a handle to.

---

## Quick reference

| OOP instinct | Mohio equivalent |
|---|---|
| `class Player { ... }` | `shape Player ... shape: done` |
| a field | a shape field (`health as int`) |
| a method with no return value | a `task` with no `returns` |
| a method that returns a value | a `task` with `take <input> as sh.Shape`, `returns sh.Shape` (or any type), `give back` |
| `this.health = this.health + amount` | build a new instance with `create` and `give back` it -- nothing is mutated in place |
| a constructor | `create <name> as sh.Shape / field value / ... / create: done` |

Next: `Docs/guides/mohio-first-app-tutorial.md` builds a small, real app -- a shape, a task, a route, a
database write, and a list-back -- using exactly this split.
