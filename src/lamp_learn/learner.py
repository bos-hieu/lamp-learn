"""A pretend learner, for trying lessons with no person and no lamp.

The lamp tells a program only which of its phrases was heard, so a test needs
someone to "say" the phrase at the right moment. Two stand-ins:

- `FakeLearner`: beside the SDK's `FakeLamp` (in-process), it reads the say
  requests the fake took and emits `phrase.heard`.
- `SimLearner`: beside lamp-sim (the virtual lamp), it listens to what the
  lamp says (`lamp.said`, the `speech` grant) and runs `lamp-sim hear PHRASE DIR`.

Each answers a "say after me" line right with chance `accuracy`.
"""

from __future__ import annotations

import asyncio
import random
import re
import shutil
from typing import Callable

from .coach import WORDS
from .lesson import Lesson


def _prompt_patterns() -> list[re.Pattern]:
    patterns = []
    for words in WORDS.values():
        for key in ("say_after_me", "again"):
            head = re.escape(words[key].split("{phrase}")[0])
            patterns.append(re.compile(f"^{head}(?P<phrase>.+)$", re.IGNORECASE))
    return patterns


PROMPTS = _prompt_patterns()


def asked_phrase(line: str) -> str | None:
    """The phrase a coach's line asks the learner to say, or None."""
    for pattern in PROMPTS:
        match = pattern.match(line.strip())
        if match:
            return match.group("phrase").strip()
    return None


class _Learner:
    def __init__(self, lesson: Lesson, accuracy: float = 0.8, seed: int | None = None, log: Callable[[str], None] | None = None):
        self.lesson = lesson
        self.accuracy = accuracy
        self.random = random.Random(seed)
        self.log = log or (lambda _line: None)

    def answer(self, asked: str) -> str | None:
        """What the learner says back: the phrase, or nothing the lamp listens for."""
        if self.random.random() < self.accuracy:
            return asked
        return None


class FakeLearner(_Learner):
    """Beside a `lampos.testing.FakeLamp`."""

    def __init__(self, fake, lesson: Lesson, delay_s: float = 0.2, **kwargs):
        super().__init__(lesson, **kwargs)
        self.fake = fake
        self.delay_s = delay_s
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        seen = 0
        while True:
            said = self.fake.asked("say")
            for request in said[seen:]:
                asked = asked_phrase(request.get("text", ""))
                if asked:
                    await asyncio.sleep(self.delay_s)
                    answer = self.answer(asked)
                    self.log(f"learner: {answer or '(mumbles)'}")
                    if answer:
                        await self.fake.emit("phrase.heard", phrase=answer.lower())
            seen = len(said)
            await asyncio.sleep(0.05)

    def start(self) -> "FakeLearner":
        self._task = asyncio.ensure_future(self._run())
        return self

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class SimLearner(_Learner):
    """Beside lamp-sim: hears the lamp's lines and answers with `lamp-sim hear`."""

    def __init__(self, lamp, sim_dir: str, lesson: Lesson, sim_bin: str = "lamp-sim", seconds_a_word: float = 0.45, **kwargs):
        super().__init__(lesson, **kwargs)
        self.lamp = lamp
        self.sim_dir = sim_dir
        self.sim_bin = shutil.which(sim_bin) or sim_bin
        self.seconds_a_word = seconds_a_word

    async def _hear(self, phrase: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.sim_bin, "hear", phrase, self.sim_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode:
            self.log(f"lamp-sim hear failed: {err.decode().strip()}")

    async def _on_said(self, event: dict) -> None:
        line = event.get("text") or event.get("line") or ""
        asked = asked_phrase(line)
        if not asked:
            return
        # lamp.said comes as the line begins: wait for it to be spoken first.
        await asyncio.sleep(1.0 + len(line.split()) * self.seconds_a_word)
        answer = self.answer(asked)
        self.log(f"learner: {answer or '(mumbles)'}")
        if answer:
            await self._hear(answer)

    async def run(self) -> None:
        await self.lamp.on("lamp.said", self._on_said)
        await self.lamp.wait_closed()
