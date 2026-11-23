"""What the learner knows: Leitner boxes kept in a JSON file on this computer.

Each item sits in a box from 1 to 5. Right moves it up one box, wrong sends it
back to box 1. An item is due again after its box's wait: 0, 1, 3, 7 and 14
days. `talk` items are never scored, so they never get a box.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .lesson import Lesson

WAIT_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
DAY_S = 86_400


def default_path() -> Path:
    home = os.environ.get("LAMP_LEARN_HOME") or Path.home() / ".lamp-learn"
    return Path(home) / "progress.json"


@dataclass
class Card:
    box: int = 1
    right: int = 0
    wrong: int = 0
    seen: float = 0.0

    def due(self, now: float) -> bool:
        return now >= self.seen + WAIT_DAYS[self.box] * DAY_S


class Progress:
    def __init__(self, path: str | Path | None = None, learner: str = "default"):
        self.path = Path(path) if path else default_path()
        self.learner = learner
        self._data: dict = self._load()

    def _load(self) -> dict:
        """The saved progress. A file that isn't progress (empty, cut off,
        edited by hand) is kept beside it as `.bad` and the score starts
        again, rather than stopping every lesson; cards that can't be read
        are dropped."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            self.path.replace(self.path.with_name(self.path.name + ".bad"))
            return {}
        fields = set(Card.__dataclass_fields__)
        clean = {}
        for learner, cards in data.items():
            if isinstance(cards, dict):
                clean[learner] = {
                    key: card for key, card in cards.items()
                    if isinstance(key, str) and "/" in key and isinstance(card, dict) and set(card) <= fields
                    and all(isinstance(v, (int, float)) for v in card.values())
                }
        return clean

    def _cards(self) -> dict:
        return self._data.setdefault(self.learner, {})

    def card(self, lesson_id: str, item_id: str) -> Card:
        raw = self._cards().get(f"{lesson_id}/{item_id}")
        return Card(**raw) if raw else Card()

    def record(self, lesson_id: str, item_id: str, right: bool, now: float | None = None) -> Card:
        card = self.card(lesson_id, item_id)
        if right:
            card.right += 1
            card.box = min(card.box + 1, 5)
        else:
            card.wrong += 1
            card.box = 1
        card.seen = time.time() if now is None else now
        self._cards()[f"{lesson_id}/{item_id}"] = card.__dict__
        self.save()
        return card

    def last_lesson(self, lesson_ids: set[str] | None = None) -> str | None:
        """The lesson practised most recently (of `lesson_ids`, if given), or
        None before the first."""
        cards = self._cards()
        if lesson_ids is not None:
            cards = {k: v for k, v in cards.items() if k.split("/", 1)[0] in lesson_ids}
        if not cards:
            return None
        key = max(cards, key=lambda k: cards[k].get("seen", 0))
        return key.split("/", 1)[0]

    def reset(self, lesson_ids: list[str] | None = None) -> int:
        """Forget this learner's score, for the given lessons or all of them.
        The file as it was is kept next to it (progress.json.bak). Returns how
        many items were forgotten."""
        cards = self._cards()
        gone = [k for k in cards if not lesson_ids or k.split("/", 1)[0] in lesson_ids]
        if not gone:
            return 0
        if self.path.exists():
            self.path.with_name(self.path.name + ".bak").write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        for k in gone:
            del cards[k]
        self.save()
        return len(gone)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def due(self, lesson: Lesson, now: float | None = None) -> list[str]:
        """The scored items of `lesson` due now, new ones first, then lowest box."""
        now = time.time() if now is None else now
        cards = self._cards()
        scored = [i for i in lesson.items if i.kind != "talk"]
        fresh = [i.id for i in scored if f"{lesson.id}/{i.id}" not in cards]
        old = [i for i in scored if f"{lesson.id}/{i.id}" in cards and self.card(lesson.id, i.id).due(now)]
        old.sort(key=lambda i: self.card(lesson.id, i.id).box)
        return fresh + [i.id for i in old]

    def report(self, lessons: list[Lesson], now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = []
        for lesson in lessons:
            scored = [i for i in lesson.items if i.kind != "talk"]
            cards = [self.card(lesson.id, i.id) for i in scored]
            seen = [c for c in cards if c.seen]
            rows.append(
                {
                    "lesson": lesson.id,
                    "items": len(scored),
                    "seen": len(seen),
                    "mastered": sum(c.box >= 4 for c in seen),
                    "due": len(self.due(lesson, now)),
                    "right": sum(c.right for c in cards),
                    "wrong": sum(c.wrong for c in cards),
                }
            )
        return rows
