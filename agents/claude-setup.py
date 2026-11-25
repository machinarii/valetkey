#!/usr/bin/env python3
"""Add valetkey's rules to Claude Code (CLAUDE.md, settings.json) and Codex (AGENTS.md) on an agent machine.

  ./install.sh claude              ask what to block, show the changes, then write them
  ./install.sh claude --yes        take every recommended answer
  ./install.sh claude --dry-run    show what would change and write nothing

Run it again to change an answer: a no removes what an earlier yes added, and a new broker address
replaces the old one. Rules and settings you added yourself are never removed: what valetkey added is
recorded in valetkey-setup.json next to settings.json. Each file is copied to
<name>.before-valetkey-<time> before it is written.

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
# Fixed paths only. Claude Code merges Read() deny rules into the Bash sandbox, which turns a
# pattern like **/.env into one mount per matching file; across a home folder that passes
# bubblewrap's limit and no command can start. The agent rules cover .env and key files.
# Commands whose purpose is to show a secret. A speed bump: a value can be reached other ways.
SECRET_COMMANDS = [
    "printenv*", "env", "gh auth token*", "gh auth status --show-token*", "op read *", "op item get *",
    "bw get *", "bw list *", "bw export *", "bw unlock*", "bw login*", "fnox get *", "fnox export *",
    "pass show *", "secret-tool *", "vault *", "valetkey-entry *", "security find-generic-password*",
    "security find-internet-password*",
]

QUESTIONS = [  # key, question, recommended answer, one line of why
    ("rules", "Add valetkey's rules for agents to CLAUDE.md?", True,
     "Tells the agent to ask for keys and tools by name and never to read, print or store one."),
    ("sandbox", "Turn on Claude Code's command sandbox?", True,
     "Commands the agent runs then see only the files and network hosts you allow."),
    ("files", "Block the agent from reading login files (SSH keys, cloud logins, tokens, shell history)?", True,
     "Covers the Read tool and sandboxed commands, at their fixed places in your home folder."),
    ("commands", "Block commands that exist to print a secret (env, printenv, gh auth token, vault, password managers)?", True,
     "A speed bump, not a wall: it stops the obvious ways, and the agent is told not to look for others."),
    ("connectors", "Turn off the connectors that come from your claude.ai account (Drive, Notion, GitHub and so on)?", True,
     "They are logins held on Anthropic's side that any session signed in as you can be offered."),
    ("escape", "Stop commands from ever running outside the sandbox?", False,
     "Safer, but anything that needs to leave it (Docker, some build tools) will stop working."),
]


def confirm(prompt):
    while True:
        answer = input(f"{prompt} [y/n] ")
        if answer in ("y", "n"):
            return answer == "y"
        print("Enter y or n.")


def ask(question, default, why, assume):
    if assume:
        return default
    print(f"\n{question}\n  {why}")
    return confirm(f"  Recommended: {'y' if default else 'n'}.")


def table(parent, key, kind):
    value = parent.setdefault(key, kind())
    if not isinstance(value, kind):
        sys.exit(f"claude-setup: settings.json has an unexpected value at '{key}'; nothing was changed")
    return value


def sync(target, wanted, owned):
    """Make `target` hold `wanted`, removing only what valetkey added before (`owned`).

    Entries that were there already are left alone and never recorded, so a later no keeps them.
    Returns (added, removed)."""
    gone = [i for i in owned if i not in wanted and i in target]
    for i in gone:
        target.remove(i)
    new = [i for i in wanted if i not in target]
    target.extend(new)
    owned[:] = [i for i in owned if i in wanted] + [i for i in new if i not in owned]
    return len(new), len(gone)


def sync_flag(parent, key, want, record, name):
    """Set parent[key] = want (want is not None), or undo valetkey's earlier setting (want is None).

    An undo only happens while the value is still the one valetkey set; the previous value returns."""
    if want is not None:
        if parent.get(key) == want:
            return ""
        record.setdefault(name, {"had": key in parent, "before": parent.get(key), "value": want})
        parent[key] = want
        return "+"
    r = record.get(name)
    if r is None or parent.get(key) != r["value"]:
        record.pop(name, None)
        return ""
    if r["had"]:
        parent[key] = r["before"]
    else:
        parent.pop(key, None)
    record.pop(name)
    return "-"


def blank_state():
    return {"deny": [], "denyRead": [], "commands": [], "hosts": [], "env": [], "flags": {}}


def file_rules():
    return [f"Read({p}/**)" for p in LOGIN_FOLDERS] + [f"Read({p})" for p in LOGIN_FILES]


def plan_settings(settings, answers, hosts, env_names, state):
    """Change `settings` (and the record `state`) in place; return one '+ ...' / '- ...' line per change."""
    lines = []
    deny = table(table(settings, "permissions", dict), "deny", list)
    sandbox = table(settings, "sandbox", dict)
    flags = state["flags"]

    def note(sign, on, off):
        if sign:
            lines.append(f"+ {on}" if sign == "+" else f"- {off}")

    note(sync_flag(sandbox, "enabled", True if answers["sandbox"] else None, flags, "sandbox"),
         "turn the command sandbox on", "turn the command sandbox back off (valetkey turned it on)")
    n, r = sync(deny, file_rules() if answers["files"] else [], state["deny"])
    m, q = sync(table(table(sandbox, "filesystem", dict), "denyRead", list),
                LOGIN_FOLDERS + LOGIN_FILES if answers["files"] else [], state["denyRead"])
    if n or m:
        lines.append(f"+ block reading login files ({n} rules for the Read tool, {m} for sandboxed commands)")
    if r or q:
        lines.append(f"- stop blocking login files ({r} Read rules, {q} sandbox rules valetkey added)")
    n, r = sync(deny, [f"Bash({c})" for c in SECRET_COMMANDS] if answers["commands"] else [], state["commands"])
    if n:
        lines.append(f"+ block {n} commands that print secrets")
    if r:
        lines.append(f"- unblock {r} commands valetkey blocked")
    note(sync_flag(settings, "disableClaudeAiConnectors", True if answers["connectors"] else None, flags, "connectors"),
         "turn off claude.ai account connectors on this machine", "turn claude.ai account connectors back on")
    note(sync_flag(sandbox, "allowUnsandboxedCommands", False if answers["escape"] else None, flags, "escape"),
         "never run a command outside the sandbox", "allow commands outside the sandbox again")
    if hosts:  # unknown addresses (nothing configured) leave the earlier ones in place
        n, r = sync(table(table(sandbox, "network", dict), "allowedDomains", list), hosts, state["hosts"])
        if n:
            lines.append("+ let sandboxed commands reach " + ", ".join(hosts))
        if r:
            lines.append(f"- remove {r} old broker or bellhop address(es)")
    if env_names is not None:
        entries = table(table(sandbox, "credentials", dict), "envVars", list)
        names = [e.get("name") for e in entries if isinstance(e, dict)]
        gone = [n for n in state["env"] if n not in env_names and n in names]
        entries[:] = [e for e in entries if not (isinstance(e, dict) and e.get("name") in gone)]
        new = [n for n in env_names if n not in names]
        entries.extend({"name": n, "mode": "deny"} for n in new)
        state["env"] = [n for n in state["env"] if n in env_names] + [n for n in new if n not in state["env"]]
        if new:
            lines.append("+ remove these variables from the agent's commands: " + ", ".join(new))
        if gone:
            lines.append("- give these variables back to the agent's commands: " + ", ".join(gone))
    return lines


def plan_rules(text, block):
    """The rules file with valetkey's block added or refreshed, or removed when `block` is None.
    None when nothing changes."""
    has = BEGIN in text and END in text
    if block is None:
        if not has:
            return None
        before = text[:text.index(BEGIN)].rstrip("\n")
        after = text[text.index(END) + len(END):].lstrip("\n")
        return before + ("\n\n" + after if before and after else "\n" if before else after)
    marked = f"{BEGIN}\n{block.strip()}\n{END}\n"
    if has:
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


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.before-valetkey-{time.strftime('%Y%m%d-%H%M%S')}")
    with open(path + ".new", "w") as f:
        f.write(text)
    os.replace(path + ".new", path)


NAME_RE = r"[A-Za-z_][A-Za-z0-9_]*"


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
    ap.add_argument("--broker-url", default=None, help="the broker's address (default: what the client saved)")
    ap.add_argument("--bellhop-url", default=None, help="the bellhop's address (default: /etc/bellhop/url)")
    ap.add_argument("--hide-env", default=None, help="comma-separated variable names to remove from the agent's commands")
    args = ap.parse_args(argv)
    print("Claude subscription login options: claude setup-token or native web login with claude auth login.\n"
          "Run valetkey-broker guide claude for steps. This installer preserves your current login method.\n"
          "Valetkey works with or without claude-mem. Run valetkey-broker guide claude-mem for memory capture setup.")
    settings_path, rules_path = os.path.expanduser(args.settings), os.path.expanduser(args.claude_md)
    codex_path = os.path.expanduser(args.codex_md)
    state_path = os.path.join(os.path.dirname(settings_path), "valetkey-setup.json")
    codex = os.path.isdir(os.path.dirname(codex_path))
    print("This sets up Claude Code" + (" and Codex" if codex else "") + " on this machine for valetkey.\n"
          "Intent: an agent here asks for keys and tools by name, and cannot easily read, print or copy a\n"
          "key or login. " + ("--yes: taking the recommended answer to each question without asking."
                               if args.yes else "Each change is explained and asked about; nothing is\n"
                               "written until you enter y at the end. Run again to change an answer."))

    try:
        with open(settings_path) as f:
            before = json.load(f)
    except FileNotFoundError:
        before = {}
    except ValueError:
        sys.exit(f"claude-setup: {settings_path} is not valid JSON; fix it first. Nothing was changed.")
    if not isinstance(before, dict):
        sys.exit(f"claude-setup: {settings_path} is not a settings object. Nothing was changed.")
    state = blank_state()
    try:
        with open(state_path) as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state.update({k: v for k, v in loaded.items() if k in state and isinstance(v, type(state[k]))})
    except (OSError, ValueError):
        pass

    broker = args.broker_url if args.broker_url is not None else (
        os.environ.get("BROKER_URL") or first_line("~/.config/valetkey/broker-url") or first_line("~/.config/valetkey/relay-url"))
    bellhop = args.bellhop_url if args.bellhop_url is not None else first_line("/etc/bellhop/url")
    hosts = [h for h in dict.fromkeys(host_of(u) for u in (broker, bellhop)) if h]
    env_names = None if args.hide_env is None else [n for n in (x.strip() for x in args.hide_env.split(",")) if n]
    if env_names and any(not re.fullmatch(NAME_RE, n) for n in env_names):
        sys.exit("claude-setup: --hide-env takes variable names only, never values")

    block = open(os.path.join(args.repo, "agents", "credential-rules.md")).read()
    in_policy = block.strip() in policy_rules(args.policy)  # `install.sh agent` put them there: twice costs tokens
    if bellhop:
        block += "\n" + open(os.path.join(args.repo, "bellhop", "agent-rules.md")).read()
    claude_md, codex_md = read_text(rules_path), read_text(codex_path)
    if in_policy:
        print("The root-owned policy already gives the agent valetkey's rules; they are not added to CLAUDE.md as well.")
    answers = {key: ask(q, default, why, args.yes) for key, q, default, why in QUESTIONS
               if not (in_policy and key == "rules")}
    answers.setdefault("rules", False)
    # The root policy reaches Claude Code only, so Codex gets the rules even when the policy has them.
    answers["codex"] = codex and ask(
        "Add valetkey's rules for agents to Codex's AGENTS.md?", True,
        "Codex reads AGENTS.md the way Claude Code reads CLAUDE.md.", args.yes)
    if not args.yes and env_names is None:
        current = ", ".join(state["env"])
        print("\nVariable names to remove from the agent's commands, separated by commas (names only; "
              + (f"Enter keeps [{current}], - for none):" if current else "Enter for none):"))
        try:
            typed = input("  ").strip()
        except EOFError:
            typed = ""
        if typed == "-":
            env_names = []
        elif typed:
            env_names = [n for n in (x.strip() for x in typed.split(",")) if re.fullmatch(NAME_RE, n)]
        elif not current:
            env_names = []

    after = copy.deepcopy(before)
    old_state = json.dumps(state, sort_keys=True)
    changes = plan_settings(after, answers, hosts, env_names, state)
    new_rules = plan_rules(claude_md, block if answers["rules"] else None)
    new_codex = plan_rules(codex_md, block if answers["codex"] else None) if codex else None

    def what(new, text, added):
        if new is None:
            return "  nothing to change"
        return "  - remove valetkey's rules" if BEGIN not in new else ("  + " + added if BEGIN not in text
                                                                        else "  + update valetkey's rules")
    print(f"\n{settings_path}:")
    print("\n".join("  " + c for c in changes) or "  nothing to change")
    print(f"{rules_path}:")
    print(what(new_rules, claude_md, "valetkey's rules for agents" + (" and the bellhop's" if bellhop else "")))
    if codex:
        print(f"{codex_path}:")
        print(what(new_codex, codex_md, "the same rules, for Codex"))
    if args.dry_run or not (changes or new_rules is not None or new_codex is not None):
        print("\nNothing written." if args.dry_run else "\nAlready up to date.")
        return 0
    if not args.yes and not confirm("\nWrite these changes?"):
        print("Nothing written.")
        return 0
    if changes:
        write(settings_path, json.dumps(after, indent=2) + "\n")
    if json.dumps(state, sort_keys=True) != old_state:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
    if new_rules is not None:
        write(rules_path, new_rules)
    if new_codex is not None:
        write(codex_path, new_codex)
    print("\nWritten. The previous files are kept beside them as *.before-valetkey-<time>.")
    print("Start a new Claude Code (or Codex) session for the changes to take effect.")
    print("The agent's user can edit these files. For rules it cannot undo: sudo ./install.sh agent")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EOFError:
        print("Nothing written.")
