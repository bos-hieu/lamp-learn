import asyncio
import json

import pytest
from lampos.testing import FakeLamp

from lamp_learn import LessonError, Progress, Session, builtin, parse
from lamp_learn.learner import FakeLearner, asked_phrase
from lamp_learn.progress import DAY_S


def tiny(**over):
    data = {
        "id": "t",
        "title": "Tiny",
        "target": "en",
        "items": [
            {"id": "a", "kind": "repeat", "phrase": "good morning", "accept": ["morning everyone"]},
            {"id": "b", "kind": "word", "phrase": "a notebook", "meaning": "a book you write in", "hint": {"vi": "quyển vở"}},
            {"id": "c", "kind": "talk", "prompt": "Tell me about your day."},
        ],
    }
    data.update(over)
    return parse(data)


def test_builtin_lessons_load():
    found = builtin()
    assert {"en-a1-greetings", "en-a1-desk", "en-a2-daily"} <= set(found)
    for lesson in found.values():
        assert len(lesson.phrases) <= 16


@pytest.mark.parametrize(
    "item, words",
    [
        ({"id": "x", "kind": "repeat", "phrase": "hi you"}, "too short"),
        ({"id": "x", "kind": "repeat", "phrase": "word " * 20}, "longer than 64"),
        ({"id": "x", "kind": "repeat"}, "needs `phrase`"),
        ({"id": "x", "kind": "talk"}, "needs `prompt`"),
        ({"id": "x", "kind": "sing", "phrase": "good morning"}, "kind is one of"),
        ({"id": "x", "kind": "repeat", "phrase": "good morning", "hint": {"vi": "a\nb"}}, "control characters"),
    ],
)
def test_lesson_refuses_what_the_lamp_refuses(item, words):
    with pytest.raises(LessonError, match=words):
        parse({"id": "t", "title": "T", "target": "en", "items": [item]})


@pytest.mark.parametrize("bad", ["../../x", "a/b", ".hidden", "", "x" * 65])
def test_lesson_id_is_a_plain_file_name(bad):
    with pytest.raises(LessonError):
        parse({"id": bad, "title": "T", "target": "en", "items": [{"id": "x", "kind": "repeat", "phrase": "good morning"}]})


def test_at_most_16_phrases():
    items = [{"id": f"i{n}", "kind": "repeat", "phrase": f"number {n} please"} for n in range(17)]
    with pytest.raises(LessonError, match="16 at most"):
        parse({"id": "t", "title": "T", "target": "en", "items": items})


def test_asked_phrase():
    assert asked_phrase("Say after me: good morning") == "good morning"
    assert asked_phrase("Once more: good morning") == "good morning"
    assert asked_phrase("Bạn hãy nói theo: good morning") == "good morning"
    assert asked_phrase("Great!") is None


def test_progress_boxes(tmp_path):
    lesson = tiny()
    progress = Progress(tmp_path / "p.json")
    assert progress.due(lesson) == ["a", "b"]
    progress.record("t", "a", True, now=0)
    assert progress.card("t", "a").box == 2
    assert progress.due(lesson, now=10) == ["b"]  # box 2 waits a day
    assert progress.due(lesson, now=DAY_S + 1) == ["b", "a"]
    progress.record("t", "a", False, now=0)
    assert progress.card("t", "a").box == 1
    again = Progress(tmp_path / "p.json")
    assert again.card("t", "a").wrong == 1
    assert json.loads((tmp_path / "p.json").read_text())["default"]["t/a"]["right"] == 1


async def test_drill_right_when_phrase_heard(tmp_path):
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, listen_s=1.0, progress=Progress(tmp_path / "p.json"), language="en")
        subscribed = [s for s in fake.asked("subscribe") if "phrase.heard" in s["events"]]
        assert subscribed[-1]["phrases"] == sorted(["good morning", "morning everyone", "a notebook"])
        assert any("touch" in s["events"] for s in fake.asked("subscribe"))

        async def learner():
            await asyncio.sleep(0.2)
            await fake.emit("phrase.heard", phrase="morning everyone")

        task = asyncio.ensure_future(learner())
        result = await session.run_item(lesson.item("a"))
        await task
        assert result.right is True and result.tries == 1
        assert fake.asked("say")[0] == {"text": "Say after me: good morning"}
        assert any(r.get("colour") == "green" for r in fake.asked("light"))
        assert fake.asked("gesture")[-1] == {"name": "nod"}
        assert session.progress.card("t", "a").right == 1
        await lamp.close()


async def test_drill_missed_after_tries_and_wrong_phrase_ignored():
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, listen_s=0.4, tries=2, language="en")

        async def learner():
            await asyncio.sleep(0.1)
            await fake.emit("phrase.heard", phrase="a notebook")  # a phrase, but not this item's

        task = asyncio.ensure_future(learner())
        result = await session.drill(lesson.item("a"))
        await task
        assert result.right is False and result.tries == 2
        texts = [r["text"] for r in fake.asked("say")]
        assert texts[:2] == ["Say after me: good morning", "Once more: good morning"]
        assert texts[-1].startswith("Let's hear it once more")
        assert fake.asked("gesture")[-1] == {"name": "caring"}
        await lamp.close()


async def test_word_item_teaches_in_learners_language():
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, listen_s=0.2, tries=1, language="vi")
        await session.run_item(lesson.item("b"))
        texts = [r["text"] for r in fake.asked("say")]
        assert texts[0] == "Từ mới: a notebook."
        assert texts[1] == "Nghĩa là: quyển vở."
        await lamp.close()


async def test_talk_is_a_turn_not_scored():
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, language="en")
        result = await session.run_item(lesson.item("c"))
        assert result.right is None and result.how == "finished"
        # The prompt, then the turns that keep the talk going (3 at most).
        asked = fake.asked("turn.start")
        assert asked[0] == {"prompt": "Tell me about your day."}
        assert len(asked) == 3 and all("prompt" not in a or a["prompt"] is None for a in asked[1:])
        assert result.tries == 3
        await lamp.close()


async def test_talk_ends_when_nobody_speaks():
    lesson = tiny()
    async with FakeLamp(grants="all", auto_done=False) as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, language="en")
        task = asyncio.ensure_future(session.run_item(lesson.item("c")))
        for n, how in enumerate(["finished", "dropped"]):
            while len(fake.asked("turn.start")) < n + 1:
                await asyncio.sleep(0.02)
            ids = [r["id"] for r in fake.requests if r["op"] == "turn.start"]
            await fake.done(ids[n], how, None if how == "finished" else "nobody spoke")
        result = await task
        assert result.tries == 2 and result.how == "dropped"
        await lamp.close()


async def test_talk_wait_replaces_more_turns():
    lesson = tiny()
    waited = []

    async def wait(item):
        waited.append(item.id)

    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, language="en", talk_wait=wait)
        result = await session.run_item(lesson.item("c"))
        assert waited == ["c"] and len(fake.asked("turn.start")) == 1 and result.tries == 1
        await lamp.close()


async def test_whole_lesson_with_fake_learner(tmp_path):
    lesson = builtin()["en-a1-greetings"]
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        learner = FakeLearner(fake, lesson, accuracy=1.0, seed=1).start()
        session = await Session.open(lamp, lesson, listen_s=1.0, language="en", progress=Progress(tmp_path / "p.json"))
        result = await session.run()
        await learner.stop()
        assert result.scored == 5 and result.right == 5
        assert fake.asked("say")[-1]["text"] == "Lesson done. You got 5 of 5."
        await lamp.close()


async def test_tap_repeats_without_using_a_try():
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, listen_s=1.0, tries=1, language="en")

        async def learner():
            await asyncio.sleep(0.2)
            await fake.emit("touch", kind="tap")
            await asyncio.sleep(0.3)
            await fake.emit("phrase.heard", phrase="good morning")

        task = asyncio.ensure_future(learner())
        result = await session.drill(lesson.item("a"))
        await task
        texts = [r["text"] for r in fake.asked("say")]
        assert texts[:2] == ["Say after me: good morning", "Again: good morning"]
        assert result.right is True and result.tries == 1
        await lamp.close()


async def test_sounds_kept_once_and_played():
    from lamp_learn.sounds import CLIPS, MISS, RIGHT

    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        session = await Session.open(lamp, lesson, listen_s=0.3, tries=1, language="en")
        assert session.sounds, "the lamp refused the clips"
        kept = {c["name"]: c for c in await lamp.sounds()}
        assert set(kept) == set(CLIPS) and all(kept[n]["ms"] < 1000 for n in kept)
        uploads = len(fake.asked("sound.upload"))
        await Session.open(lamp, lesson)  # the same clips are there: nothing sent again
        assert len(fake.asked("sound.upload")) == uploads
        await session.drill(lesson.item("a"))  # nobody answers: the miss tone
        assert fake.asked("sound")[-1] == {"name": MISS}

        async def learner():
            await asyncio.sleep(0.1)
            await fake.emit("phrase.heard", phrase="good morning")

        task = asyncio.ensure_future(learner())
        await session.drill(lesson.item("a"))
        await task
        assert fake.asked("sound")[-1] == {"name": RIGHT}
        await lamp.close()


async def test_ring_shows_progress():
    lesson = tiny()
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        learner = FakeLearner(fake, lesson, accuracy=1.0).start()
        session = await Session.open(lamp, lesson, listen_s=0.5, tries=1, language="en", sounds=False)
        await session.run()
        await learner.stop()
        rings = [r["pixels"] for r in fake.asked("light") if "pixels" in r]
        assert rings and all(len(p) == 32 for p in rings)
        last = rings[-1]
        # Three items over 32 pixels: right (green), right (green), talk (purple).
        assert last[0] == "#22c55e" and last[12] == "#22c55e" and last[30] == "#8b5cf6"
        # The first drawing shows item 0 as the current one, in white.
        assert rings[0][0] == "#ffffff"
        await lamp.close()


async def test_refused_without_hear_grant():
    lesson = tiny()
    async with FakeLamp(grants=("body", "light", "speak")) as fake:
        lamp = await fake.connect()
        with pytest.raises(Exception, match="hear"):
            await Session.open(lamp, lesson)
        await lamp.close()


async def test_mcp_tools_on_fake(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMP_LEARN_HOME", str(tmp_path))
    from lamp_learn.mcp_server import Link, build

    link = Link(None, None, fake=True, accuracy=1.0)
    server = build(link)
    names = {tool.name for tool in await server.list_tools()}
    assert {"list_lessons", "start_lesson", "drill", "teach", "run_lesson", "practice_talk", "say", "write_lesson", "progress"} <= names

    async def call(name, **args):
        out = await server.call_tool(name, args)
        if isinstance(out, tuple):
            structured = out[1]
            return structured.get("result", structured)
        texts = [json.loads(block.text) for block in out]
        return texts[0] if len(texts) == 1 else texts

    toml = 'id = "own"\ntitle = "Own"\ntarget = "en"\n[[items]]\nid = "x"\nkind = "repeat"\nphrase = "thank you very much"\n'
    saved = await call("write_lesson", toml=toml)
    assert saved["phrases"] == ["thank you very much"]
    started = await call("start_lesson", lesson="own", language="en", listen_s=1.0)
    assert started["due"] == ["x"]
    result = await call("drill", lesson="own", item="x")
    assert result["right"] is True
    rows = await call("progress")
    assert any(r["lesson"] == "own" and r["right"] == 1 for r in rows)
    await link.close()
    if link._fake is not None:
        await link._fake.__aexit__(None, None, None)


async def test_free_talk_pushes_skills_and_opens_practice():
    import lamp_learn.freetalk as freetalk
    from lamp_learn.freetalk import SKILLS, free_talk, opening

    freetalk.PUSH_GAP_S = 0
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        result = await free_talk(lamp, "travel", "A2", most_turns=3)
        names = {s["name"] for s in await lamp.skills()}
        assert set(SKILLS) <= names
        turns = fake.asked("turn.start")
        assert turns[0]["prompt"] == opening("travel", "A2")
        assert "A2" in turns[0]["prompt"] and "travel" in turns[0]["prompt"]
        assert 1 <= result.turns <= 3
        await lamp.close()


def test_skills_pass_the_lamps_check():
    import subprocess, sys
    from pathlib import Path
    from lamp_learn.freetalk import SKILLS, skill_folder

    lamp_cli = Path(sys.executable).with_name("lamp")
    for name in SKILLS:
        out = subprocess.run([str(lamp_cli), "skill", "check", str(skill_folder(name))], capture_output=True, text=True)
        assert out.returncode == 0 and "ok" in out.stdout, out.stdout + out.stderr


def test_pick_reviews_first_then_next_new_lesson(tmp_path):
    from lamp_learn import lesson as lessons
    from lamp_learn.listen import pick

    progress = Progress(tmp_path / "p.json")
    first, due = pick(progress)
    assert first.level == "A1" and due is False
    # Answer every item of that lesson: nothing is due today, so the next new one.
    for item in first.items:
        if item.kind != "talk":
            progress.record(first.id, item.id, True)
    second, due = pick(progress)
    assert second.id != first.id and due is False
    # A day later the first lesson's reviews are due: they come first.
    import time
    later = time.time() + 2 * 86400
    import lamp_learn.progress as progress_module
    real = progress_module.time.time
    progress_module.time.time = lambda: later
    try:
        again, due = pick(progress)
    finally:
        progress_module.time.time = real
    assert again.id == first.id and due is True


async def test_listen_starts_a_lesson_on_the_wake_phrase(tmp_path, monkeypatch):
    import lamp_learn.freetalk as freetalk
    import lamp_learn.listen as listen
    from lamp_learn.listen import serve

    monkeypatch.setattr(listen, "START_PAUSE_S", 0)
    monkeypatch.setattr(freetalk, "PUSH_GAP_S", 0)

    async with FakeLamp(grants="all") as fake:
        connections = []

        async def connect():
            lamp = await fake.connect()
            connections.append(lamp)
            return lamp

        async def learner():
            # Wait until the wake phrases are registered, then say one.
            for _ in range(50):
                if any("start my english lesson" in s.get("phrases", []) for s in fake.asked("subscribe")):
                    break
                await asyncio.sleep(0.05)
            await fake.emit("phrase.heard", phrase="start my english lesson")

        task = asyncio.ensure_future(learner())
        lines = []
        await asyncio.wait_for(
            serve(connect, progress=Progress(tmp_path / "p.json"), listen_s=0.2, log=lines.append, once=True), 60
        )
        await task
        assert any(l.startswith("lesson: ") for l in lines), lines
        assert any(l.startswith("lesson done: ") for l in lines), lines
        assert len(connections) == 2  # one to wait, one for the lesson
        pushed = {p.get("name") for p in fake.asked("skill.push") if p.get("name")}
        assert {"english-lesson", "english-tutor", "english-marks"} <= pushed, pushed


async def test_wake_prefers_the_level_phrase():
    from lamp_learn.listen import wait_for_wake

    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        task = asyncio.ensure_future(wait_for_wake(lamp))
        for _ in range(50):
            if any("advanced english lesson" in s.get("phrases", []) for s in fake.asked("subscribe")):
                break
            await asyncio.sleep(0.05)
        await fake.emit("phrase.heard", phrase="english lesson")
        await fake.emit("phrase.heard", phrase="advanced english lesson")
        assert await asyncio.wait_for(task, 5) == "advanced english lesson"
        await lamp.close()


def test_level_phrases_fit_the_lamps_limits():
    from lamp_learn.lesson import PHRASE_MAX_CHARS, PHRASE_MIN_LETTERS, PHRASES_MAX
    from lamp_learn.listen import WAKE

    assert len(WAKE) <= PHRASES_MAX
    for phrase in WAKE:
        assert sum(c.isalpha() for c in phrase) >= PHRASE_MIN_LETTERS and len(phrase) <= PHRASE_MAX_CHARS


def test_next_and_repeat_choose_the_right_lesson(tmp_path):
    from lamp_learn.listen import choose
    from lamp_learn.progress import Progress

    progress = Progress(tmp_path / "p.json")
    # Nothing practised: repeat falls back to the first lesson, with a note.
    found, _, note = choose("repeat english lesson", progress)
    assert found.level == "A1" and "no lesson to repeat" in note
    for item in found.items:
        if item.kind != "talk":
            progress.record(found.id, item.id, True, now=1000.0)
    # Repeat gives the last lesson, every item; next skips it.
    again, due_only, note = choose("english lesson again", progress)
    assert again.id == found.id and not due_only and not note
    after, due_only, _ = choose("next english lesson", progress)
    assert after.id != found.id and not due_only


def test_wake_prefers_next_over_the_plain_lesson_phrase():
    from lamp_learn.listen import START_LESSON, WAKE

    assert "next english lesson" in WAKE and "english lesson" in START_LESSON


def test_reset_forgets_one_lesson_or_all(tmp_path):
    from lamp_learn.cli import main
    from lamp_learn.progress import Progress

    path = tmp_path / "progress.json"
    p = Progress(path)
    p.record("en-a1-greetings", "good-morning", True)
    p.record("en-a1-desk", "pencil", True)
    import os

    os.environ["LAMP_LEARN_HOME"] = str(tmp_path)
    try:
        assert main(["reset", "en-a1-desk", "--yes"]) == 0
        left = Progress(path)._cards()
        assert list(left) == ["en-a1-greetings/good-morning"]
        assert (tmp_path / "progress.json.bak").exists()
        assert main(["reset", "no-such-lesson", "--yes"]) == 2
        assert main(["reset", "--yes"]) == 0
        assert Progress(path)._cards() == {}
    finally:
        del os.environ["LAMP_LEARN_HOME"]
