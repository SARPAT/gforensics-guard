"""Speak a line of video/reference_script.md aloud (edge-tts), for the narrated reference demo.

  python video/say.py --prepare        synthesize every SAY/ASK line to video/.audio/<ID>.mp3
  python video/say.py S3a              play one line (synthesizes it first if needed)
  python video/say.py --text "..."     speak free text (asks, honest corrections)
"""
import argparse, asyncio, re, shutil, subprocess, sys, tempfile
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent
SCRIPT = HERE / "reference_script.md"
AUDIO = HERE / ".audio"
VOICE = "en-US-AndrewNeural"
LINE = re.compile(r"^(?:SAY|ASK)\[(\w+)\]\s+(.+)$")


def lines():
    return {m[1]: m[2] for m in (LINE.match(l) for l in SCRIPT.read_text().splitlines()) if m}


def synth(text, path):
    asyncio.run(edge_tts.Communicate(text, VOICE).save(str(path)))


def play(path):
    for cmd in (["mpv", "--no-video", "--really-quiet"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["cvlc", "--play-and-exit"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd + [str(path)], check=False)
            return
    sys.exit("no audio player found (install mpv)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?")
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--text")
    a = ap.parse_args()
    AUDIO.mkdir(exist_ok=True)
    if a.text:
        with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
            synth(a.text, f.name)
            play(f.name)
        return
    all_lines = lines()
    if a.prepare:
        for k, t in all_lines.items():
            synth(t, AUDIO / f"{k}.mp3")
        print(f"prepared {len(all_lines)} lines in {AUDIO}")
        return
    path = AUDIO / f"{a.id}.mp3"
    if not path.exists():
        synth(all_lines[a.id], path)
    play(path)


if __name__ == "__main__":
    main()
