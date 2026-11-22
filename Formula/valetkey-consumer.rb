# valetkey consumer client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew install valetkey-consumer
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyConsumer < Formula
  desc "valetkey client for a trusted service that must hold a key itself"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.1.0"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  depends_on "python@3.13"
  conflicts_with "valetkey-agent", because: "one machine has one valetkey role"
  conflicts_with "valetkey-owner", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay", "client/vault"
    bin.install "relay/tools/fnox-to-relay.py" => "fnox-to-relay"
    (share/"valetkey").install "agents/credential-rules.md"
    rewrite_shebang detected_python_shebang, *bin.children
  end

  def caveats
    <<~EOS
      Point it at your broker once (names only, never a key):
        relay set-url https://broker.<tailnet>.ts.net
      Projects pull keys through fnox's vault provider: install fnox (https://fnox.jdx.dev).
      List this machine in the broker's trusted_value_nodes; never run agents on it.
    EOS
  end

  test do
    assert_match "Machine boundaries", shell_output("#{bin}/relay guide")
  end
end
