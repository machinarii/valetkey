# valetkey owner client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew install valetkey-owner
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyOwner < Formula
  desc "valetkey client for an owner device: relay and vault-entry"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.1.0"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  depends_on "python@3.13"
  conflicts_with "valetkey-agent", because: "one machine has one valetkey role"
  conflicts_with "valetkey-consumer", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay", "client/vault", "client/vault-entry"
    bin.install "relay/tools/fnox-to-relay.py" => "fnox-to-relay"
    (share/"valetkey").install "agents/credential-rules.md"
    rewrite_shebang detected_python_shebang, *bin.children
  end

  def caveats
    <<~EOS
      Point it at your broker once (names only, never a key):
        relay set-url https://broker.<tailnet>.ts.net
      Owner devices type keys with vault-entry; never run agents on this machine.
      List it in the broker's /etc/relay/entry.toml owner_nodes.
    EOS
  end

  test do
    assert_match "Machine boundaries", shell_output("#{bin}/relay guide")
  end
end
