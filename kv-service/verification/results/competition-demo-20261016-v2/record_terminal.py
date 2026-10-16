from __future__ import annotations

# Record the stdout/stderr of a local demo command with original chunk timing.
import argparse
import codecs
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cast", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a command after --")
    with args.cast.open("x") as stream:
        stream.write(
            json.dumps(
                dict(
                    version=2,
                    width=125,
                    height=35,
                    timestamp=int(time.time()),
                    title="RailFuse live process-output capture",
                    env={"TERM": "xterm-256color"},
                )
            )
            + "\n"
        )
        started = time.monotonic()
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        while True:
            chunk = process.stdout.read1(4096)
            if not chunk:
                break
            text = decoder.decode(chunk)
            stream.write(
                json.dumps(
                    [round(time.monotonic() - started, 6), "o", text],
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()
            print(text, end="", flush=True)
        code = process.wait()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
