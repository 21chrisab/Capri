"""Tier 3: the Gemini brain. Fast model, minimal thinking, streamed, with tools.

One turn:
  stream Gemini -> speak sentences as they arrive (Utterance)
  if it calls tools -> play one ack, run them, send results, stream again (max 4 rounds)
Conversation history is kept as plain text for ~10 minutes, so follow-ups like
"turn it down a bit more" work.
"""
from __future__ import annotations

import datetime as dt
import itertools
import os
import threading
import time
from typing import TYPE_CHECKING

from .memory import Memory
from .tools import ACTION_TOOLS, DECLS, HAND_TOOLS, ToolBox

if TYPE_CHECKING:
    from .gen import Generation
    from .timing import TurnTimer
    from .voice import Voice

PROMPT_CAP = 6000          # characters: SOUL + USER + rules + memory index
HISTORY_TURNS = 6
IDLE_RESET_S = 600
MAX_ROUNDS = 10        # computer tasks take several observe/click rounds

RULES = """# How you work
- Now: {now}.
- Never say your own name out loud (hearing "Capri" is how the user interrupts you).
- You can control this PC with your tools. For a task, just call the tool. Don't announce it; an ack already played.
  After tools run, stay quiet unless something failed or there's an answer to say.
- Questions about current events, weather, scores or prices: use web_search, then answer in a sentence or two.
- Working inside an app: observe (window name optional) -> click / type_text by id -> observe again only if you
  need to see the result. Prefer keyboard shortcuts when you know them. If observe shows nothing useful, use
  read_screen; use look only as a last resort. Each tool result says whether the screen changed: if it didn't,
  try something else instead of repeating. Before anything irreversible (send, delete, buy), ask the user first.
  Never type passwords, PINs or card numbers. If you get stuck after a few tries, say what's blocking you.
- Agents: for big jobs (writing code or documents, deep research, comparisons, plans) or when the user asks for
  Claude, Codex or Gemini, call handoff with a complete task (include everything relevant from the conversation);
  set agent only if the user named one. Then say one short line like "Handing that off, I'll tell you when it's
  done." Don't try to do big jobs yourself by voice.
- When the user corrects how you work or sets a standing rule, save it with learn_lesson. Follow the Lessons above.
- Tasks and projects live in the user's life folder, not in memory. "What should I work on", plans, deadlines:
  check life_today / life_projects first. A to-do or "remind me to" -> life_add (inbox unless the project is clear).
- Memory: the index below lists the user's memory files. If a question touches one, recall it first.
  When the user shares something worth keeping (people, tastes, routines, plans), save it with remember,
  one short fact per call, in the best-fitting file. Don't save small talk.
# Memory index
{index}"""


class Brain:
    def __init__(self, voice: "Voice", gen: "Generation", memory: Memory, apps=None,
                 client=None, models: list[str] | None = None, hands=None, handoff=None, life=None) -> None:
        self.voice, self.gen, self.memory = voice, gen, memory
        self.models = models or [m for m in [os.environ.get("CAPRI_MODEL", "gemini-3.1-flash-lite"),
                                             *os.environ.get("CAPRI_MODEL_FALLBACKS", "").split(",")] if m.strip()]
        self.client = client if client is not None else self._client()
        self.tools = ToolBox(memory, self.web_search, apps=apps, voice=voice, hands=hands, handoff=handoff,
                             life=life)
        if hands is not None and getattr(hands, "vision", "unset") is None:
            hands.vision = self.vision
        self.history: list = []
        self.last_turn = 0.0
        self._model_ok: str | None = None

    # ---------- setup ----------
    @staticmethod
    def _client():
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            return None
        from google import genai
        return genai.Client(api_key=key)

    @property
    def ready(self) -> bool:
        return self.client is not None

    def system_prompt(self) -> str:
        head = self.memory.pinned()
        extra = "- You can't see or click inside apps on this computer yet.\n" if self.tools.hands is None else ""
        rules = RULES.format(now=dt.datetime.now().strftime("%A %B %d, %I:%M %p"), index="{index}")
        if extra:
            rules = rules.replace("# Memory index", extra + "# Memory index", 1)
        room = max(300, PROMPT_CAP - len(head) - len(rules))
        return head + "\n\n" + rules.replace("{index}", self.memory.index(max_chars=room))

    def _config(self, model: str):
        from google.genai import types as t
        thinking = (t.ThinkingConfig(thinking_level="minimal") if model.startswith("gemini-3")
                    else t.ThinkingConfig(thinking_budget=0))
        decls = DECLS if self.tools.hands is not None else [d for d in DECLS if d["name"] not in HAND_TOOLS]
        return t.GenerateContentConfig(
            system_instruction=self.system_prompt(),
            tools=[t.Tool(function_declarations=[t.FunctionDeclaration(**d) for d in decls])],
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=thinking,
            max_output_tokens=400,
        )

    def _stream(self, contents):
        """generate_content_stream on the first model that exists; remembers which one worked."""
        from google.genai import errors
        order = [self._model_ok] if self._model_ok else self.models
        last: Exception | None = None
        for model in order:
            try:
                stream = self.client.models.generate_content_stream(
                    model=model, contents=contents, config=self._config(model))
                it = iter(stream)
                first = next(it, None)          # forces the request so a bad model name fails here
                self._model_ok = model
                return model, first, it
            except errors.ClientError as e:
                if getattr(e, "code", None) == 404:
                    last = e
                    continue
                raise
        raise RuntimeError(f"none of the models are available: {self.models}") from last

    # ---------- web search (separate grounded call) ----------
    def web_search(self, query: str) -> str:
        from google.genai import types as t
        model = self._model_ok or self.models[0]
        cfg = t.GenerateContentConfig(
            tools=[t.Tool(google_search=t.GoogleSearch())],
            thinking_config=(t.ThinkingConfig(thinking_level="minimal") if model.startswith("gemini-3")
                             else t.ThinkingConfig(thinking_budget=0)),
            max_output_tokens=300)
        r = self.client.models.generate_content(
            model=model, config=cfg,
            contents=f"Search and answer in at most 3 short sentences, facts only: {query}")
        return (r.text or "no results").strip()[:1500]

    # ---------- vision (last resort for hands.look) ----------
    def vision(self, image, question: str) -> str:
        import io

        from google.genai import types as t
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=80)
        models = [m for m in [os.environ.get("CAPRI_VISION_MODEL", "gemini-3.5-flash"),
                              *os.environ.get("CAPRI_VISION_FALLBACKS", "").split(","), *self.models] if m.strip()]
        from google.genai import errors
        for model in models:
            try:
                r = self.client.models.generate_content(
                    model=model,
                    contents=[t.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), question],
                    config=t.GenerateContentConfig(max_output_tokens=200))
                return (r.text or "").strip() or "couldn't tell"
            except errors.ClientError as e:
                if getattr(e, "code", None) == 404:
                    continue
                raise
        return "no vision model available"

    # ---------- a turn ----------
    def think(self, text: str, timer: "TurnTimer") -> str:
        from google.genai import types as t
        if not self.ready:
            self.voice.say("Add your Gemini key to the dot env file and restart me.")
            return "no key"
        if time.monotonic() - self.last_turn > IDLE_RESET_S:
            self.history = []
        g = self.gen.value
        started = threading.Event()
        contents = [*self.history, t.Content(role="user", parts=[t.Part(text=text)])]
        spoken, did = [], []
        acked = False
        for _ in range(MAX_ROUNDS):
            utt = self.voice.utterance(started)
            parts, calls = [], []
            _, first, rest = self._stream(contents)
            for chunk in itertools.chain([first] if first is not None else [], rest):  # truly streamed
                if not self.gen.is_current(g):
                    utt.close()
                    return "interrupted"
                cand = chunk.candidates[0] if chunk.candidates else None
                if cand is None or cand.content is None or not cand.content.parts:
                    continue
                for part in cand.content.parts:
                    parts.append(part)
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.text and not part.thought:
                        timer.mark("llm_first_token")
                        utt.feed(part.text)
            utt.close()
            if utt.text.strip():
                spoken.append(utt.text.strip())
            contents.append(t.Content(role="model", parts=parts))
            if not calls:
                break
            if not acked and not utt.spoke and any(c.name in ACTION_TOOLS for c in calls):
                self.voice.ack("On it.", started)
                acked = True
            responses = []
            if any(c.name in HAND_TOOLS for c in calls):
                timer.kind = "computer"
            for c in calls:
                if not self.gen.is_current(g):
                    return "interrupted"
                result = self.tools.run(c.name, c.args)
                did.append(f"{c.name}({', '.join(f'{k}={v}' for k, v in (c.args or {}).items())}) -> {result}")
                responses.append(t.Part.from_function_response(name=c.name, response={"result": result}))
            timer.mark("tools_done")
            contents.append(t.Content(role="user", parts=responses))
        if started.wait(8):
            timer.mark("first_audio")
        reply = " ".join(spoken) or ("; ".join(did) if did else "")
        self._remember_turn(text, reply, did)
        return reply

    def _remember_turn(self, user: str, reply: str, did: list[str]) -> None:
        from google.genai import types as t
        summary = reply if reply else "(did it)"
        if did and not reply.startswith(did[0][:10]):
            summary = (reply + " " if reply else "") + "[tools: " + "; ".join(did)[:300] + "]"
        self.history += [t.Content(role="user", parts=[t.Part(text=user)]),
                         t.Content(role="model", parts=[t.Part(text=summary)])]
        self.history = self.history[-HISTORY_TURNS * 2:]
        self.last_turn = time.monotonic()
        try:
            self.memory.log_day(user, summary)
        except OSError:
            pass

