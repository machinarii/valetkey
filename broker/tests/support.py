"""Helpers for tests that run against the fake bw and tailscale executables."""
import json
import os

FAKES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakes")


def fake_path():
    """PATH with the fakes directory first."""
    return FAKES_DIR + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")


def write_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f)


def fresh_env(state_path, whois=None):
    """Environment for running a fake-backed process."""
    return {
        "PATH": fake_path(),
        "HOME": os.environ.get("HOME", "/tmp"),
        "FAKE_BW_STATE": state_path,
        "FAKE_WHOIS": json.dumps(whois or {}),
    }
