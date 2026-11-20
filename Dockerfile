# syntax=docker/dockerfile:1
# Chainguard's Wolfi Python images: rebuilt daily from patched sources, no shell or package manager in the
# runtime image, so vulnerability scans stay clean. Both stages must have the same Python version (packages
# are installed for it), so Dependabot updates the two digests together (see .github/dependabot.yml).
FROM cgr.dev/chainguard/python:latest-dev@sha256:c047e6d1e47b09144a771bfde6595a00c49dc8571caa7dd62df88634e84304ae AS build
USER root
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Install into the interpreter's own prefix (/usr), not a venv: no pip ends up in the image, and scanners
# treat the files as installed packages rather than loose source.
RUN pip install --no-cache-dir --no-compile --root-user-action=ignore --prefix=/install ".[all]" && mkdir /data

FROM cgr.dev/chainguard/python:latest@sha256:f6e5c2e056393d48fa8afa1ea58a1394db4cd5de1a8f0f48105c8e88fcd4f9d3
LABEL org.opencontainers.image.source="https://github.com/OpeyemiAdeniji/KubeSift" \
      org.opencontainers.image.description="Evidence-based Kubernetes troubleshooting" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /install /usr
COPY --from=build --chown=10001:10001 /data /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KUBESIFT_DATA_DIR=/data HOME=/data
USER 10001
VOLUME /data
EXPOSE 8080
ENTRYPOINT ["kubesift"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
