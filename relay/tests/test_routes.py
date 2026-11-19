import os
import tempfile
import unittest
from pathlib import Path

from relay.relaylib import routes

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class ValidateItemNameTests(unittest.TestCase):
    def test_validate_project_name_requires_env(self):
        err = routes.validate_item_name("resend-api-key-blog", "blog")
        self.assertIn("dev|qa|prod", err)
        self.assertIsNone(routes.validate_item_name("resend-api-key-blog-qa", "blog"))

    def test_validate_tool_name_without_project(self):
        self.assertIsNone(routes.validate_item_name("notion-api-key", None))
        self.assertIsNotNone(routes.validate_item_name("Notion_Key", None))
        self.assertIn("reserved", routes.validate_item_name("x-backup-20261106", None))
        for name in ("x-backup-2026110\u0666", "x-backup-20261106\n"):
            self.assertNotIn("reserved", routes.validate_item_name(name, None) or "", repr(name))


class MatchItemTests(unittest.TestCase):
    def test_match_item_exact_and_suffix_glob(self):
        pats = ["*-shop-qa"]
        self.assertTrue(routes.match_item("pgpassword-shop-qa", pats))
        self.assertFalse(routes.match_item("pgpassword-shop-prod", pats))
        self.assertTrue(routes.match_item("exact-name", ["exact-name"]))
        self.assertFalse(routes.match_item("axb", ["a*b"]))


class LoadRoutesTests(unittest.TestCase):
    def load_text(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "routes.toml")
            with open(p, "w") as f:
                f.write(text)
            return routes.load_routes(p)

    def test_load_routes_example_file(self):
        r = routes.load_routes(CONFIG_DIR / "routes.example.toml")
        self.assertEqual(r.projects["shop"].org, "infra")
        self.assertEqual(r.for_org("local").projects, {})
        self.assertEqual(r.for_org("infra").api, {})
        self.assertIn("stripe-shop", r.for_org("local").api)

    def test_load_routes_rejects_bad_entries(self):
        good_api = ('upstream = "https://x.example"\nauth = "Bearer {}"\n'
                    'methods = ["GET"]\npaths = ["/"]\nnodes = ["a"]\n')
        cases = {
            "bad-org": '[value.bad-org]\norg = "mars"\nitem = "x-key"\nnodes = ["a"]\n',
            "no-nodes": '[value.no-nodes]\norg = "local"\nitem = "x-key"\nnodes = []\n',
            "http-up": '[api.http-up]\norg = "local"\nitem = "x-key"\n'
                       + good_api.replace("https", "http"),
            "value": '[value.value]\norg = "local"\nitem = "x-key"\nnodes = ["a"]\n',
            "bad-item": '[value.bad-item]\norg = "local"\nitem = "Bad_Item"\nnodes = ["a"]\n',
            "empty-proj": '[project.empty-proj]\norg = "infra"\nnodes = ["a"]\nitems = []\n',
        }
        for entry, text in cases.items():
            with self.subTest(entry=entry):
                with self.assertRaises(routes.RoutesError) as cm:
                    self.load_text(text)
                self.assertIn(entry, str(cm.exception))

    def test_reserved_route_names(self):
        self.assertIn("backups", routes.RESERVED_ROUTES)
        api = ('upstream = "https://x.example"\nauth = "Bearer {}"\n'
               'methods = ["GET"]\npaths = ["/"]\nnodes = ["a"]\nitem = "x-key"\norg = "local"\n')
        for name in ("value", "check", "refresh", "names", "v1", "backups"):
            for text in (f"[api.{name}]\n" + api,
                         f'[value.{name}]\norg = "local"\nitem = "x-key"\nnodes = ["a"]\n',
                         f'[project.{name}]\norg = "infra"\nnodes = ["a"]\nitems = ["*-x-qa"]\n'):
                with self.subTest(text=text):
                    with self.assertRaises(routes.RoutesError) as cm:
                        self.load_text(text)
                    self.assertIn("reserved", str(cm.exception))

    def test_notes_suffix_allowed(self):
        r = self.load_text('[value.n]\norg = "local"\nitem = "tok-key/notes"\nnodes = ["a"]\n')
        self.assertEqual(r.values["n"].item, "tok-key/notes")

    def test_raw_reads_default_to_denied_and_policy_survives_org_filter(self):
        self.assertFalse(self.load_text("").may_read_values("devbox"))
        r = self.load_text('[security]\nagent_nodes = ["devbox"]\ntrusted_value_nodes = ["app"]')
        for org in ("local", "infra"):
            self.assertFalse(r.for_org(org).may_read_values("devbox"))
            self.assertTrue(r.for_org(org).may_read_values("app"))
        with self.assertRaises(routes.RoutesError):
            r.validate_owners(["devbox"])

    def test_policy_rejects_role_conflicts_and_typos(self):
        for text in ('[security]\nagent_nodes=["a"]\ntrusted_value_nodes=["a"]',
                     '[security]\ntrusted_values_nodes=["a"]',
                     '[security]\nagent_nodes="a"',
                     '[security]\nagent_nodes=["A"]',
                     '[security]\nagent_nodes=["a"]\n[value.key]\norg="local"\nitem="key"\nnodes=["a"]',
                     '[security]\nagent_nodes=["a"]\n[project.app]\norg="infra"\nitems=["*-qa"]\nnodes=["a"]'):
            with self.subTest(text=text), self.assertRaises(routes.RoutesError):
                self.load_text(text)

    def test_api_prefix_matches_path_segments(self):
        for path in ("/emails", "/emails/one", "/emails/a%20b", "/emails/caf%C3%A9"):
            self.assertTrue(routes.api_path_allowed(path, ["/emails"]), path)
        for path in ("/emails-admin", "/emails/%2e%2e/admin", "/emails/%252fadmin",
                     "/emails\\admin", "/emails/%5cadmin", "/emails/./x", "/emails//x"):
            self.assertFalse(routes.api_path_allowed(path, ["/emails"]), path)

    def test_names_reject_newlines(self):
        self.assertIsNotNone(routes.validate_item_name("key\n", None))

    def test_upstream_rejects_ambiguous_origins(self):
        import json
        for up in ("https://user@api.example", "https://api.example/path", "https://api.example?x=1",
                   "https://api.example:8443", "https://", "https://api.example\n"):
            text = ('[api.test]\norg="local"\nitem="key"\nnodes=["a"]\nauth="Bearer {}"\n'
                    'methods=["GET"]\npaths=["/"]\nupstream=' + json.dumps(up))
            with self.subTest(up=up), self.assertRaises(routes.RoutesError):
                self.load_text(text)


class EntryConfigTests(unittest.TestCase):
    def test_load_entry_config(self):
        c = routes.load_entry_config(CONFIG_DIR / "entry.example.toml")
        self.assertEqual(c.owner_nodes, ["laptop", "tablet", "phone"])
        self.assertEqual(c.tailnet_name, "broker.example.ts.net")


if __name__ == "__main__":
    unittest.main()
