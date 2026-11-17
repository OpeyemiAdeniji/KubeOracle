#!/usr/bin/env bash
# Build kubectl-sift for Linux inside a quay.io/pypa/manylinux_2_28 container (used by CI and the release).
# manylinux's own Pythons have no shared libpython, which PyInstaller needs, so a current standalone Python from
# uv is used instead. The result needs only glibc 2.28+ (RHEL 8, Amazon Linux 2023, Ubuntu 20.04, Debian 10...).
set -euo pipefail
PYINSTALLER_VERSION=6.22.3
/opt/python/cp312-cp312/bin/pip install -q --root-user-action=ignore uv
export PATH=/opt/python/cp312-cp312/bin:$PATH
uv python install 3.12
uv venv -p 3.12 /tmp/kubesift-build
VIRTUAL_ENV=/tmp/kubesift-build uv pip install ".[anthropic]" "pyinstaller==${PYINSTALLER_VERSION}"
/tmp/kubesift-build/bin/python scripts/build_binary.py
