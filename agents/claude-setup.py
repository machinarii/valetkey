#!/usr/bin/env python3
"""Add valetkey's rules to Claude Code (CLAUDE.md, settings.json) and Codex (AGENTS.md) on an agent machine.

  ./install.sh claude              ask what to block, show the changes, then write them
  ./install.sh claude --yes        take every recommended answer
  ./install.sh claude --dry-run    show what would change and write nothing

It merges into what is already there: no existing rule or setting is removed, and running it
again changes nothing. Each file is copied to <name>.before-valetkey-<time> before it is written.

These are your own user settings, so the agent's user can change them back. They reduce accidents;
they are not a boundary. For rules the agent cannot undo, also run: sudo ./install.sh agent
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
import time
import urllib.parse

BEGIN = "<!-- valetkey:begin (written by `install.sh claude`; edit the source files, not this block) -->"
END = "<!-- valetkey:end -->"

# Where tools commonly keep a login. Blocked for the Read tool and for sandboxed commands.
LOGIN_FOLDERS = [
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.config/gh", "~/.config/gcloud", "~/.config/doctl",
    "~/.config/rclone", "~/.config/stripe", "~/.config/op", "~/.config/Bitwarden CLI", "~/.vercel",
    "~/.cloudflared", "~/.azure", "~/.oci", "~/.huggingface",
]
LOGIN_FILES = [
    "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.git-credentials", "~/.docker/config.json",
    "~/.cache/huggingface/token", "~/.claude/.credentials.json", "~/.codex/auth.json",
    "~/.bash_history", "~/.zsh_history",
]
# Files that usually hold a key, wherever they are. `.env.example` stays readable on purpose.
KEY_FILES = ["**/.env", "**/.env.local", "**/.env.production", "**/.env.*.local", "**/*.pem", "**/*.key",
             "**/*.p12", "**/*.pfx", "**/credentials.json", "**/service-account*.json", "**/*.tfvars",
             "**/*.tfstate"]
# Commands whose purpose is to show a secret. A speed bump: a value can be reached other ways.
SECRET_COMMANDS = [
    "printenv*", "env", "gh auth token*", "gh auth status --show-token*", "op read *", "op item get *",
    "bw get *", "bw list *", "bw export *", "bw unlock*", "bw login*", "fnox get *", "fnox export *",
    "pass show *", "secret-tool *", "vault *", "vault-entry *", "security find-generic-password*",
    "security find-internet-password*",
]

QUESTIONS = [  # key, question, recommended answer, one line of why
    ("rules", "Add valetkey's rules for agents to CLAUDE.md?", True,
     "Tells the agent to ask for keys and tools by name and never to read, print or store one."),
    ("sandbox", "Turn on Claude Code's command sandbox?", True,
     "Commands the agent runs then see only the files and network hosts you allow."),
    ("files", "Block the agent from reading login and key files (SSH keys, cloud logins, .env, tokens)?", True,
     "Covers the Read tool and sandboxed commands. .env.example files stay readable."),
    ("commands", "Block commands that exist to print a secret (env, printenv, gh auth token, vault, password managers)?", True,
     "A speed bump, not a wall: it stops the obvious ways, and the agent is told not to look for others."),
    ("connectors", "Turn off the connectors that come from your claude.ai account (Drive, Notion, GitHub and so on)?", True,
     "They are logins held on Anthropic's side that any session signed in as you can be offered."),
    ("escape", "Stop commands from ever running outside the sandbox?", False,
     "Safer, but anything that needs to leave it (Docker, some build tools) will stop working."),
]


def ask(question, default, why, assume):
    if assume:
        return default
    print(f"\n{question}\n  {why}")
    try:
        answer = input(f"  [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer.startswith("y")


def add(target, items):
    """Append the items that are not there yet; return how many were added."""
    new = [i for i in items if i not in target]
    target.extend(new)
    return len(new)


def table(parent, key, kind):
    value = parent.setdefault(key, kind())
    if not isinstance(value, kind):
        sys.exit(f"claude-setup: settings.json has an unexpected value at '{key}'; nothing was changed")
    return value


def plan_settings(settings, answers, hosts, env_names):
    """Change `settings` in place; return one line per change, for the owner to read."""
    lines = []
    deny = table(table(settings, "permissions", dict), "deny", list)
    sandbox = table(settings, "sandbox", dict)
    if answers["sandbox"] and sandbox.get("enabled") is not True:
        sandbox["enabled"] = True
        lines.append("turn the command sandbox on")
    if answers["files"]:
        rules = [f"Read({p}/**)" for p in LOGIN_FOLDERS] + [f"Read({p})" for p in LOGIN_FILES + KEY_FILES]
        n = add(deny, rules)
        m = add(table(table(sandbox, "filesystem", dict), "denyRead", list), LOGIN_FOLDERS + LOGIN_FILES + KEY_FILES)
        if n or m:
            lines.append(f"block reading login and key files ({n} rules for the Read tool, {m} for sandboxed commands)")
    if answers["commands"]:
        n = add(deny, [f"Bash({c})" for c in SECRET_COMMANDS])
        if n:
            lines.append(f"block {n} commands that print secrets")
    if answers["connectors"] and settings.get("disableClaudeAiConnectors") is not True:
        settings["disableClaudeAiConnectors"] = True
        lines.append("turn off claude.ai account connectors on this machine")
    if answers["escape"] and sandbox.get("allowUnsandboxedCommands") is not False:
        sandbox["allowUnsandboxedCommands"] = False
        lines.append("never run a command outside the sandbox")
    if hosts:
        n = add(table(table(sandbox, "network", dict), "allowedDomains", list), hosts)
        if n:
            lines.append("let sandboxed commands reach " + ", ".join(hosts))
    if env_names:
        entries = table(table(sandbox, "credentials", dict), "envVars", list)
        have = {e.get("name") for e in entries if isinstance(e, dict)}
        new = [n for n in env_names if n not in have]
        entries.extend({"name": n, "mode": "deny"} for n in new)
        if new:
            lines.append("remove these variables from the agent's commands: " + ", ".join(new))
    return lines


def plan_rules(text, block):
    """CLAUDE.md with the valetkey block added or refreshed; None when it is already current."""
    marked = f"{BEGIN}\n{block.strip()}\n{END}\n"
    if BEGIN in text and END in text:
        new = text[:text.index(BEGIN)] + marked + text[text.index(END) + len(END):].lstrip("\n")
    else:
        new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + marked
    return None if new == text else new


def policy_rules(path):
    """The standing instructions a root-owned policy already gives the agent; '' when there are none."""
    try:
        with open(path) as f:
            return str(json.load(f).get("claudeMd", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def host_of(url):
    return urllib.parse.urlsplit(url.strip()).hostname or ""


def first_line(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return f.readline().strip()
    except OSError:
        return ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.before-valetkey-{time.strftime('%Y%m%d-%H%M%S')}")
    with open(path + ".new", "w") as f:
        f.write(text)
    os.replace(path + ".new", path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="install.sh claude", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--yes", action="store_true", help="take every recommended answer")
    ap.add_argument("--dry-run", action="store_true", help="show the changes and write nothing")
    ap.add_argument("--settings", default="~/.claude/settings.json")
    ap.add_argument("--claude-md", default="~/.claude/CLAUDE.md")
    ap.add_argument("--codex-md", default="~/.codex/AGENTS.md",
                    help="Codex's rules file; offered only when its folder exists (Codex is installed)")
    ap.add_argument("--policy", default="/etc/claude-code/managed-settings.json",
                    help="the root-owned policy file; rules it already holds are not added a second time")
    ap.add_argument("--relay-url", default=None, help="the broker's address (default: what the client saved)")
    ap.add_argument("--desk-url", default=None, help="the desk's address (default: /etc/bellhop/url)")
    ap.add_argument("--hide-env", default="", help="comma-separated variable names to remove from the agent's commands")
    args = ap.parse_args(argv)
    settings_path, rules_path = os.path.expanduser(args.settings), os.path.expanduser(args.claude_md)
    codex_path = os.path.expanduser(args.codex_md)
    codex = os.path.isdir(os.path.dirname(codex_path))
    print("This sets up Claude Code" + (" and Codex" if codex else "") + " on this machine for valetkey.\n"
          "Intent: an agent here asks for keys and tools by name, and cannot easily read, print or copy a\n"
          "key or login. " + ("--yes: taking the recommended answer to each question without asking."
                               if args.yes else "Each change is explained and asked about; nothing is\n"
                               "written until you say yes at the end."))

    try:
        with open(settings_path) as f:
            before = json.load(f)
    except FileNotFoundError:
        before = {}
    except ValueError:
        sys.exit(f"claude-setup: {settings_path} is not valid JSON; fix it first. Nothing was changed.")
    if not isinstance(before, dict):
        sys.exit(f"claude-setup: {settings_path} is not a settings object. Nothing was changed.")

    relay = args.relay_url if args.relay_url is not None else (
        os.environ.get("RELAY_URL") or first_line("~/.config/valetkey/relay-url"))
    desk = args.desk_url if args.desk_url is not None else first_line("/etc/bellhop/url")
    hosts = [h for h in dict.fromkeys(host_of(u) for u in (relay, desk)) if h]
    env_names = [n for n in (x.strip() for x in args.hide_env.split(",")) if n]
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n) for n in env_names):
        sys.exit("claude-setup: --hide-env takes variable names only, never values")

    block = open(os.path.join(args.repo, "agents", "credential-rules.md")).read()
    in_policy = block.strip() in policy_rules(args.policy)  # `install.sh agent` put them there: twice costs tokens
    if in_policy:
        print("The root-owned policy already gives the agent valetkey's rules; not adding them to CLAUDE.md as well.")
    answers = {key: ask(q, default, why, args.yes) for key, q, default, why in QUESTIONS
               if not (in_policy and key == "rules")}
    answers.setdefault("rules", False)
    # The root policy reaches Claude Code only, so Codex gets the rules even when the policy has them.
    answers["codex"] = codex and ask(
        "Add valetkey's rules for agents to Codex's AGENTS.md?", True,
        "Codex reads AGENTS.md the way Claude Code reads CLAUDE.md.", args.yes)
    if not args.yes and not env_names:
        print("\nVariable names to remove from the agent's commands, separated by commas (names only; Enter for none):")
        try:
            typed = input("  ").strip()
        except EOFError:
            typed = ""
        env_names = [n for n in (x.strip() for x in typed.split(",")) if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)]

    after = copy.deepcopy(before)
    changes = plan_settings(after, answers, hosts, env_names)
    if desk:
        block += "\n" + open(os.path.join(args.repo, "bellhop", "agent-rules.md")).read()

    def planned(path):
        try:
            current = open(path).read()
        except FileNotFoundError:
            current = ""
        return plan_rules(current, block)
    new_rules = planned(rules_path) if answers["rules"] else None
    new_codex = planned(codex_path) if answers["codex"] else None

    print(f"\n{settings_path}:")
    print("\n".join("  + " + c for c in changes) or "  nothing to change")
    print(f"{rules_path}:")
    print("  + valetkey's rules for agents" + (" and the desk's" if desk else "") if new_rules is not None
          else "  nothing to change")
    if answers["codex"]:
        print(f"{codex_path}:")
        print("  + the same rules, for Codex" if new_codex is not None else "  nothing to change")
    if args.dry_run or not (changes or new_rules is not None or new_codex is not None):
        print("\nNothing written." if args.dry_run else "\nAlready up to date.")
        return 0
    if not args.yes:
        try:
            if not input("\nWrite these changes? [y/N] ").strip().lower().startswith("y"):
                print("Nothing written.")
                return 0
        except EOFError:
            print("Nothing written.")
            return 0
    if changes:
        write(settings_path, json.dumps(after, indent=2) + "\n")
    if new_rules is not None:
        write(rules_path, new_rules)
    if new_codex is not None:
        write(codex_path, new_codex)
    print("\nWritten. The previous files are kept beside them as *.before-valetkey-<time>.")
    print("Start a new Claude Code (or Codex) session for the changes to take effect.")
    print("The agent's user can edit these files. For rules it cannot undo: sudo ./install.sh agent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
