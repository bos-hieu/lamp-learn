"""On an installed app's socket the phrases go by `listen_for`, never `on(phrases=)`."""
import asyncio

from lamp_learn import hearing


class AppSocket:
    """Like the SDK on an app's socket: one list, replaced whole; on(phrases=) refused."""

    app = "lamp-learn"

    def __init__(self):
        self.lists = []
        self.handlers = []
        self.trigger_handlers = []
        self.took_turns = []

    def on(self, event, handler, *, phrases=None):
        if phrases and event == "phrase.heard":
            raise RuntimeError("bad_request: on an app's socket the phrases are the app's own list")
        self.trigger_handlers.append(handler)

    async def took(self, turn):
        self.took_turns.append(turn)

    def off(self, event, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)

    async def listen_for(self, phrases, handler=None):
        self.lists.append(list(phrases))
        if handler is not None:
            self.handlers.append(handler)
        return {}

    async def unsubscribe(self, events):
        raise AssertionError("an app's socket never unsubscribes phrase.heard")


class ComputerSocket:
    def __init__(self):
        self.calls = []

    async def on(self, event, handler, *, phrases=None):
        self.calls.append(("on", event, list(phrases or [])))

    async def unsubscribe(self, events):
        self.calls.append(("unsubscribe", events))


def test_app_socket_uses_listen_for_and_keeps_one_handler():
    lamp = AppSocket()
    first, second = (lambda e: None), (lambda e: None)
    asyncio.run(hearing.listen_for(lamp, first, ["english lesson about food"]))
    asyncio.run(hearing.listen_for(lamp, second, ["i am hungry", "stop english lesson"]))
    asyncio.run(hearing.listen_again(lamp, second, ["i am hungry", "stop english lesson"]))
    assert lamp.lists == [["english lesson about food"], ["i am hungry", "stop english lesson"], ["i am hungry", "stop english lesson"]]
    assert len(lamp.handlers) == 1 and len(lamp.trigger_handlers) == 1


def test_trigger_takes_the_turn_and_reaches_the_current_handler_once():
    lamp = AppSocket()
    heard = []

    async def go():
        await hearing.listen_for(lamp, lambda e: heard.append(e["phrase"]), ["stop english lesson", "i am hungry"])
        await lamp.trigger_handlers[0]({"trigger": "stop english lesson", "phrase": "stop english lesson", "turn": "t1"})
        await lamp.handlers[0]({"phrase": "stop english lesson"})  # the same line, as phrase.heard
        await lamp.handlers[0]({"phrase": "I am hungry"})

    asyncio.run(go())
    assert lamp.took_turns == ["t1"]
    assert heard == ["stop english lesson", "i am hungry"]


def test_computer_socket_keeps_on_with_phrases():
    lamp = ComputerSocket()
    handler = lambda e: None
    asyncio.run(hearing.listen_for(lamp, handler, ["english lesson"]))
    asyncio.run(hearing.listen_again(lamp, handler, ["english lesson"]))
    assert lamp.calls == [("on", "phrase.heard", ["english lesson"]), ("unsubscribe", ["phrase.heard"]), ("on", "phrase.heard", ["english lesson"])]
