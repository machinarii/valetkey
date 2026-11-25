"""Only for updating a broker from 0.4 or earlier. That updater checks the configuration with
`relaylib.routes` from `relay/` before it installs anything, so the names must still resolve here.
Remove this folder once no broker older than 0.5 is left to update."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "broker"))
from brokerlib.routes import RoutesError, load_entry_config, load_routes  # noqa: E402,F401
