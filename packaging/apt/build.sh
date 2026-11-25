#!/bin/bash
# Build the valetkey .deb and a small apt repository around it.
#
#   packaging/apt/build.sh [OUT_DIR]     default: dist/apt
#
# The package carries the installer and the files it reads, in /usr/share/valetkey, and one
# command: valetkey-setup, which runs that installer. Nothing is set up at install time; the
# owner chooses the machine's role by running valetkey-setup.
#
# APT_GPG_KEY=<fingerprint>  signs the repository (InRelease, Release.gpg) and writes the public
#   key as valetkey.gpg. Without it the repository is unsigned: for local tests only.
# Needs dpkg-deb and dpkg-scanpackages (dpkg-dev); gpg to sign. Only tracked files are packaged.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-$repo/dist/apt}
version=$(cat "$repo/VERSION")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad VERSION: $version" >&2; exit 1; }
for c in dpkg-deb dpkg-scanpackages git; do
  command -v "$c" >/dev/null || { echo "missing: $c" >&2; exit 1; }
done

stage=$(mktemp -d)
trap 'rm -rf -- "${stage:?}"' EXIT
pkg=$stage/valetkey_${version}_all
share=$pkg/usr/share/valetkey
install -d -m 755 "$pkg/DEBIAN" "$share" "$pkg/usr/bin"

# The installer and what it reads. Tests, the bellhop test VM, docs and images stay out.
(cd "$repo" && git ls-files -z -- install.sh VERSION LICENSE NOTICE agents client bellhop broker relay config deploy \
    ':!relay/tests' ':!bellhop/test_bellhop.py' ':!bellhop/testvm' ':!bellhop/fakes' | tar --null -T - -cf -) \
  | tar -xf - -C "$share"

cat > "$pkg/usr/bin/valetkey-setup" <<'SH'
#!/bin/sh
# valetkey-setup: choose this machine's role and set it up, or update it after an upgrade.
# Runs the installer the valetkey package ships. With no arguments it shows the role menu.
exec bash /usr/share/valetkey/install.sh "$@"
SH
chmod 755 "$pkg/usr/bin/valetkey-setup"

cat > "$pkg/DEBIAN/control" <<EOF
Package: valetkey
Version: $version
Architecture: all
Maintainer: machinarii <machinarii@users.noreply.github.com>
Depends: python3 (>= 3.9), bash, ca-certificates
Section: admin
Priority: optional
Homepage: https://github.com/machinarii/valetkey
Description: credential broker for AI agents (installer for every role)
 Agents call your services through a broker with no key; the broker checks the
 calling machine and adds the key on the way out. This package ships the
 installer for every role. Run valetkey-setup to choose this machine's role:
 agent machine, approver device, trusted server, broker, bellhop or vault.
EOF
cat > "$pkg/DEBIAN/postinst" <<'SH'
#!/bin/sh
# Nothing is changed here: the owner picks the role. After an upgrade the same command updates it.
set -e
if [ "$1" = configure ]; then
  echo "valetkey: run 'valetkey-setup' to set up this machine's role, or to apply this version to it."
fi
SH
chmod 755 "$pkg/DEBIAN/postinst"
chmod -R go-w "$pkg"  # whatever the builder's umask was
find "$pkg" -type d -exec chmod 755 {} +

rm -rf -- "${out:?}"
install -d -m 755 "$out"
dpkg-deb --root-owner-group --build "$pkg" "$out/valetkey_${version}_all.deb" >/dev/null

# A flat repository: deb [signed-by=...] <URL> ./
(
  cd "$out"
  dpkg-scanpackages --multiversion . > Packages 2>/dev/null
  gzip -9 -k -n Packages
  {
    echo "Origin: valetkey"
    echo "Label: valetkey"
    echo "Suite: stable"
    echo "Architectures: all"
    echo "Date: $(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S UTC')"
    echo "SHA256:"
    for f in Packages Packages.gz; do
      printf ' %s %s %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c %s "$f")" "$f"
    done
  } > Release
  if [ -n "${APT_GPG_KEY:-}" ]; then
    gpg --batch --yes -u "$APT_GPG_KEY" --clearsign -o InRelease Release
    gpg --batch --yes -u "$APT_GPG_KEY" --armor --detach-sign -o Release.gpg Release
    gpg --batch --yes --export "$APT_GPG_KEY" > valetkey.gpg
    echo "ok    signed with $APT_GPG_KEY"
  else
    echo "warn  unsigned repository (set APT_GPG_KEY to sign): for local tests only" >&2
  fi
)
echo "ok    $out/valetkey_${version}_all.deb and its apt repository"
