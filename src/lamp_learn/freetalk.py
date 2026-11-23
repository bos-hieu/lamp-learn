"""Free talk: a real-time English conversation with the lamp's own model.

Nothing outside the lamp answers. lamp-learn pushes one skill to the lamp,
English Tutor (`skills/english-tutor`): its tool, `english_tutor`, is called by the lamp's model with
step `start` when a practice opens (its note makes the model a conversation
partner and tutor), then `good` or `fix` before each answer (a nod, or a
tilt before a correction), and `lesson` when a lesson is asked for.
Then it opens the practice with `start_turn`: the lamp says the opening line,
and its model answers the learner's next line, and the next, as long as they
keep talking.

Once the skills are on the lamp, the learner can also just say "let's practise
English" to it, with no computer running.

What the computer never gets: what the learner said or the lamp answered (a
turn's words stay on the lamp). So free talk is not scored; the drills are.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable

import tomllib

SKILLS = ("english-tutor",)
# Set by the Store app (`lamp_learn.app`): the lamp's installer puts the
# companion skill in place with the app, so nothing is pushed from here.
SKILLS_MANAGED = False
# Skills lamp-learn pushed before, taken off the lamp when English Tutor goes
# on: the marks and the lesson hand-off (now its good, fix and lesson steps)
# and "english", its name for a day. (english-tutor 0.1 is replaced in place.)
OLD_SKILLS = ("english-marks", "english-lesson", "english")

# A turn with no opening line that ends "finished" faster than this answered
# no one: a person's line and the model's answer take longer.
NO_MODEL_S = 1.5
# A turn the lamp drops as "not answered" has caught a sound it didn't take
# as the learner's (often the tail of its own reply): listen again, at most
# this many times in a row, rather than end the talk.
MOST_RELISTENS = 2
NOT_ANSWERED = "not answered"
# The lamp refuses a turn while it counts a conversation as going on (45 s
# after its own voice answered someone, e.g. "English conversation" itself):
# try the opening again this often, for this long.
CONVERSATION = "a conversation is going on"
RETRY_OPEN_S = 5.0
RETRY_OPEN_FOR_S = 50.0

# The lamp takes one skill push each 10 s.
PUSH_GAP_S = 10.5

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# Topic -> an opening question, simple enough for A1-A2, and a richer one for B1 and up.
TOPICS: dict[str, tuple[str, str]] = {
    "free": ("How are you today?", "What's on your mind today?"),
    "daily": ("What do you do in the morning?", "Walk me through a typical day for you."),
    "travel": ("Do you like to travel?", "Tell me about the best trip you've ever taken."),
    "food": ("What food do you like?", "What's a dish from your country you'd recommend, and why?"),
    "work": ("What is your job?", "What do you enjoy most, and least, about your work?"),
    "hobbies": ("What do you do at the weekend?", "Tell me about a hobby you'd like to get better at."),
    "technology": ("Do you use your phone a lot?", "Has technology made our lives better or just busier?"),
    "opinions": ("Do you like the city or the countryside?", "What's something most people believe that you disagree with?"),
}


def opening(topic: str, level: str) -> str:
    """What the lamp says to open the practice: it names the level and topic,
    so the lamp's model, hearing its own words, knows both."""
    level = level.upper() if level.upper() in LEVELS else "B1"
    easy, rich = TOPICS.get(topic, TOPICS["free"])
    question = easy if level in ("A1", "A2") else rich
    about = "" if topic == "free" else f" Today: {topic}."
    return f"Let's practise your English, at {level} level.{about} {question}"


def skill_folder(name: str) -> Path:
    return Path(str(resources.files("lamp_learn").joinpath("skills", name)))


def skill_version(name: str) -> str:
    with open(skill_folder(name) / "skill.toml", "rb") as f:
        return str(tomllib.load(f).get("version", ""))


@dataclass
class TalkResult:
    turns: int
    seconds: float
    how: str  # the last turn's end: finished, cut_short or dropped
    why: str = ""

    def as_dict(self) -> dict:
        return {"turns": self.turns, "seconds": self.seconds, "how": self.how, "why": self.why}


async def install(lamp, log: Callable[[str], None] = lambda line: None) -> list[str]:
    """Put the tutor's skills on the lamp, unless the same versions are there.
    Returns the ones pushed. Raises the lamp's refusal (`no_grant`
    "not granted: skills", or "skill push is not on this lamp")."""
    import asyncio

    if SKILLS_MANAGED:
        return []
    kept = {s["name"]: s for s in await lamp.skills()}
    pushed = []
    for name in SKILLS:
        have = kept.get(name)
        if have and have.get("version") == skill_version(name) and have.get("on", True):
            continue
        if pushed:
            await asyncio.sleep(PUSH_GAP_S)  # the lamp takes one push each 10 s
        await lamp.push_skill(str(skill_folder(name)))
        log(f"skill on the lamp: {name} {skill_version(name)}")
        pushed.append(name)
    for name in OLD_SKILLS:
        if name in kept:
            try:
                await lamp.remove_skill(name)
                log(f"skill taken off the lamp: {name} (now part of english-tutor)")
            except Exception as error:  # a lamp that refuses: the old one stays, harmless
                log(f"couldn't take {name} off the lamp: {error}")
    return pushed


async def free_talk(
    lamp,
    topic: str = "free",
    level: str = "B1",
    *,
    most_turns: int = 20,
    log: Callable[[str], None] = lambda line: None,
) -> TalkResult:
    """Open a practice and keep it going while the learner talks: each new
    turn answers their next line, up to `most_turns`. Ends when they stop
    talking (`dropped`, "nobody spoke"), a turn is cut short, or the lamp
    refuses one. On lamp-sim, which runs no model, the first turn ends at once."""
    start = time.monotonic()
    await install(lamp, log)
    line = opening(topic, level)
    log(f"turn: {line}")
    done = await lamp.start_turn(line, wait=True)
    how, why = _how(done)
    waited = 0.0
    while CONVERSATION in why and waited < RETRY_OPEN_FOR_S:
        log(f"turn: the lamp is still talking with you; trying again in {RETRY_OPEN_S:.0f} s")
        await asyncio.sleep(RETRY_OPEN_S)
        waited += RETRY_OPEN_S
        done = await lamp.start_turn(line, wait=True)
        how, why = _how(done)
    turns = 1
    relistens = 0
    while turns < most_turns:
        if how == "dropped" and why == NOT_ANSWERED and relistens < MOST_RELISTENS:
            relistens += 1
            log("turn: (the lamp caught a sound, not your line: listening again)")
        elif how == "finished":
            relistens = 0
            log("turn: (listening for your next line)")
        else:
            break
        began = time.monotonic()
        done = await lamp.start_turn(None, wait=True)
        how, why = _how(done)
        turns += 1
        if how == "finished" and time.monotonic() - began < NO_MODEL_S:
            # Nobody's line was answered that fast: no model runs here (lamp-sim).
            how, why = "dropped", "no model answered (lamp-sim runs none)"
            log("talk: the lamp's model didn't answer: lamp-sim runs none")
    log(f"talk: done ({turns} turns, {how}{': ' + why if why else ''})")
    return TalkResult(turns, round(time.monotonic() - start, 2), how, why)


def _how(done) -> tuple[str, str]:
    if not isinstance(done, dict):
        return "", ""
    how = str(done.get("how", ""))
    why = str(done.get("why", done.get("reason", "")) or "")
    # The lamp's reason may repeat the how ("dropped: nobody spoke").
    return how, why.removeprefix(how + ": ") if how else why
