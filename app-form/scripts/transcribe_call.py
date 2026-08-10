"""Transcribe a call recording or voice memo to text, ready for cap-app extraction.

Self-contained (stdlib + ffmpeg): extracts mono 16 kHz audio, splits into
chunks under the Whisper API size limit, uploads to Groq (or OpenAI) Whisper,
and writes a plain-text transcript next to the audio file.

Usage:
    python transcribe_call.py "path/to/call.m4a" [out.txt]

API key: GROQ_API_KEY or OPENAI_API_KEY, from the environment or
~/.config/watch/.env (KEY=value lines).
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"
CHUNK_SECONDS = 1200  # 20 min at 64 kbps mono ~= 9.6 MB, safely under limits


def load_key() -> tuple[str, str]:
    def from_dotenv(name: str) -> str | None:
        path = Path.home() / ".config" / "watch" / ".env"
        if not path.exists():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
        return None

    for env, backend, endpoint, model in (
        ("GROQ_API_KEY", "groq", GROQ_ENDPOINT, GROQ_MODEL),
        ("OPENAI_API_KEY", "openai", OPENAI_ENDPOINT, OPENAI_MODEL),
    ):
        key = os.environ.get(env) or from_dotenv(env)
        if key:
            return endpoint, model, key
    raise SystemExit("No GROQ_API_KEY / OPENAI_API_KEY found (env or ~/.config/watch/.env).")


def extract_audio(src: str, out_dir: Path) -> Path:
    out = out_dir / "call_16k.mp3"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {result.stderr.strip()}")
    return out


def duration_seconds(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    return float(result.stdout.strip() or 0)


def split_chunks(audio: Path, out_dir: Path) -> list[Path]:
    total = duration_seconds(audio)
    if total <= CHUNK_SECONDS:
        return [audio]
    chunks = []
    start, i = 0, 0
    while start < total:
        chunk = out_dir / f"chunk_{i}.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(start), "-t", str(CHUNK_SECONDS), "-i", str(audio),
             "-acodec", "copy", str(chunk)],
            capture_output=True, text=True)
        chunks.append(chunk)
        start += CHUNK_SECONDS
        i += 1
    return chunks


def whisper(endpoint: str, model: str, key: str, audio: Path) -> str:
    boundary = f"----CallBoundary{uuid.uuid4().hex}"
    buf = io.BytesIO()
    for name, value in (("model", model), ("response_format", "json"), ("temperature", "0")):
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    mime = mimetypes.guess_type(audio.name)[0] or "audio/mpeg"
    buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"{audio.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    buf.write(audio.read_bytes())
    buf.write(f"\r\n--{boundary}--\r\n".encode())

    req = Request(endpoint, data=buf.getvalue(), method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "cap-app-transcribe/1.0 (python-urllib)",
    })
    try:
        with urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))["text"].strip()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Whisper HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = sys.argv[1]
    if not os.path.exists(src):
        raise SystemExit(f"File not found: {src}")
    out_txt = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(src).with_suffix(".transcript.txt")

    endpoint, model, key = load_key()
    with tempfile.TemporaryDirectory(prefix="cap-call-") as tmp:
        tmp_dir = Path(tmp)
        audio = extract_audio(src, tmp_dir)
        chunks = split_chunks(audio, tmp_dir)
        print(f"[transcribe] {len(chunks)} chunk(s), backend={model}", file=sys.stderr)
        parts = [whisper(endpoint, model, key, c) for c in chunks]

    text = "\n".join(parts).strip()
    out_txt.write_text(text, encoding="utf-8")
    print(f"[transcribe] {len(text)} chars -> {out_txt}", file=sys.stderr)
    print(text[:500] + ("..." if len(text) > 500 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
