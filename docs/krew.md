# kubectl plugin without Python (standalone binary and krew)

Every release ships a standalone `kubectl-sift` for Linux (amd64, arm64), macOS (Intel, Apple silicon) and
Windows (amd64). No Python needed. Put it on your `PATH` and kubectl finds it as `kubectl sift`.

| | |
|---|---|
| Includes | every CLI command (`diagnose`, `explain`, `changes`, `lint`, `preflight`, `upgrade-check`, `cost`, `drill`, ...) and AI explanations with Ollama, OpenAI-compatible endpoints or Anthropic |
| Not included | `serve` (web dashboard) and `mcp` (AI assistants): install with `pip install "kubesift[all]"` or use the Helm chart |
| Linux | glibc 2.28 or newer: RHEL/Rocky 8+, Amazon Linux 2 and 2023, Ubuntu 20.04+, Debian 10+ (not Alpine/musl: use pip or the image) |

## Install with one line

```bash
# Linux and macOS (installs to ~/.local/share/kubesift, links kubectl-sift and kubesift into your PATH)
curl -sSfL https://raw.githubusercontent.com/OpeyemiAdeniji/KubeSift/main/scripts/install.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/OpeyemiAdeniji/KubeSift/main/scripts/install.ps1 | iex
```

Both check the download against the release's SHA-256 before installing. `KUBESIFT_VERSION=v2.1.7` pins a
version. Read the script first if you prefer: it is short.

## Install from a release by hand

```bash
# Linux amd64 (other files: kubectl-sift_linux_arm64.tar.gz, _darwin_amd64, _darwin_arm64, _windows_amd64.zip)
curl -sSLO https://github.com/OpeyemiAdeniji/KubeSift/releases/latest/download/kubectl-sift_linux_amd64.tar.gz
tar -xzf kubectl-sift_linux_amd64.tar.gz            # creates ./kubectl-sift/
sudo mv kubectl-sift /opt/kubectl-sift && sudo ln -s /opt/kubectl-sift/kubectl-sift /usr/local/bin/kubectl-sift
kubectl sift diagnose
```

Check the download against its `.sha256` file from the same release. On macOS, a file downloaded with a browser is
quarantined by Gatekeeper; `curl` downloads (and krew) are not. If you used a browser:
`xattr -dr com.apple.quarantine kubectl-sift`.

## Install with krew

Once the plugin is in the krew index:

```bash
kubectl krew install sift
kubectl sift diagnose
```

Before that, any release can be installed with krew straight from its manifest:

```bash
kubectl krew install --manifest-url=https://github.com/OpeyemiAdeniji/KubeSift/releases/latest/download/sift.yaml
```

## Maintainers: getting into the krew index

The release workflow attaches `sift.yaml` (the krew manifest, with each archive's URL and SHA-256) to every
GitHub release.

1. Fork [kubernetes-sigs/krew-index](https://github.com/kubernetes-sigs/krew-index).
2. Copy the release's `sift.yaml` to `plugins/sift.yaml` in the fork.
3. Test it locally: `kubectl krew install --manifest=plugins/sift.yaml`, then `kubectl sift version`.
4. Open a pull request titled "Add sift plugin". The krew maintainers review the name and description against the
   [plugin naming guide](https://krew.sigs.k8s.io/docs/developer-guide/develop/naming-guide/) and may ask for
   changes.
5. After it is merged, later releases can be proposed automatically by
   [krew-release-bot](https://github.com/rajatjindal/krew-release-bot) (add its step to the release workflow).
