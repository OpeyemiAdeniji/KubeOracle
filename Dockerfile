# syntax=docker/dockerfile:1
# Chainguard's Wolfi Python images: rebuilt daily from patched sources, no shell or package manager in the
# runtime image, so vulnerability scans stay clean. Both stages must have the same Python version (packages
# are installed for it), so Dependabot updates the two digests together (see .github/dependabot.yml).
FROM cgr.dev/chainguard/python:latest-dev@sha256:50bef4bfe98ea2f3c8c146a04847c8857e0afb1c8803e2047f140f10c028a7ee AS build
USER root
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Install into the interpreter's own prefix (/usr), not a venv: no pip ends up in the image, and scanners
# treat the files as installed packages rather than loose source.
RUN pip install --no-cache-dir --no-compile --root-user-action=ignore --prefix=/install ".[all]" && mkdir /data

FROM cgr.dev/chainguard/python:latest@sha256:6a467605cd347cb7af8404af3648d01c2b39a967c342b70bbd5ef39dd0b74f5c
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
