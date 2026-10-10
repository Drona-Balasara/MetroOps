# MetroOps Response — Design System (Phase 1)

The visual foundation and application shell for the operations console. This file is
the documented rationale; the implementation lives in `static/style.css` (tokens +
components), `static/index.html` (shell), and `static/app.js` (shell behaviour).

## Brief and subject grounding

MetroOps is a single-operator, offline-first incident console for metro control-room
and station staff. The design is grounded in **transit control rooms and station
signage**: calm, dense, legible-at-a-glance information, with status communicated the
way a platform indicator or departure board communicates it — unambiguously, by label
and position, never by colour alone.

## Deliberate deviations from the generated default

The UI/UX Pro Max generator proposed a "Dark Mode (OLED)" direction (near-black `#0F172A`
background, single operational-green accent, JetBrains Mono for all text, imported from a
Google Fonts CDN). That is useful input but three parts of it are generic tells or break
a hard constraint, so they were changed on purpose (per `frontend-design` guidance):

1. **No CDN fonts.** Offline-first forbids external requests, so typography uses
   system font stacks only. No `@import`, no web fonts.
2. **Not monospace-everywhere.** A mono face for all copy is the "hacker-console" tell and
   hurts reading of incident descriptions. Monospace is reserved for *numerics that align
   in columns* (IDs, scores, counts, timestamps); the UI and prose use a system sans.
3. **Not near-black + one neon accent.** The background is a layered blue-**slate** system
   (not pure black), and the palette carries a full functional set — a wayfinding-blue
   primary plus a four-step severity scale plus a four-step lifecycle scale — rather than
   one bright accent doing all the work. Boldness is spent in one place: severity.

GSAP and any JS animation library are **not** added in Phase 1; motion is CSS-only. A
library would be a new runtime dependency to bundle locally, unjustified for a shell.

## Tokens

All tokens are CSS custom properties on `:root` in `static/style.css`.

### Colour — surfaces (deep blue-slate, dark control room)
| Token | Hex | Use |
|-------|-----|-----|
| `--bg` | `#0D1520` | App background |
| `--surface` | `#141F2C` | Panels, cards |
| `--surface-raised` | `#1B2A3A` | Hover / raised |
| `--surface-inset` | `#0A111A` | Inputs, wells |
| `--border` / `--border-strong` | `#263645` / `#324557` | Dividers, outlines |
| `--fg` / `--fg-muted` / `--fg-subtle` | `#E8EEF4` / `#9FB2C4` / `#6E8296` | Text tiers |

### Colour — primary & semantic
`--primary #2E6FEA` (wayfinding azure, interactive) · `--online #3FB950` (system healthy).

### Severity scale (the one bold system; always paired with a text label)
Critical `#FF4D4F` · High `#FF8A3D` · Medium `#F2B600` · Low `#5A8DB8`.

### Lifecycle scale (distinct hues from severity, to avoid confusion)
Active `#3BA7E0` · Acknowledged `#F2B600` · In Progress `#29B6A8` · Resolved `#3FB950`.

Severity and status are **never conveyed by colour alone** — each badge carries its text
label (e.g. `CRITICAL · 85/100`, `In Progress`), satisfying the no-colour-alone rule.

### Type (system stacks, no external fonts)
- Sans (UI + prose): `system-ui, -apple-system, "Segoe UI", Roboto, …`
- Mono (aligned numerics only): `"SFMono-Regular", "Cascadia Code", Consolas, …`
- Scale (base 16px root): 12 / 13 / 15 / 16 / 18 / 22 / 28px. Body line-height 1.5.

### Spacing (dense, 4/8 based): 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64px
### Radii: 4 / 6 / 10px + pill. ### Motion: 120 / 180 / 260ms, ease-out; all gated by `prefers-reduced-motion`.

## Layout — application shell

```
+----------+--------------------------------------------------+
| SIDEBAR  | TOPBAR  [≡] Incidents        UTC clock [Report]  |
| brand    +--------------------------------------------------+
| nav      | WORKSPACE                                        |
|  Incid.  |   [ Report an incident      panel ]              |
|  Overvw  |   [ Incident queue + filter panel ]              |
|  Netwk   |                                                  |
| ──────── |                                                  |
| health   |                                                  |
+----------+--------------------------------------------------+
```

- **Left aligned**, fixed-width sidebar (`240px`) + fluid workspace. Content max-width keeps
  reading lines under ~80ch.
- **Responsive:** sidebar is permanent ≥1024px; below that it becomes an off-canvas drawer
  toggled by the topbar `≡` button with a scrim. No horizontal scroll at 375px.
- Future views (Overview, Network) appear in the nav as disabled `Soon` items — present for
  orientation, visibly non-functional rather than dead controls that look clickable.

## Components (foundation, used now or by the next phase)

Buttons (primary / secondary / ghost / danger / small, with hover·focus·active·disabled·
loading states) · inputs, select, textarea, checkbox with field hints and error state ·
status-dot + health pill · severity and lifecycle badges · alerts (info/success/warning/
error) · panels · tables · empty / loading / error states.

## Quality floor

Keyboard focus visible everywhere (`:focus-visible` ring), 4.5:1 text contrast on
surfaces, 44×44px touch targets, skip link, `prefers-reduced-motion` honoured, `color-scheme`
set, responsive to 375px. Copy is sentence case, active voice, written for the operator
(e.g. "Submit incident" → toast "Incident INC-0001 submitted").
