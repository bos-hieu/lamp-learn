"""Listening mode: start a lesson by talking to the lamp.

    lamp-learn listen

Leave it running. It registers a few phrases with the lamp and waits:
- "start my English lesson" (or "English lesson"): today's lesson. Reviews
  that are due come first, from the lesson with the most; with none due, the
  next lesson not started yet, in level order.
- "beginner / intermediate / advanced English lesson": the same, from
  A1-A2, B1-B2 or C1-C2 only ("start my advanced English lesson" works too).
- "next English lesson": the next lesson not started yet, skipping reviews.
- "repeat English lesson": the last lesson practised, every item.
- "list English lessons" (or "list beginner / intermediate / advanced
  lessons"): the lamp says the lessons at that level, then listens for one
  by name ("family lesson").
- With `--levels`, a lesson by name too: "start family lesson" (as many as
  fit in the lamp's 16 phrases, in level order).
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
# The next new lesson, skipping reviews; the last lesson again, every item.
# (The lamp listens for 16 phrases at most, so each has one form.)
NEXT_LESSON = ("next english lesson",)
REPEAT_LESSON = ("repeat english lesson",)
FREE_TALK = ("english free talk",)
# The lamp says a level's lessons, then listens for one of them by name.
# Both "lessons" and "lesson": the recognizer writes either, and a missed
# list phrase would leave only "english lesson", which starts a lesson.
LIST_LESSONS = {
    "list english lessons": None,  # the learner's own level
    "list english lesson": None,
    "list beginner lessons": ("A1", "A2"),
    "list beginner lesson": ("A1", "A2"),
    "list intermediate lessons": ("B1", "B2"),
    "list intermediate lesson": ("B1", "B2"),
    "list advanced lessons": ("C1", "C2"),
    "list advanced lesson": ("C1", "C2"),
}
WAKE = START_LESSON + tuple(LEVEL_LESSON) + NEXT_LESSON + REPEAT_LESSON + FREE_TALK + tuple(LIST_LESSONS)

GROUPS = (("beginner", ("A1", "A2")), ("intermediate", ("B1", "B2")), ("advanced", ("C1", "C2")))

# How long the lamp waits for a lesson's name after listing them.
CHOOSE_S = 30.0

# A lesson's spoken name: said back as "<name> lesson" to choose it.
SPOKEN = {"numbers-time": "numbers", "jobinterview": "job interview", "daily": "daily routine"}

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


def pick_next(progress: Progress, levels: tuple[str, ...] | None = None) -> lessons.Lesson | None:
    """The next lesson not fully started, in level order; None when all are."""
    found = [l for l in lessons.builtin().values() if not levels or l.level in levels]
    found.sort(key=lambda l: (LEVEL_ORDER.get(l.level, 9), l.id))
    for row, l in zip(progress.report(found), found):
        if row["seen"] < row["items"]:
            return l
    return None


def spoken_name(lesson: lessons.Lesson) -> str:
    tail = lesson.id.split("-", 2)[-1]
    return SPOKEN.get(tail, tail.replace("-", " "))


def menu(progress: Progress, phrase: str, levels: tuple[str, ...] | None = None) -> tuple[str, dict[str, lessons.Lesson]]:
    """What the lamp says to list a level's lessons, and the phrase that
    chooses each. The plain "list English lessons" lists the learner's level:
    the last lesson's group, else beginner (or the first of `levels`)."""
    group = LIST_LESSONS.get(phrase)
    if group is None:
        last = progress.last_lesson()
        level = lessons.builtin()[last].level if last in lessons.builtin() else (levels[0] if levels else "A1")
        group = next(g for _, g in GROUPS if level in g)
    name = next(n for n, g in GROUPS if g == group)
    found = [l for l in lessons.builtin().values() if l.level in group]
    found.sort(key=lambda l: (LEVEL_ORDER.get(l.level, 9), l.id))
    found = found[:15]
    choices = {f"{spoken_name(l)} lesson": l for l in found}
    example = spoken_name(found[1] if len(found) > 1 else found[0]) if found else "family"
    parts = (
        [f"{name.capitalize()} lessons:"]
        + [f"{spoken_name(l)}." for l in found]
        + [
            f"Which one? Say its name and the word lesson, like {example} lesson.",
            "For another level, say " + " or ".join(f"list {n} lessons" for n, g in GROUPS if g != group) + ".",
        ]
    )
    return " ".join(parts), choices


def _sentences(line: str) -> list[str]:
    """The list's heading, each name, then each sentence: what `menu` joined."""
    head, _, rest = line.partition(": ")
    if not rest:
        return [line]
    out = [head + ":"]
    for word in rest.split(". "):
        word = word.strip()
        if word:
            out.append(word if word.endswith(".") else word + ".")
    return out


async def choose_from_menu(lamp, line: str, choices: dict[str, lessons.Lesson], wait_s: float | None = None) -> lessons.Lesson | None:
    """Say the list, then listen for a lesson's name for `CHOOSE_S`."""
    loop = asyncio.get_running_loop()
    picked: asyncio.Future = loop.create_future()

    def on_phrase(event: dict) -> None:
        phrase = str(event.get("phrase", "")).lower()
        if phrase in choices and not picked.done():
            picked.set_result(choices[phrase])

    await lamp.on("phrase.heard", on_phrase, phrases=list(choices))
    # One sentence at a time, so the names come with a pause between them.
    for part in _sentences(line):
        if picked.done():
            break
        await lamp.say(part, wait=True)
    try:
        return await asyncio.wait_for(picked, wait_s if wait_s is not None else CHOOSE_S)
    except asyncio.TimeoutError:
        return None


def choose(phrase: str, progress: Progress, levels: tuple[str, ...] | None = None) -> tuple[lessons.Lesson | None, bool, str]:
    """What a heard phrase asks for: (lesson, due items only, a line to say
    first or ""). No lesson means nothing fits."""
    if phrase in REPEAT_LESSON:
        last = progress.last_lesson()
        found = lessons.builtin().get(last) if last else None
        if found:
            return found, False, ""
        phrase, note = START_LESSON[0], "There is no lesson to repeat yet, so here is your first."
    elif phrase in NEXT_LESSON:
        found = pick_next(progress, levels)
        if found:
            return found, False, ""
        phrase, note = START_LESSON[0], "You have started every lesson, so here are your reviews."
    else:
        note = ""
    chosen = pick(progress, LEVEL_LESSON.get(phrase, levels))
    if chosen is None:
        return None, False, note
    return chosen[0], chosen[1], note


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


def wake_phrases(levels: tuple[str, ...] | None = None) -> tuple[list[str], dict[str, lessons.Lesson]]:
    """The phrases to wait for, and the lessons started by name. With
    `levels`, the phrases for other levels make room for lessons said by
    name ("start family lesson"), up to the lamp's 16."""
    if not levels:
        return list(WAKE), {}
    keep = [
        p
        for p in WAKE
        if not ((p in LEVEL_LESSON and not set(LEVEL_LESSON[p]) & set(levels)) or (p in LIST_LESSONS and LIST_LESSONS[p] and not set(LIST_LESSONS[p]) & set(levels)))
    ]
    found = sorted((l for l in lessons.builtin().values() if l.level in levels), key=lambda l: (LEVEL_ORDER.get(l.level, 9), l.id))
    direct = {}
    for l in found:
        if len(keep) + len(direct) >= 16:
            break
        direct[f"{spoken_name(l)} lesson"] = l
    return keep + list(direct), direct


async def wait_for_wake(lamp, phrases: list[str] | None = None) -> str:
    """Register the wake phrases and return the one heard. A line can hold
    two ("start my advanced English lesson" holds "English lesson" too):
    after the first, a moment more is given, and the more specific one wins."""
    wanted = list(phrases or WAKE)
    loop = asyncio.get_running_loop()
    first: asyncio.Future = loop.create_future()
    got: list[str] = []

    def on_phrase(event: dict) -> None:
        phrase = str(event.get("phrase", "")).lower()
        if phrase in wanted:
            got.append(phrase)
            if not first.done():
                first.set_result(phrase)

    await lamp.on("phrase.heard", on_phrase, phrases=wanted)
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
        if phrase not in START_LESSON:
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
    show: Callable[[str, str], None] | None = None,
) -> None:
    """Wait for a wake phrase, run what it asks, and wait again. `connect` is
    a coroutine function giving a connected lamp (an async context manager).
    With `once`, returns after the first lesson or talk. `show(title, text)`
    puts the lesson list on this computer's screen too (a notification)."""
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
                log('listening: say "start my English lesson", "next English lesson", "repeat English lesson", "list English lessons", a beginner, intermediate or advanced English lesson, or "English free talk" to the lamp')
                phrases, direct = wake_phrases(levels)
                phrase = await wait_for_wake(lamp, phrases)
            log(f"heard: {phrase}")
            # Let the lamp's own short "starting" line be said first.
            await asyncio.sleep(START_PAUSE_S)
            if phrase in direct:
                found = direct[phrase]
                log(f"lesson: {found.id} (said by name)")
                async with await _connect(connect) as lamp:
                    session = await Session.open(lamp, found, progress=progress, language=language, listen_s=listen_s, log=log)
                    result = await session.run()
                    log(f"lesson done: {result.right} of {result.scored} right{' (stopped)' if result.stopped else ''}")
                if once:
                    return
                continue
            if phrase in LIST_LESSONS:
                line, choices = menu(progress, phrase, levels)
                log(f"list: {line}")
                if show:
                    show("English lessons", line)
                async with await _connect(connect) as lamp:
                    found = await choose_from_menu(lamp, line, choices)
                    if found is None:
                        await lamp.say("I didn't catch a lesson. Say list English lessons to hear them again.", wait=True)
                if found is None:
                    log("list: nothing chosen")
                    if once:
                        return
                    continue
                log(f"lesson: {found.id} (chosen)")
                async with await _connect(connect) as lamp:
                    session = await Session.open(lamp, found, progress=progress, language=language, listen_s=listen_s, log=log)
                    result = await session.run()
                    log(f"lesson done: {result.right} of {result.scored} right{' (stopped)' if result.stopped else ''}")
                if once:
                    return
                continue
            async with await _connect(connect) as lamp:
                if phrase in FREE_TALK:
                    from .freetalk import free_talk

                    await free_talk(lamp, "free", talk_level, log=log)
                else:
                    found, due, note = choose(phrase, progress, levels)
                    if note:
                        await lamp.say(note, wait=True)
                    if found is None:
                        await lamp.say("I have no lessons for that level.", wait=True)
                    else:
                        log(f"lesson: {found.id} ({'reviews due' if due else 'new'})")
                        session = await Session.open(lamp, found, progress=progress, language=language, listen_s=listen_s, log=log)
                        result = await session.run(due_only=due)
                        log(f"lesson done: {result.right} of {result.scored} right{' (stopped)' if result.stopped else ''}")
            if once:
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log(f"lamp: {type(error).__name__}: {error}; trying again in {backoff:.0f} s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
