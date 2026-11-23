"""`lamp-learn`: run language lessons on a paired lamp, a fake lamp or lamp-sim.

    lamp-learn list
    lamp-learn check my-lesson.toml
    lamp-learn demo en-a1-greetings              # fake lamp + pretend learner, no setup
    lamp-learn run en-a1-greetings [--due]       # on the paired lamp (a real one or lamp-sim)
    lamp-learn learner en-a1-greetings --sim-dir ~/.lampsim/b   # pretend learner for lamp-sim
    lamp-learn run next | repeat                  # the next new lesson, or the last one again
    lamp-learn progress
    lamp-learn reset [LESSON ...] [--yes]          # forget the score, for some lessons or all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from . import lesson as lessons
from .coach import Session
from .progress import Progress


def _print(line: str) -> None:
    print(line, flush=True)


def _list(args) -> int:
    for found in lessons.builtin().values():
        kinds = {}
        for item in found.items:
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        shown = ", ".join(f"{n} {k}" for k, n in kinds.items())
        _print(f"{found.id:24} {found.target}  {found.level:3} {found.title} ({shown})")
    return 0


def _check(args) -> int:
    bad = 0
    for path in args.files:
        try:
            found = lessons.load(path)
            _print(f"ok   {path}: {found.id}, {len(found.items)} items, {len(found.phrases)} phrases to listen for")
        except (lessons.LessonError, OSError, ValueError) as error:
            bad += 1
            _print(f"bad  {path}: {error}")
    return 1 if bad else 0


def _report(result) -> None:
    for item in result.items:
        mark = {True: "right", False: "missed", None: "talk"}[item.right]
        extra = f" ({item.how})" if item.how else f" in {item.tries} tries" if item.right is not None else ""
        _print(f"  {item.item:24} {mark}{extra}")
    _print(f"{result.lesson}: {result.right} of {result.scored} right")


def _session_kwargs(args) -> dict:
    return {
        "progress": None if args.no_progress else Progress(learner=args.learner),
        "language": args.language,
        "listen_s": args.listen,
        "tries": args.tries,
        "log": _print if args.verbose else None,
    }


async def _demo(args) -> int:
    from lampos.testing import FakeLamp

    from .learner import FakeLearner

    found = lessons.find(args.lesson, args.folder)
    async with FakeLamp(grants="all") as fake:
        lamp = await fake.connect()
        learner = FakeLearner(fake, found, accuracy=args.accuracy, seed=args.seed, log=_print).start()
        try:
            kwargs = _session_kwargs(args)
            kwargs["log"] = _print
            session = await Session.open(lamp, found, **kwargs)
            result = await session.run(due_only=args.due)
        finally:
            await learner.stop()
            await lamp.close()
    _report(result)
    return 0


def _resolve(args) -> lessons.Lesson:
    """A lesson id or file, or `next` (the next lesson not started) or
    `repeat` (the last one practised)."""
    if args.lesson in ("next", "repeat"):
        from .listen import pick_next

        progress = Progress(learner=args.learner)
        if args.lesson == "next":
            found = pick_next(progress)
            if found is None:
                raise lessons.LessonError("every lesson is started: run one by name, or `reset`")
            return found
        last = progress.last_lesson()
        if not last:
            raise lessons.LessonError("no lesson practised yet: try `run next`")
        return lessons.find(last, args.folder)
    return lessons.find(args.lesson, args.folder)


async def _run(args) -> int:
    from lampos import Lamp

    found = _resolve(args)
    _print(f"lesson: {found.id} ({found.level} {found.title})")
    async with await Lamp.connect(args.lamp, host=args.host) as lamp:
        session = await Session.open(lamp, found, **_session_kwargs(args))
        result = await session.run(args.items or None, due_only=args.due)
    _report(result)
    if args.json:
        _print(json.dumps(result.as_dict()))
    return 0


async def _talk(args) -> int:
    from lampos import Lamp

    from .freetalk import free_talk

    async with await Lamp.connect(args.lamp, host=args.host) as lamp:
        result = await free_talk(lamp, args.topic, args.level, most_turns=args.turns, log=_print if args.verbose else (lambda line: None))
    _print(f"free talk: {result.turns} turn(s) in {result.seconds} s, ended {result.how}{': ' + result.why if result.why else ''}")
    return 0


def _notify(title: str, text: str) -> None:
    """A macOS notification; nothing elsewhere."""
    import shutil
    import subprocess

    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    quote = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'display notification "{quote(text)}" with title "{quote(title)}"'],
        check=False,
        capture_output=True,
    )


async def _listen(args) -> int:
    from lampos import Lamp

    from .listen import serve

    log = _print
    if args.log_file:
        sink = open(args.log_file, "a", encoding="utf-8", buffering=1)

        def log(line: str) -> None:
            _print(line)
            # Each line with its time, for finding what took long.
            sink.write(time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d} " + line + "\n")

    await serve(
        lambda: Lamp.connect(args.lamp, host=args.host),
        progress=Progress(learner=args.learner),
        levels=tuple(l.upper() for l in args.levels) if args.levels else None,
        language=args.language,
        listen_s=args.listen,
        talk_level=args.talk_level,
        log=log,
        show=None if args.no_notify else _notify,
    )
    return 0


async def _learner(args) -> int:
    from lampos import Lamp

    from .learner import SimLearner

    found = lessons.find(args.lesson, args.folder)
    async with await Lamp.connect(args.lamp, host=args.host) as lamp:
        learner = SimLearner(lamp, args.sim_dir, found, sim_bin=args.sim_bin, accuracy=args.accuracy, seed=args.seed, log=_print)
        _print(f"pretend learner on {args.sim_dir}: answering {found.id} right {int(args.accuracy * 100)}% of the time")
        await learner.run()
    return 0


def _progress(args) -> int:
    progress = Progress(learner=args.learner)
    for row in progress.report(list(lessons.builtin().values())):
        _print(f"{row['lesson']:24} seen {row['seen']}/{row['items']}  mastered {row['mastered']}  due {row['due']}  right {row['right']}  wrong {row['wrong']}")
    return 0


def _autotest(args) -> int:
    from . import autotest

    if args.suite == "fuzz":
        report = autotest.scenario_fuzz(args.log, voice=args.voice, pause=args.pause)
    elif args.suite == "freetalk":
        report = autotest.scenario_freetalk(args.log, voice=args.voice, pause=args.pause)
    elif args.suite == "levels":
        report = autotest.scenario_levels(args.log, voice=args.voice, pause=args.pause)
    else:
        report = autotest.scenario(args.log, voice=args.voice, pause=args.pause)
    return 0 if report.passed == len(report.steps) else 1


def _reset(args) -> int:
    progress = Progress(learner=args.learner)
    known = lessons.builtin()
    for lesson_id in args.lessons:
        if lesson_id not in known and not any(k.startswith(lesson_id + "/") for k in progress._cards()):
            _print(f"lamp-learn: no lesson {lesson_id!r} (see `lamp-learn list`)")
            return 2
    which = ", ".join(args.lessons) if args.lessons else "every lesson"
    if not args.yes:
        answer = input(f"Forget {args.learner}'s score for {which}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            _print("nothing changed")
            return 1
    gone = progress.reset(args.lessons or None)
    _print(f"forgot {gone} item(s) for {args.learner} ({which}); the old file is {progress.path}.bak" if gone else "nothing to forget")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lamp-learn", description="Language lessons on a lampOS lamp")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="the built-in lessons").set_defaults(run=_list)

    check = commands.add_parser("check", help="check lesson files against the lamp's limits")
    check.add_argument("files", nargs="+")
    check.set_defaults(run=_check)

    def lesson_args(p, with_session: bool = True):
        p.add_argument("lesson", help="a built-in lesson id, a .toml file, or (run) next / repeat")
        p.add_argument("--folder", help="a folder of your own lessons")
        p.add_argument("--learner", default="default", help="whose progress (default: default)")
        if with_session:
            p.add_argument("--language", help="the learner's language for the coach's lines and hints (default: the lamp's)")
            p.add_argument("--listen", type=float, default=8.0, help="seconds to listen for an answer (8)")
            p.add_argument("--tries", type=int, default=2, help="tries an item (2)")
            p.add_argument("--due", action="store_true", help="only the items due for review")
            p.add_argument("--no-progress", action="store_true", help="don't keep the score")
            p.add_argument("-v", "--verbose", action="store_true")

    demo = commands.add_parser("demo", help="a lesson on a fake lamp with a pretend learner: no lamp, no pairing")
    lesson_args(demo)
    demo.add_argument("--accuracy", type=float, default=0.8)
    demo.add_argument("--seed", type=int)
    demo.set_defaults(run=_demo, no_progress=True)

    run = commands.add_parser("run", help="a lesson on the paired lamp (a real lamp or lamp-sim)")
    lesson_args(run)
    run.add_argument("--lamp", help="which paired lamp (default: the only one)")
    run.add_argument("--host", help="the lamp's address, if not the pairing's")
    run.add_argument("--items", nargs="*", help="only these item ids")
    run.add_argument("--json", action="store_true")
    run.set_defaults(run=_run)

    talk = commands.add_parser("talk", help="free talk: a real-time English conversation with the lamp's own model")
    talk.add_argument("--topic", default="free", help="free, daily, travel, food, work, hobbies, technology, opinions")
    talk.add_argument("--level", default="B1", help="A1 to C2 (default B1)")
    talk.add_argument("--turns", type=int, default=20, help="the most exchanges (default 20)")
    talk.add_argument("--lamp")
    talk.add_argument("--host")
    talk.add_argument("-v", "--verbose", action="store_true")
    talk.set_defaults(run=_talk)

    listen = commands.add_parser("listen", help='wait for "start my English lesson" said to the lamp, then run today\'s lesson')
    listen.add_argument("--levels", nargs="*", help="only lessons at these levels, e.g. A1 A2")
    listen.add_argument("--language", help="the coach's language for meanings (en, vi, ja, zh, ko)")
    listen.add_argument("--listen", type=float, default=15, help="seconds to wait for each answer (default 15)")
    listen.add_argument("--talk-level", default=None, help='the level for "English conversation" (default: the last lesson\'s level, else the highest of --levels, else B1)')
    listen.add_argument("--learner", default="default")
    listen.add_argument("--log-file", help="also write the log here (for `lamp-learn autotest`)")
    listen.add_argument("--no-notify", action="store_true", help="don't show the lesson list as a notification (macOS)")
    listen.add_argument("--lamp")
    listen.add_argument("--host")
    listen.set_defaults(run=_listen)

    learner = commands.add_parser("learner", help="a pretend learner for lamp-sim: answers the coach with `lamp-sim hear`")
    lesson_args(learner, with_session=False)
    learner.add_argument("--sim-dir", required=True, help="the lamp-sim folder (as given to `lamp-sim run`)")
    learner.add_argument("--sim-bin", default="lamp-sim", help="the lamp-sim program (default: lamp-sim on PATH)")
    learner.add_argument("--lamp")
    learner.add_argument("--host")
    learner.add_argument("--accuracy", type=float, default=0.8)
    learner.add_argument("--seed", type=int)
    learner.set_defaults(run=_learner)

    web = commands.add_parser("web", help="a page in the browser to try lessons on lamp-sim")
    web.add_argument("--sim-dir", default="~/.lampsim/learn", help="the lamp-sim folder (default ~/.lampsim/learn)")
    web.add_argument("--sim-bin", default="lamp-sim", help="the lamp-sim program (default: lamp-sim on PATH)")
    web.add_argument("--lamp")
    web.add_argument("--host")
    web.add_argument("--port", type=int, default=8740)
    web.add_argument("--no-open", action="store_true", help="don't open the browser")
    web.set_defaults(run=lambda a: __import__("lamp_learn.web", fromlist=["run"]).run(a))

    prog = commands.add_parser("progress", help="the learner's score per lesson")
    prog.add_argument("--learner", default="default")
    prog.set_defaults(run=_progress)

    autotest = commands.add_parser("autotest", help="a voice test on a real lamp: this computer speaks the learner's lines out loud")
    autotest.add_argument("--log", required=True, help="listening mode's --log-file")
    autotest.add_argument("--voice", help="the text-to-speech voice (macOS: say -v '?' lists them)")
    autotest.add_argument("--pause", type=float, default=1.5, help="seconds to wait after the lamp asks before answering (1.5)")
    autotest.add_argument("--suite", choices=["basic", "levels", "freetalk", "fuzz"], default="basic", help="basic: listening mode with --levels A1 A2; levels: every level, listening mode with no --levels; freetalk: English conversation, any listening mode; fuzz: off-script speech, any listening mode")
    autotest.set_defaults(run=_autotest)

    reset = commands.add_parser("reset", help="forget the learner's score, for some lessons or all")
    reset.add_argument("lessons", nargs="*", help="lesson ids (default: every lesson)")
    reset.add_argument("--learner", default="default")
    reset.add_argument("-y", "--yes", action="store_true", help="don't ask first")
    reset.set_defaults(run=_reset)

    args = parser.parse_args(argv)
    try:
        result = args.run(args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return result
    except lessons.LessonError as error:
        _print(f"lamp-learn: {error}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
