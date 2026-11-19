"""Build the standalone `kubectl-sift` binary (no Python needed) and pack it for krew.

    python scripts/build_binary.py            # -> dist/kubectl-sift_<os>_<arch>.tar.gz (.zip on Windows) + .sha256

Run it where `pip install ".[anthropic]" pyinstaller` has been done. On Linux use
scripts/build_binary_manylinux.sh inside a manylinux_2_28 container instead, so the result runs on glibc 2.28+
(RHEL 8, Amazon Linux 2023, Ubuntu 20.04, Debian 10 and newer).

The binary carries the CLI and the Anthropic SDK (for --llm-provider anthropic); the long-running `serve` and
`mcp` modes stay pip-only.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
NAME = "kubectl-sift"

OS = {"linux": "linux", "darwin": "darwin", "win32": "windows"}[sys.platform]
ARCH = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]


def build() -> Path:
    entry = ROOT / "build" / "kubectl_sift_entry.py"
    entry.parent.mkdir(exist_ok=True)
    entry.write_text("from kubesift.cli import main\n\nmain()\n", encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(entry), "--name", NAME, "--onedir", "--noconfirm", "--clean",
        "--distpath", str(DIST / "bin"), "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--collect-data", "kubesift",           # drill scenarios and other packaged files
        "--collect-submodules", "kubesift.checks",
        # Server and MCP modes need their extras; keep them out so the binary stays small and quiet.
        "--exclude-module", "kubesift.server", "--exclude-module", "kubesift.mcp_server",
        "--exclude-module", "tkinter",
    ], check=True)
    return DIST / "bin" / NAME


def pack(folder: Path) -> Path:
    shutil.copy(ROOT / "LICENSE", folder / "LICENSE")
    stem = f"{NAME}_{OS}_{ARCH}"
    if OS == "windows":
        out = DIST / f"{stem}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.rglob("*")):
                z.write(f, Path(NAME) / f.relative_to(folder))
    else:
        out = DIST / f"{stem}.tar.gz"
        # dereference: store the files links point to, not the links. krew cannot unpack symbolic links, and the
        # macOS build has some (Python.framework); 2.2.0's macOS archive failed with "unable to handle file type 50".
        with tarfile.open(out, "w:gz", dereference=True) as t:
            t.add(folder, arcname=NAME)
        with tarfile.open(out) as t:
            links = [m.name for m in t.getmembers() if m.issym() or m.islnk()]
        if links:
            sys.exit(f"{out.name} still contains links, which krew cannot unpack: {links[:5]}")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    # newline="\n": in text mode Windows writes \r\n, and `sha256sum -c` then can't find the file.
    (DIST / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8", newline="\n")
    return out


def smoke(folder: Path) -> None:
    """The packed binary must start and run a real analysis, not just print its version."""
    exe = folder / (NAME + (".exe" if OS == "windows" else ""))
    for args in (["version"], ["checks", "-o", "json"]):
        r = subprocess.run([str(exe), *args], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            sys.exit(f"{exe} {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    manifest = "apiVersion: v1\nkind: Pod\nmetadata: {name: x}\nspec:\n  containers: [{name: c, image: nginx}]\n"
    r = subprocess.run([str(exe), "lint", "-", "-o", "json"], input=manifest, capture_output=True, text=True,
                       encoding="utf-8")
    if "image-mutable-tag" not in r.stdout:  # an untagged image must be flagged
        sys.exit(f"lint smoke test found nothing:\n{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    folder = build()
    smoke(folder)
    print(pack(folder))
