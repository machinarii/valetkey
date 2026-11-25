# valetkey approver client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew trust machinarii/valetkey
#                                  brew install valetkey-approver
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyApprover < Formula
  desc "valetkey client for an approver device: valetkey-relay and valetkey-entry"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.3.0"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  conflicts_with "valetkey-agent", because: "one machine has one valetkey role"
  conflicts_with "valetkey-trusted-server", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay" => "valetkey-relay"
    bin.install "client/vault"  # fnox calls it by this name
    bin.install "client/vault-entry" => "valetkey-entry"
    bin.install "relay/tools/fnox-to-relay.py" => "valetkey-fnox-to-relay"
    (share/"valetkey").install "agents/credential-rules.md"
  end

  def caveats
    <<~EOS
      Needs python3 3.9 or newer on PATH (macOS and most Linux systems have it).
      Point it at your broker once (names only, never a key):
        valetkey-relay set-url https://broker.<tailnet>.ts.net
      Use valetkey-entry on an approver device to enter keys; never run agents on this machine.
      List it in the broker's /etc/relay/entry.toml owner_nodes.
    EOS
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/valetkey-relay version").strip unless build.head?
    assert_match "Machine boundaries", shell_output("#{bin}/valetkey-relay guide")
  end
end
