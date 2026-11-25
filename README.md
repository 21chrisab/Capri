# Capri v2

Hold **Alt**, talk, let go. (Alt+Tab, Alt+F4 and other shortcuts still work normally: if you press another key while holding Alt, Capri ignores it. Change the key in `config.toml`.)
Or just say **"Capri"** (hands-free, see below).
1. **Instant commands** (open apps, sites, music, volume, windows, tabs, your tasks) run with no AI, in well under half a second.
2. **Everything else** goes to Gemini 3.1 Flash-Lite. She streams her answer sentence by sentence, so she starts talking before she's done thinking. She can use her tools (apps, windows, music, volume, web search) and her memory.

## 1. Add your Gemini key (once)
1. Go to https://aistudio.google.com/apikey and click **Create API key**. Use the Google Cloud project that has your Google AI Pro developer credits.
2. Open the file **`.env`** in this folder with Notepad. Turn on "show hidden files" if you don't see it.
3. Paste the key after `GEMINI_API_KEY=` with no spaces or quotes, like this:
   ```
   GEMINI_API_KEY=AIzaSy...your key...
   ```
4. Save. Never share this file or put it on GitHub (`.gitignore` already excludes it).

`CAPRI_MODEL` in the same file picks the model. If Google renames it, she tries `CAPRI_MODEL_FALLBACKS` in order automatically.

## 2. Setup / update
Unzip over your Capri folder (`Downloads\Capri2`). The zip has no `.env` and no `memory\` folder, so your key and her memory are kept. Then run:
```
powershell -ExecutionPolicy Bypass -File setup.ps1
```
This installs anything new. Run with `capri.bat`. At startup she prints `brain: ready (gemini-3.1-flash-lite)`.

## Instant commands (the reflex)
Common commands skip the AI completely. She understands them even when they're buried in normal talk:
- **Several at once:** "open Discord and turn the volume down", "open Claude and my Chrome".
- **Behind chatter:** "yeah so can you open up my Gmail", "I said open Spotify".
- **Follow-ups:** after a volume change, "higher", "lower", "a hundred" or "again" just work. After a song skip, "next one".
- **Sites and searches:** "go to x dot com", "search for Valorant patch notes".
- **Tabs and windows:** "new tab", "close this tab", "next tab", "snap it left", "take a screenshot".
- **Spotify:** "play Blinding Lights" searches Spotify and presses play. "Play some music" just resumes.
- **Safety rails:** at most 3 actions per sentence, no repeats, and saying "open Chrome" twice within 8 seconds switches to it instead of opening a second copy. If you trail off ("open the…"), she waits instead of guessing.

**Optional, for fuzzier wording:** install [Ollama](https://ollama.com), then run `ollama pull qwen3.5:4b` once. A small model on your PC then catches phrasings the rules miss. It can only pick from the fixed list of actions above, never write a reply, and anything it isn't sure about goes to Gemini. Without Ollama everything still works; turn it off with `local_model = ""` under `[reflex]` in `config.toml`.

## Hands-free: just say "Capri"
- **Say "Capri"** (or "Hey Capri, open Spotify") from anywhere in the room. She wakes up, and if you said a command with her name, she does it straight away. Her name alone gets a quick "Yeah?".
- **Then just talk.** No name or hotkey needed while she's awake, so follow-ups work naturally.
- **She goes back to sleep after 10 seconds of quiet.** The clock only runs while neither of you is talking or she's working. Esc also puts her to sleep.
- **Privacy:** while asleep, a tiny voice detector runs on your PC and only clips of speech are transcribed, also on your PC, to check for her name. Nothing is sent anywhere until she's awake.
- **While she's talking she ignores the mic,** so she doesn't answer herself. Say "Capri, stop" to cut her off, or press Alt/Esc.
- Settings are in `config.toml` under `[listen]`: `idle_timeout_s`, `end_silence_s` (raise it if she cuts you off mid-sentence), `vad_threshold` (lower it if she doesn't hear you from far away), and `mode = "hotkey"` to turn hands-free off.

## Handoff to Claude
For big jobs, Capri hands off to **Claude** (Claude Code, running on your Claude Pro plan, no API key) and keeps talking to you while it works.
- "Capri, have Claude research far-field mics under 50 bucks and write me a comparison."
- "Hand this to Claude." She packs up what you were just talking about.
- She also hands off on her own when a job is too big for voice: writing code or documents, deep research, plans.
- When it's done she tells you Claude's one-line summary. Then: "open Claude's result" (opens `RESULT.md`), "what did Claude find?", or "is Claude done yet?"
- Every job gets its own folder: `handoffs\<date>-<name>\` with `TASK.md`, `RESULT.md` and any files Claude made.
- **Sandboxed:** Claude can search and read the web and create or edit files only inside that job folder. It can't run commands.

**One-time setup:** install Claude Code in PowerShell with `irm https://claude.ai/install.ps1 | iex`, then run `claude` once and log in with your Claude account. `capri.bat doctor` shows "Claude Code (handoff) OK" when it's ready. Settings are under `[handoff]` in `config.toml`.

**Codex and Gemini too.** Say "have Codex write a CSV parser" or "give this to Gemini" to use your ChatGPT or Google AI plan instead. Same job folders, same spoken summary. Install with `npm i -g @openai/codex` / `npm i -g @google/gemini-cli` and run each once to log in. They're held to the same limits as Claude: Codex runs in its `workspace-write` sandbox (writes only in the job folder, no network), Gemini runs in `auto_edit` mode (shell commands need approval, which a background job can't give, so they're refused). `default_agent` in `[handoff]` picks who gets jobs when you don't say.

## Life OS: tasks, projects, dashboard
Your tasks and projects live in a separate **life** folder in Google Drive (`G:\My Drive\life`), shared with
Claude Code and your dashboard. Capri finds it on her own; if it's somewhere else, set `[life] dir` in `config.toml`.
- "Add call the advisor to my inbox" / "Note to self, email Professor Lee" (instant)
- "What's on my plate today?" reads your Top 3 (instant)
- "Mark the mic task done" / "I finished the degree plan" (instant)
- "Open my dashboard" (instant)
- "What should I work on?", "Add order the mic to Capri, due Friday" (Gemini, with the life tools)

Planning, reviews and the Opportunities tab are Claude Code commands inside the life folder. See its README.

**Dashboard with a Capri sidebar:** while Capri runs, open **http://127.0.0.1:8765** (or say "open my dashboard").
You get the life dashboard plus a sidebar showing what she's doing (listening / thinking / speaking / using the
computer), your recent conversation, a box to type to her instead of talking, and Esc to stop her. It rebuilds
itself every 15 minutes. Only your PC can open it. Turn it off or change the port under `[web]` in `config.toml`.

## Memory: a folder, not a file
```
memory\
  SOUL.md            her personality. Edit it to change how she talks. She can't change it herself.
  USER.md            core facts about you (always loaded)
  LESSONS.md         corrections and standing rules ("always X", "don't Y"). Newest 15 always loaded.
  modes\coding.md    rules for each workflow mode: general, coding, assignments, content_studio
  people\jordan.md   one file per person
  topics\music.md    tastes, routines, your setup...
  projects\...       ongoing stuff
  daily\2026-11-09.md   automatic log of what you asked today
```
- Tell her things ("my best friend Jordan plays Valorant") and she files them in the right place.
- She always sees a one-line **index** of every file. She only opens a file when it's relevant, so memory can grow without slowing her down.
- "Forget that Jordan plays Valorant" removes the line.
- Correct how she works ("don't read me code out loud") and she saves it to `LESSONS.md`. Handed-off agents get the lessons too.
- "Switch to coding mode" / "content studio mode" / "assignments mode" / "general mode" changes her rules. Edit the files in `modes\` to change what each mode means, or add your own. The active mode's rules go to handed-off agents as well.
- `memory\` is in `.gitignore`, so what she knows about you stays off GitHub.
- You can make your own folders and files. They show up in her index automatically.
- Every change she makes is logged in `logs\memory_writes.jsonl`.

## How she sees your apps (no screenshots by default)
1. **UI Automation.** Windows already knows every button, text box and list item in most apps, with its name and exact position. She reads that as a short numbered list, picks a number, and Windows clicks the exact spot. It's fast and precise, and nothing is sent as an image.
2. **Windows OCR.** If an app doesn't expose its buttons (games, some Electron apps), she reads the words on screen with Windows' built-in text recognition. It runs on your PC with nothing to download.
3. **Screenshot to Gemini.** Only as a last resort, for pictures, charts or games, and only of one window.

After every click or keypress she checks whether the window actually changed, so she doesn't repeat a dead click.

**Safety:** she won't type into password, PIN, card or code fields. She asks before pressing send, delete, buy or post buttons. **Esc** or clicking the little pill above the taskbar stops her instantly. When she's driving, the pill says "Capri is using the computer · Esc to stop".

**Discord, Spotify and VS Code** are Electron apps. They only show their buttons to Windows when started with `--force-renderer-accessibility`. Without it she falls back to OCR, which works but is less precise. For Discord, right-click its shortcut → Properties → add ` --force-renderer-accessibility` to the end of Target.

## Try saying
- "What's the weather tomorrow?" (web search)
- "Remember I like lo-fi when I code." Later: "Put on something I like for coding."
- "Open Discord and turn the volume down." Then: "lower." (instant, no AI)
- "Play my Liked Songs on Spotify." (observe → click)
- "In Chrome, open a new tab and search for Valorant patch notes."
- "What's on my second monitor?" (list_windows + read_screen)
- "Read me the last message in this chat."
- "What did I ask you earlier today?"
- Press the hotkey while she's talking to cut her off.

## Commands
| Command | What it does |
|---|---|
| `capri.bat doctor` | Checks every part (Python, packages, models, key, mic, GPU) and says how to fix what's broken |
| `capri.bat` | Run Capri. Esc cuts her off. Ctrl+C quits. |
| `capri.bat budget` | Speed per turn kind: `[instant]`, `[brain]`, `[computer]`. Targets: instant ≤ 450 ms, brain ≤ 1.2 s to first audio |
| `capri.bat bench-stt` | Compare speech engines on your voice |
| `capri.bat say Hey` | Voice check |
| `capri.bat devices` | List mics and speakers |

## Coming next
- **M5:** she learns repeated tasks and replays them with no AI.
- **Later:** echo cancellation so you can talk over her without saying her name, and smarter end-of-sentence detection.

## Capri on Mac (Phase 1)
1. Run `./setup.sh` once (creates `~/.capri/venv`, downloads Kokoro models to `~/.capri/models`, and writes `config.local.toml`).
   Then paste your Gemini key into `~/.capri/.env` (`open -e ~/.capri/.env`). It's kept outside this folder because the
   folder lives in Google Drive.
   Optional: share one memory with the PC by setting `[memory] dir` in `config.local.toml` to a Google Drive folder.
2. Run `./capri.sh` to start Capri, or `./capri.sh doctor` to check every part.
3. Grant the three macOS permissions in **System Settings → Privacy & Security** for your Terminal app:
   - **Microphone** (listening)
   - **Input Monitoring** (hold **Option** to talk)
   - **Accessibility** (window controls & hotkey)
4. **What works in Phase 1:** hold **Option** to talk, say `"Capri"` wake word, transcribe, Gemini brain, Kokoro voice, open/close/focus/minimize/maximize apps, Spotify & volume controls, memory, life folder, Claude handoff, and the dashboard at **http://127.0.0.1:8765**. Seeing/clicking inside apps (`hands`) is not on macOS yet.
