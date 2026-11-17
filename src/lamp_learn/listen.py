"""Listening mode: start a lesson by talking to the lamp.

    lamp-learn listen

Leave it running. It registers a few phrases with the lamp and waits:
- "start my English lesson" (or "English lesson"): today's lesson. Reviews
  that are due come first, from the lesson with the most; with none due, the
  next lesson not started yet, in level order.
- "beginner / intermediate / advanced English lesson": the same, from
  A1-A2, B1-B2 or C1-C2 only ("start my advanced English lesson" works too).
- "English free talk": a conversation with the lamp's own model (free talk).

It hears these through the lamp's own phrase matching, as a lesson's answers
are: nothing else you say reaches the computer. While a lesson runs, the
waiting connection is closed, so the lesson's phrases have the lamp to
themselves; it opens again after. If the lamp goes away (an update restarts
it), it connects again, waiting longer each time, up to a minute.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from . import lesson as lessons
from .coach import Session
from .progress import Progress

START_LESSON = ("start my english lesson", "english lesson")
# A level said with the lesson: whole words, which the lamp hears better
# than "B1".
LEVEL_LESSON = {
    "beginner english lesson": ("A1", "A2"),
    "intermediate english lesson": ("B1", "B2"),
    "advanced english lesson": ("C1", "C2"),
}
FREE_TALK = ("english free talk",)
WAKE = START_LESSON + tuple(LEVEL_LESSON) + FREE_TALK

# Phrases from one line arrive together: how long to wait for a second.
SAME_LINE_S = 0.8

# After a wake phrase, the lamp's model says a short "starting" line
# (english-lesson); the lesson begins after it.
START_PAUSE_S = 3.0

LEVEL_ORDER = {level: n for n, level in enumerate(("A1", "A2", "B1", "B2", "C1", "C2"))}


def pick(progress: Progress, levels: tuple[str, ...] | None = None) -> tuple[lessons.Lesson, bool] | None:
    """Today's lesson and whether to run only its due items: the started lesson
    with the most reviews due, else the next one not started (A1 first)."""
    found = [l for l in lessons.builtin().values() if not levels or l.level in levels]
    found.sort(key=lambda l: (LEVEL_ORDER.get(l.level, 9), l.id))
    rows = {r["lesson"]: r for r in progress.report(found)}
    reviews = []
    for l in found:
        r = rows[l.id]
        review_due = r["due"] - (r["items"] - r["seen"])  # due counts new items too
        if r["seen"] and review_due > 0:
            reviews.append((review_due, l))
    if reviews:
        reviews.sort(key=lambda pair: -pair[0])
        return reviews[0][1], True
    for l in found:
        if rows[l.id]["seen"] < rows[l.id]["items"]:
            return l, False
    return (found[0], False) if found else None


async def _connect(connect, tries: int = 6):
    """Connect, trying again briefly: a relay or a lamp that just closed the
    last connection may refuse the next for a moment."""
    for n in range(tries):
        try:
            return await connect()
        except (ConnectionError, OSError):
            if n == tries - 1:
                raise
            await asyncio.sleep(0.5 * (n + 1))


async def wait_for_wake(lamp) -> str:
    """Register the wake phrases and return the one heard. A line can hold
    two ("start my advanced English lesson" holds "English lesson" too):
    after the first, a moment more is given, and a level's phrase wins."""
    loop = asyncio.get_running_loop()
    first: asyncio.Future = loop.create_future()
    got: list[str] = []

    def on_phrase(event: dict) -> None:
        phrase = str(event.get("phrase", "")).lower()
        if phrase in WAKE:
            got.append(phrase)
            if not first.done():
                first.set_result(phrase)

    await lamp.on("phrase.heard", on_phrase, phrases=list(WAKE))
    # A connection that drops (the lamp restarts, a relay or the network goes)
    # ends the wait, so the caller connects again instead of waiting forever.
    closed = asyncio.ensure_future(lamp.wait_closed())
    try:
        await asyncio.wait({first, closed}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        closed.cancel()
    if not first.done():
        raise ConnectionError("the lamp's connection closed while waiting")
    await asyncio.sleep(SAME_LINE_S)
    for phrase in got:
        if phrase in LEVEL_LESSON:
            return phrase
    return got[0]


async def serve(
    connect: Callable,
    *,
    progress: Progress | None = None,
    levels: tuple[str, ...] | None = None,
    language: str | None = None,
    listen_s: float = 15,
    talk_level: str = "B1",
    log: Callable[[str], None] = print,
    once: bool = False,
) -> None:
    """Wait for a wake phrase, run what it asks, and wait again. `connect` is
    a coroutine function giving a connected lamp (an async context manager).
    With `once`, returns after the first lesson or talk."""
    progress = progress or Progress()
    backoff = 1.0
    installed = False
    while True:
        try:
            async with await _connect(connect) as lamp:
                backoff = 1.0
                if not installed:
                    # The lamp's model learns that these words are the computer's
                    # to handle (english-lesson), and the tutor's skills.
                    from .freetalk import install

                    await install(lamp, log)
                    installed = True
                log('listening: say "start my English lesson" (or a beginner, intermediate or advanced English lesson) or "English free talk" to the lamp')
                phrase = await wait_for_wake(lamp)
            log(f"heard: {phrase}")
            # Let the lamp's own short "starting" line be said first.
            await asyncio.sleep(START_PAUSE_S)
            async with await _connect(connect) as lamp:
                if phrase in FREE_TALK:
                    from .freetalk import free_talk

                    await free_talk(lamp, "free", talk_level, log=log)
                else:
                    chosen = pick(progress, LEVEL_LESSON.get(phrase, levels))
                    if chosen is None:
                        await lamp.say("I have no lessons for that level.", wait=True)
                    else:
                        found, due = chosen
                        log(f"lesson: {found.id} ({'reviews due' if due else 'new'})")
                        session = await Session.open(lamp, found, progress=progress, language=language, listen_s=listen_s, log=log)
                        result = await session.run(due_only=due)
                        log(f"lesson done: {result.right} of {result.scored} right")
            if once:
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log(f"lamp: {type(error).__name__}: {error}; trying again in {backoff:.0f} s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
