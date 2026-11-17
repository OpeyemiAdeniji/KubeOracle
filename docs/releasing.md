# Releasing

A release is one tag. The [release workflow](https://github.com/OpeyemiAdeniji/KubeSift/blob/main/.github/workflows/release.yml) then:

| Artifact | Where | Supply-chain evidence |
|---|---|---|
| Python package | PyPI `kubesift` | Trusted Publishing (no stored token) + PEP 740 attestations |
| Container image (amd64 + arm64) | `ghcr.io/opeyemiadeniji/kubesift` | cosign keyless signature, SBOM, SLSA provenance |
| Helm chart | `oci://ghcr.io/opeyemiadeniji/charts/kubesift` | cosign keyless signature |
| GitHub release | Releases page | generated notes |

## One-time setup

### PyPI Trusted Publishing
1. Sign in at <https://pypi.org> (create an account and turn on 2FA if needed).
2. Go to **Your account → Publishing → Add a new pending publisher** and enter:
   - PyPI project name: `kubesift`
   - Owner: `OpeyemiAdeniji`
   - Repository name: `KubeSift`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In GitHub: **Settings → Environments → New environment** named `pypi`. Optionally add yourself as a
   required reviewer, so every PyPI upload waits for your approval.

### GHCR visibility
After the first release, open the `kubesift` package and the `charts/kubesift` package on your GitHub
profile (**Packages**) and set their visibility to **Public**, so people can pull without logging in.

## Cutting a release

1. Update the version in `src/kubesift/__init__.py` and `deploy/helm/kubesift/Chart.yaml` (`version`, `appVersion`
   and the image tag in the `artifacthub.io/images` annotation; `tests/test_release.py` checks they match), and
   replace the `artifacthub.io/changes` annotation with what changed in this version (Artifact Hub shows it).
2. Merge to `main`, then tag. The workflow refuses a tag that doesn't match the package version.
   Optionally run the **AI eval** workflow (Actions tab) first if the prompt or LLM code changed; it needs a
   provider API key as a repository secret.

```bash
git tag v2.0.0
git push origin v2.0.0
```

## Verifying a release (what users and security teams run)

Releases are signed with cosign 3 (Sigstore bundle format, stored as OCI referrers), so verify with cosign 3 or
newer. The release workflow runs the same `cosign verify` right after signing and fails if it doesn't pass.

```bash
cosign verify ghcr.io/opeyemiadeniji/kubesift:2.0.0 \
  --certificate-identity-regexp 'https://github.com/OpeyemiAdeniji/KubeSift/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

docker buildx imagetools inspect ghcr.io/opeyemiadeniji/kubesift:2.0.0 --format '{{ json .SBOM }}'
```
