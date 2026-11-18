#!/bin/sh
# Install the standalone kubectl-sift (no Python needed) for Linux or macOS.
#
#   curl -sSfL https://raw.githubusercontent.com/OpeyemiAdeniji/KubeSift/main/scripts/install.sh | sh
#
# Options (environment variables):
#   KUBESIFT_VERSION      a release tag such as v2.1.7 (default: the latest release)
#   KUBESIFT_INSTALL_DIR  where the program folder goes (default: ~/.local/share/kubesift)
#   KUBESIFT_BIN_DIR      where the commands are linked (default: /usr/local/bin if writable, else ~/.local/bin)
#
# The download is checked against the SHA-256 published with the release before anything is installed.
set -eu

REPO="OpeyemiAdeniji/KubeSift"
say() { printf '%s\n' "$*"; }
die() { printf 'kubesift install: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

need uname
need tar
if command -v curl >/dev/null 2>&1; then
  fetch() { curl -sSfL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q "$1" -O "$2"; }
else
  die "curl or wget is required"
fi

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) die "unsupported OS $(uname -s); on Windows run scripts/install.ps1, elsewhere: pipx install \"kubesift[all]\"" ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  arm64 | aarch64) arch=arm64 ;;
  *) die "unsupported CPU $(uname -m); use: pipx install \"kubesift[all]\"" ;;
esac
if [ "$os" = linux ] && { [ -e /etc/alpine-release ] || ldd --version 2>&1 | grep -qi musl; }; then
  die "musl-based Linux (e.g. Alpine) is not supported by the binary; use: pipx install \"kubesift[all]\" or the container image"
fi

version="${KUBESIFT_VERSION:-latest}"
if [ "$version" = latest ]; then
  base="https://github.com/$REPO/releases/latest/download"
else
  case "$version" in v*) ;; *) version="v$version" ;; esac
  base="https://github.com/$REPO/releases/download/$version"
fi
archive="kubectl-sift_${os}_${arch}.tar.gz"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM
say "Downloading $archive ($version)..."
fetch "$base/$archive" "$tmp/$archive" || die "download failed: $base/$archive"
fetch "$base/$archive.sha256" "$tmp/$archive.sha256" || die "checksum download failed"

expected="$(cut -d' ' -f1 < "$tmp/$archive.sha256" | tr -d '\r')"
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$tmp/$archive" | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
  actual="$(shasum -a 256 "$tmp/$archive" | cut -d' ' -f1)"
else
  die "sha256sum or shasum is required to check the download"
fi
[ "$expected" = "$actual" ] || die "checksum mismatch for $archive (expected $expected, got $actual); not installing"
say "Checksum OK."

install_dir="${KUBESIFT_INSTALL_DIR:-$HOME/.local/share/kubesift}"
if [ -n "${KUBESIFT_BIN_DIR:-}" ]; then
  bin_dir="$KUBESIFT_BIN_DIR"
elif [ -w /usr/local/bin ]; then
  bin_dir=/usr/local/bin
else
  bin_dir="$HOME/.local/bin"
fi
mkdir -p "$install_dir" "$bin_dir"
tar -xzf "$tmp/$archive" -C "$tmp"
rm -rf "$install_dir/kubectl-sift"
mv "$tmp/kubectl-sift" "$install_dir/kubectl-sift"
ln -sf "$install_dir/kubectl-sift/kubectl-sift" "$bin_dir/kubectl-sift"
ln -sf "$install_dir/kubectl-sift/kubectl-sift" "$bin_dir/kubesift"

"$bin_dir/kubectl-sift" version >/dev/null 2>&1 || die "installed, but the binary does not start; please open an issue"
say "Installed $("$bin_dir/kubectl-sift" version) to $install_dir"
say "Commands: $bin_dir/kubectl-sift and $bin_dir/kubesift"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) say "Add $bin_dir to your PATH, e.g.: echo 'export PATH=\"$bin_dir:\$PATH\"' >> ~/.profile" ;;
esac
say "Try: kubectl sift diagnose"
