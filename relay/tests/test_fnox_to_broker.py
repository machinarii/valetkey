import importlib.util
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "fnox-to-broker.py"

SAMPLE = """\
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ KEY                    ┃ PROVIDER┃ VALUE               ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ SHOP_PGPASSWORD │ age     │ SUPERSECRETVALUE111 │
│ DATABASE_URL           │ age     │ postgres://u:HUNTER2 │
│ ACME_API_URL          │ plain   │ https://x.example   │
└────────────────────────┴─────────┴─────────────────────┘
== production
┃ KEY                    ┃ PROVIDER┃ VALUE               ┃
│ DATABASE_URL           │ age     │ PRODVALUE999        │
│ STRIPE_KEY             │ age     │ PRODVALUE888        │
"""


def load():
    spec = importlib.util.spec_from_file_location("fnox_to_relay", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(args, stdin):
    env = {k: v for k, v in os.environ.items() if k != "BROKER_URL"}
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin,
                          capture_output=True, text=True, env=env)


ARGS = ["--project", "shop", "--env", "qa", "--org", "infra"]


class FnoxToRelayTest(unittest.TestCase):
    def test_item_name_rule(self):
        m = load()
        self.assertEqual(m.item_name("SHOP_PGPASSWORD", "shop", "qa"),
                         "pgpassword-shop-qa")
        self.assertEqual(m.item_name("ACME_API_URL", "shop", "prod"),
                         "acme-api-url-shop-prod")
        self.assertEqual(m.item_name("MY_PROJ_X_KEY", "my-proj-x", "dev"), "key-my-proj-x-dev")

    def test_project_must_be_kebab_case(self):
        r = run(["--project", "my.proj_x", "--env", "qa", "--org", "infra"], SAMPLE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("kebab-case", r.stderr)

    def test_generates_default_and_production_sections(self):
        r = run(ARGS, SAMPLE)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = tomllib.loads(r.stdout)
        p = t["providers"]["relay"]
        self.assertEqual(p, {"type": "vault",
                             "address": "https://broker.example.ts.net/infra",
                             "path": "shop", "token": "tailscale"})
        self.assertEqual(set(t["secrets"]), {"SHOP_PGPASSWORD", "DATABASE_URL", "ACME_API_URL"})
        self.assertEqual(t["secrets"]["DATABASE_URL"],
                         {"provider": "relay", "value": "database-url-shop-qa",
                          "if_missing": "error"})
        prod = t["profiles"]["production"]["secrets"]
        self.assertEqual(set(prod), {"DATABASE_URL", "STRIPE_KEY"})
        self.assertEqual(prod["STRIPE_KEY"]["value"], "stripe-key-shop-prod")
        self.assertNotIn("KEY", t["secrets"])

    def test_no_values_in_output(self):
        r = run(ARGS + ["--broker-url", "https://b.example"], SAMPLE)
        for v in ("SUPERSECRETVALUE111", "HUNTER2", "PRODVALUE999", "PRODVALUE888", "postgres://"):
            self.assertNotIn(v, r.stdout)
        self.assertIn("names only", r.stdout.lower())
        self.assertIn('address = "https://b.example/infra"', r.stdout)

    def test_every_secret_fails_loudly_when_missing(self):
        t = tomllib.loads(run(ARGS, SAMPLE).stdout)
        secrets = list(t["secrets"].values()) + list(t["profiles"]["production"]["secrets"].values())
        self.assertEqual({s["if_missing"] for s in secrets}, {"error"})

    def test_notes_keys_get_notes_refs(self):
        r = run(ARGS + ["--notes", "SHOP_PGPASSWORD, STRIPE_KEY"], SAMPLE)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = tomllib.loads(r.stdout)
        self.assertEqual(t["secrets"]["SHOP_PGPASSWORD"]["value"], "pgpassword-shop-qa/notes")
        self.assertEqual(t["secrets"]["DATABASE_URL"]["value"], "database-url-shop-qa")
        prod = t["profiles"]["production"]["secrets"]
        self.assertEqual(prod["STRIPE_KEY"]["value"], "stripe-key-shop-prod/notes")
        r = run(ARGS + ["--notes", "NOT_LISTED"], SAMPLE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in the fnox list input: NOT_LISTED", r.stderr)

    def test_notes_keys_from_run_log(self):
        import tempfile
        log = ("ok    SHOP_PGPASSWORD -> infra-org/infra-broker-collection/shop as "
               "'shop SHOP_PGPASSWORD' (notes)\n"
               "ok    DATABASE_URL -> infra-org/infra-broker-collection/shop as "
               "'shop DATABASE_URL' (password)\n"
               "skip  ACME_API_URL: 'shop ACME_API_URL' already in infra-org\n")
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write(log)
            f.flush()
            r = run(ARGS + ["--run-log", f.name], SAMPLE)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = tomllib.loads(r.stdout)
        self.assertEqual(t["secrets"]["SHOP_PGPASSWORD"]["value"], "pgpassword-shop-qa/notes")
        self.assertEqual(t["secrets"]["DATABASE_URL"]["value"], "database-url-shop-qa")

    def test_env_must_be_dev_qa_or_prod(self):
        for args in (["--env", "staging"], ["--env", "qa", "--prod-env", "production"]):
            a = ["--project", "shop", "--org", "infra"] + args
            r = run(a, SAMPLE)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("must be one of dev|qa|prod", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_no_keys_exits_2(self):
        r = run(ARGS, "nothing here\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no keys", r.stderr)


if __name__ == "__main__":
    unittest.main()
