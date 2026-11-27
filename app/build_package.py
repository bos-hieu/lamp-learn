"""Build lamp-learn's Store package (format 2, lampOS docs/apps.md).

    python3 app/build_package.py [--min-build N] [--out dist/]

Writes `lamp-learn-<version>.tar`: `lamp-skill.json` at the top and one folder
`lamp-learn/` with the app's Python under `src/`, the two clips under
`assets/`, English Tutor under `skills/english-tutor/`. The
Store checks, signs (`lamp-skill.sig`) and lists it; this script never signs.
Only what runs on the lamp goes in: no CLI, web page, MCP server or voice test.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "lamp_learn"
MANIFEST = ROOT / "app" / "lamp-skill.draft.json"
# The modules the app runs; the rest are the computer's tools.
MODULES = ["__init__.py", "app.py", "coach.py", "freetalk.py", "hearing.py", "lesson.py", "listen.py", "progress.py", "sounds.py"]
SKILL = "english-tutor"


def files(with_skills: bool = True) -> dict[str, bytes]:
    """Every file of the app folder, by its path inside it."""
    sys.path.insert(0, str(ROOT / "src"))
    from lamp_learn import sounds

    out: dict[str, bytes] = {}
    for name in MODULES:
        out[f"src/lamp_learn/{name}"] = (PKG / name).read_bytes()
    for toml in sorted((PKG / "lessons").glob("*.toml")):
        out[f"src/lamp_learn/lessons/{toml.name}"] = toml.read_bytes()
    for part in sorted((PKG / "skills" / SKILL).iterdir()) if with_skills else []:
        if part.is_file() and not part.name.startswith("."):
            out[f"skills/{SKILL}/{part.name}"] = part.read_bytes()
    out[f"assets/{sounds.RIGHT}.wav"] = sounds.right_chime()
    out[f"assets/{sounds.MISS}.wav"] = sounds.miss_tone()
    # No README: an app's files are only src/, assets/ (.wav) and skills/.
    return out


def build(min_build: int, out_dir: Path, with_skills: bool = True) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["min_build"] = min_build
    if not with_skills:
        manifest.pop("skills", None)
    body = files(with_skills)
    manifest["assets"] = sorted(p for p in body if p.startswith("assets/"))
    manifest["files"] = [
        {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for path, data in sorted(body.items())
    ]
    raw = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    name = manifest["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    tar_path = out_dir / f"{name}-{manifest['version']}.tar"

    def add(tar: tarfile.TarFile, path: str, data: bytes) -> None:
        info = tarfile.TarInfo(path)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tar:
        add(tar, "lamp-skill.json", raw)
        for path, data in sorted(body.items()):
            add(tar, f"{name}/{path}", data)
    return tar_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-build", type=int, default=14072, help="the lamp build sequence number it needs")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--no-skills", action="store_true", help="leave English Tutor out (an app with no companion)")
    args = parser.parse_args()
    path = build(args.min_build, args.out, with_skills=not args.no_skills)
    print(f"{path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
