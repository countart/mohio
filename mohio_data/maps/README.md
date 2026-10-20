# Mohio Language Packs

**Write Mohio in your language.**

This directory contains `.langmap` files: vocabulary maps that let developers write Mohio source code in any human language. The compiler translates vocabulary before parsing, so the grammar, connector system, and enforcement architecture are identical regardless of which language you write in.

---

## How it works

A `.langmap` file maps Mohio's English vocabulary to another language's words.

```
// en-pt.langmap -- Portuguese vocabulary map
listen for      <->  escutar
find            <->  encontrar
give back       <->  retornar
save to         <->  salvar em
not confident   <->  não confiante
confidence above <-> confiança acima
sector          <->  setor
```

`->`: one direction (English → target language)  
`<->`: bidirectional (either can be the source file)

**What never translates:** The connector system (`on.`, `do.`, `as.`, `is.`, `not.`), AI primitives (`ai.decide`, `ai.audit`, `ai.chain`), and block closers (`: done`). These are structural; the grammar owns them. Only vocabulary words change.

**Partial packs work.** A 50-word map covering the core verbs is immediately useful. Words not in the map pass through in English unchanged: no error, no warning. Write 90% of a file in Portuguese and leave the rest in English. Ship the pack. Improve it over time.

---

## Using a language pack

```bash
# Run a file written in another language: its first line names the pack,
#   // language: klingon
mio run examples/klingon_hello.mho

# Translate an English file into another language; the result is written beside it
# as <name>_<language>.mho, here examples/contact_klingon.mho
mio translate examples/contact.mho --to klingon
```

Or declare it in your `journey.mho` for the whole project:

```mohio
languages
    current     PT
    deploy      EN
    map
        PT -> EN    using maps/en-pt.langmap
    map: done
languages: done
```

---

## Packs in this directory

| File | Language | Coverage | Status |
|------|----------|----------|--------|
| `en-emoji.langmap` | Emoji | The hello demo (`examples/emoji_hello.mho`) | Community demo |
| `en-klingon.langmap` | Klingon (tlhIngan Hol) | The hello demo (`examples/klingon_hello.mho`) and the fraud detection demo | Community demo |


---

## Write your own pack

The format is simple. Any text editor. Any language.

```
// maps/en-es.langmap
// Mohio Spanish vocabulary map
// Lines starting with // are comments -- ignored by the compiler

// Core flow verbs
listen for      <->  escuchar
find            <->  encontrar
retrieve        <->  recuperar
save to         <->  guardar en
update          <->  actualizar
remove from     <->  eliminar de
give back       <->  retornar
check           <->  verificar
when            <->  cuando
otherwise       <->  de lo contrario

// AI primitives -- vocabulary only, NOT the ai. prefix
// ai.decide stays as ai.decide -- only the word after matters
// (leave these as-is if you want ai. primitives to stay English)
not confident   <->  no confiante
confidence above <-> confianza por encima de
weigh           <->  evaluar

// Status values
true            <->  verdadero
false           <->  falso

// Sector and compliance
sector          <->  sector
financial       <->  financiero
healthcare      <->  salud
```

**Rules:**

1. One mapping per line
2. `source -> target` for one-way, `source <-> target` for bidirectional  
3. Longer phrases are matched first: `listen for` matches before `listen`
4. Connectors (`on.`, `do.`, `as.`, etc.) are never in a map; they are structure
5. String values inside quotes are never translated
6. Comments start with `//`

**Tips:**

- Start with the 20 most common verbs; that covers 80% of real code
- Test your pack on a shipped example first: `mio translate examples/contact.mho --to yourlang`, then
  `mio check examples/contact_yourlang.mho`
- Check the language reference for the full vocabulary surface: `start-here/LANGUAGE-REFERENCE.md`
- Connectors and AI primitives don't need entries; they pass through automatically

---

## Submit your pack

Open a pull request adding your `.langmap` file to this directory.

**Naming convention:** `en-[langcode].langmap`  
Examples: `en-pt.langmap`, `en-es.langmap`, `en-hi.langmap`, `en-fil.langmap`

Include in your PR:
- The `.langmap` file
- One sentence in the PR description: what language, how many vocabulary entries, which demo you tested against

That's it. We'll review, test against the full vocabulary surface, and merge.

**Certified packs.** Packs that cover 100% of the Mohio vocabulary surface, pass the full test suite, and have been reviewed by a native speaker get the Certified badge. Certified packs are maintained alongside the language as vocabulary evolves.

---

## The Klingon demo

`en-klingon.langmap` covers exactly the vocabulary needed for the fraud detection demo. It took 45 minutes to write. The compiled output is identical to the English version. The audit trail fires. The sector profile enforces. In every quadrant.

```bash
mio run examples/klingon_hello.mho                     # a program written in Klingon; prints nuqneH
mio translate examples/contact.mho --to klingon        # writes examples/contact_klingon.mho
mio check examples/contact_klingon.mho                 # the translated form checks clean
```

What will you write Mohio in?

---

*Mohio Language Project · [mohio.io](https://mohio.io) · [hello@mohio.io](mailto:hello@mohio.io)*  
*Language pack format: open. Contributions welcome.*