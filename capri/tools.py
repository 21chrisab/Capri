"""The brain's toolbox: declarations Gemini sees + the code that runs them.

Kept small on purpose (about 10 tools). Screen control via UI Automation
joins in M4.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

from .memory import Memory

DECLS: list[dict] = [
    {"name": "open_app", "description": "Open/launch an installed Windows app by name.",
     "parameters_json_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "close_app", "description": "Close an open app by name.",
     "parameters_json_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "focus_app", "description": "Bring an open app's window to the front.",
     "parameters_json_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "window", "description": "Act on the current foreground window.",
     "parameters_json_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["close", "minimize", "maximize"]}}, "required": ["action"]}},
    {"name": "media", "description": "Control music playback (Spotify desktop app). 'play' opens Spotify if needed.",
     "parameters_json_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["play", "pause", "next", "previous"]}}, "required": ["action"]}},
    {"name": "volume", "description": "Change system volume. amount is percent (step size for up/down, target for set).",
     "parameters_json_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["up", "down", "set", "mute", "unmute"]},
         "amount": {"type": "integer"}}, "required": ["action"]}},
    {"name": "web_search", "description": "Search the web for current info (news, weather, scores, facts that change).",
     "parameters_json_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "recall", "description": "Open a memory file by path from the memory index, e.g. 'people/jordan.md'.",
     "parameters_json_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "remember", "description": (
        "Save a durable fact the user told you. path picks the file: people/<name>.md for a person, "
        "topics/<topic>.md for tastes/routines/setup, projects/<name>.md for ongoing work, USER.md for core facts "
        "about the user. Reuse existing files from the index when they fit. fact is one short line."),
     "parameters_json_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "fact": {"type": "string"}}, "required": ["path", "fact"]}},
    # --- eyes + hands (M4) ---
    {"name": "observe", "description": (
        "See an app: returns the window's buttons, fields, list items and text as a numbered list. "
        "Defaults to the app the user is in; pass window to look at another open app (e.g. 'spotify'). "
        "Ids reset on every observe."),
     "parameters_json_schema": {"type": "object", "properties": {"window": {"type": "string"}}}},
    {"name": "click", "description": (
        "Click an element by id from the latest observe. For send/delete/buy-type buttons, set confirmed=true "
        "only after the user clearly said yes."),
     "parameters_json_schema": {"type": "object", "properties": {
         "id": {"type": "string"}, "confirmed": {"type": "boolean"}}, "required": ["id"]}},
    {"name": "type_text", "description": (
        "Type text. With id, clicks that field first; without id, types into whatever has focus. "
        "enter=true presses Enter after. Never used for passwords/PINs/card numbers."),
     "parameters_json_schema": {"type": "object", "properties": {
         "text": {"type": "string"}, "id": {"type": "string"}, "enter": {"type": "boolean"}}, "required": ["text"]}},
    {"name": "press_keys", "description": "Press a key or shortcut, e.g. 'ctrl+t', 'enter', 'alt+tab', 'ctrl+l'.",
     "parameters_json_schema": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}},
    {"name": "scroll", "description": "Scroll the current window (or over element id) up or down.",
     "parameters_json_schema": {"type": "object", "properties": {
         "direction": {"type": "string", "enum": ["up", "down"]}, "id": {"type": "string"},
         "amount": {"type": "integer"}}, "required": ["direction"]}},
    {"name": "read_screen", "description": "Read all visible text in a window (OCR), for reading articles, chats, errors.",
     "parameters_json_schema": {"type": "object", "properties": {"window": {"type": "string"}}}},
    {"name": "look", "description": (
        "LAST RESORT: send a screenshot of one window to a vision model with a question. Only when observe and "
        "read_screen can't answer (images, charts, games, layout)."),
     "parameters_json_schema": {"type": "object", "properties": {
         "question": {"type": "string"}, "window": {"type": "string"}}, "required": ["question"]}},
    {"name": "list_windows", "description": "List open windows and which monitor each is on.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    # --- handoff to a background agent (Claude / Codex / Gemini) ---
    {"name": "handoff", "description": (
        "Hand a big task to a background agent (the user can keep talking to you). Use when the user asks for "
        "Claude, Codex or Gemini, or for work too big for voice: writing code or documents, deep research, "
        "comparisons, plans. task must be complete and self-contained: include every relevant detail from the "
        "conversation. title is 3-6 words. agent: only set it when the user names one; otherwise leave it out."),
     "parameters_json_schema": {"type": "object", "properties": {
         "task": {"type": "string"}, "title": {"type": "string"},
         "agent": {"type": "string", "enum": ["claude", "codex", "gemini"]}}, "required": ["task"]}},
    {"name": "handoff_status", "description": "Check on the background agents' jobs.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    {"name": "handoff_result", "description": "Read a finished Claude job's full result to answer questions about it.",
     "parameters_json_schema": {"type": "object", "properties": {"which": {"type": "string"}}}},
    {"name": "handoff_open", "description": "Open a finished Claude job's result file on screen for the user.",
     "parameters_json_schema": {"type": "object", "properties": {"which": {"type": "string"}}}},
    # --- life OS (tasks/projects in the user's life folder) ---
    {"name": "life_today", "description": "Read today.md: the user's Top 3, other tasks for today and calendar.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    {"name": "life_projects", "description": "List the user's projects with status and next action.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    {"name": "life_project", "description": "Read one project file (tasks, notes) by name, e.g. 'skatebored'.",
     "parameters_json_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "life_add", "description": (
        "Add a to-do. Without project it goes to the inbox (default when unsure). due is YYYY-MM-DD."),
     "parameters_json_schema": {"type": "object", "properties": {
         "text": {"type": "string"}, "project": {"type": "string"}, "due": {"type": "string"}}, "required": ["text"]}},
    {"name": "life_done", "description": "Check off an open task by a few words from it.",
     "parameters_json_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}},
    {"name": "life_dashboard", "description": "Rebuild and open the user's life dashboard in the browser.",
     "parameters_json_schema": {"type": "object", "properties": {}}},
    {"name": "set_mode", "description": (
        "Switch the workflow mode when the user asks: general, coding, assignments or content_studio. "
        "The mode's rules apply to you and to handed-off agents."),
     "parameters_json_schema": {"type": "object", "properties": {"mode": {"type": "string"}}, "required": ["mode"]}},
    {"name": "learn_lesson", "description": (
        "Save a lasting correction or workflow rule the user gives you (\"don't do X\", \"always Y\") to "
        "LESSONS.md. One short imperative sentence. Not for facts about the user: use remember for those."),
     "parameters_json_schema": {"type": "object", "properties": {"lesson": {"type": "string"}}, "required": ["lesson"]}},
    {"name": "forget", "description": "Remove lines containing text from a memory file when the user asks to forget it.",
     "parameters_json_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "text": {"type": "string"}}, "required": ["path", "text"]}},
]

HAND_TOOLS = {"observe", "click", "type_text", "press_keys", "scroll", "read_screen", "look", "list_windows"}
LIFE_TOOLS = {"life_today", "life_projects", "life_project", "life_add", "life_done", "life_dashboard"}
ACTION_TOOLS = {"open_app", "close_app", "focus_app", "window", "media", "volume",
                "life_add", "life_done", "life_dashboard"} | HAND_TOOLS


class ToolBox:
    def __init__(self, memory: Memory, search: Callable[[str], str], apps=None, voice=None, hands=None,
                 handoff=None, life=None) -> None:
        self.memory, self.search, self.apps, self.voice, self.hands = memory, search, apps, voice, hands
        self.handoff = handoff
        self.life = life
        self.on_hands: Callable[[], None] | None = None   # overlay hook: "using the computer"

    def run(self, name: str, args: dict | None) -> str:
        args = dict(args or {})
        if name in HAND_TOOLS:
            if self.hands is None:
                return "screen control isn't available"
            if self.on_hands:
                self.on_hands()
            try:
                return str(getattr(self.hands, name)(**args))
            except Exception as e:
                return f"error: {type(e).__name__}: {e}"
        if name in LIFE_TOOLS and self.life is None:
            return "the life folder isn't set up (set [life] dir in config.toml)"
        try:
            fn = getattr(self, f"t_{name}")
        except AttributeError:
            return f"unknown tool {name}"
        try:
            return str(fn(**args))
        except Exception as e:  # tools report errors to the model instead of crashing the turn
            return f"error: {type(e).__name__}: {e}"

    # --- handoff ---
    def _ho(self):
        if self.handoff is None:
            raise RuntimeError("handoff to agents isn't set up")
        return self.handoff

    def t_handoff(self, task: str, title: str | None = None, agent: str | None = None) -> str:
        return self._ho().start(task, title, agent=agent)
    def t_handoff_status(self) -> str: return self._ho().status()
    def t_handoff_result(self, which: str = "latest") -> str: return self._ho().result(which)
    def t_handoff_open(self, which: str = "latest") -> str: return self._ho().open(which)

    # --- workflow mode & lessons ---
    def t_set_mode(self, mode: str) -> str: return self.memory.set_mode(mode)
    def t_learn_lesson(self, lesson: str) -> str: return self.memory.learn_lesson(lesson)

    # --- life OS ---
    def t_life_today(self) -> str: return self.life.today_text()
    def t_life_projects(self) -> str: return self.life.projects_text()
    def t_life_project(self, name: str) -> str: return self.life.project_text(name)
    def t_life_add(self, text: str, project: str = "", due: str = "") -> str:
        return self.life.add_task(text, project, due)
    def t_life_done(self, task: str) -> str: return self.life.done(task)
    def t_life_dashboard(self) -> str: return self.life.dashboard()

    # --- memory / web (no Windows needed) ---
    def t_recall(self, path: str) -> str: return self.memory.recall(path)
    def t_remember(self, path: str, fact: str) -> str: return self.memory.remember(path, fact)
    def t_forget(self, path: str, text: str) -> str: return self.memory.forget(path, text)
    def t_web_search(self, query: str) -> str: return self.search(query)
    def t_get_time(self) -> str: return dt.datetime.now().strftime("%A %I:%M %p")

    # --- OS controls ---
    def t_open_app(self, name: str) -> str:
        from .sysapps import launch
        app = self.apps.find(name)
        if app is None:
            return f"no installed app matches '{name}'"
        launch(app)
        return f"opened {app.name}"

    def t_close_app(self, name: str) -> str:
        from . import sysctl
        hwnd = sysctl.find_window(name)
        if hwnd is None:
            return f"{name} isn't open"
        sysctl.close(hwnd)
        return f"closed {name}"

    def t_focus_app(self, name: str) -> str:
        from . import sysctl
        hwnd = sysctl.find_window(name)
        if hwnd is None:
            return f"{name} isn't open"
        sysctl.focus(hwnd)
        return f"focused {name}"

    def t_window(self, action: str) -> str:
        from . import sysctl
        hwnd = sysctl.foreground()
        {"close": sysctl.close, "minimize": sysctl.minimize, "maximize": sysctl.maximize}[action](hwnd)
        return f"{action}d the window"

    def t_media(self, action: str) -> str:
        from . import sysctl
        from .actions import spotify_play, spotify_playing
        if action == "play":
            return spotify_play(self.apps)
        if action == "pause":
            if spotify_playing() is not False:
                sysctl.media_play_pause()
            return "paused"
        (sysctl.media_next if action == "next" else sysctl.media_prev)()
        return f"skipped {action}"

    def t_volume(self, action: str, amount: int | None = None) -> str:
        from . import sysctl
        if action == "up":
            sysctl.volume_up(amount or 10)
        elif action == "down":
            sysctl.volume_down(amount or 10)
        elif action == "set":
            sysctl.volume_set(max(0, min(100, amount if amount is not None else 50)))
        else:
            sysctl.mute_toggle()
        return f"volume {action}{f' {amount}' if amount is not None else ''}"
