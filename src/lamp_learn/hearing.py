"""The phrases lamp-learn listens for, on either kind of connection.

On a computer's connection a handler subscribes with its own phrases
(`lamp.on("phrase.heard", handler, phrases=...)`) and the lamp merges every
list. On an installed app's socket the app has one list, replaced whole, and
`on(..., phrases=...)` is refused (`bad_request`): the app calls
`lamp.listen_for(phrases, handler)`, and its handler hears every phrase the
lamp matched. lamp-learn listens for one thing at a time (the wake phrases,
a lesson, a list's choices), so on an app's socket the newest list and
handler replace the last ones.

An app's manifest triggers come as `app.trigger`, listened for or not. The
app takes that turn at once (`took`), so the lamp's voice keeps quiet, and
the trigger goes to the current handler as a phrase. A phrase that comes
both ways within a moment is passed on once."""

import asyncio
import inspect
import time
from typing import Any, Callable

_CURRENT = "_lamp_learn_phrase_handler"
_TRIGGERS = "_lamp_learn_triggers_on"
_LAST = "_lamp_learn_last_phrase"
SAME_PHRASE_S = 3.0


def on_app_socket(lamp: Any) -> bool:
    return getattr(lamp, "app", None) is not None and hasattr(lamp, "listen_for")


async def _deliver(lamp: Any, handler: Callable[[dict], Any], event: dict) -> None:
    phrase = str(event.get("phrase", "")).lower()
    now = time.monotonic()
    last = getattr(lamp, _LAST, None)
    if last and last[0] == phrase and now - last[1] < SAME_PHRASE_S:
        return
    setattr(lamp, _LAST, (phrase, now))
    result = handler({**event, "phrase": phrase})
    if inspect.isawaitable(result):
        await result


def _listen_to_triggers(lamp: Any) -> None:
    if getattr(lamp, _TRIGGERS, False):
        return

    async def on_trigger(event: dict) -> None:
        turn = event.get("turn")
        if turn:
            try:
                await lamp.took(turn)
            except Exception:  # the voice answers itself; the lesson goes on
                pass
        current = getattr(lamp, _CURRENT, None)
        phrase = str(event.get("trigger") or event.get("phrase") or "")
        if current is not None and phrase:
            await current({"phrase": phrase, "via": "app.trigger"})

    lamp.on("app.trigger", on_trigger)
    setattr(lamp, _TRIGGERS, True)


async def listen_for(lamp: Any, handler: Callable[[dict], Any], phrases: list[str]) -> None:
    """Call `handler` for each `phrase.heard` among `phrases`. Raises the
    lamp's refusal, such as `no_grant` "not granted: hear"."""
    phrases = list(phrases)
    if not on_app_socket(lamp):
        await lamp.on("phrase.heard", handler, phrases=phrases)
        return
    previous = getattr(lamp, _CURRENT, None)
    if previous is not None:
        lamp.off("phrase.heard", previous)
        setattr(lamp, _CURRENT, None)

    async def deliver(event: dict) -> None:
        await _deliver(lamp, handler, event)

    await lamp.listen_for(phrases, deliver)
    setattr(lamp, _CURRENT, deliver)
    _listen_to_triggers(lamp)


async def listen_again(lamp: Any, handler: Callable[[dict], Any], phrases: list[str]) -> None:
    """Send the same phrases once more (a list the lamp may not have taken)."""
    if not on_app_socket(lamp):
        await lamp.unsubscribe(["phrase.heard"])
    await listen_for(lamp, handler, phrases)
