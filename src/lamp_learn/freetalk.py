"""Free talk: a real-time English conversation with the lamp's own model.

Nothing outside the lamp answers. lamp-learn pushes two skills to the lamp
(`skills/`): `english-tutor`, whose tool the lamp's model calls when a
practice opens and whose note makes it a conversation partner and tutor, and
`english-marks`, which nods for a good line and tilts before a correction.
Then it opens the practice with `start_turn`: the lamp says the opening line,
and its model answers the learner's next line, and the next, as long as they
keep talking.

Once the skills are on the lamp, the learner can also just say "let's practise
English" to it, with no computer running.

What the computer never gets: what the learner said or the lamp answered (a
turn's words stay on the lamp). So free talk is not scored; the drills are.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

SKILLS = ("english-tutor", "english-marks", "english-lesson")

# A turn with no opening line that ends "finished" faster than this answered
# no one: a person's line and the model's answer take longer.
NO_MODEL_S = 1.5

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
    turns = 1
    while how == "finished" and turns < most_turns:
        log("turn: (listening for your next line)")
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
    return str(done.get("how", "")), str(done.get("why", done.get("reason", "")) or "")
