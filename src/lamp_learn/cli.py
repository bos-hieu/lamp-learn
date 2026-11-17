"""`lamp-learn`: run language lessons on a paired lamp, a fake lamp or lamp-sim.

    lamp-learn list
    lamp-learn check my-lesson.toml
    lamp-learn demo en-a1-greetings              # fake lamp + pretend learner, no setup
    lamp-learn run en-a1-greetings [--due]       # on the paired lamp (a real one or lamp-sim)
    lamp-learn learner en-a1-greetings --sim-dir ~/.lampsim/b   # pretend learner for lamp-sim
    lamp-learn progress
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

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


async def _run(args) -> int:
    from lampos import Lamp

    found = lessons.find(args.lesson, args.folder)
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


async def _listen(args) -> int:
    from lampos import Lamp

    from .listen import serve

    await serve(
        lambda: Lamp.connect(args.lamp, host=args.host),
        progress=Progress(learner=args.learner),
        levels=tuple(l.upper() for l in args.levels) if args.levels else None,
        language=args.language,
        listen_s=args.listen,
        talk_level=args.talk_level,
        log=_print,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lamp-learn", description="Language lessons on a lampOS lamp")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="the built-in lessons").set_defaults(run=_list)

    check = commands.add_parser("check", help="check lesson files against the lamp's limits")
    check.add_argument("files", nargs="+")
    check.set_defaults(run=_check)

    def lesson_args(p, with_session: bool = True):
        p.add_argument("lesson", help="a built-in lesson id, or a .toml file")
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
    listen.add_argument("--language", help="the coach's language for meanings (en, vi)")
    listen.add_argument("--listen", type=float, default=15, help="seconds to wait for each answer (default 15)")
    listen.add_argument("--talk-level", default="B1", help='the level for "English free talk" (default B1)')
    listen.add_argument("--learner", default="default")
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
