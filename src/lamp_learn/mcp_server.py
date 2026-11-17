"""`lamp-learn-mcp`: language lessons on the paired lamp, as MCP tools for an
agent such as Claude Code or Codex.

    claude mcp add lamp-learn -- lamp-learn-mcp
    codex mcp add lamp-learn -- lamp-learn-mcp

The agent plans and writes lessons; the lamp says them, listens for each
phrase and lights up when it hears it. The lamp never hands over what the
learner said, only which of the lesson's phrases was heard, so the agent
scores by phrase, and open talk goes to the lamp's own model (`practice_talk`).

Pair first, with the lampos CLI (`lamp pair CODE --pin PIN --host HOST`), as
for lamp-mcp. Add `--fake` to try every tool on an in-process fake lamp with a
pretend learner.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from lampos import Lamp, LampError
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from . import lesson as lessons
from .coach import Session
from .lesson import SAY_MAX_BYTES, Item, LessonError
from .progress import Progress


def lessons_folder() -> Path:
    home = os.environ.get("LAMP_LEARN_HOME") or Path.home() / ".lamp-learn"
    return Path(home) / "lessons"


class Link:
    """The connection and the open lesson. One lesson listens at a time: a
    new lesson closes the connection and opens another, so the lamp is asked
    to change phrases once a lesson."""

    def __init__(self, lamp_id: str | None, host: str | None, fake: bool, accuracy: float):
        self.lamp_id, self.host, self.fake, self.accuracy = lamp_id, host, fake, accuracy
        self.lamp = None
        self.session: Session | None = None
        self._fake = None
        self._learner = None
        self.lock = asyncio.Lock()

    async def _connect(self):
        if self.fake:
            from lampos.testing import FakeLamp

            if self._fake is None:
                self._fake = FakeLamp(grants="all")
                await self._fake.__aenter__()
            return await self._fake.connect()
        return await Lamp.connect(self.lamp_id, host=self.host)

    async def close(self) -> None:
        if self._learner is not None:
            await self._learner.stop()
            self._learner = None
        if self.lamp is not None:
            await self.lamp.close()
        self.lamp, self.session = None, None

    async def session_for(self, lesson_id: str, language: str | None = None, listen_s: float = 8.0, learner: str = "default") -> Session:
        found = lessons.find(lesson_id, lessons_folder())
        current = self.session
        if current is not None and current.lesson.id == found.id and not getattr(self.lamp, "closed", False):
            if language:
                current.language = language
            current.listen_s = listen_s
            current.progress = Progress(learner=learner)
            return current
        await self.close()
        self.lamp = await self._connect()
        self.session = await Session.open(
            self.lamp, found, progress=Progress(learner=learner), language=language, listen_s=listen_s
        )
        if self.fake:
            from .learner import FakeLearner

            self._learner = FakeLearner(self._fake, found, accuracy=self.accuracy).start()
        return self.session

    async def any_lamp(self):
        if self.lamp is None:
            self.lamp = await self._connect()
        return self.lamp


def build(link: Link) -> FastMCP:
    server = FastMCP(
        "lamp-learn",
        instructions=(
            "Language lessons on the user's lamp. Workflow: list_lessons → start_lesson → drill/teach each item "
            "(or run_lesson for the whole thing) → progress. The lamp only reports which registered phrase it heard, "
            "never free speech: score with drill, and use practice_talk for open conversation (the lamp's own model "
            "answers; not scored). To make a new lesson, write TOML with write_lesson; check limits with check_lesson: "
            "each phrase to listen for has at least 8 letters and at most 64 characters, 16 phrases a lesson at most."
        ),
    )

    async def guarded(fn):
        async with link.lock:
            try:
                return await fn()
            except LessonError as error:
                raise ToolError(str(error)) from None
            except LampError as error:
                raise ToolError(f"the lamp refused: {error}") from None

    @server.tool()
    async def list_lessons() -> list[dict]:
        """Every lesson: the built-in ones and those written with write_lesson."""
        found = dict(lessons.builtin())
        folder = lessons_folder()
        if folder.exists():
            for path in sorted(folder.glob("*.toml")):
                try:
                    own = lessons.load(path)
                    found[own.id] = own
                except LessonError:
                    continue
        return [l.summary() for l in found.values()]

    @server.tool()
    async def show_lesson(lesson: str) -> dict:
        """One lesson's items in full (phrase, meaning, example, hints, talk prompts)."""
        try:
            found = lessons.find(lesson, lessons_folder())
        except LessonError as error:
            raise ToolError(str(error)) from None
        return {**found.summary(), "intro": found.intro, "items": [item.__dict__ for item in found.items]}

    @server.tool()
    async def check_lesson(toml: str) -> dict:
        """Check a lesson written as TOML against the lamp's limits, without saving it."""
        try:
            import tomllib
        except ImportError:  # pragma: no cover
            import tomli as tomllib
        try:
            found = lessons.parse(tomllib.loads(toml))
        except (LessonError, tomllib.TOMLDecodeError) as error:
            raise ToolError(str(error)) from None
        return {"ok": True, "id": found.id, "items": len(found.items), "phrases": found.phrases}

    @server.tool()
    async def write_lesson(toml: str) -> dict:
        """Check a lesson written as TOML and save it among the user's own lessons
        (by its id). Format: id, title, target (e.g. "en"), level, intro; then
        [[items]] with id, kind ("repeat", "word" or "talk"), phrase (repeat/word),
        meaning and example (word), prompt (talk), accept (other ways the phrase may
        be heard) and hint (a table by language, e.g. hint = { vi = "..." })."""
        checked = await check_lesson(toml)
        folder = lessons_folder()
        folder.mkdir(parents=True, exist_ok=True)
        # parse() already holds the id to [A-Za-z0-9_-]; check the path too.
        base = folder.resolve()
        path = (base / f"{checked['id']}.toml").resolve()
        if path.parent != base:
            raise ToolError("id: not a plain file name")
        path.write_text(toml, encoding="utf-8")
        return {**checked, "saved": str(path)}

    @server.tool()
    async def start_lesson(lesson: str, language: str | None = None, listen_s: float = 8.0, learner: str = "default") -> dict:
        """Open a lesson on the lamp: registers its phrases so the lamp listens for
        them. `language` is the learner's own (for the coach's lines and hints;
        default the lamp's). Returns the items, and those due for review."""

        async def go():
            session = await link.session_for(lesson, language, listen_s, learner)
            return {
                **session.lesson.summary(),
                "language": session.language,
                "due": session.progress.due(session.lesson) if session.progress else [],
            }

        return await guarded(go)

    @server.tool()
    async def drill(lesson: str, item: str, learner: str = "default") -> dict:
        """Ask the learner to say an item's phrase and listen for it (two tries).
        Returns right/missed, tries and seconds; the score is kept."""

        async def go():
            session = await link.session_for(lesson, learner=learner)
            found = session.lesson.item(item)
            if found.kind == "talk":
                raise ToolError("a talk item isn't scored: use practice_talk")
            result = await session.drill(found)
            session.progress.record(session.lesson.id, found.id, bool(result.right))
            return result.as_dict()

        return await guarded(go)

    @server.tool()
    async def teach(lesson: str, item: str) -> dict:
        """Say a word item's phrase, its meaning (the hint in the learner's language
        when there is one) and its example, without scoring. Follow with drill."""

        async def go():
            session = await link.session_for(lesson)
            await session.teach(session.lesson.item(item))
            return {"taught": item}

        return await guarded(go)

    @server.tool()
    async def run_lesson(lesson: str, due_only: bool = False, language: str | None = None, learner: str = "default") -> dict:
        """Run a whole lesson on the lamp, item by item; takes a minute or more.
        `due_only`: only the items due for review, then the talks."""

        async def go():
            session = await link.session_for(lesson, language, learner=learner)
            return (await session.run(due_only=due_only)).as_dict()

        return await guarded(go)

    @server.tool()
    async def practice_talk(prompt: str) -> dict:
        """Open a practice conversation: the lamp says `prompt` and its own model
        answers the learner's next line. Not scored: nothing heard comes back."""
        if len(prompt.encode()) > SAY_MAX_BYTES:
            raise ToolError(f"prompt: at most {SAY_MAX_BYTES} bytes")

        async def go():
            lamp = await link.any_lamp()
            done = await lamp.start_turn(prompt, wait=True)
            return {"how": done.get("how"), "why": done.get("why")}

        return await guarded(go)

    @server.tool()
    async def free_talk(topic: str = "free", level: str = "B1", most_turns: int = 20) -> dict:
        """A real-time English conversation with the lamp's own model as tutor.
        Puts the English Tutor skill on the lamp (once), opens
        the practice and keeps it going while the learner talks. Topics: free,
        daily, travel, food, work, hobbies, technology, opinions; levels A1-C2.
        Not scored: what was said stays on the lamp."""
        from .freetalk import free_talk as talk

        async def go():
            lamp = await link.any_lamp()
            return (await talk(lamp, topic, level, most_turns=most_turns)).as_dict()

        return await guarded(go)

    @server.tool()
    async def say(text: str) -> dict:
        """Say one line in the lamp's voice, word for word (an explanation, praise,
        a correction). At most 900 bytes, one line."""
        if len(text.encode()) > SAY_MAX_BYTES:
            raise ToolError(f"text: at most {SAY_MAX_BYTES} bytes")

        async def go():
            lamp = await link.any_lamp()
            done = await lamp.say(" ".join(text.split()), wait=True)
            return {"how": done.get("how"), "why": done.get("why")}

        return await guarded(go)

    @server.tool()
    async def progress(learner: str = "default") -> list[dict]:
        """The learner's score per lesson: seen, mastered (box 4 or 5), due, right, wrong."""
        found = await list_lessons()
        return Progress(learner=learner).report([lessons.find(l["id"], lessons_folder()) for l in found])

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lamp-learn-mcp")
    parser.add_argument("--lamp", help="which paired lamp (default: the only one)")
    parser.add_argument("--host", help="the lamp's address, if not the pairing's")
    parser.add_argument("--fake", action="store_true", help="an in-process fake lamp with a pretend learner")
    parser.add_argument("--accuracy", type=float, default=0.8, help="the pretend learner's chance to answer right (--fake)")
    args = parser.parse_args(argv)
    link = Link(args.lamp, args.host, args.fake, args.accuracy)
    build(link).run()


if __name__ == "__main__":
    sys.exit(main())
