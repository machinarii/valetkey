"""install.sh claude: merges valetkey's rules into Claude Code's CLAUDE.md and settings.json."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "agents" / "claude-setup.py"


class ClaudeSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / "claude" / "settings.json"
        self.rules = Path(self.tmp.name) / "claude" / "CLAUDE.md"

    def run_setup(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--settings", str(self.settings), "--claude-md", str(self.rules),
             "--relay-url", "https://broker.example.ts.net", "--desk-url", "",
             "--policy", str(Path(self.tmp.name) / "no-policy.json"), *args],
            input=stdin, capture_output=True, text=True, timeout=30)

    def test_recommended_answers_merge_and_keep_what_was_there(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({
            "model": "kept", "permissions": {"allow": ["Bash(git status *)"], "deny": ["Read(~/mine/**)"]},
            "sandbox": {"network": {"allowedDomains": ["github.com"]}}}))
        self.rules.write_text("# My own notes\n\nKeep this line.\n")
        r = self.run_setup("--yes", "--hide-env", "SOME_TOKEN")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(self.settings.read_text())
        self.assertEqual(s["model"], "kept")
        self.assertEqual(s["permissions"]["allow"], ["Bash(git status *)"])
        deny = s["permissions"]["deny"]
        for rule in ("Read(~/mine/**)", "Read(~/.ssh/**)", "Read(~/.netrc)", "Read(**/.env)", "Bash(printenv*)", "Bash(vault *)"):
            self.assertIn(rule, deny)
        self.assertNotIn("Read(**/.env.example)", deny)
        self.assertTrue(s["sandbox"]["enabled"])
        self.assertIn("~/.ssh", s["sandbox"]["filesystem"]["denyRead"])
        self.assertEqual(s["sandbox"]["network"]["allowedDomains"], ["github.com", "broker.example.ts.net"])
        self.assertEqual(s["sandbox"]["credentials"]["envVars"], [{"name": "SOME_TOKEN", "mode": "deny"}])
        self.assertTrue(s["disableClaudeAiConnectors"])
        self.assertNotIn("allowUnsandboxedCommands", s["sandbox"])     # not a recommended default
        text = self.rules.read_text()
        self.assertTrue(text.startswith("# My own notes\n\nKeep this line.\n"))
        self.assertIn("valetkey:begin", text)
        self.assertEqual(len(list(self.settings.parent.glob("*.before-valetkey-*"))), 2)

    def test_running_twice_changes_nothing(self):
        self.assertEqual(self.run_setup("--yes").returncode, 0)
        first = (self.settings.read_text(), self.rules.read_text())
        again = self.run_setup("--yes")
        self.assertIn("Already up to date", again.stdout)
        self.assertEqual((self.settings.read_text(), self.rules.read_text()), first)
        self.assertEqual(first[1].count("valetkey:begin"), 1)

    def test_answers_decide_what_is_blocked(self):
        # rules: no, sandbox: yes, files: no, commands: yes, connectors: no, escape: yes, no variables, write: yes
        r = self.run_setup(stdin="n\ny\nn\ny\nn\ny\n\ny\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(self.settings.read_text())
        self.assertIn("Bash(printenv*)", s["permissions"]["deny"])
        self.assertNotIn("Read(~/.ssh/**)", s["permissions"]["deny"])
        self.assertNotIn("disableClaudeAiConnectors", s)
        self.assertIs(s["sandbox"]["allowUnsandboxedCommands"], False)
        self.assertFalse(self.rules.exists())

    def test_declining_at_the_end_and_dry_run_write_nothing(self):
        self.assertIn("Nothing written", self.run_setup(stdin="\n\n\n\n\n\n\nn\n").stdout)
        self.assertIn("Nothing written", self.run_setup("--yes", "--dry-run").stdout)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.rules.exists())

    def test_broken_settings_and_values_as_names_are_refused(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{ not json")
        r = self.run_setup("--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_text(), "{ not json")
        self.settings.write_text("{}")
        r = self.run_setup("--yes", "--hide-env", "TOKEN=abc123")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_text(), "{}")

    def test_rules_already_in_the_root_policy_are_not_added_twice(self):
        policy = Path(self.tmp.name) / "policy.json"
        policy.write_text(json.dumps({"claudeMd": (ROOT / "agents" / "credential-rules.md").read_text() + "\nmore"}))
        r = self.run_setup("--yes", "--policy", str(policy))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already gives the agent", r.stdout)
        self.assertFalse(self.rules.exists())
        self.assertTrue(json.loads(self.settings.read_text())["sandbox"]["enabled"])


if __name__ == "__main__":
    unittest.main()
