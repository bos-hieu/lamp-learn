# Contributing to lamp-learn

Thanks for helping. Lessons, coach languages, fixes and docs are all welcome.

## Set up

```sh
python3 -m venv .venv
.venv/bin/pip install "git+https://github.com/autonomous-ai/lampOS@lamp-voice#subdirectory=lampOS/sdk/python"
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

The tests use the SDK's fake lamp: no lamp or pairing needed.

## Adding a lesson

1. Add a TOML file to `src/lamp_learn/lessons/` (format in the README).
2. Check it against the lamp's limits: `.venv/bin/lamp-learn check src/lamp_learn/lessons/your-lesson.toml`.
3. Try it with `.venv/bin/lamp-learn demo your-lesson-id`, and on a lamp or lamp-sim if you can.

## Changing a skill

Skills in `src/lamp_learn/skills/` must pass `.venv/bin/lamp skill check <folder>`.
Bump `version` in `skill.toml` on any change, so lamps pick up the new one.

## Pull requests

- One change per pull request, with tests where they make sense; `pytest -q` passes.
- Say what you tried it on: fake lamp, lamp-sim or a real lamp.
- Never commit pairing files, pins, codes, lamp addresses or progress files.
  Use placeholders (HOST, CODE, PIN) in docs.

By contributing you agree your work is licensed under Apache-2.0.
