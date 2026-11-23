"""The coach: runs a lesson on a lamp through the lampos SDK.

    async with await Lamp.connect() as lamp:
        session = await Session.open(lamp, lesson.find("en-a1-greetings"))
        result = await session.run()

What the lamp lets a program do, and so what the coach can do:

- say a line in the lamp's voice (`speak`);
- learn that one of its own registered phrases was heard, never what else was
  said (`hear`): a `repeat` or `word` item is right when its phrase is heard
  within `listen_s` after the coach asked for it;
- open a turn the lamp's own model answers (`speak` + `hear`): a `talk` item,
  never scored, as nothing heard comes back;
- light and gestures for feedback (`light`, `body`).

One session listens for one lesson's phrases (16 at most, the lamp's cap per
computer). To change lesson, close the connection and open a new one, so the
phrases change once and the lamp's 6-changes-a-minute rate is never near.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import hearing
from .lesson import Item, Lesson
from .progress import Progress

# What the coach says, in the learner's language. A lesson's own words (its
# phrases, meanings and examples) are in the target language.
WORDS = {
    "en": {
        "say_after_me": "Say after me: {phrase}",
        "again": "Once more: {phrase}",
        "not_quite": "Not quite. Listen again: {phrase}",
        "repeat": "Again: {phrase}",
        "stopped": "Okay, we'll stop here.",
        "resumed": "Sorry, I lost my connection for a moment. Let's go on.",
        "right": ["Great!", "Well done!", "Perfect!", "Nice!"],
        "not_heard": "Let's hear it once more: {phrase}. We'll try it again later.",
        "close": "Close! Say: {phrase}",
        "no_one": "I can't hear you right now, so let's stop here. Say start my English lesson when you're ready.",
        "late": "There it is! Well done.",
        "new_word": "New words: {phrase}.",
        "means": "It means: {meaning}.",
        "phrase_means": "This means: {meaning}.",
        "talk_hint": "{hint}",
        "example": "For example: {example}",
        "start": "Let's start: {title}.",
        "done": "Lesson done. You got {right} of {scored}.",
        "talk_done": "Nice talking with you.",
    },
    "vi": {
        "say_after_me": "Bạn hãy nói theo: {phrase}",
        "again": "Nói lại nhé: {phrase}",
        "not_quite": "Chưa đúng lắm. Nghe lại nhé: {phrase}",
        "repeat": "Nghe lại: {phrase}",
        "stopped": "Được, mình dừng ở đây nhé.",
        "resumed": "Xin lỗi, mình vừa mất kết nối. Mình học tiếp nhé.",
        "right": ["Giỏi lắm!", "Tốt lắm!", "Chính xác!", "Hay quá!"],
        "not_heard": "Nghe lại nhé: {phrase}. Lát nữa mình thử lại.",
        "close": "Gần đúng rồi! Nói: {phrase}",
        "no_one": "Mình không nghe thấy bạn, nên mình dừng ở đây nhé. Khi nào sẵn sàng, hãy nói start my English lesson.",
        "late": "Đúng rồi! Giỏi lắm.",
        "new_word": "Từ mới: {phrase}.",
        "means": "Nghĩa là: {meaning}.",
        "phrase_means": "Câu này nghĩa là: {meaning}.",
        "talk_hint": "{hint}",
        "example": "Ví dụ: {example}",
        "start": "Bắt đầu nào: {title}.",
        "done": "Xong bài rồi. Bạn đúng {right} trên {scored} câu.",
        "talk_done": "Nói chuyện với bạn vui lắm.",
    },
    "ja": {
        "say_after_me": "続けて言ってみてください: {phrase}",
        "again": "もう一度どうぞ: {phrase}",
        "not_quite": "おしいです。もう一度聞いてください: {phrase}",
        "repeat": "もう一度聞きましょう: {phrase}",
        "stopped": "わかりました。今日はここまでにしましょう。",
        "resumed": "すみません、少し接続が切れました。続けましょう。",
        "right": ["すごいです!", "よくできました!", "正解です!", "いいですね!"],
        "not_heard": "もう一度聞いてください: {phrase}。あとでまた練習しましょう。",
        "close": "あと少しです! こう言ってください: {phrase}",
        "no_one": "声が聞こえないので、ここまでにしましょう。準備ができたら start my English lesson と言ってくださいね。",
        "late": "そうです! よくできました。",
        "new_word": "新しい言葉です: {phrase}。",
        "means": "意味は「{meaning}」です。",
        "phrase_means": "この文の意味は「{meaning}」です。",
        "talk_hint": "{hint}",
        "example": "例えば: {example}",
        "start": "始めましょう: {title}。",
        "done": "レッスン終了です。{scored}問中{right}問正解でした。",
        "talk_done": "お話しできて楽しかったです。",
    },
    "zh": {
        "say_after_me": "请跟我说: {phrase}",
        "again": "再说一遍: {phrase}",
        "not_quite": "还不太对。再听一遍: {phrase}",
        "repeat": "再听一遍: {phrase}",
        "stopped": "好的，我们就到这里吧。",
        "resumed": "不好意思，刚才断线了一下。我们继续吧。",
        "right": ["太棒了!", "做得好!", "完全正确!", "真不错!"],
        "not_heard": "再听一遍: {phrase}。我们等一下再试。",
        "close": "差一点就对了! 请说: {phrase}",
        "no_one": "我现在听不到你的声音，我们先到这里吧。准备好了就说 start my English lesson。",
        "late": "对了! 做得好。",
        "new_word": "新单词: {phrase}。",
        "means": "意思是: {meaning}。",
        "phrase_means": "这句话的意思是: {meaning}。",
        "talk_hint": "{hint}",
        "example": "例如: {example}",
        "start": "我们开始吧: {title}。",
        "done": "这一课结束了。{scored}题里你答对了{right}题。",
        "talk_done": "和你聊天很开心。",
    },
    "ko": {
        "say_after_me": "따라 말해 보세요: {phrase}",
        "again": "한 번 더요: {phrase}",
        "not_quite": "조금 아쉬워요. 다시 들어 보세요: {phrase}",
        "repeat": "다시 들어 봐요: {phrase}",
        "stopped": "좋아요, 여기까지 할게요.",
        "resumed": "미안해요, 잠깐 연결이 끊겼어요. 계속해요.",
        "right": ["잘했어요!", "훌륭해요!", "정확해요!", "멋져요!"],
        "not_heard": "다시 들어 보세요: {phrase}. 이따가 다시 해 봐요.",
        "close": "거의 맞았어요! 이렇게 말해 보세요: {phrase}",
        "no_one": "지금 목소리가 안 들려서 여기까지 할게요. 준비되면 start my English lesson 이라고 말해 주세요.",
        "late": "맞았어요! 잘했어요.",
        "new_word": "새 단어예요: {phrase}.",
        "means": "뜻은 이래요: {meaning}.",
        "phrase_means": "이 문장의 뜻은 이래요: {meaning}.",
        "talk_hint": "{hint}",
        "example": "예를 들면: {example}",
        "start": "시작해 볼까요: {title}.",
        "done": "수업 끝났어요! {scored}개 중에 {right}개 맞혔어요.",
        "talk_done": "이야기 나눠서 즐거웠어요.",
    },
}


@dataclass
class ItemResult:
    item: str
    kind: str
    right: bool | None  # None: not scored (talk)
    tries: int = 0
    seconds: float = 0.0
    how: str = ""  # a talk's done: finished, cut_short, dropped
    late: bool = False  # right, just after the coach had moved on

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class LessonResult:
    lesson: str
    items: list[ItemResult] = field(default_factory=list)
    stopped: bool = False  # the learner stopped it

    @property
    def scored(self) -> int:
        return sum(r.right is not None for r in self.items)

    @property
    def right(self) -> int:
        return sum(bool(r.right) for r in self.items)

    def as_dict(self) -> dict:
        return {"lesson": self.lesson, "right": self.right, "scored": self.scored, "items": [r.as_dict() for r in self.items]}


# Said during a lesson, it ends the lesson; so does stroking the lamp's head.
STOP_PHRASE = "stop english lesson"
# Said during a drill, the coach says what the line means (in the learner's
# language when it has a hint in it) and asks for it again, without using a
# try. Short and common, so a beginner's accent still gets it through.
HELP_PHRASES = ("what does it mean", "what does that mean")
# The lamp listens for 16 phrases at most.
PHRASES_MOST = 16
# After an item is missed and the phrase said once more, how long a right
# answer still counts (the learner often says it just then).
LATE_S = 6.0
# How long a held turn is given to end by itself after a phrase was heard.
TURN_SETTLE_S = 3.0
# Items in a row with nothing of the lesson heard, after which the lesson
# ends by itself (nobody is there, or the lamp can't hear them).
QUIET_ITEMS = 2
# A lesson phrase heard this soon after a coach's line that was cut off (or
# while it is said) is the lamp hearing its own voice, not the learner: on
# lamp-29e7, "Let's hear it once more: good morning", cut off by the learner,
# came back as "good morning" 0.6 s later. A line that finished hasn't come
# back.
ECHO_S = 2.0
# After a window in which someone spoke, how long to wait for the lamp's
# match of a line that was still being said (on lamp-29e7 it came ~3 s late).
SPOKE_GRACE_S = 4.0


def _words(text: str) -> str:
    """Text as the lamp matches it: lower case, no apostrophes, words only."""
    text = text.lower().replace("'", "").replace("’", "")
    return " " + " ".join("".join(c if c.isalnum() else " " for c in text).split()) + " "
# How long the lamp is muted to end its open conversation.
MUTE_S = 0.8
UNMUTE_SETTLE_S = 1.0
# LAMP_LEARN_NO_CLOSE_MUTE=1: skip the brief mute that closes a conversation
# (build 14967 restarts its voice on that mute and leaves the lamp muted).


class _Stopped(Exception):
    pass


async def wait_quiet(lamp, most_s: float = 25.0, calm_s: float = 1.2) -> None:
    """Wait until the lamp hasn't been talking for `calm_s` (its answer to
    the line that started the lesson), at most `most_s`."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    calm_since = None
    while loop.time() - start < most_s:
        try:
            state = await lamp.get("lamp")
            talking = bool((state.get("lamp") or {}).get("talking"))
        except Exception:
            return
        now = loop.time()
        if talking:
            calm_since = None
        else:
            calm_since = calm_since or now
            if now - calm_since >= calm_s:
                return
        await asyncio.sleep(0.3)


async def close_conversation(lamp, log: Callable[[str], None] = lambda line: None) -> None:
    """Close the lamp's open conversation. The voice keeps listening for
    follow-ups after it answers, and answers every line said then. Once it
    has stopped talking, this computer mutes the lamp for a moment and lifts
    it: that ends the window (tried on lamp-29e7: a turn started and ended at
    once did not). Never fatal."""
    await wait_quiet(lamp)
    if os.environ.get("LAMP_LEARN_NO_CLOSE_MUTE") == "1":
        log("conversation not closed: the mute step is off (LAMP_LEARN_NO_CLOSE_MUTE)")
        return
    try:
        await lamp.set_listening(False)
        try:
            await asyncio.sleep(MUTE_S)
        finally:
            await lamp.set_listening(True)
        # The microphone takes a moment to open again; a turn started at
        # once was dropped on lamp-29e7.
        await asyncio.sleep(UNMUTE_SETTLE_S)
        log("conversation closed: lesson lines go to the lesson")
    except Exception as error:
        log(f"conversation not closed: {error}")


class Session:
    """One lesson on one connection."""

    def __init__(
        self,
        lamp,
        lesson: Lesson,
        *,
        progress: Progress | None = None,
        language: str | None = None,
        listen_s: float = 8.0,
        tries: int = 2,
        feedback: bool = True,
        log: Callable[[str], None] | None = None,
        talk_turns: int = 3,
        talk_wait: Callable[[Item], Awaitable[None]] | None = None,
        sounds: bool = True,
        ring_progress: bool = True,
        tap_repeat: bool = True,
        hold_turn: bool = False,
        close_talk: bool = True,
    ):
        self.lamp = lamp
        self.lesson = lesson
        self.progress = progress
        # The learner's language for the coach's own lines and the hints: the
        # owner's language on the lamp, unless told otherwise.
        self.language = language or getattr(lamp, "language", "en") or "en"
        self.listen_s = listen_s
        self.tries = max(1, tries)
        self.feedback = feedback
        self.log = log or (lambda _line: None)
        # A practice talk is this many exchanges at most: the lamp's model
        # answers one line a turn, and the talk ends when nobody speaks.
        self.talk_turns = max(1, talk_turns)
        # Awaited after a talk's first turn, in place of the turns that follow:
        # a stand-in with no model (lamp-sim) lets the learner talk, then go on.
        self.talk_wait = talk_wait
        # A chime for right and a soft tone for a miss, kept on the lamp as
        # this computer's clips; off if the lamp refuses them.
        self.sounds = sounds
        # The lesson's progress drawn round the ring: one segment an item.
        self.ring_progress = ring_progress and bool(getattr(lamp, "ring_pixels", None))
        # A tap on the lamp's head while it listens says the phrase again.
        self.tap_repeat = tap_repeat
        # While it listens for an answer, hold a turn open: a line that is one
        # of the lesson's phrases then goes to the lesson alone, and the
        # lamp's own voice doesn't answer it too ("Take what?").
        self.hold_turn = hold_turn
        # Before a lesson's first line and after each practice talk, close the
        # lamp's open conversation (a turn of its own, ended at once): the
        # learner's next lines then need the lamp's name before its voice
        # answers them, so "can i try this on" said for the lesson isn't
        # answered "Try what on?" as well.
        self.close_talk = close_talk
        self._heard: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._stopped = False
        # Whether the lamp has said any phrase since the lesson began, and
        # whether its phrases were sent again because it hadn't.
        self._heard_any = False
        self._heard_item = False  # any phrase heard in the current item
        self._spoke_item = False  # anyone spoke while the current item listened
        self._quiet_items = 0
        self._gave_up = False
        self._saying = False
        self._said_at = 0.0  # when the coach's last line ended (monotonic)
        self._last_said = ""  # that line, as words (`_words`)
        self._last_cut = False  # that line didn't finish (cut off, dropped)
        self._listening = False
        self._spoke_in_window = False
        self._relistened = False
        self._marks: list[str] = []  # each item's segment colour so far

    @classmethod
    async def open(cls, lamp, lesson: Lesson, **kwargs) -> "Session":
        session = cls(lamp, lesson, **kwargs)
        await session.listen()
        return session

    @staticmethod
    def phrases_for(lesson: Lesson) -> list[str]:
        """What a lesson listens for: its phrases, the stop phrase, then its
        near misses, while the lamp's cap of 16 leaves room."""
        phrases = list(lesson.phrases)
        if phrases and STOP_PHRASE not in phrases and len(phrases) < PHRASES_MOST:
            phrases.append(STOP_PHRASE)
        for phrase in HELP_PHRASES if phrases else ():
            if len(phrases) < PHRASES_MOST and phrase not in phrases:
                phrases.append(phrase)
        for item in lesson.items:
            for phrase in item.near_misses:
                if len(phrases) < PHRASES_MOST and phrase not in phrases:
                    phrases.append(phrase)
        return phrases

    async def listen(self) -> None:
        """Register the lesson's phrases with the lamp, once. Raises the
        lamp's refusal, such as `no_grant` "not granted: hear"."""
        phrases = self.phrases_for(self.lesson)
        if phrases:
            await hearing.listen_for(self.lamp, self._on_phrase, phrases)
        # Who is speaking: for closing the conversation, and to tell a learner
        # who answers wrong from one who isn't there.
        try:
            await self.lamp.on("lamp.changed", self._on_lamp_changed)
        except Exception as error:  # an older lamp: the window is closed only at the start
            self.log(f"lamp state events are off: {error}")
        if self.tap_repeat:
            try:
                await self.lamp.on("touch", self._on_touch)
            except Exception as error:  # a lamp from before touch events: no tap, the rest works
                self.tap_repeat = False
                self.log(f"tap to repeat is off: {error}")
        if self.sounds:
            await self._keep_sounds()

    async def _keep_sounds(self) -> None:
        """Put the coach's clips on the lamp unless the same ones are there."""
        from .sounds import CLIPS

        try:
            kept = {clip["name"]: clip.get("bytes") for clip in await self.lamp.sounds()}
            for name, make in CLIPS.items():
                data = make()
                if kept.get(name) != len(data):
                    await self.lamp.upload_sound(name, data)
                    self.log(f"sound kept on the lamp: {name}")
        except Exception as error:
            self.sounds = False
            self.log(f"sounds are off: {error}")

    def _on_phrase(self, event: dict) -> None:
        phrase = event.get("phrase")
        if isinstance(phrase, str):
            self.log(f"heard: {phrase}")
            self._heard_any = True
            self._heard_item = True
            if phrase.lower() == STOP_PHRASE:
                self._stop()
                return
            if phrase.lower() in HELP_PHRASES:
                self._heard.put_nowait(("help", phrase.lower()))
                return
            self._heard.put_nowait(("heard", phrase.lower()))

    def _on_lamp_changed(self, event: dict) -> None:
        # `talking` is someone speaking to the lamp (on lamp-29e7 it comes
        # with the learner's line, never with the coach's say). Something
        # said while it listens that isn't a lesson phrase may be a question
        # to the lamp, which its voice then answers in an open window.
        state = event.get("lamp_state") if isinstance(event.get("lamp_state"), dict) else event
        if state.get("talking") and self._listening:
            self._spoke_in_window = True
            self._spoke_item = True

    def _on_touch(self, event: dict) -> None:
        if event.get("kind") == "tap":
            self._heard.put_nowait(("tap", "tap"))
        elif event.get("kind") == "pet":
            self.log("pet: stop")
            self._stop()

    def _stop(self) -> None:
        self._stopped = True
        self._heard.put_nowait(("stop", "stop"))

    async def _show_progress(self, current: int | None = None) -> None:
        """Draw the lesson round the ring: green right, orange missed, purple a
        talk, white the item now, dim the ones to come. Lasts 30 s, the most
        one light may; drawn again at each item."""
        if not self.ring_progress:
            return
        pixels = self.lamp.ring_pixels
        total = getattr(self, "_total", 0) or len(self.lesson.items)
        colours = []
        for n in range(total):
            if n < len(self._marks):
                colours.append(self._marks[n])
            elif n == current:
                colours.append("#ffffff")
            else:
                colours.append("#1a1a1a")
        ring = []
        for p in range(pixels):
            n = p * total // pixels
            # One dark pixel between segments when each has room for it.
            edge = (p + 1) * total // pixels != n
            ring.append("#000000" if edge and pixels // total >= 3 else colours[n])
        try:
            await self.lamp.ring(ring, for_ms=30_000, brightness=40)
        except Exception as error:
            self.ring_progress = False
            self.log(f"ring progress is off: {error}")

    async def _play(self, name: str) -> None:
        if not self.sounds:
            return
        try:
            await self.lamp.sound(name)
        except Exception as error:
            self.log(f"sound refused: {error}")

    def _word(self, key: str, **fields) -> str:
        words = WORDS.get(self.language, WORDS["en"])
        text = words[key]
        if isinstance(text, list):
            text = random.choice(text)
        return text.format(**fields)

    async def say(self, text: str, *, even_stopped: bool = False) -> dict:
        # A stop heard while the lamp spoke ends the lesson before its next line.
        if self._stopped and not even_stopped:
            raise _Stopped
        self.log(f"say: {text}")
        self._saying = True
        self._last_said = _words(text)
        try:
            done = await self.lamp.say(text, wait=True)
        finally:
            self._saying = False
            self._said_at = time.monotonic()
        self._last_cut = isinstance(done, dict) and done.get("how") not in (None, "finished")
        if self._last_cut:
            self.log(f"say: not finished ({done.get('how')}: {done.get('why', '')})")
        return done

    async def _cheer(self, right: bool) -> None:
        if not self.feedback:
            return
        self.log(f"feedback: {'right' if right else 'missed'}")
        from .sounds import MISS, RIGHT

        await self._play(RIGHT if right else MISS)
        try:
            if right:
                await self.lamp.light("green", 3, 2_500, pattern="pulse")
                await self.lamp.gesture("nod")
            else:
                await self.lamp.light("orange", 2, 2_500, pattern="breathe")
                await self.lamp.gesture("caring")
        except Exception as error:  # feedback is a nicety: a refused light or move never ends a lesson
            self.log(f"feedback refused: {error}")

    def _drain(self) -> None:
        while not self._heard.empty():
            self._heard.get_nowait()
        if self._stopped:  # a stop while the lamp spoke still counts
            self._heard.put_nowait(("stop", "stop"))

    async def _wait_for(self, phrases: list[str], close: list[str] = (), seconds: float | None = None) -> str:
        """`right` when a phrase is heard, `close` on a near miss, `tap` on a
        tap on the head, else `timeout` after `listen_s` (or `seconds`)."""
        wanted = {p.lower() for p in phrases}
        near = {p.lower() for p in close}
        deadline = time.monotonic() + (self.listen_s if seconds is None else seconds)
        graced = False
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                if graced or not self._spoke_in_window:
                    return "timeout"
                # Someone spoke: the lamp's match for a line comes a few
                # seconds after it ends, so a line begun late in the window
                # is still on its way. Wait for it once before judging.
                graced = True
                self.log("someone spoke: waiting a moment for the lamp's match")
                deadline = time.monotonic() + SPOKE_GRACE_S
                continue
            try:
                kind, heard = await asyncio.wait_for(self._heard.get(), timeout=left)
            except asyncio.TimeoutError:
                continue
            if kind in ("tap", "stop", "help"):
                return kind
            if self._echo(heard):
                self.log(f"heard: {heard} (the coach's own line, not counted)")
                continue
            if heard in wanted:
                return "right"
            if heard in near:
                return "close"

    def _echo(self, heard: str) -> bool:
        """`heard` is in the line the coach is saying or just said."""
        recent = self._saying or (self._last_cut and time.monotonic() - self._said_at < ECHO_S)
        return recent and _words(heard) in self._last_said

    async def _listen_for(self, item: Item, seconds: float | None = None) -> str:
        """`_wait_for` the item's phrases, holding a turn open meanwhile (see
        `hold_turn`). A turn the voice answered (a line that isn't a phrase) is
        opened again while there is time; whatever is open at the end is
        ended. A lamp that refuses the turn changes nothing."""
        self.log(f"listen: {' | '.join(item.phrases)}")
        self._listening = True
        self._spoke_in_window = False
        try:
            return await self._listen_turn(item, seconds)
        finally:
            self._listening = False

    async def _listen_turn(self, item: Item, seconds: float | None) -> str:
        waiting = asyncio.ensure_future(self._wait_for(item.phrases, item.near_misses, seconds))
        if not self.hold_turn:
            return await waiting
        turn: asyncio.Future | None = None
        opened = 0
        try:
            while not waiting.done():
                if turn is None or (turn.done() and opened < 3):
                    if turn is not None and turn.done():
                        reason = self._turn_end(turn)
                        if "refused" in reason or "error" in reason:
                            turn = None
                            await waiting
                            break
                    turn = asyncio.ensure_future(self.lamp.start_turn(None, wait=True))
                    opened += 1
                await asyncio.wait({waiting, turn}, return_when=asyncio.FIRST_COMPLETED)
                if turn.done() and opened >= 3:
                    await waiting
        finally:
            if turn is not None and not turn.done() and waiting.done() and not waiting.cancelled() and waiting.result() in ("right", "close"):
                # The lamp drops a held turn whose line was one of the phrases
                # ("not answered"); ending it first would hand the line to the
                # voice instead. Give it a moment to.
                try:
                    await asyncio.wait_for(asyncio.shield(turn), TURN_SETTLE_S)
                except Exception:
                    pass
            if turn is not None and not turn.done():
                try:
                    await self.lamp.end_turn()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(turn), 2.0)
                except Exception:
                    turn.cancel()
            elif turn is not None:
                turn.exception() if not turn.cancelled() else None
        return waiting.result()

    @staticmethod
    def _turn_end(turn: asyncio.Future) -> str:
        """How a held turn ended, in a few words, for deciding to open another."""
        if turn.cancelled():
            return "error"
        error = turn.exception()
        if error is not None:
            return f"error {error}"
        done = turn.result()
        return str(done.get("why", "")) if isinstance(done, dict) else ""

    # Taps an item may ask for, past which a tap is let go by.
    MOST_REPEATS = 5

    async def drill(self, item: Item) -> ItemResult:
        """Ask for the item's phrase and listen for it, up to `tries` times. A
        tap on the head says the phrase again and listens afresh, without
        using a try."""
        start = time.monotonic()
        result = ItemResult(item.id, item.kind, False)
        repeats = 0
        prompted = False  # the last near miss already said the phrase
        wrong = False  # the last try heard someone speak, not the phrase
        for attempt in range(1, self.tries + 1):
            if not prompted:
                key = "say_after_me" if attempt == 1 else ("not_quite" if wrong else "again")
                if attempt == 1 and item.kind == "repeat" and self._explains(item):
                    # A beginner hears what the line means, in their own
                    # language, before saying it.
                    await self.say(self._word("phrase_means", meaning=item.hint_for(self.language)))
                await self.say(self._word(key, phrase=item.phrase))
            prompted = False
            # Anything heard while the lamp spoke is not the learner's answer.
            self._drain()
            result.tries = attempt
            outcome = await self._listen_for(item)
            while outcome in ("tap", "help") and repeats < self.MOST_REPEATS and not self._stopped:
                repeats += 1
                if outcome == "help":
                    self.log("help: what it means")
                    await self._explain(item)
                    await self.say(self._word("say_after_me", phrase=item.phrase))
                else:
                    self.log("tap: repeat")
                    await self.say(self._word("repeat", phrase=item.phrase))
                self._drain()
                outcome = await self._listen_for(item)
            if outcome == "stop" or self._stopped:
                raise _Stopped
            wrong = outcome not in ("right", "close") and self._spoke_in_window
            if wrong:
                self.log("feedback: wrong (someone spoke, not the phrase)")
                if not self._heard_any:
                    # Someone spoke and the lamp matched nothing all lesson:
                    # its phrases may not have taken. Don't wait a whole item.
                    await self._listen_again()
            if outcome != "right" and self._spoke_in_window:
                # Something was said that wasn't the phrase: maybe a question
                # to the lamp ("lamp, what does it mean?"), which reopens its
                # conversation. Close it before the next try.
                await self._close_conversation()
            if outcome == "right":
                result.right = True
                await self._cheer(True)
                await self.say(self._word("right"))
                break
            if outcome == "close" and attempt < self.tries:
                self.log("feedback: close")
                await self.say(self._word("close", phrase=item.phrase))
                prompted = True
        else:
            await self._cheer(False)
            await self.say(self._word("not_heard", phrase=item.phrase))
            # Said just after the coach moved on, it still counts.
            self._drain()
            if not self._stopped and await self._listen_for(item, LATE_S) == "right":
                self.log("feedback: right (late)")
                result.right = result.late = True
                await self._cheer(True)
                await self.say(self._word("late"))
            elif self._stopped:
                raise _Stopped
            if not self._heard_any:
                await self._listen_again()
        result.seconds = round(time.monotonic() - start, 2)
        return result

    async def _listen_again(self) -> None:
        """Nothing at all heard through a whole item: on a real lamp a
        lesson's phrases have now and then not taken (a lesson deaf from its
        first line). Send them again, once a lesson."""
        if self._relistened or not self.lesson.phrases:
            return
        self._relistened = True
        self.log("lamp heard nothing yet: sending the lesson's phrases again")
        try:
            await hearing.listen_again(self.lamp, self._on_phrase, self.phrases_for(self.lesson))
        except Exception as error:
            self.log(f"couldn't send the phrases again: {error}")

    async def _explain(self, item: Item) -> None:
        """What the item's line means: its hint in the learner's language,
        else its meaning, else (nothing to say) just the line again."""
        hint = item.hint_for(self.language) if self.language != self.lesson.target else ""
        meaning = hint or item.meaning
        if meaning:
            await self.say(self._word("phrase_means" if item.kind == "repeat" else "means", meaning=meaning))

    def _explains(self, item: Item | None = None) -> bool:
        """The coach speaks a language other than the lesson's, and the item
        has a hint in it: then it explains in the learner's language."""
        if self.language == self.lesson.target:
            return False
        return item is None or bool(item.hint_for(self.language))

    async def teach(self, item: Item) -> None:
        """Say a word item's phrase, its meaning and an example."""
        await self.say(self._word("new_word", phrase=item.phrase))
        hint = item.hint_for(self.language)
        meaning = hint or item.meaning
        if meaning:
            await self.say(self._word("means", meaning=meaning))
        if item.example:
            await self.say(self._word("example", example=item.example))

    async def talk(self, item: Item) -> ItemResult:
        """A practice talk: the lamp says the prompt and its own model answers
        the learner's next line. Nothing heard comes back, so it is not scored."""
        start = time.monotonic()
        if self._explains(item):
            # What to talk about and an example answer, in the learner's
            # language, before the question in English.
            await self.say(self._word("talk_hint", hint=item.hint_for(self.language)))
        self.log(f"turn: {item.prompt}")
        done = await self.lamp.start_turn(item.prompt, wait=True)
        how = done.get("how", "") if isinstance(done, dict) else ""
        if how != "finished":
            self.log(f"turn: {how} ({done.get('why', '') if isinstance(done, dict) else ''})")
        turns = 1
        if self.talk_wait is not None:
            await self.talk_wait(item)
        else:
            # Keep the talk going: each new turn answers the learner's next
            # line, until they stop talking (`dropped`, "nobody spoke") or a
            # turn is cut short.
            while how == "finished" and turns < self.talk_turns:
                self.log("turn: (listening for your next line)")
                done = await self.lamp.start_turn(None, wait=True)
                how = done.get("how", "") if isinstance(done, dict) else ""
                turns += 1
        self.log("talk: done")
        await self._close_conversation()
        return ItemResult(item.id, item.kind, None, turns, round(time.monotonic() - start, 2), how)

    async def _close_conversation(self) -> None:
        """See `close_talk` and `close_conversation`."""
        if self.close_talk:
            await close_conversation(self.lamp, self.log)

    async def run_item(self, item: Item) -> ItemResult:
        if item.kind == "talk":
            return await self.talk(item)
        if item.kind == "word":
            await self.teach(item)
        self._heard_item = self._spoke_item = False
        result = await self.drill(item)
        if self.progress is not None:
            self.progress.record(self.lesson.id, item.id, bool(result.right))
        # A learner who speaks but gets it wrong is not "nobody there".
        self._quiet_items = 0 if (self._heard_item or self._spoke_item) else self._quiet_items + 1
        if self._quiet_items >= QUIET_ITEMS:
            self._gave_up = True
            self._stopped = True
        return result

    async def run(self, item_ids: list[str] | None = None, *, due_only: bool = False, intro: bool = True) -> LessonResult:
        """Run the lesson: every item in order, the ones named, or with
        `due_only` the scored items due for review (then the talks). What it
        planned and what is done so far stay readable (`planned`, `result`),
        so a lesson cut off by a dropped connection can go on from there."""
        if item_ids:
            items = [self.lesson.item(i) for i in item_ids]
        elif due_only and self.progress is not None:
            due = self.progress.due(self.lesson)
            items = [self.lesson.item(i) for i in due] + [i for i in self.lesson.items if i.kind == "talk"]
        else:
            items = list(self.lesson.items)
        result = LessonResult(self.lesson.id)
        self.planned, self.result = items, result
        self._marks, self._total = [], len(items)
        await self._show_progress(0)
        await self._close_conversation()
        try:
            if intro:
                intro_text = self.lesson.intro
                if self.language != self.lesson.target:
                    intro_text = self.lesson.intro_hint.get(self.language) or ""
                await self.say(intro_text or self._word("start", title=self.lesson.title))
        except _Stopped:
            pass
        for n, item in enumerate(items):
            if self._stopped:
                break
            await self._show_progress(n)
            try:
                done = await self.run_item(item)
            except _Stopped:
                break
            result.items.append(done)
            self._marks.append({True: "#22c55e", False: "#f59e0b", None: "#8b5cf6"}[done.right])
        if self._stopped:
            # The item it stopped on isn't scored; the ones done are kept.
            result.stopped = True
            await self.say(self._word("no_one" if self._gave_up else "stopped"), even_stopped=True)
            return result
        await self._show_progress()
        if result.scored:
            await self.say(self._word("done", right=result.right, scored=result.scored))
        return result
