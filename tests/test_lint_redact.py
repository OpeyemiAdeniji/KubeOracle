import json

from kubesift.lint import lint, lint_text
from kubesift.redact import redact
from kubesift.report import to_sarif

BAD = """
apiVersion: batch/v1beta1
kind: CronJob
metadata: {name: nightly}
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: job
            image: busybox
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: shop}
spec:
  replicas: 1
  selector: {matchLabels: {app: web}}
  template:
    metadata: {labels: {app: web}}
    spec:
      containers:
      - name: web
        image: nginx:latest
        ports: [{containerPort: 80}]
        env:
        - {name: DB_PASSWORD, value: hunter2hunter2}
        securityContext: {privileged: true}
        livenessProbe: {httpGet: {path: /health, port: 80}}
        readinessProbe: {httpGet: {path: /health, port: 80}}
---
apiVersion: v1
kind: Service
metadata: {name: web, namespace: shop}
spec:
  selector: {app: web}
  ports: [{port: 80, targetPort: 8080}]
"""

GOOD = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: api}
spec:
  replicas: 3
  selector: {matchLabels: {app: api}}
  template:
    metadata: {labels: {app: api}}
    spec:
      imagePullSecrets: [{name: regcred}]
      containers:
      - name: api
        image: ghcr.io/acme/api:1.2.3
        ports: [{containerPort: 8080}]
        resources: {requests: {cpu: 100m, memory: 128Mi}, limits: {memory: 256Mi}}
        securityContext: {runAsNonRoot: true, allowPrivilegeEscalation: false}
        startupProbe: {httpGet: {path: /healthz, port: 8080}, failureThreshold: 30}
        livenessProbe: {httpGet: {path: /healthz, port: 8080}}
        readinessProbe: {httpGet: {path: /ready, port: 8080}}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: {name: api}
spec: {maxUnavailable: 1, selector: {matchLabels: {app: api}}}
"""


def test_lint_finds_problems(settings):
    checks = {f.check for f in lint_text(BAD, settings, target_version="1.36")}
    for expected in ("api-removed", "image-mutable-tag", "security-plaintext-secret", "security-privileged",
                     "probe-liveness-equals-readiness", "service-port-mismatch", "resources-missing",
                     "reliability-single-replica"):
        assert expected in checks, expected


def test_lint_clean_manifest(settings):
    findings = lint_text(GOOD, settings, target_version="1.36")
    assert not [f for f in findings if f.severity.rank >= 2], [(f.check, f.message) for f in findings]


def test_lint_files_and_sarif(tmp_path, settings):
    (tmp_path / "app.yaml").write_text(BAD, encoding="utf-8")
    findings = lint([str(tmp_path)], settings)
    assert all(f.source and f.source.endswith(("#0", "#1", "#2")) for f in findings)
    sarif = json.loads(to_sarif(findings))
    assert sarif["runs"][0]["results"]
    assert sarif["runs"][0]["tool"]["driver"]["rules"]


def test_redaction():
    text = ("password=supersecret token: abcdefghijkl Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.sig_abcdefgh "
            "postgres://admin:s3cret@db:5432/app AKIAABCDEFGHIJKLMNOP ghp_abcdefghijklmnopqrstuvwxyz123456 bob@example.com")
    out = redact(text)
    for secret in ("supersecret", "abcdefghijkl", "s3cret", "AKIAABCDEFGHIJKLMNOP", "ghp_abcdefghij", "bob@example.com",
                   "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in out, secret
    assert "db:5432" in out
