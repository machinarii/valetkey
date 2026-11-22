# valetkey agent client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew install valetkey-agent
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyAgent < Formula
  desc "valetkey client for an agent machine: relay only, never a key"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.1.0"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  depends_on "python@3.13"
  conflicts_with "valetkey-owner", because: "one machine has one valetkey role"
  conflicts_with "valetkey-consumer", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay"
    (share/"valetkey").install "agents/credential-rules.md"
    rewrite_shebang detected_python_shebang, *bin.children
  end

  def caveats
    <<~EOS
      Point it at your broker once (names only, never a key):
        relay set-url https://broker.<tailnet>.ts.net
      This is an agent machine: list it in the broker's [security].agent_nodes,
      never in trusted_value_nodes or owner_nodes. Agent rules: relay guide
      For Claude Code and Codex settings, run from a copy of the repo: ./install.sh claude
    EOS
  end

  test do
    assert_match "Machine boundaries", shell_output("#{bin}/relay guide")
  end
end
