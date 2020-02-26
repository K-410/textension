# textension
Productivity enhancements for Blender's text editor

Textension is an effort to consolidate operators and tweaks written for blender's text editor. Some are cosmetic changes, but most deal with increasing productivity and generally trying to match the writing experience of a modern text editor.

- Move editor settings to footer
- Display line, column, selection and editor settings in footer
- Highlight matching words in main view and in scroll bar
- With text selected, `Alt D` / `Alt F` goes to next / previous match 
- A streamlined Go to Line operator `Ctrl J`
- Automatically insert closing bracket
- Deleting both bracket pairs when empty.
- Select All `Ctrl A` no longer centers the cursor
- With line numbers display enabled, clicking a number selects the line
- When selecting text, allow scrolling with mouse wheel
- Double-click and drag now selects entire words
- Triple-click now selects whole line
- Use `Mouse 4` / `Mouse 5` to jump cursor through history

- Cutting or copying without selection cuts or copies the whole line

- Extend current selection with `Shift LMB`
- Line break performs automatic indents and trims whitespace
- Editor default display settings are stored in user preferences
- `Home` now toggles cursor position between start and first indent
- `Alt A` expands cursor selection to closest brackets
- `Shift Tab` now properly unindents lines under tab width
- Animated scrolling:
  - Mouse `Wheel Up` / `Wheel Down` (smooth)
  - Page `PageUp` / `PageDown`
  - Top / bottom `Ctrl Home` / `Ctrl End`
- Toggle header `Alt`
- Auto-finish `def`, `class` and control flow statements `Shift Enter`
  - Add parentheses where applicable (if missing)
  - Add colon
