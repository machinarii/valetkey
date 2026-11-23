# valetkey agent client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew trust machinarii/valetkey
#                                  brew install valetkey-agent
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyAgent < Formula
  desc "valetkey client for an agent machine: relay only, never a key"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.1.1"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  conflicts_with "valetkey-owner", because: "one machine has one valetkey role"
  conflicts_with "valetkey-consumer", because: "one machine has one valetkey role"

  def install
    bin.install "client/relay"
    (share/"valetkey").install "agents/credential-rules.md"
  end

  def caveats
    <<~EOS
      Needs python3 3.9 or newer on PATH (macOS and most Linux systems have it).
      Point it at your broker once (names only, never a key):
        relay set-url https://broker.<tailnet>.ts.net
      This is an agent machine: list it in the broker's [security].agent_nodes,
      never in trusted_value_nodes or owner_nodes. Agent rules: relay guide
      For Claude Code and Codex settings, run from a copy of the repo: ./install.sh claude
    EOS
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/relay version").strip unless build.head?
    assert_match "Machine boundaries", shell_output("#{bin}/relay guide")
  end
end
