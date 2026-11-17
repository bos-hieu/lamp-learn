# lamp-learn: language lessons on a lampOS lamp

A small SDK on top of the lampOS SDK (`lampos`, Python) that turns the lamp into
a language coach. It is generic (each lesson names its `target` language), and
ships with English lessons first. Two ways in:

- **Python library + CLI** (`lamp_learn`, `lamp-learn`): lessons in TOML, a coach
  that runs them, a score per learner (spaced repetition).
- **MCP server** (`lamp-learn-mcp`): the same as tools for an agent (Claude Code,
  Codex). The agent plans and writes lessons, and the lamp says them and listens.

Built on the lampOS SDK ([`sdk/GUIDE.md`](https://github.com/autonomous-ai/lampOS/blob/lamp-voice/lampOS/sdk/GUIDE.md), `lamp-voice` branch).
Licensed under Apache-2.0. Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## What the lamp allows, and so what a lesson can do

| Lesson part | Lamp call | Grant | Scored? |
|---|---|---|---|
| Coach's lines, new words, meanings, examples | `say(text, wait=True)` | `speak` | — |
| `repeat` and `word` drills: "Say after me: good morning" | `on("phrase.heard", phrases=[...])` | `hear` | yes: right when the phrase is heard within `listen_s` |
| `talk`: free practice talk | `start_turn(prompt)`: the lamp's own model answers | `speak` + `hear` | no: nothing the learner says comes back |
| Right/wrong feedback | `light` green pulse + `nod`, orange breathe + `caring` | `light`, `body` | — |

The lamp **never tells a program what a person said**. It only reports which of
the program's registered phrases it heard. So:

- there is no pronunciation score or free-answer grading. "Right" means the lamp's
  recognizer wrote the phrase;
- each phrase has **8 letters or more and 64 characters or fewer**, and there are
  **16 phrases a lesson at most** (the lamp's cap for each computer). Changing
  phrases is limited to 6 a minute, so one session listens for one lesson's
  phrases. `lamp-learn check` and `write_lesson` refuse a lesson that breaks
  these rules;
- the lamp matches a phrase as a run of whole words in a finished line, so
  "good morning" is heard inside "oh, good morning lamp". Use short phrases of
  common words, and add the ways a recognizer may write it in `accept`
  ("i wake up at 7" for "i wake up at seven").

## Install

The lampos SDK isn't on PyPI yet. Install it from the lampOS repo, then lamp-learn:

```sh
git clone https://github.com/bos-hieu/lamp-learn && cd lamp-learn
python3 -m venv .venv
.venv/bin/pip install "git+https://github.com/autonomous-ai/lampOS@lamp-voice#subdirectory=lampOS/sdk/python"
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

## Use it on a real lamp

1. **Pair this computer once.** In the lamp app (2.5.6 or newer), the owner turns
   on developer access for the lamp, which shows a pairing CODE and the lamp's PIN.
   Then:

   ```sh
   .venv/bin/lamp pair CODE --pin PIN --host HOST    # HOST: the lamp's address, e.g. lamp-xxxx.local
   .venv/bin/lamp hello
   ```

   Give it at least `speak`, `hear`, `light`, `body` and `sound`; `skills` too for
   free talk and listening mode. The full guide, with what each error means, is
   lampOS's [Connect your computer](https://github.com/autonomous-ai/lampOS/blob/lamp-voice/lampOS/docs/guides/connect-your-computer.md).

   **On a Mac**, if Python fails with `No route to host` while `nc -vz HOST 7731`
   from Terminal works, macOS's Local Network privacy setting is blocking Python.
   Turn Python on in System Settings, Privacy & Security, Local Network; if it isn't
   listed, see the guide's "On a Mac when Python is not listed".

2. **Run a lesson:** `.venv/bin/lamp-learn run en-a1-greetings -v`, or
   `run next` (the next lesson not started) or `run repeat` (the last one again).

   Start over with `.venv/bin/lamp-learn reset` (every lesson) or
   `reset en-a1-greetings ...` (some). It asks first (`--yes` skips that) and keeps
   the old file as `progress.json.bak`. `--learner NAME` keeps separate scores.

3. **Or leave listening mode running** and start lessons by talking to the lamp:

   ```sh
   .venv/bin/lamp-learn listen
   ```

   | Say to the lamp | What runs |
   |---|---|
   | "start my English lesson" | reviews due first, else the next new lesson |
   | "beginner / intermediate / advanced English lesson" | the same, from A1-A2, B1-B2 or C1-C2 |
   | "next English lesson" | the next lesson not started yet, skipping reviews |
   | "repeat English lesson" | the last lesson practised, every item |
   | "list English lessons" / "list beginner (intermediate, advanced) lessons" | the lamp says that level's lessons; then say one within 30 s, like "family lesson" (on a Mac the list also shows as a notification) |
   | "start family lesson" (with `--levels`) | that lesson by name: as many lessons as fit in the lamp's 16 phrases |
   | "English free talk" | a conversation with the lamp's own AI |

   **Stop a lesson:** stroke the lamp's head, or say "stop English lesson". What
   you finished is kept; the item it stopped on isn't scored.

   On first start it pushes its three skills to the lamp (`english-lesson`,
   `english-tutor`, `english-marks`, in `src/lamp_learn/skills/`). They show on the
   lamp's Skills page in the app. `english-lesson` only makes sense with lamp-learn
   running; the other two work on their own ("let's practise English").

## Try it with no lamp

### 1. The fake lamp, in one command

```sh
.venv/bin/lamp-learn list
.venv/bin/lamp-learn demo en-a1-greetings --language vi --accuracy 0.7
```

`demo` starts the SDK's fake lamp in this process, with a pretend learner who
answers each "say after me" right 70% of the time, and prints every line the
lamp would say.

### 2. lamp-sim, the virtual lamp (the lamp's real door and rules)

Build it once from a lampOS checkout (`cargo build -p lamp-sim -p lamp-body -p lamp-control -p lamp-skills`
in `lampOS/`), then run these in three terminals:

```sh
# 1: the lamp. Its say goes to macOS `say`, so you hear the coach.
BIN=/path/to/lampOS/target/debug
mkdir -m 700 -p ~/.lampsim
$BIN/lamp-sim run ~/.lampsim/learn --bin-dir $BIN

# 2: pair once, then the pretend learner, who answers with `lamp-sim hear PHRASE DIR`
export LAMPOS_HOME=~/.lampos-sim
$BIN/lamp-sim code ~/.lampsim/learn        # prints CODE and PIN
.venv/bin/lamp pair CODE --pin PIN --host 127.0.0.1
.venv/bin/lamp-learn learner en-a1-greetings --sim-dir ~/.lampsim/learn --sim-bin $BIN/lamp-sim

# 3: the lesson
export LAMPOS_HOME=~/.lampos-sim
.venv/bin/lamp-learn run en-a1-greetings -v
```

Or use the browser demo, with lamp-sim running and paired:

```sh
LAMPOS_HOME=~/.lampos-sim .venv/bin/lamp-learn web --sim-bin $BIN/lamp-sim   # opens http://127.0.0.1:8740/?t=TOKEN
```

The page picks a lesson and shows what the lamp says, its 32 ring lights (read from lamp-sim's `ring.json`) and a countdown while it
listens. You answer with "Say it right" / "Say something else", a typed line, or 🎤 (Chrome's speech recognition on the
laptop microphone); each answer goes to lamp-sim as `lamp-sim hear LINE`. It serves 127.0.0.1 only, with a fresh token in the URL.

Or play the learner yourself: when the lamp says "Say after me: good morning",
run `$BIN/lamp-sim hear "good morning" ~/.lampsim/learn`.

Simulator limits: lamp-sim has no model, so a `talk` item's turn has nobody to
answer it. Its real voice (`--real-voice`) is refused on a Mac.

## Use it from Claude Code or Codex

```sh
claude mcp add lamp-learn -- $PWD/.venv/bin/lamp-learn-mcp
# no lamp: the fake one, with a pretend learner
claude mcp add lamp-learn-fake -- $PWD/.venv/bin/lamp-learn-mcp --fake
# Codex
codex mcp add lamp-learn -- $PWD/.venv/bin/lamp-learn-mcp
```

For lamp-sim, set `LAMPOS_HOME=~/.lampos-sim` in the server's environment
(`claude mcp add -e LAMPOS_HOME=$HOME/.lampos-sim ...`).

Tools: `list_lessons`, `show_lesson`, `check_lesson`, `write_lesson`,
`start_lesson`, `teach`, `drill`, `run_lesson`, `practice_talk`, `say`,
`progress`. Example ask: *"Make me a 6-item A2 English lesson about ordering
coffee with Vietnamese hints, then run it on the lamp and tell me what to review."*

## Lesson format

```toml
id = "en-a1-greetings"
title = "Greetings"
target = "en"                 # the language learned
level = "A1"
intro = "Let's practise saying hello in English."   # optional

[[items]]
id = "how-are-you"
kind = "repeat"               # repeat | word | talk
phrase = "how are you today"  # what the lamp listens for: ≥ 8 letters, ≤ 64 chars
accept = ["how are you"]      # other ways it may be heard; each counts as right
hint = { vi = "hôm nay bạn thế nào" }   # in the learner's language

[[items]]
id = "notebook"
kind = "word"                 # taught (word, meaning, example), then drilled
phrase = "a notebook"
meaning = "a book of empty pages you write in"
example = "I write my homework in a notebook."

[[items]]
id = "talk-intro"
kind = "talk"                 # the lamp's model talks with the learner; not scored
prompt = "Tell me your name and how you are today, in English."
```

The coach's own lines exist in English and Vietnamese (`coach.WORDS`). Add a
language there to coach in it. The learner's language defaults to the lamp
owner's (`lamp.language`).

## Library

```python
from lampos import Lamp
from lamp_learn import Session, Progress, find

async with await Lamp.connect() as lamp:
    session = await Session.open(lamp, find("en-a1-desk"), progress=Progress(), language="vi")
    result = await session.run(due_only=True)
    print(result.as_dict())
```

The score is in `~/.lamp-learn/progress.json` (`LAMP_LEARN_HOME` moves it), in
Leitner boxes 1 to 5. An item is due again after 0, 1, 3, 7 or 14 days.

## Not yet verified

- On a real lamp: the recognizer is tuned to the owner's language, so whether an
  English phrase is heard on a lamp set to Vietnamese is untested. Also untested:
  whether a phrase is heard with no wake word, and how often a correct phrase is
  missed (the guide notes misses on 9 Nov builds).
- `talk` items on lamp-sim: no model runs there.

## Free talk: a real-time conversation with the lamp's own AI

    lamp-learn talk --topic travel --level B1 -v

lamp-learn puts two skills on the lamp (`src/lamp_learn/skills/`):
- `english-tutor`: its tool `practise_english` turns the lamp's model into a conversation partner and tutor;
- `english-marks`: it nods for a good line and tilts before a correction.

It then opens the practice with `start_turn` and keeps opening turns while you talk. Nothing outside the lamp answers, and what you say stays on the lamp, so free talk is not scored.

Once the skills are on the lamp, you can also just say "let's practise English" to it, with no computer.

There is a 💬 Start free talk button on the web page and a `free_talk` MCP tool.

lamp-sim runs no AI, so free talk there stops after the opening line ("no model answered"). It needs a real lamp until lamp-sim can run one.
