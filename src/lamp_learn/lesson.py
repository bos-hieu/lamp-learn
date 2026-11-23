"""Lessons: a TOML file of items a learner hears, repeats and talks about.

    id = "en-a1-greetings"
    title = "Greetings"
    target = "en"            # the language being learned
    level = "A1"

    [[items]]
    id = "good-morning"
    kind = "repeat"          # repeat | word | talk
    phrase = "good morning everyone"
    hint = { vi = "chào buổi sáng mọi người" }

The lamp never tells a program what a person said, only that one of the
phrases the program registered was heard. So a `repeat` or `word` item is
checked by registering its phrase, and a `talk` item is handed to the lamp's
own model as a turn, with no score. The checks below are the lamp's own
limits on registered phrases, so a lesson that loads here is one the lamp takes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import tomllib

KINDS = ("repeat", "word", "talk")
LESSON_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# The lamp's limits on registered phrases (lampOS/sdk/GUIDE.md, `subscribe`).
PHRASE_MIN_LETTERS = 8
PHRASE_MAX_CHARS = 64
PHRASES_MAX = 16
# What `say` takes at most, in UTF-8 bytes.
SAY_MAX_BYTES = 900


class LessonError(ValueError):
    pass


def letters(text: str) -> int:
    return sum(unicodedata.category(c)[0] in "LMN" for c in text)


@dataclass
class Item:
    id: str
    kind: str
    phrase: str = ""
    meaning: str = ""
    example: str = ""
    prompt: str = ""
    hint: dict[str, str] = field(default_factory=dict)
    # Other ways the recognizer may write the phrase ("good morning" for
    # "good morning everyone"); each one is registered and counts as right.
    accept: list[str] = field(default_factory=list)
    # Near misses: heard, they get "Close! Say: <phrase>" and another try,
    # never a point ("i would take it" for "i will take it"). Listened for
    # only while the lamp's 16 phrases leave room.
    close: list[str] = field(default_factory=list)

    @property
    def phrases(self) -> list[str]:
        return [self.phrase, *self.accept] if self.kind in ("repeat", "word") else []

    @property
    def near_misses(self) -> list[str]:
        """`close` and the usual slip of "would" for "will", minus anything
        that counts as right."""
        if self.kind not in ("repeat", "word"):
            return []
        found = list(self.close)
        for phrase in self.phrases:
            words = phrase.split()
            if "will" in words:
                found.append(" ".join("would" if w == "will" else w for w in words))
        right = set(self.phrases)
        out: list[str] = []
        for phrase in found:
            if phrase not in right and phrase not in out:
                out.append(phrase)
        return out

    def hint_for(self, language: str) -> str:
        return self.hint.get(language, "")


@dataclass
class Lesson:
    id: str
    title: str
    target: str
    level: str
    items: list[Item]
    intro: str = ""
    path: Path | None = None
    # The intro in the learner's language, for a beginner: said in place of
    # `intro` when the coach speaks that language.
    intro_hint: dict[str, str] = field(default_factory=dict)

    @property
    def phrases(self) -> list[str]:
        seen: list[str] = []
        for item in self.items:
            for phrase in item.phrases:
                if phrase.lower() not in (p.lower() for p in seen):
                    seen.append(phrase)
        return seen

    def item(self, item_id: str) -> Item:
        for item in self.items:
            if item.id == item_id:
                return item
        raise LessonError(f"{self.id}: no item {item_id!r}")

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "target": self.target,
            "level": self.level,
            "items": [{"id": i.id, "kind": i.kind, "phrase": i.phrase or i.prompt} for i in self.items],
        }


def _check_said(where: str, text: str) -> None:
    if len(text.encode()) > SAY_MAX_BYTES:
        raise LessonError(f"{where}: at most {SAY_MAX_BYTES} bytes, as the lamp's voice takes it")
    if any(unicodedata.category(c) == "Cc" for c in text):
        raise LessonError(f"{where}: no newline, tab or other control characters")


def _check_phrase(where: str, phrase: str) -> None:
    if letters(phrase) < PHRASE_MIN_LETTERS:
        raise LessonError(f"{where}: {phrase!r} is too short for the lamp to listen for: {PHRASE_MIN_LETTERS} letters at least")
    if len(phrase) > PHRASE_MAX_CHARS:
        raise LessonError(f"{where}: {phrase!r} is longer than {PHRASE_MAX_CHARS} characters")


def parse(data: dict, path: Path | None = None) -> Lesson:
    name = str(path or data.get("id", "lesson"))
    for key in ("id", "title", "target"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise LessonError(f"{name}: `{key}` is required")
    # The id names the lesson's file when saved: letters, digits, - and _ only.
    if not LESSON_ID.fullmatch(data["id"]):
        raise LessonError(f"{name}: id: a letter or digit, then letters, digits, '-' or '_', 64 at most")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise LessonError(f"{name}: at least one [[items]]")
    items: list[Item] = []
    ids: set[str] = set()
    for n, raw in enumerate(raw_items, 1):
        where = f"{name} item {raw.get('id', n)}"
        kind = raw.get("kind", "repeat")
        if kind not in KINDS:
            raise LessonError(f"{where}: kind is one of {', '.join(KINDS)}")
        item = Item(
            id=str(raw.get("id") or f"item-{n}"),
            kind=kind,
            phrase=raw.get("phrase", ""),
            meaning=raw.get("meaning", ""),
            example=raw.get("example", ""),
            prompt=raw.get("prompt", ""),
            hint=dict(raw.get("hint", {})),
            accept=list(raw.get("accept", [])),
            close=list(raw.get("close", [])),
        )
        if item.id in ids:
            raise LessonError(f"{where}: id used twice")
        ids.add(item.id)
        if kind in ("repeat", "word"):
            if not item.phrase:
                raise LessonError(f"{where}: a {kind} item needs `phrase`")
            for phrase in item.phrases + item.close:
                _check_phrase(where, phrase)
        if kind == "talk" and not item.prompt:
            raise LessonError(f"{where}: a talk item needs `prompt`")
        for key in ("phrase", "meaning", "example", "prompt"):
            _check_said(f"{where} {key}", getattr(item, key))
        for language, text in item.hint.items():
            _check_said(f"{where} hint.{language}", text)
        items.append(item)
    lesson = Lesson(
        id=data["id"],
        title=data["title"],
        target=data["target"],
        level=str(data.get("level", "")),
        intro=data.get("intro", ""),
        items=items,
        path=path,
        intro_hint=dict(data.get("intro_hint", {})),
    )
    _check_said(f"{name} intro", lesson.intro)
    for language, text in lesson.intro_hint.items():
        _check_said(f"{name} intro_hint.{language}", text)
    if len(lesson.phrases) > PHRASES_MAX:
        raise LessonError(
            f"{name}: {len(lesson.phrases)} phrases to listen for; the lamp listens for {PHRASES_MAX} at most: split the lesson"
        )
    return lesson


def load(path: str | Path) -> Lesson:
    path = Path(path)
    with path.open("rb") as f:
        return parse(tomllib.load(f), path)


def builtin() -> dict[str, Lesson]:
    """The lessons that come with lamp-learn, by id."""
    found = {}
    for entry in sorted(resources.files("lamp_learn").joinpath("lessons").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            lesson = parse(tomllib.loads(entry.read_text(encoding="utf-8")), Path(entry.name))
            found[lesson.id] = lesson
    return found


def find(name: str, folder: str | Path | None = None) -> Lesson:
    """A lesson by file path, by id among the built-in ones, or in `folder`."""
    path = Path(name)
    if path.suffix == ".toml" and path.exists():
        return load(path)
    if folder:
        for candidate in Path(folder).glob("*.toml"):
            lesson = load(candidate)
            if lesson.id == name:
                return lesson
    lessons = builtin()
    if name in lessons:
        return lessons[name]
    raise LessonError(f"no lesson {name!r}: try `lamp-learn list`")
