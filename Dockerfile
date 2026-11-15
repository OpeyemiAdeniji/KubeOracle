# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/OpeyemiAdeniji/KubeSift" \
      org.opencontainers.image.description="Evidence-based Kubernetes troubleshooting" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KUBESIFT_DATA_DIR=/data
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[all]" && rm /tmp/*.whl \
 && useradd --uid 10001 --create-home kubesift && mkdir /data && chown 10001 /data
USER 10001
VOLUME /data
EXPOSE 8080
ENTRYPOINT ["kubesift"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
