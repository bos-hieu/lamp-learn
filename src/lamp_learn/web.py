"""`lamp-learn web`: a page in the browser to try lessons on lamp-sim.

    lamp-learn web --sim-dir ~/.lampsim/learn --sim-bin .../lamp-sim
    # open the URL it prints

The page picks a lesson and starts it on the paired lamp (lamp-sim), shows
what the lamp says and the ring's 32 lights, and answers for the learner:
"say it right" and "say something else" buttons, a box to type any line, or
the browser's own speech recognition (Chrome) on the laptop's microphone. Each
answer goes to the lamp as `lamp-sim hear LINE DIR`: the lamp's real phrase
matching decides what was heard, as it would for a recognised line.

It listens on 127.0.0.1 only, and answers only with the token in the URL it
prints and only from its own page (any website open in the browser can reach
localhost).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import webbrowser
from http import HTTPStatus
from importlib import resources
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from . import lesson as lessons
from .coach import Session
from .progress import Progress


class Demo:
    def __init__(self, sim_dir: str, sim_bin: str, lamp_id: str | None, host: str | None, port: int):
        self.sim_dir = str(Path(sim_dir).expanduser())
        self.sim_bin = shutil.which(sim_bin) or sim_bin
        self.lamp_id, self.host, self.port = lamp_id, host, port
        self.token = secrets.token_urlsafe(16)
        self.clients: set = set()
        self.task: asyncio.Task | None = None
        self.lamp = None
        self.prompt: str | None = None
        self.talk_done = asyncio.Event()

    async def talk_wait(self, item) -> None:
        """lamp-sim has no model to answer a talk: let the learner talk on the
        page, and go on when they press Continue (or after 3 minutes)."""
        self.talk_done.clear()
        await self.send(type="talk", prompt=item.prompt)
        try:
            await asyncio.wait_for(self.talk_done.wait(), timeout=180)
        except asyncio.TimeoutError:
            pass
        await self.send(type="talk_end")

    # Out to every page.

    async def send(self, **message) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send(text)
            except Exception:
                self.clients.discard(ws)

    def log(self, line: str) -> None:
        kind, _, text = line.partition(": ")
        if kind == "listen":
            # The lamp has said its line: the learner's turn now.
            phrases = [p.strip() for p in text.split(" | ") if p.strip()]
            self.prompt = phrases[0]
            asyncio.ensure_future(self.send(type="prompt", phrase=phrases[0], phrases=phrases))
            return
        if kind == "talk":
            return  # the page shows the talk from its own talk/talk_end messages
        if kind == "feedback":
            self.prompt = None
        asyncio.ensure_future(self.send(type="line", who=kind, text=text))

    # The lesson.

    async def run_lesson(self, lesson_id: str, language: str | None, listen_s: float, due: bool) -> None:
        from lampos import Lamp

        try:
            found = lessons.find(lesson_id)
            await self.send(type="state", running=True, lesson=found.summary())
            async with await Lamp.connect(self.lamp_id, host=self.host) as lamp:
                self.lamp = lamp
                session = await Session.open(
                    lamp, found, progress=Progress(), language=language or None, listen_s=listen_s, log=self.log,
                    talk_wait=self.talk_wait,
                )
                result = await session.run(due_only=due)
                await self.send(type="result", result=result.as_dict())
        except asyncio.CancelledError:
            if self.lamp is not None:
                try:
                    await self.lamp.stop()
                except Exception:
                    pass
            await self.send(type="line", who="info", text="lesson stopped")
            raise
        except Exception as error:
            await self.send(type="line", who="error", text=f"{type(error).__name__}: {error}")
        finally:
            self.lamp, self.prompt = None, None
            await self.send(type="state", running=False)

    async def run_free_talk(self, topic: str, level: str) -> None:
        from lampos import Lamp

        from .freetalk import free_talk

        def log(line: str) -> None:
            kind, _, text = line.partition(": ")
            who = "turn" if kind == "turn" and not text.startswith("(") else "info"
            asyncio.ensure_future(self.send(type="line", who=who, text=text if who == "turn" else line))

        try:
            await self.send(type="state", running=True, freetalk=True)
            async with await Lamp.connect(self.lamp_id, host=self.host) as lamp:
                self.lamp = lamp
                result = await free_talk(lamp, topic, level, log=log)
                await self.send(type="freetalk_end", result=result.as_dict())
        except asyncio.CancelledError:
            if self.lamp is not None:
                try:
                    await self.lamp.stop()
                except Exception:
                    pass
            await self.send(type="line", who="info", text="free talk stopped")
            raise
        except Exception as error:
            await self.send(type="line", who="error", text=f"{type(error).__name__}: {error}")
        finally:
            self.lamp = None
            await self.send(type="state", running=False)

    async def hear(self, line: str) -> None:
        line = " ".join(line.split())[:300]
        if not line:
            return
        await self.send(type="line", who="you", text=line)
        proc = await asyncio.create_subprocess_exec(
            self.sim_bin, "hear", line, self.sim_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode:
            await self.send(type="line", who="error", text=f"lamp-sim hear: {err.decode().strip()}")

    async def on_message(self, raw: str) -> None:
        message = json.loads(raw)
        op = message.get("op")
        if op == "start":
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except BaseException:
                    pass
            listen = min(max(float(message.get("listen", 15)), 3), 60)
            self.task = asyncio.ensure_future(
                self.run_lesson(str(message.get("lesson")), message.get("language"), listen, bool(message.get("due")))
            )
        elif op == "free_talk":
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except BaseException:
                    pass
            self.task = asyncio.ensure_future(
                self.run_free_talk(str(message.get("topic", "free")), str(message.get("level", "B1")))
            )
        elif op == "stop" and self.task and not self.task.done():
            self.task.cancel()
        elif op == "hear":
            await self.hear(str(message.get("text", "")))
        elif op == "talk_done":
            self.talk_done.set()
        elif op == "tap":
            # A tap on the lamp's head, as lamp-sim's touch stimulus gives it.
            await self.send(type="line", who="info", text="👆 you tapped the lamp's head")
            proc = await asyncio.create_subprocess_exec(
                self.sim_bin, "touch", "tap", self.sim_dir, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await proc.communicate()
            if proc.returncode:
                await self.send(type="line", who="error", text=f"lamp-sim touch: {err.decode().strip()}")
        elif op == "progress":
            rows = Progress().report(list(lessons.builtin().values()))
            await self.send(type="progress", rows=rows)

    # The ring, read from lamp-sim's ring.json.

    async def watch_ring(self) -> None:
        path = Path(self.sim_dir) / "ring.json"
        last = None
        while True:
            try:
                text = path.read_text()
                if text != last:
                    last = text
                    await self.send(type="ring", ring=json.loads(text))
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)

    # HTTP and the live link.

    def _allowed(self, request) -> bool:
        origin = request.headers.get("Origin")
        return origin in (None, f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")

    def process_request(self, connection, request):
        path, _, query = request.path.partition("?")
        if path == "/favicon.ico":
            return connection.respond(HTTPStatus.NO_CONTENT, "")
        if f"t={self.token}" not in query.split("&") or not self._allowed(request):
            return connection.respond(HTTPStatus.FORBIDDEN, "open the URL lamp-learn web printed\n")
        if path == "/ws":
            return None  # the websocket handshake goes on
        if path == "/":
            page = resources.files("lamp_learn").joinpath("web.html").read_text(encoding="utf-8")
            data = {"lessons": [l.summary() for l in lessons.builtin().values()]}
            body = page.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False)).encode()
            headers = Headers(
                [
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                    ("Referrer-Policy", "no-referrer"),
                ]
            )
            return Response(HTTPStatus.OK, "OK", headers, body)
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    async def handler(self, ws) -> None:
        self.clients.add(ws)
        await ws.send(json.dumps({"type": "state", "running": bool(self.task and not self.task.done())}))
        try:
            async for raw in ws:
                try:
                    await self.on_message(raw)
                except Exception as error:
                    await self.send(type="line", who="error", text=str(error))
        finally:
            self.clients.discard(ws)

    async def main(self, open_browser: bool) -> None:
        ring = asyncio.ensure_future(self.watch_ring())
        async with serve(self.handler, "127.0.0.1", self.port, process_request=self.process_request) as server:
            url = f"http://127.0.0.1:{self.port}/?t={self.token}"
            print(f"lamp-learn web: {url}", flush=True)
            if open_browser:
                webbrowser.open(url)
            try:
                await server.serve_forever()
            finally:
                ring.cancel()


def run(args) -> int:
    demo = Demo(args.sim_dir, args.sim_bin, args.lamp, args.host, args.port)
    try:
        asyncio.run(demo.main(not args.no_open))
    except KeyboardInterrupt:
        pass
    return 0
