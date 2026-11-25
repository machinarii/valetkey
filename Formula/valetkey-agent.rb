# valetkey agent client. Install: brew tap machinarii/valetkey https://github.com/machinarii/valetkey
#                                  brew trust machinarii/valetkey
#                                  brew install valetkey-agent
# Release: bump the tag in all three formulas to the new VERSION and push the tag.
class ValetkeyAgent < Formula
  desc "valetkey client for an agent machine: broker only, never a key"
  homepage "https://github.com/machinarii/valetkey"
  url "https://github.com/machinarii/valetkey.git", tag: "v0.5.1"
  license "MIT"
  head "https://github.com/machinarii/valetkey.git", branch: "main"

  conflicts_with "valetkey-approver", because: "one machine has one valetkey role"
  conflicts_with "valetkey-trusted-server", because: "one machine has one valetkey role"

  def install
    bin.install "client/broker" => "valetkey-broker"
    # Claude Code and Codex rules and settings; the same step as `./install.sh claude`.
    (libexec/"agents").install "agents/claude-setup.py", "agents/credential-rules.md"
    (share/"valetkey").install_symlink libexec/"agents/credential-rules.md"  # `valetkey-broker guide` reads it here
    (libexec/"bellhop").install "bellhop/agent-rules.md"
    (bin/"valetkey-agent-setup").write <<~SH
      #!/bin/sh
      exec python3 -I "#{libexec}/agents/claude-setup.py" --repo "#{libexec}" "$@"
    SH
    (bin/"valetkey-agent-setup").chmod 0755
  end

  def caveats
    <<~EOS
      Needs python3 3.9 or newer on PATH (macOS and most Linux systems have it).
      Point it at your broker once (names only, never a key):
        valetkey-broker set-url https://broker.<tailnet>.ts.net
      This is an agent machine: list it in the broker's [security].agent_nodes,
      never in trusted_value_nodes or owner_nodes. Agent rules: valetkey-broker guide
      Then set up Claude Code and Codex (it asks before each change):
        valetkey-agent-setup
    EOS
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/valetkey-broker version").strip unless build.head?
    assert_match "Machine boundaries", shell_output("#{bin}/valetkey-broker guide")
    assert_match "install.sh claude", shell_output("#{bin}/valetkey-agent-setup --help")
  end
end
