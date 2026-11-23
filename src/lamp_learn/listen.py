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
- With `--levels`, a lesson by topic too: "English lesson about family" (as
  many as fit in the lamp's 16 phrases, in level order).
- "English conversation": a conversation with the lamp's own model (free talk).

It hears these through the lamp's own phrase matching, as a lesson's answers
are: nothing else you say reaches the computer. While a lesson runs, the
waiting connection is closed, so the lesson's phrases have the lamp to
themselves; it opens again after. If the lamp goes away (an update restarts
it), it connects again, waiting longer each time, up to a minute.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from . import hearing
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
# Not "english free talk": the lamp missed it about half the time.
FREE_TALK = ("english conversation",)
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

# After a wake phrase, the lamp's model says English Tutor's short lesson
# line ("Okay. If your lesson app is running, it starts now..."); the lesson
# begins after it.
START_PAUSE_S = 6.0

class PhraseBudget:
    """The lamp takes 6 changes of a computer's phrases a minute (sending the
    same set again is free). Past that a change is refused, and the lamp goes
    on listening for the old phrases, so a wake phrase is never heard. This
    waits instead, before each change."""

    MOST = 6
    WINDOW_S = 60.0

    def __init__(self, log: Callable[[str], None] = lambda line: None):
        self.log = log
        self.changes: list[float] = []
        self.current: frozenset[str] | None = None

    async def spend(self, phrases) -> None:
        wanted = frozenset(p.lower() for p in phrases)
        if wanted == self.current:
            return
        now = time.monotonic()
        self.changes = [t for t in self.changes if now - t < self.WINDOW_S]
        if len(self.changes) >= self.MOST - 1:  # keep one in hand
            wait = self.WINDOW_S - (now - self.changes[0]) + 0.5
            self.log(f"phrases: waiting {wait:.0f} s for the lamp's limit of {self.MOST} changes a minute")
            await asyncio.sleep(wait)
        self.changes.append(time.monotonic())
        self.current = wanted


LEVEL_ORDER = {level: n for n, level in enumerate(("A1", "A2", "B1", "B2", "C1", "C2"))}


# A lesson cut off by a dropped connection goes on from where it stopped, at
# most this many times.
MOST_RESUMES = 2


def _dropped(error: BaseException) -> bool:
    return isinstance(error, (ConnectionError, OSError)) or getattr(error, "code", None) == "closed"


class _Held:
    """A connection already open, used as one opened here (not closed here)."""

    def __init__(self, lamp):
        self.lamp = lamp

    async def __aenter__(self):
        return self.lamp

    async def __aexit__(self, *exc):
        return False


async def run_lesson(connect, found, due: bool, *, progress, language, listen_s, log, budget=None, lamp=None) -> "object":
    """Run one lesson on its own connection. If the connection drops part way,
    connect again and go on with the items not done, saying so; the items
    done are kept. Returns the whole lesson's result."""
    from .coach import LessonResult

    done: list = []
    remaining: list[str] | None = None
    for attempt in range(MOST_RESUMES + 1):
        session = None
        try:
            opened = _Held(lamp) if lamp is not None and attempt == 0 else await _connect(connect)
            async with opened as lamp:
                if budget:
                    await budget.spend(Session.phrases_for(found))
                session = await Session.open(lamp, found, progress=progress, language=language, listen_s=listen_s, log=log)
                if attempt:
                    await session.say(session._word("resumed"))
                result = await session.run(remaining, due_only=due and remaining is None, intro=attempt == 0)
                whole = LessonResult(found.id, done + result.items, result.stopped)
                log(f"lesson done: {whole.right} of {whole.scored} right{' (stopped)' if whole.stopped else ''}")
                return whole
        except Exception as error:
            if not _dropped(error) or attempt == MOST_RESUMES:
                raise
            planned = getattr(session, "planned", None)
            got = getattr(session, "result", None)
            if planned is None:
                raise  # it never began: the usual retry in serve
            done += got.items if got else []
            ids = {r.item for r in done}
            remaining = [i.id for i in planned if i.id not in ids] if remaining is None else [i for i in remaining if i not in ids]
            if not remaining:
                return LessonResult(found.id, done)
            log(f"lamp: the connection dropped in the lesson ({error}); going on with {len(remaining)} item(s)")
            await asyncio.sleep(2)
    return LessonResult(found.id, done)


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


def about(lesson: lessons.Lesson) -> str:
    """The phrase that starts a lesson by its topic: "lesson about family"
    (inside "start an English lesson about family" too)."""
    return f"lesson about {spoken_name(lesson)}"


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
    if not line.strip():
        return []
    head, _, rest = line.partition(": ")
    if not rest:
        return [line]
    out = [head + ":"]
    for word in rest.split(". "):
        word = word.strip()
        if word:
            out.append(word if word.endswith(".") else word + ".")
    return out


async def choose_from_menu(
    lamp,
    line: str,
    choices: dict[str, lessons.Lesson],
    wait_s: float | None = None,
    log: Callable[[str], None] = lambda line: None,
    budget: PhraseBudget | None = None,
) -> lessons.Lesson | None:
    """Say the list, then listen for a lesson's name for `CHOOSE_S`."""
    loop = asyncio.get_running_loop()
    picked: asyncio.Future = loop.create_future()

    def on_phrase(event: dict) -> None:
        phrase = str(event.get("phrase", "")).lower()
        if phrase in choices and not picked.done():
            picked.set_result(choices[phrase])

    if budget:
        await budget.spend(choices)
    await hearing.listen_for(lamp, on_phrase, list(choices))
    # One sentence at a time, so the names come with a pause between them.
    for part in _sentences(line):
        if picked.done():
            break
        began = loop.time()
        await lamp.say(part, wait=True)
        if loop.time() - began > 8:
            log(f"list: slow to say ({loop.time() - began:.0f} s): {part}")
    log("list: said; waiting for a lesson's name")
    try:
        return await asyncio.wait_for(picked, wait_s if wait_s is not None else CHOOSE_S)
    except asyncio.TimeoutError:
        return None


def choose(phrase: str, progress: Progress, levels: tuple[str, ...] | None = None) -> tuple[lessons.Lesson | None, bool, str]:
    """What a heard phrase asks for: (lesson, due items only, a line to say
    first or ""). No lesson means nothing fits."""
    if phrase in REPEAT_LESSON:
        every = lessons.builtin()
        last = progress.last_lesson(None if levels is None else {i for i, l in every.items() if l.level in levels})
        found = every.get(last) if last else None
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
    asked = LEVEL_LESSON.get(phrase)
    if asked and levels is not None:
        # A level asked for by voice, within the levels set: "beginner" with
        # A2 B1 set is A2. With none of the group set, the group as asked.
        asked = tuple(l for l in asked if l in levels) or asked
    chosen = pick(progress, asked or levels)
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
    `levels`, every lesson at those levels can be asked for by topic
    ("English lesson about family"), up to the lamp's 16: the topics come
    before the spare forms (a level phrase that says no more than "English
    lesson", the singular "list ... lesson"), which fill what is left."""
    if not levels:
        return list(WAKE), {}
    wanted = set(levels)

    def fits(group) -> bool:
        return bool(set(group) & wanted)

    # A level phrase is spare when the levels are all inside that group:
    # "English lesson" already asks for the same lessons.
    spare_level = {p for p, g in LEVEL_LESSON.items() if fits(g) and wanted <= set(g)}
    core, spare = [], []
    for p in WAKE:
        if p in LEVEL_LESSON:
            if not fits(LEVEL_LESSON[p]):
                continue
            (spare if p in spare_level else core).append(p)
        elif p in LIST_LESSONS:
            group = LIST_LESSONS[p]
            if group is not None and not fits(group):
                continue
            (spare if p.endswith(" lesson") else core).append(p)
        else:
            core.append(p)
    found = sorted((l for l in lessons.builtin().values() if l.level in levels), key=lambda l: (LEVEL_ORDER.get(l.level, 9), l.id))
    direct = {}
    for l in found:
        if len(core) + len(direct) >= 16:
            break
        direct[about(l)] = l
    room = 16 - len(core) - len(direct)
    return core + list(direct) + spare[: max(0, room)], direct


async def wait_for_wake(lamp, phrases: list[str] | None = None, budget: PhraseBudget | None = None) -> str:
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

    if budget:
        await budget.spend(wanted)
    await hearing.listen_for(lamp, on_phrase, wanted)
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


def free_talk_level(progress: Progress, levels: tuple[str, ...] | None) -> str:
    """The learner's level for free talk: their last lesson's, else the
    highest level listened for, else B1. A beginner isn't talked to at B1."""
    last = progress.last_lesson()
    builtin = lessons.builtin()
    if last in builtin and (not levels or builtin[last].level in levels):
        return builtin[last].level
    if levels:
        return max(levels, key=lambda l: LEVEL_ORDER.get(l, 0))
    return "B1"


async def serve(
    connect: Callable,
    *,
    progress: Progress | None = None,
    levels: tuple[str, ...] | None = None,
    language: str | None = None,
    listen_s: float = 15,
    talk_level: str | None = None,
    log: Callable[[str], None] = print,
    once: bool = False,
    show: Callable[[str, str], None] | None = None,
) -> None:
    """Wait for a wake phrase, run what it asks, and wait again. `connect` is
    a coroutine function giving a connected lamp (an async context manager).
    With `once`, returns after the first lesson or talk. `show(title, text)`
    puts the lesson list on this computer's screen too (a notification)."""
    progress = progress or Progress()
    budget = PhraseBudget(log)
    backoff = 1.0
    installed = False
    while True:
        try:
            async with await _connect(connect) as lamp:
                backoff = 1.0
                if not installed:
                    # The lamp's model learns that these words are the computer's
                    # to handle (English Tutor's lesson step), and its tutor.
                    from .freetalk import install

                    await install(lamp, log)
                    installed = True
                log('listening: say "start my English lesson", "next English lesson", "repeat English lesson", "list English lessons", a beginner, intermediate or advanced English lesson, or "English conversation" to the lamp')
                phrases, direct = wake_phrases(levels)
                phrase = await wait_for_wake(lamp, phrases, budget)
            log(f"heard: {phrase}")
            # Let the lamp's own short "starting" line be said first.
            await asyncio.sleep(START_PAUSE_S)
            if phrase in direct:
                found = direct[phrase]
                log(f"lesson: {found.id} (asked by topic)")
                await run_lesson(connect, found, False, progress=progress, language=language, listen_s=listen_s, log=log, budget=budget)
                if once:
                    return
                continue
            if phrase in LIST_LESSONS:
                line, choices = menu(progress, phrase, levels)
                log(f"list: {line}")
                if show:
                    show("English lessons", line)
                async with await _connect(connect) as lamp:
                    from .coach import close_conversation

                    # The voice may answer "list English lessons" itself; let
                    # it finish and close its window, so the choice is heard.
                    await close_conversation(lamp, log)
                    found = await choose_from_menu(lamp, line, choices, log=log, budget=budget)
                    if found is None:
                        await lamp.say("I didn't catch a lesson. Say list English lessons to hear them again.", wait=True)
                if found is None:
                    log("list: nothing chosen")
                    if once:
                        return
                    continue
                log(f"lesson: {found.id} (chosen)")
                await run_lesson(connect, found, False, progress=progress, language=language, listen_s=listen_s, log=log, budget=budget)
                if once:
                    return
                continue
            async with await _connect(connect) as lamp:
                if phrase in FREE_TALK:
                    from .freetalk import free_talk

                    await free_talk(lamp, "free", talk_level or free_talk_level(progress, levels), log=log)
                else:
                    found, due, note = choose(phrase, progress, levels)
                    if note:
                        await lamp.say(note, wait=True)
                    if found is None:
                        await lamp.say("I have no lessons for that level.", wait=True)
                    else:
                        log(f"lesson: {found.id} ({'reviews due' if due else 'new'})")
                        await run_lesson(connect, found, due, progress=progress, language=language, listen_s=listen_s, log=log, budget=budget, lamp=lamp)
            if once:
                return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log(f"lamp: {type(error).__name__}: {error}; trying again in {backoff:.0f} s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
