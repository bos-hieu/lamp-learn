"""The coach: runs a lesson on a lamp through the lampos SDK.

    async with await Lamp.connect() as lamp:
        session = await Session.open(lamp, lesson.find("en-a1-greetings"))
        result = await session.run()

What the lamp lets a program do, and so what the coach can do:

- say a line in the lamp's voice (`speak`);
- learn that one of its own registered phrases was heard, never what else was
  said (`hear`): a `repeat` or `word` item is right when its phrase is heard
  within `listen_s` after the coach asked for it;
- open a turn the lamp's own model answers (`speak` + `hear`): a `talk` item,
  never scored, as nothing heard comes back;
- light and gestures for feedback (`light`, `body`).

One session listens for one lesson's phrases (16 at most, the lamp's cap per
computer). To change lesson, close the connection and open a new one, so the
phrases change once and the lamp's 6-changes-a-minute rate is never near.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .lesson import Item, Lesson
from .progress import Progress

# What the coach says, in the learner's language. A lesson's own words (its
# phrases, meanings and examples) are in the target language.
WORDS = {
    "en": {
        "say_after_me": "Say after me: {phrase}",
        "again": "Once more: {phrase}",
        "repeat": "Again: {phrase}",
        "stopped": "Okay, we'll stop here.",
        "right": ["Great!", "Well done!", "Perfect!", "Nice!"],
        "not_heard": "Let's hear it once more: {phrase}. We'll try it again later.",
        "new_word": "New words: {phrase}.",
        "means": "It means: {meaning}.",
        "example": "For example: {example}",
        "start": "Let's start: {title}.",
        "done": "Lesson done. You got {right} of {scored}.",
        "talk_done": "Nice talking with you.",
    },
    "vi": {
        "say_after_me": "Bạn hãy nói theo: {phrase}",
        "again": "Nói lại nhé: {phrase}",
        "repeat": "Nghe lại: {phrase}",
        "stopped": "Được, mình dừng ở đây nhé.",
        "right": ["Giỏi lắm!", "Tốt lắm!", "Chính xác!", "Hay quá!"],
        "not_heard": "Nghe lại nhé: {phrase}. Lát nữa mình thử lại.",
        "new_word": "Từ mới: {phrase}.",
        "means": "Nghĩa là: {meaning}.",
        "example": "Ví dụ: {example}",
        "start": "Bắt đầu nào: {title}.",
        "done": "Xong bài rồi. Bạn đúng {right} trên {scored} câu.",
        "talk_done": "Nói chuyện với bạn vui lắm.",
    },
}


@dataclass
class ItemResult:
    item: str
    kind: str
    right: bool | None  # None: not scored (talk)
    tries: int = 0
    seconds: float = 0.0
    how: str = ""  # a talk's done: finished, cut_short, dropped

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class LessonResult:
    lesson: str
    items: list[ItemResult] = field(default_factory=list)
    stopped: bool = False  # the learner stopped it

    @property
    def scored(self) -> int:
        return sum(r.right is not None for r in self.items)

    @property
    def right(self) -> int:
        return sum(bool(r.right) for r in self.items)

    def as_dict(self) -> dict:
        return {"lesson": self.lesson, "right": self.right, "scored": self.scored, "items": [r.as_dict() for r in self.items]}


# Said during a lesson, it ends the lesson; so does stroking the lamp's head.
STOP_PHRASE = "stop english lesson"


class _Stopped(Exception):
    pass


class Session:
    """One lesson on one connection."""

    def __init__(
        self,
        lamp,
        lesson: Lesson,
        *,
        progress: Progress | None = None,
        language: str | None = None,
        listen_s: float = 8.0,
        tries: int = 2,
        feedback: bool = True,
        log: Callable[[str], None] | None = None,
        talk_turns: int = 3,
        talk_wait: Callable[[Item], Awaitable[None]] | None = None,
        sounds: bool = True,
        ring_progress: bool = True,
        tap_repeat: bool = True,
    ):
        self.lamp = lamp
        self.lesson = lesson
        self.progress = progress
        # The learner's language for the coach's own lines and the hints: the
        # owner's language on the lamp, unless told otherwise.
        self.language = language or getattr(lamp, "language", "en") or "en"
        self.listen_s = listen_s
        self.tries = max(1, tries)
        self.feedback = feedback
        self.log = log or (lambda _line: None)
        # A practice talk is this many exchanges at most: the lamp's model
        # answers one line a turn, and the talk ends when nobody speaks.
        self.talk_turns = max(1, talk_turns)
        # Awaited after a talk's first turn, in place of the turns that follow:
        # a stand-in with no model (lamp-sim) lets the learner talk, then go on.
        self.talk_wait = talk_wait
        # A chime for right and a soft tone for a miss, kept on the lamp as
        # this computer's clips; off if the lamp refuses them.
        self.sounds = sounds
        # The lesson's progress drawn round the ring: one segment an item.
        self.ring_progress = ring_progress and bool(getattr(lamp, "ring_pixels", None))
        # A tap on the lamp's head while it listens says the phrase again.
        self.tap_repeat = tap_repeat
        self._heard: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._stopped = False
        self._marks: list[str] = []  # each item's segment colour so far

    @classmethod
    async def open(cls, lamp, lesson: Lesson, **kwargs) -> "Session":
        session = cls(lamp, lesson, **kwargs)
        await session.listen()
        return session

    async def listen(self) -> None:
        """Register the lesson's phrases with the lamp, once. Raises the
        lamp's refusal, such as `no_grant` "not granted: hear"."""
        phrases = list(self.lesson.phrases)
        # The stop phrase too, while the lamp's cap leaves room for it.
        if phrases and STOP_PHRASE not in phrases and len(phrases) < 16:
            phrases.append(STOP_PHRASE)
        if phrases:
            await self.lamp.on("phrase.heard", self._on_phrase, phrases=phrases)
        if self.tap_repeat:
            try:
                await self.lamp.on("touch", self._on_touch)
            except Exception as error:  # a lamp from before touch events: no tap, the rest works
                self.tap_repeat = False
                self.log(f"tap to repeat is off: {error}")
        if self.sounds:
            await self._keep_sounds()

    async def _keep_sounds(self) -> None:
        """Put the coach's clips on the lamp unless the same ones are there."""
        from .sounds import CLIPS

        try:
            kept = {clip["name"]: clip.get("bytes") for clip in await self.lamp.sounds()}
            for name, make in CLIPS.items():
                data = make()
                if kept.get(name) != len(data):
                    await self.lamp.upload_sound(name, data)
                    self.log(f"sound kept on the lamp: {name}")
        except Exception as error:
            self.sounds = False
            self.log(f"sounds are off: {error}")

    def _on_phrase(self, event: dict) -> None:
        phrase = event.get("phrase")
        if isinstance(phrase, str):
            self.log(f"heard: {phrase}")
            if phrase.lower() == STOP_PHRASE:
                self._stop()
                return
            self._heard.put_nowait(("heard", phrase.lower()))

    def _on_touch(self, event: dict) -> None:
        if event.get("kind") == "tap":
            self._heard.put_nowait(("tap", "tap"))
        elif event.get("kind") == "pet":
            self.log("pet: stop")
            self._stop()

    def _stop(self) -> None:
        self._stopped = True
        self._heard.put_nowait(("stop", "stop"))

    async def _show_progress(self, current: int | None = None) -> None:
        """Draw the lesson round the ring: green right, orange missed, purple a
        talk, white the item now, dim the ones to come. Lasts 30 s, the most
        one light may; drawn again at each item."""
        if not self.ring_progress:
            return
        pixels = self.lamp.ring_pixels
        total = getattr(self, "_total", 0) or len(self.lesson.items)
        colours = []
        for n in range(total):
            if n < len(self._marks):
                colours.append(self._marks[n])
            elif n == current:
                colours.append("#ffffff")
            else:
                colours.append("#1a1a1a")
        ring = []
        for p in range(pixels):
            n = p * total // pixels
            # One dark pixel between segments when each has room for it.
            edge = (p + 1) * total // pixels != n
            ring.append("#000000" if edge and pixels // total >= 3 else colours[n])
        try:
            await self.lamp.ring(ring, for_ms=30_000, brightness=40)
        except Exception as error:
            self.ring_progress = False
            self.log(f"ring progress is off: {error}")

    async def _play(self, name: str) -> None:
        if not self.sounds:
            return
        try:
            await self.lamp.sound(name)
        except Exception as error:
            self.log(f"sound refused: {error}")

    def _word(self, key: str, **fields) -> str:
        words = WORDS.get(self.language, WORDS["en"])
        text = words[key]
        if isinstance(text, list):
            text = random.choice(text)
        return text.format(**fields)

    async def say(self, text: str) -> dict:
        self.log(f"say: {text}")
        return await self.lamp.say(text, wait=True)

    async def _cheer(self, right: bool) -> None:
        if not self.feedback:
            return
        self.log(f"feedback: {'right' if right else 'missed'}")
        from .sounds import MISS, RIGHT

        await self._play(RIGHT if right else MISS)
        try:
            if right:
                await self.lamp.light("green", 3, 2_500, pattern="pulse")
                await self.lamp.gesture("nod")
            else:
                await self.lamp.light("orange", 2, 2_500, pattern="breathe")
                await self.lamp.gesture("caring")
        except Exception as error:  # feedback is a nicety: a refused light or move never ends a lesson
            self.log(f"feedback refused: {error}")

    def _drain(self) -> None:
        while not self._heard.empty():
            self._heard.get_nowait()
        if self._stopped:  # a stop while the lamp spoke still counts
            self._heard.put_nowait(("stop", "stop"))

    async def _wait_for(self, phrases: list[str]) -> str:
        """`right` when a phrase is heard, `tap` on a tap on the head, else
        `timeout` after `listen_s`."""
        wanted = {p.lower() for p in phrases}
        deadline = time.monotonic() + self.listen_s
        while (left := deadline - time.monotonic()) > 0:
            try:
                kind, heard = await asyncio.wait_for(self._heard.get(), timeout=left)
            except asyncio.TimeoutError:
                return "timeout"
            if kind in ("tap", "stop"):
                return kind
            if heard in wanted:
                return "right"
        return "timeout"

    # Taps an item may ask for, past which a tap is let go by.
    MOST_REPEATS = 5

    async def drill(self, item: Item) -> ItemResult:
        """Ask for the item's phrase and listen for it, up to `tries` times. A
        tap on the head says the phrase again and listens afresh, without
        using a try."""
        start = time.monotonic()
        result = ItemResult(item.id, item.kind, False)
        repeats = 0
        for attempt in range(1, self.tries + 1):
            key = "say_after_me" if attempt == 1 else "again"
            await self.say(self._word(key, phrase=item.phrase))
            # Anything heard while the lamp spoke is not the learner's answer.
            self._drain()
            result.tries = attempt
            self.log(f"listen: {' | '.join(item.phrases)}")
            outcome = await self._wait_for(item.phrases)
            while outcome == "tap" and repeats < self.MOST_REPEATS and not self._stopped:
                repeats += 1
                self.log("tap: repeat")
                await self.say(self._word("repeat", phrase=item.phrase))
                self._drain()
                self.log(f"listen: {' | '.join(item.phrases)}")
                outcome = await self._wait_for(item.phrases)
            if outcome == "stop" or self._stopped:
                raise _Stopped
            if outcome == "right":
                result.right = True
                await self._cheer(True)
                await self.say(self._word("right"))
                break
        else:
            await self._cheer(False)
            await self.say(self._word("not_heard", phrase=item.phrase))
        result.seconds = round(time.monotonic() - start, 2)
        return result

    async def teach(self, item: Item) -> None:
        """Say a word item's phrase, its meaning and an example."""
        await self.say(self._word("new_word", phrase=item.phrase))
        hint = item.hint_for(self.language)
        meaning = hint or item.meaning
        if meaning:
            await self.say(self._word("means", meaning=meaning))
        if item.example:
            await self.say(self._word("example", example=item.example))

    async def talk(self, item: Item) -> ItemResult:
        """A practice talk: the lamp says the prompt and its own model answers
        the learner's next line. Nothing heard comes back, so it is not scored."""
        start = time.monotonic()
        self.log(f"turn: {item.prompt}")
        done = await self.lamp.start_turn(item.prompt, wait=True)
        how = done.get("how", "") if isinstance(done, dict) else ""
        turns = 1
        if self.talk_wait is not None:
            await self.talk_wait(item)
        else:
            # Keep the talk going: each new turn answers the learner's next
            # line, until they stop talking (`dropped`, "nobody spoke") or a
            # turn is cut short.
            while how == "finished" and turns < self.talk_turns:
                self.log("turn: (listening for your next line)")
                done = await self.lamp.start_turn(None, wait=True)
                how = done.get("how", "") if isinstance(done, dict) else ""
                turns += 1
        self.log("talk: done")
        return ItemResult(item.id, item.kind, None, turns, round(time.monotonic() - start, 2), how)

    async def run_item(self, item: Item) -> ItemResult:
        if item.kind == "talk":
            return await self.talk(item)
        if item.kind == "word":
            await self.teach(item)
        result = await self.drill(item)
        if self.progress is not None:
            self.progress.record(self.lesson.id, item.id, bool(result.right))
        return result

    async def run(self, item_ids: list[str] | None = None, *, due_only: bool = False) -> LessonResult:
        """Run the lesson: every item in order, the ones named, or with
        `due_only` the scored items due for review (then the talks)."""
        if item_ids:
            items = [self.lesson.item(i) for i in item_ids]
        elif due_only and self.progress is not None:
            due = self.progress.due(self.lesson)
            items = [self.lesson.item(i) for i in due] + [i for i in self.lesson.items if i.kind == "talk"]
        else:
            items = list(self.lesson.items)
        result = LessonResult(self.lesson.id)
        self._marks, self._total = [], len(items)
        await self._show_progress(0)
        await self.say(self.lesson.intro or self._word("start", title=self.lesson.title))
        for n, item in enumerate(items):
            if self._stopped:
                break
            await self._show_progress(n)
            try:
                done = await self.run_item(item)
            except _Stopped:
                break
            result.items.append(done)
            self._marks.append({True: "#22c55e", False: "#f59e0b", None: "#8b5cf6"}[done.right])
        if self._stopped:
            # The item it stopped on isn't scored; the ones done are kept.
            result.stopped = True
            await self.say(self._word("stopped"))
            return result
        await self._show_progress()
        if result.scored:
            await self.say(self._word("done", right=result.right, scored=result.scored))
        return result
