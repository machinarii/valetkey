import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from broker.brokerlib import routes as routes_mod

TOOL = Path(__file__).resolve().parent.parent / "tools" / "gen-fnox-config.py"

ROUTES = """
[api.stripe-shop]
org      = "local"
item     = "stripe-api-key-shop-live"
upstream = "https://api.stripe.com"
auth     = "Bearer {}"
methods  = ["GET", "POST"]
paths    = ["/v1/"]
nodes    = ["laptop"]

[api.resend]
org      = "infra"
item     = "resend-api-key/notes"
upstream = "https://api.resend.com"
auth     = "Bearer {}"
methods  = ["POST"]
paths    = ["/emails"]
nodes    = ["devbox"]

[value.some-token]
org   = "infra"
item  = "some-token"
nodes = ["devbox"]
"""


def load_tool():
    spec = importlib.util.spec_from_file_location("gen_fnox_config", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GenFnoxConfigTests(unittest.TestCase):
    def setUp(self):
        self.gen = load_tool()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.routes_path = os.path.join(self.dir.name, "routes.toml")
        with open(self.routes_path, "w") as f:
            f.write(ROUTES)
        self.routes = routes_mod.load_routes(self.routes_path)

    def generate(self, org):
        text = self.gen.generate(self.routes, org, "org-id-123", None)
        return text, tomllib.loads(text)

    def test_generates_rules_for_org_only(self):
        text, conf = self.generate("infra")
        self.assertEqual(len(conf["proxy"]["rules"]), 1)
        self.assertNotIn("api.stripe.com", text)
        rule = conf["proxy"]["rules"][0]
        self.assertEqual(rule["domain"], "api.resend.com")
        self.assertEqual(rule["methods"], ["POST"])
        self.assertEqual(rule["paths"], ["/emails", "/emails/**"])
        self.assertEqual(conf["proxy"]["egress"], "strict")
        self.assertIs(conf["proxy"]["audit"], True)
        self.assertEqual(conf["providers"]["vw"],
                         {"type": "bitwarden", "organization_id": "org-id-123"})
        self.assertEqual(list(conf["secrets"]), ["BROKER_API_RESEND"])  # value routes not here

    def test_placeholder_and_env_false(self):
        _, conf = self.generate("local")
        secret = conf["secrets"]["BROKER_API_STRIPE_SHOP"]
        self.assertIs(secret["env"], False)
        self.assertEqual(secret["provider"], "vw")
        self.assertEqual(secret["if_missing"], "warn")  # one missing item must not stop the broker
        self.assertEqual(secret["value"], "stripe-api-key-shop-live")
        rule = conf["proxy"]["rules"][0]
        self.assertEqual(rule["secret"], "BROKER_API_STRIPE_SHOP")
        self.assertEqual(rule["placeholder"], "fnox-ph-stripe-shop-not-a-secret")
        self.assertEqual(rule["header"], "authorization")
        # The broker sends exactly this placeholder (broker_read.placeholder).
        from broker import broker_read
        self.assertEqual(rule["placeholder"], broker_read.placeholder("stripe-shop"))

    def test_model_rules_match_only_the_selected_provider_endpoints(self):
        for client, provider, host, header, paths in (
            ("claude", "anthropic", "api.anthropic.com", "x-api-key", ["/v1/messages", "/v1/messages/count_tokens"]),
            ("codex", "openai", "api.openai.com", "authorization", ["/v1/responses", "/v1/responses/compact"]),
            ("claude", "openrouter", "openrouter.ai", "authorization", ["/api/v1/messages", "/api/v1/messages/count_tokens"]),
            ("codex", "openrouter", "openrouter.ai", "authorization", ["/api/v1/responses"]),
        ):
            with self.subTest(client=client, provider=provider):
                Path(self.routes_path).write_text(
                    f'[model.inference]\nprovider="{provider}"\nclient="{client}"\n'
                    'org="local"\nitem="model-api-key"\nnodes=["agent"]\n')
                self.routes = routes_mod.load_routes(self.routes_path)
                _, config = self.generate("local")
                rule, = config["proxy"]["rules"]
                self.assertEqual((rule["domain"], rule["header"], rule["methods"], rule["paths"]),
                                 (host, header, ["POST"], paths))
                self.assertIs(config["secrets"][rule["secret"]]["env"], False)
                self.assertFalse(self.routes.for_org("infra").api)

    def test_notes_ref_passes_through(self):
        _, conf = self.generate("infra")
        self.assertEqual(conf["secrets"]["BROKER_API_RESEND"]["value"], "resend-api-key/notes")

    def test_zero_api_routes_gives_no_proxy_rules(self):
        # with-bw-session greps for this line to decide whether to run `fnox proxy run`.
        rule_line = re.compile(r"^\[\[proxy\.rules\]\]", re.M)
        path = os.path.join(self.dir.name, "noapi.toml")
        with open(path, "w") as f:
            # Today's shape: the one api route is in the other org, this org has only a project.
            f.write(ROUTES.split("[api.resend]")[0].replace('org      = "local"', 'org      = "infra"', 1))
            f.write('\n[project.shop]\norg = "local"\nnodes = ["devbox"]\nitems = ["*-shop-qa"]\n')
        routes = routes_mod.load_routes(path)  # what broker-routes check runs: valid
        self.assertEqual(len(routes.for_org("local").api), 0)
        text = self.gen.generate(routes, "local", "org-id-123", None)
        conf = tomllib.loads(text)
        self.assertEqual(len(rule_line.findall(text)), 0)
        self.assertNotIn("rules", conf["proxy"])
        self.assertEqual(conf["secrets"], {})
        self.assertEqual(len(rule_line.findall(self.gen.generate(routes, "infra", "o", None))), 1)

    def test_cli_writes_toml_and_collection_is_optional(self):
        out = subprocess.run(
            [sys.executable, "-I", str(TOOL), "--routes", self.routes_path, "--org", "local",
             "--org-id", "org-id-123", "--collection-id", "col-9"],
            capture_output=True, text=True, check=True).stdout
        conf = tomllib.loads(out)
        self.assertEqual(conf["providers"]["vw"]["collection"], "col-9")
        self.assertIs(conf["root"], True)
        empty = subprocess.run(
            [sys.executable, "-I", str(TOOL), "--routes", self.routes_path, "--org", "infra",
             "--org-id", "org-id-123"], capture_output=True, text=True, check=True).stdout
        self.assertNotIn("collection", tomllib.loads(empty)["providers"]["vw"])


if __name__ == "__main__":
    unittest.main()
