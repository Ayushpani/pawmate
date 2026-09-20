# Pawmate Prototype

A **runnable-today** desktop cat, built to prove the core "desktop pet" mechanics
from `docs/` (the master plan) before investing in the full Rust/Tauri/Three.js
stack. Plain Python, one file, no build step.

> This was built in a Linux sandbox and validated as far as that environment
> allows: syntax, the sprite renderer, command parsing, and a full Tk
> event-loop smoke test under Xvfb (headless X11) all pass. The Windows-only
> pieces — true click-through via `-transparentcolor`, `WS_EX_NOACTIVATE`,
> and reading real desktop icon positions — **could not be exercised here**
> because there's no Windows GUI in this sandbox, and they fail gracefully
> (with a console message) rather than crashing when unavailable. Run it on
> your laptop and tell me what breaks — that's the fastest way to harden it.

## The character

The default is a **procedural blob** — no download needed, and it runs by
default:

```powershell
python pawmate_prototype.py
```

Why a blob: every failed attempt at this pet was an *anatomy* failure —
legs, proportions, gait. A blob has no anatomy to get wrong. It's animated
purely with volume-preserving squash & stretch, which is continuous maths
rather than a fixed set of drawn frames, so it can be sampled at any
smoothness and never looks like the wrong animal.

### Or use a downloaded sprite pack instead

```powershell
python pawmate_prototype.py --sprites          # use assets/cat/ instead of the blob
python pawmate_prototype.py --sprites --flip   # if the art faces LEFT and walks backwards
```

Packs whose art faces left will walk backwards without `--flip`, because
rendering assumes the character faces right and mirrors it for leftward
travel.

The sandbox this was built in has `itch.io` and `opengameart.org` blocked by
its network proxy, so the pack has to be downloaded on your machine. Good
free options:

- https://carysaurus.itch.io/black-cat-sprites
- https://frolicforge.itch.io/cat-animation-high-res

**Steps:**

1. Download and unzip the pack.
2. Put the animation files into `prototype/assets/cat/`. Any of these
   layouts works — no renaming needed in most cases:

   ```
   assets/cat/walk/0.png, 1.png, ...     <- a folder per animation
   assets/cat/idle/0.png, ...

   assets/cat/walk.png                   <- OR a horizontal strip per animation
   assets/cat/idle.png
   ```

3. Check what it found:

   ```powershell
   python pawmate_prototype.py --inspect
   ```

   That prints each animation and its frame count, and writes
   `assets/contact_sheet.png` — a labeled grid of every loaded frame, so you
   can confirm nothing got mis-sliced before running for real.

4. Run normally. Frame counts are picked up automatically.

It recognises common names case-insensitively (`Walk`, `walking`, `Cat_Run`,
`sleep`, `Sitting`, …) — see `_ANIM_ALIASES` in the source. If your pack uses
names it doesn't recognise, either rename the files/folders to `walk`,
`idle`, `sit`, `sleep`, `jump`, or send me the `--inspect` output and I'll
add the aliases.

Strips are split on transparent gutters when present, otherwise by assuming
square frames (width being an exact multiple of height). Non-square frames
are letterboxed with the feet aligned to the bottom, so a cat isn't left
floating.

## What it actually does now

Beyond roaming around, it implements the core of the master plan:

**Activity tracking (plan T1/T2/T3)** — samples the foreground app + window
title every 2s, detects idle (2 min threshold), coalesces into segments, and
stores them in local SQLite at `%LOCALAPPDATA%\Pawmate\pawmate.db`.
Categorises productive / neutral / distracting by window title first, then
exe — so a browser counts as productive on docs and distracting on YouTube,
which the exe alone can't tell you. **Nothing leaves your machine**: the
tracking module has no network code in it at all.

**Today's dashboard (plan T5/R1)** — right-click → *Today's activity*, or
type `today`. Focus score, active/deep-work/distracting time, a
where-the-time-went split, a 24h timeline, and top apps with bars.

The score is the plan's deterministic formula, not a vibe:
`100 × (0.50·P + 0.30·DW + 0.20·B) × (1 − 0.25·S)` — productive ratio, deep
work vs a 180min target, breaks, penalised by switching rate. Deep work only
counts contiguous productive blocks of 25min+.

**Wellbeing nudges (plan W1)** — water every 45 min and a stretch reminder
every 50 min, delivered as a speech bubble with buttons, and paced off real
activity (being away from the desk counts as a break, so it won't nag an
empty chair).

**Focus sessions (plan W3)** — right-click → *Focus session*, or `focus 25`.
It sits and guards, then celebrates with your deep-work total.

**Pause tracking** — right-click → *Tracking* → 15 min / 1 hour / until
tomorrow. Password managers are excluded from tracking by default.

**Eyes follow your cursor.** Drawn as live canvas items over the body rather
than baked into frames, so the gaze is continuous instead of needing a
cached image per direction.

**Commands** (double-click the pet, or right-click → Chat):
`today` · `water` · `focus 25` · `pause 30m` · `resume` ·
`open <app>` · `close <app>` · `walk` / `sit` / `sleep`

## Motion, not just art

Three separate things were making it read as a slideshow rather than an
animal, and they had different fixes:

1. **Actions had no motion at all.** Jump/swipe swapped sprite frames with
   the window standing perfectly still — two pictures flickering. The jump
   now physically moves the window through a parabolic arc.
2. **The walk skated.** The window glided smoothly while the leg-frames
   swapped on a wall-clock timer, so feet and ground disagreed. The walk
   cycle is now advanced by **distance travelled** (`distance / STRIDE_PX`),
   which makes skating structurally impossible: however fast the cat is
   moving, including mid-acceleration, the paws advance exactly one stride
   per stride-length of ground covered.
3. **It drifted diagonally across the middle of the screen.** Cats walk on
   surfaces. The cat now lives on the **floor** (the bottom of the work
   area, so it stands on the taskbar rather than behind it) and only walks
   horizontally, easing in and braking to a stop rather than snapping
   between full speed and zero.

The remaining limit is frame count — see "Using your own cat sprites" above.

## What it does

- Transparent, always-on-top, **click-through** overlay window (empty space
  passes clicks through to whatever's beneath; the cat itself is clickable)
- **Whatever sprite pack you drop in** (see above), or a bundled 2-frame
  fallback if you haven't. Idle / sit / sleep / walk, plus a **jump**
  celebration on a successful app open and a **paw-swipe** on app close.
- **Walks along the floor**, horizontally, at an unhurried pace — easing
  into a walk and braking to a stop, pausing to sit or nap. It does not
  drift diagonally across the middle of your desktop.
- **Drag** to pick it up and move it
- **Open any installed app by name through chat** — resolved dynamically
  against everything Windows' own Start menu knows about (via
  `Get-StartApps`, the same list Start menu search uses — covers classic
  desktop apps *and* Store/UWP apps). Typo the name ("chrom") and it asks
  **"did you mean 'Chrome'?"** instead of failing with a raw file-not-found
  error. There's no hardcoded app list to fall out of date.
- Right-click menu:
  - **Close App** — pick from currently open windows; always asks
    **"Close 'X'?"** before doing it (destructive action = your click, never
    automatic). Kept as a menu because it lists *running* windows, which
    the Start menu can't show you — that's genuinely different information,
    not a worse copy of a button you already have.
  - Sit / Sleep / Walk, Chat, Open app…, Quit
  - (There's no static "Open App" list — the first version had one and it
    was just a clunkier Start menu. Chat's resolver replaces it.)
- **Works with zero AI.** Double-click the cat (or use the menu) to open a
  command bar; typed commands like `open notepad`, `close chrome`, `walk`,
  `sit`, `sleep` are parsed locally, no network call. `open` and `close`
  always go through the same dynamic resolver/confirm flow either way.
- **Optional free LLM** for free-text commands: set `OPENROUTER_API_KEY`
  (OpenRouter's free-tier models) or `CF_ACCOUNT_ID` + `CF_API_TOKEN`
  (Cloudflare Workers AI free tier) as environment variables and the command
  bar will ask a free model to turn your sentence into an action. It only
  ever proposes `open_app` / `close_app` / pose changes / a spoken reply —
  and closing a window still shows the confirm dialog either way.

## Setup (Windows 10/11, your laptop)

```powershell
cd prototype
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python pawmate_prototype.py
```

A cat should appear standing on your taskbar within a second or two and
start strolling along it.

### Try it

- **Double-click** the cat → type `open notepad` — it launches Notepad and
  plays a little celebration jump.
- **Double-click** the cat → type `open chrom` (a typo) — it should ask
  *"did you mean 'Google Chrome'?"* rather than erroring out.
- **Right-click** the cat → **Close App** → pick Notepad from the list →
  confirm — Notepad closes with a paw-swipe animation.
- **Drag** the cat around with the left mouse button.
- **Double-click** the cat → type `sleep` → it curls up and naps.
- Try typing in another window (e.g. Notepad) while the cat wanders near
  the cursor — it should never steal focus or block your keystrokes,
  since the window carries `WS_EX_NOACTIVATE`.

### Enabling free AI (optional)

Pick **one**:

**OpenRouter** (https://openrouter.ai — sign up free, create a key):

```powershell
$env:OPENROUTER_API_KEY = "sk-or-..."
# optional, defaults to a free model:
$env:OPENROUTER_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
python pawmate_prototype.py
```

**Cloudflare Workers AI** (https://dash.cloudflare.com — free account has an
AI Neurons daily allowance; grab your Account ID and an API token with
Workers AI access):

```powershell
$env:CF_ACCOUNT_ID = "..."
$env:CF_API_TOKEN = "..."
python pawmate_prototype.py
```

With either set, double-click the cat and type something like *"open the
calculator"* or *"close notepad for me"* — free text, not just the fixed
command words.

## If you see nothing at all (blank desktop, no cat)

There's console output printed by `_apply_win32_styles` right after startup —
look for a line starting `[win32-style]`. If it says `FAILED`, the error
message tells you what broke. If it says `applied to hwnd=...`, the window
styling itself worked; paste both that line and what you're seeing (or not
seeing) and I can narrow it down further. A cat should already be visible
*before* that line prints, since the first frame is now drawn immediately on
startup rather than waiting for the first animation tick — if it's not there
even for that first instant, the issue is likely in Tk's `-transparentcolor`
setup itself rather than the later Win32 styling pass.

## Known rough edges (expected — this is a spike, not the product)

- **Desktop-icon walking** depends on reading Explorer's `SysListView32`
  window from another process. This is a well-known technique but brittle
  across Windows builds/desktop layouts (e.g. "Show desktop icons" off,
  tablet mode, some OEM shells). If it can't find any icons it logs a line
  to the console and falls back to wandering randomly — it will never crash
  because of this.
- Run it as a **normal user**, not elevated/Administrator — an elevated
  process can have trouble reading a non-elevated Explorer's memory, and
  generally you don't want a pet running with admin rights anyway.
- The cat is small 32px pixel art (upscaled 4x, nearest-neighbor so it
  stays crisp instead of blurry), not the live 3D Quaternius model from
  the master plan — that's Phase 4 territory once the Tauri/Three.js shell
  exists, and this placeholder sprite (see `assets/CREDIT.md`) isn't meant
  to survive to a real release either way. This prototype exists purely to
  prove the *mechanics* (transparency, click-through, precise movement,
  app open/close with confirmation, optional free-LLM chat) on real
  Windows before that investment.
- Walk direction only picks from 4 cardinal sprites (E/W mirrored, N, S)
  by whichever of dx/dy is larger, not true 8-way — diagonal movement
  snaps to the nearer cardinal direction rather than showing a dedicated
  diagonal frame. Good enough to make "which way is it going" legible;
  a real product would use the sheet's diagonal frames too.
- **App resolution** needs `powershell` on PATH (it ships with every
  Windows 10/11 install, so this should always be true) and takes ~0.5–1s
  to build the first time, in a background thread — if you `open` something
  in the first second after launch it may say "still indexing," just retry.
  It refreshes itself every 10 minutes in case you install something new.
- No packaging yet (no `.exe`). `pyinstaller pawmate_prototype.py
  --onefile --windowed` will produce a double-clickable `.exe` once you're
  happy with the behavior — ask and I'll wire that up too.

## Mapping back to the master plan

| Prototype behavior | Master-plan feature IDs |
|---|---|
| Transparent/click-through/no-focus-steal window | Spikes S1–S4, §4.5 |
| Walk/idle/sit/sleep + procedural walk cycle | P2, P4 (simplified — no 3D/bones yet) |
| Desktop-icon visiting | not a v1 feature in the plan; added because you asked for it as a "precision movement" demo |
| Open App / Close App with confirm | A3 tools `open_tab`-style + A4 confirm cards (simplified: whole apps, not browser tabs) |
| Local command parsing | A2 slash commands (works with zero AI) |
| OpenRouter/Cloudflare free chat → action | A5 providers, A3 agent (drastically simplified: one tool call, no streaming, no failover chain) |

This is intentionally a shortcut past Phases 0–4 of the real plan to get
something tangible in your hands today. It is **not** a replacement for
building `paw-core`/`paw-platform`/`pawd` in Rust — treat it as a fast
feasibility spike you can point at and say "yes, keep building this."
