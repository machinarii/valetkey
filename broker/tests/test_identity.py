import json
import os
import subprocess
import unittest
from unittest import mock

from broker.brokerlib.identity import Whois, peer_ip
from broker.tests.support import fake_path


class CountingRunner:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return subprocess.run(*args, **kwargs)


def answers(node, own="broker.test.ts.net."):
    """Runner: `tailscale whois` gives node, `tailscale status` this broker's own DNS name."""
    def run(cmd, **kw):
        reply = {"Self": {"DNSName": own}} if cmd[1] == "status" else {"Node": node}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(reply))
    return mock.Mock(side_effect=run)


class Handler:
    def __init__(self, addr, headers):
        self.client_address = (addr, 50000)
        self.headers = headers


class WhoisTests(unittest.TestCase):
    def setUp(self):
        env = {"PATH": fake_path(),
               "FAKE_WHOIS": json.dumps({"127.0.0.1": "DEVBOX.test.ts.net."})}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_machine_lowercases_first_label(self):
        self.assertEqual(Whois().machine("127.0.0.1"), "devbox")

    def test_unknown_ip_is_empty_and_cached(self):
        runner = CountingRunner()
        w = Whois(runner=runner)
        self.assertEqual(w.machine("10.9.9.9"), "")
        self.assertEqual(w.machine("10.9.9.9"), "")
        self.assertEqual(runner.calls, 1)

    def test_cache_expires_after_ttl(self):
        runner = CountingRunner()
        w = Whois(ttl=0, runner=runner)
        w.machine("127.0.0.1")
        w.machine("127.0.0.1")
        self.assertEqual(runner.calls, 4)  # whois and status, each time

    def test_runner_timeout_is_empty(self):
        def runner(*a, **k):
            raise subprocess.TimeoutExpired("tailscale", 5)
        self.assertEqual(Whois(runner=runner).machine("127.0.0.1"), "")

    def test_tagged_machine_cannot_be_an_owner(self):
        runner = answers({"Name": "laptop.test.ts.net.", "Tags": ["tag:workload"]})
        w = Whois(runner=runner)
        self.assertEqual(w.machine("100.64.0.7"), "laptop")
        self.assertEqual(w.owner_machine("100.64.0.7"), "")
        self.assertEqual(runner.call_count, 2)  # one whois, one status; the second call is cached

    def test_display_name_cannot_override_dns_identity(self):
        runner = answers({"Name": "agent.test.ts.net.", "ComputedName": "laptop"})
        self.assertEqual(Whois(runner=runner).machine("100.64.0.7"), "agent")

    def test_same_short_name_in_another_tailnet_is_unknown(self):
        # Node sharing: the other tailnet's admin names their device after an owner's.
        for name in ("laptop.other.ts.net.", "laptop", "laptop.test.ts.net.evil.ts.net."):
            w = Whois(runner=answers({"Name": name}))
            self.assertEqual((w.machine("100.64.0.7"), w.owner_machine("100.64.0.7")), ("", ""), name)

    def test_unknown_own_tailnet_fails_closed(self):
        for own in ("", "broker"):
            w = Whois(runner=answers({"Name": "laptop.test.ts.net."}, own=own))
            self.assertEqual(w.machine("100.64.0.7"), "", own)


class PeerIpTests(unittest.TestCase):
    def test_peer_ip_trusts_header_only_from_loopback(self):
        hdr = {"X-Broker-Peer": "100.64.0.7"}
        self.assertEqual(peer_ip(Handler("127.0.0.1", hdr)), "100.64.0.7")
        self.assertEqual(peer_ip(Handler("127.0.0.1", {})), "127.0.0.1")
        self.assertEqual(peer_ip(Handler("100.64.0.9", hdr)), "100.64.0.9")


if __name__ == "__main__":
    unittest.main()
