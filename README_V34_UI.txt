Santa Maria Helper V34 — UI changes (2026-11-07)

Changes based on user screenshots:
- Input text is pure white (#ffffff) in the AI question editor, search boxes, binder input and settings edit fields.
- Rule categories have moved out of the small left navigation bar into the Rules tab, directly under the search box.
- Each main category expands to show its topics using its arrow; selecting category/topic filters the rule list.
- Replaced the book emoji in the compact navigation with a monochrome text glyph to avoid inconsistent emoji rendering.
- Removed category-tree layout refresh from sidebar hover animations.

Checks: Python compilation and 23 existing automated tests passed. Windows GUI was NOT executed in this environment (PySide6 not installed). Live Cloudflare/Groq AI is NOT verified/fixed by this UI release; the 'cloud AI unavailable' indication requires separate cloud diagnostics.

To install: extract into a fresh directory, run build_exe.bat on Windows and launch the newly generated EXE. No Worker update needed for these UI changes.
