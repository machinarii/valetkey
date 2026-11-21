#!/usr/bin/env python3
"""Measure what valetkey's Claude Code setup adds to every session, in tokens and in time.

  python3 agents/token-cost.py [extra claude options, such as --model sonnet]

Run it on the agent machine as the agent's user. It sends one short prompt from four throwaway
folders (nothing added, the rules, the settings, both) and prints the prompt size and the response
time Claude Code reports for each. Your own ~/.claude files are not touched. Every run is a real,
small request on your Claude account: twelve in all.
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = "Reply with only the word: ok"
RUNS = 3
CASES = [("nothing added", False, False), ("rules in CLAUDE.md", True, False),
         ("settings (sandbox and blocks)", False, True), ("rules and settings", True, True)]


def prepare(folder, spare, rules, settings):
    """Write what `install.sh claude --yes` would: into the folder to test it, or out of the way."""
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "claude-setup.py"), "--yes",
                    "--claude-md", os.path.join(folder if rules else spare, "CLAUDE.md"),
                    "--settings", os.path.join(folder if settings else spare, "settings.json")],
                   check=True, stdout=subprocess.DEVNULL)


def measure(folder, extra):
    cmd = ["claude", "-p", PROMPT, "--output-format", "json"] + extra
    if os.path.exists(os.path.join(folder, "settings.json")):
        cmd += ["--settings", os.path.join(folder, "settings.json")]
    out = json.loads(subprocess.run(cmd, cwd=folder, check=True, capture_output=True, text=True).stdout)
    use = out["usage"]
    tokens = sum(use.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return tokens, out["duration_api_ms"], ", ".join(out.get("modelUsage", {}))


def main():
    extra = sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        rows = []
        for i, (name, rules, settings) in enumerate(CASES):
            folder, spare = os.path.join(tmp, f"case{i}"), os.path.join(tmp, f"spare{i}")  # same-length paths
            os.makedirs(folder)
            os.makedirs(spare)
            prepare(folder, spare, rules, settings)
            runs = [measure(folder, extra) for _ in range(RUNS)]
            rows.append((name, statistics.median(r[0] for r in runs), statistics.median(r[1] for r in runs), runs[0][2]))
    base = rows[0][1]
    print(f"model: {rows[0][3]}   (median of {RUNS} runs each)")
    print(f"{'':32}{'prompt tokens':>14}{'added':>8}{'response ms':>13}")
    for name, tokens, ms, _ in rows:
        print(f"{name:32}{tokens:>14,.0f}{tokens - base:>+8,.0f}{ms:>13,.0f}")


if __name__ == "__main__":
    main()
