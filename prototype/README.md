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

## What it does

- Transparent, always-on-top, **click-through** overlay window (empty space
  passes clicks through to whatever's beneath; the cat itself is clickable)
- A procedurally drawn cat (no external assets, so no licensing to sort out
  for a prototype) with **idle / walk / sit / sleep** poses and a walk cycle
- Wanders the screen, and — best-effort — walks between your **real desktop
  icon positions**, pausing at each one, so it visibly "visits your folders"
- **Drag** to pick it up and move it
- Right-click menu:
  - **Open App** — Notepad / Calculator / Explorer / Paint / Browser, or
    browse for any `.exe`
  - **Close App** — pick from currently open windows; always asks
    **"Close 'X'?"** before doing it (destructive action = your click, never
    automatic)
  - Sit / Sleep / Walk, Chat, Quit
- **Works with zero AI.** Double-click the cat (or use the menu) to open a
  command bar; typed commands like `open notepad`, `close chrome`, `walk`,
  `sit`, `sleep` are parsed locally, no network call.
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

A cat should appear near the center of your screen within a second or two,
start wandering, and (if it can find your desktop icons) start visiting them.

### Try it

- **Right-click** the cat → **Open App → Notepad** — Notepad opens.
- **Right-click** the cat → **Close App** → pick Notepad from the list →
  confirm — Notepad closes.
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
- The cat is flat-shaded 2D (PIL-drawn), not the live 3D Quaternius model
  from the master plan — that's Phase 4 territory once the Tauri/Three.js
  shell exists. This prototype exists purely to prove the *mechanics*
  (transparency, click-through, precise movement, app open/close with
  confirmation, optional free-LLM chat) on real Windows before that
  investment.
- `Browser` in the Open App list shells out to `start microsoft-edge:`,
  which opens your **default** browser via the OS association, not
  necessarily Edge specifically.
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
