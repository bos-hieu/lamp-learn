"""lamp-learn as a lampOS Store app (`python3 -I -m lamp_learn.app`).

The lamp runs this in its own unit (`lampapp@lamp-learn.service`) and gives it
the app socket (`LAMP_APP_SOCKET`) and a data folder (`STATE_DIRECTORY`).
Listening mode then runs as on a computer, with the owner's settings from the
phone's app (`app.settings.get`): the levels, the hint language, the seconds
to answer and the learner's name.

Connects with the SDK's `Lamp.connect_app` when it has one; otherwise through
the SDK's own no-pairing path on the lamp (the door over a unix socket, the
socket's peer check deciding who this is), pointed at `LAMP_APP_SOCKET`. With
no socket there it ends at once with a line saying so, exit 78 (configuration),
so systemd does not restart it in a loop.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
# Exit code for "this lamp cannot run the app" (sysexits EX_CONFIG).
NOT_CONFIGURED = 78
APP_SOCKET = "/run/lampos-apps/apps.sock"
READY_LINE = "English lessons are ready. Say: start my English lesson."
PANEL_LINES = [
    'Say "start my English lesson" to begin.',
    'Say "list English lessons" to choose one.',
    'Say "English conversation" to practise talking.',
]


def levels_between(low: str, high: str) -> tuple[str, ...]:
    """The levels from `low` to `high` (choice values, any case), in order;
    swapped if given the wrong way round."""
    a, b = LEVELS.index(low.upper()), LEVELS.index(high.upper())
    if a > b:
        a, b = b, a
    return LEVELS[a : b + 1]


def options(settings: dict) -> dict:
    """The owner's settings (the manifest's keys) as `listen.serve`'s options."""
    low = str(settings.get("level_from") or "a1")
    high = str(settings.get("level_to") or "a2")
    try:
        levels = levels_between(low, high)
    except ValueError:
        levels = ("A1", "A2")
    seconds = settings.get("answer_seconds", 15)
    try:
        seconds = min(30.0, max(5.0, float(seconds)))
    except (TypeError, ValueError):
        seconds = 15.0
    return {
        "levels": levels,
        "language": str(settings.get("hint_language") or "en"),
        "listen_s": seconds,
        "learner": str(settings.get("learner") or "default")[:32] or "default",
    }


async def _greet(lamp) -> None:
    """Show the app is running: its panel in the phone's app and one line said.
    Each is best effort: a refusal is journalled, never fatal."""
    try:
        await lamp.request("app.panel.set", title="English lessons", lines=PANEL_LINES)
    except Exception as error:
        _log(f"lamp-learn: panel not set ({error})")
    try:
        await lamp.say(READY_LINE, wait=True)
    except Exception as error:
        _log(f"lamp-learn: ready line not said ({error})")


def _log(line: str) -> None:
    # The unit's journal: one line each, with the time.
    print(time.strftime("%H:%M:%S ") + line, flush=True)


async def main() -> int:
    # Until the lamp keeps its voice through an app's brief mute (#8081), the
    # installed app skips the mute that closes a conversation.
    os.environ.setdefault("LAMP_LEARN_NO_CLOSE_MUTE", "1")
    state = os.environ.get("STATE_DIRECTORY")
    if state:
        # Progress and reviews live in the app's own data folder, and so does
        # the SDK's own folder (there is no home for the app's user).
        os.environ.setdefault("LAMP_LEARN_HOME", state.split(":")[0])
        os.environ.setdefault("LAMPOS_HOME", os.path.join(state.split(":")[0], "lampos"))

    from lampos import Lamp

    from . import freetalk
    from .listen import serve
    from .progress import Progress

    # The installer places English Tutor with the app; the app never pushes skills.
    freetalk.SKILLS_MANAGED = True
    connect_app = getattr(Lamp, "connect_app", None)
    if connect_app is None:
        socket = os.environ.get("LAMP_APP_SOCKET") or APP_SOCKET
        if not os.path.exists(socket):
            _log(f"lamp-learn: no app socket at {socket}; not starting")
            return NOT_CONFIGURED
        # The SDK's no-pairing path on the lamp, at the app's socket.
        os.environ["LAMPOS_AGENT_SOCKET"] = socket
        os.environ.pop("LAMP_CONTROL_TOKEN", None)

        def connect_app():
            return Lamp.connect()

    async with await connect_app() as lamp:
        try:
            got = await lamp.request("app.settings.get")
        except Exception as error:  # an older socket: the defaults
            _log(f"lamp-learn: settings not read ({error}); using the defaults")
            got = {}
        settings = got.get("settings", {}) if isinstance(got, dict) else {}
        await _greet(lamp)
    chosen = options(settings)
    _log(f"lamp-learn: levels {'-'.join(chosen['levels'][::max(1, len(chosen['levels']) - 1)])}, "
         f"hints {chosen['language']}, {chosen['listen_s']:.0f} s to answer")
    await serve(
        connect_app,
        progress=Progress(learner=chosen["learner"]),
        levels=chosen["levels"],
        language=chosen["language"],
        listen_s=chosen["listen_s"],
        log=_log,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
    except (OSError, ConnectionError) as error:
        # The lamp's socket refused or closed: one line, and systemd decides.
        _log(f"lamp-learn: the lamp's app socket did not answer ({type(error).__name__}: {error})")
        sys.exit(1)
