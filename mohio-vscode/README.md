<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Language — VS Code extension

Syntax highlighting for `.mho` files. Highlights keywords, the `ai.`/`mio*`/`cm.` namespaces, casts (`as.int`, `as.text`, ...), types, `{{ }}` interpolation, strings, numbers, `//` and `/* */` comments, and named closers (`task: done`).

## Install (fastest, no build tools)

1. Copy this whole `mohio-vscode` folder into your VS Code extensions directory:
   - Windows: `%USERPROFILE%\.vscode\extensions\`
   - macOS/Linux: `~/.vscode/extensions/`
   So you end up with `.vscode/extensions/mohio-vscode/package.json`.
2. Fully quit and reopen VS Code.
3. Open any `.mho` file. Bottom-right of the window should say "Mohio". If it says "Plain Text", click it and pick "Mohio".

That's it, files should now be colored instead of white.

## Alternative: run it live (for editing the grammar)

1. Open the `mohio-vscode` folder in VS Code.
2. Press `F5`. A second VS Code window ("Extension Development Host") opens with the extension active.
3. Open a `.mho` file in that window to see highlighting; edit the grammar and reload to iterate.

## Alternative: package as a .vsix (to share/install cleanly)

Requires Node:
```
npm install -g @vscode/vsce
cd mohio-vscode
vsce package
```
Produces `mohio-0.1.0.vsix`. Install with: VS Code → Extensions panel → "..." menu → "Install from VSIX".

## Notes

- This is v0.1, highlighting only. Snippets, hover docs, and `mio check` integration are later additions.
- The keyword set is drawn from the actual Mohio grammar (`mohio.lark`). As the language adds or retires words, update `syntaxes/mohio.tmLanguage.json` to match, keep it in sync with the grammar so the highlighting never teaches a retired word.
- If a color looks wrong, the scope names follow standard TextMate conventions, so any VS Code theme will color them; different themes will render Mohio differently, which is expected.
