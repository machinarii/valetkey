# valetkey consumer client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew trust machinarii/valetkey
#                                  brew install valetkey-consumer
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyConsumer < Formula
  desc "valetkey client for a trusted service that must hold a key itself"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.1.1"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  conflicts_with "valetkey-agent", because: "one machine has one valetkey role"
  conflicts_with "valetkey-owner", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay", "client/vault"
    bin.install "relay/tools/fnox-to-relay.py" => "fnox-to-relay"
    (share/"valetkey").install "agents/credential-rules.md"
  end

  def caveats
    <<~EOS
      Needs python3 3.9 or newer on PATH (macOS and most Linux systems have it).
      Point it at your broker once (names only, never a key):
        relay set-url https://broker.<tailnet>.ts.net
      Projects pull keys through fnox's vault provider: install fnox (https://fnox.jdx.dev).
      List this machine in the broker's trusted_value_nodes; never run agents on it.
    EOS
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/relay version").strip unless build.head?
    assert_match "Machine boundaries", shell_output("#{bin}/relay guide")
  end
end
