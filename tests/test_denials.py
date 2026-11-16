"""'Forbidden' has many causes; only one is Kubernetes RBAC. Each must be named for what it is."""

import pytest
from fixtures import pod, ts

from kubesift.checks import run_checks
from kubesift.denials import classify_denial

# (log or event text, expected kind, words the explanation must contain)
CASES = [
    ('secrets is forbidden: User "system:serviceaccount:default:rbac-test" cannot list resource "secrets" in API '
     'group "" in the namespace "default"', "rbac", ["ServiceAccount default/rbac-test", "list secrets", "-n default"]),
    # the same, inside the API's JSON body as curl prints it
    ('  "message": "deployments.apps is forbidden: User \\"system:serviceaccount:ci:deployer\\" cannot patch resource '
     '\\"deployments\\" in API group \\"apps\\" in the namespace \\"prod\\"",', "rbac", ["ci/deployer", "patch deployments.apps"]),
    ('nodes is forbidden: User "system:serviceaccount:mon:agent" cannot list resource "nodes" in API group "" at the '
     'cluster scope', "rbac", ["cluster-wide"]),
    ('pods "web-1" is forbidden: violates PodSecurity "restricted:latest": runAsNonRoot != true', "pod-security",
     ["restricted:latest", "not RBAC"]),
    ('pods "web-2" is forbidden: exceeded quota: compute, requested: cpu=2, used: cpu=8, limited: cpu=8', "quota",
     ["compute", "not RBAC"]),
    ('pods "web-3" is forbidden: maximum memory usage per Container is 1Gi, but limit is 2Gi', "limit-range", ["LimitRange"]),
    ('admission webhook "validate.kyverno.svc-fail" denied the request: policy require-labels: label app is required',
     "policy", ["validate.kyverno.svc-fail", "not RBAC"]),
    ('pods "x" is forbidden: unable to create new content in namespace shop because it is being terminated',
     "namespace-terminating", ["being deleted"]),
    ("botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the GetObject operation: "
     "Access Denied", "cloud-iam", ["AWS IAM", "IRSA"]),
    ("User: arn:aws:sts::123:assumed-role/node/i-1 is not authorized to perform: sqs:SendMessage on resource",
     "cloud-iam", ["sqs:SendMessage"]),
    ("google.api_core.exceptions.Forbidden: 403 GET https://storage.googleapis.com/b: app@p.iam.gserviceaccount.com "
     "does not have storage.objects.get access", "cloud-iam", ["Google Cloud IAM", "storage.objects.get"]),
    ("AuthorizationFailed: The client 'x' does not have authorization to perform action "
     "'Microsoft.Storage/storageAccounts/read'", "cloud-iam", ["Azure", "Microsoft.Storage/storageAccounts/read"]),
    ("POST https://payments.internal:8443/charge failed: 403 Forbidden", "http-403",
     ["https://payments.internal:8443", "not Kubernetes RBAC"]),
]


@pytest.mark.parametrize("text,kind,words", CASES)
def test_each_refusal_is_named_for_what_it_is(text, kind, words):
    d = classify_denial("starting worker\n" + text + "\nretrying in 5s")
    assert d is not None and d.kind == kind, d
    for w in words:
        assert w in " ".join([d.explanation, d.remediation, *d.commands]), (w, d)
    assert text.strip()[:40] in d.line  # evidence is the line that proves it
    if kind != "rbac":
        assert "Kubernetes RBAC:" not in d.explanation


def test_rbac_gives_the_exact_permission_check():
    d = classify_denial(CASES[0][0])
    assert d.commands == ["kubectl auth can-i list secrets --as=system:serviceaccount:default:rbac-test -n default"]
    d = classify_denial(CASES[1][0])
    assert d.commands == ["kubectl auth can-i patch deployments.apps --as=system:serviceaccount:ci:deployer -n prod"]


@pytest.mark.parametrize("text", [
    "GET /healthz 200 in 3ms", "connected to db", "processed 403 items in 2s", "permission check passed",
    "error: connection refused",
])
def test_ordinary_lines_are_not_refusals(text):
    assert classify_denial(text) is None


def _running_unready(name):
    return pod(name, statuses=[{"name": "app", "ready": False, "restartCount": 0, "state": {"running": {"startedAt": ts(minutes_ago=20)}}}],
               conditions=[{"type": "Ready", "status": "False", "lastTransitionTime": ts(minutes_ago=19)}])


def test_running_pod_that_is_refused_is_reported(settings):
    from test_features import make

    snap = make(pods=[_running_unready("rbac-app"), _running_unready("s3-app"), _running_unready("fine")])
    snap.logs["shop/rbac-app/app"] = {"previous": False, "errors": [CASES[0][0]], "tail": []}
    snap.logs["shop/s3-app/app"] = {"previous": False, "errors": [CASES[8][0]], "tail": []}
    snap.logs["shop/fine/app"] = {"previous": False, "errors": ["error: connection refused"], "tail": []}
    found = {f.resource.name: f for f in run_checks(snap, settings) if f.check == "pod-access-denied"}
    assert set(found) == {"rbac-app", "s3-app"}
    assert "Kubernetes RBAC" in found["rbac-app"].message and found["rbac-app"].commands[0].startswith("kubectl auth can-i")
    assert "AWS IAM" in found["s3-app"].message and "RBAC" not in found["s3-app"].message.replace("not Kubernetes RBAC", "")


def test_controller_events_name_policy_limitrange_and_terminating_namespaces(settings):
    from fixtures import event
    from test_features import make

    snap = make(events=[
        event("ReplicaSet", "a-1", "FailedCreate", 'admission webhook "validate.kyverno.svc-fail" denied the request: x'),
        event("ReplicaSet", "b-1", "FailedCreate", 'pods "b" is forbidden: maximum memory usage per Container is 1Gi'),
        event("ReplicaSet", "c-1", "FailedCreate",
              'pods "c" is forbidden: unable to create new content in namespace shop because it is being terminated'),
    ])
    titles = {f.title for f in run_checks(snap, settings) if f.check == "platform-warning"}
    assert {"Admission policy denies changes", "LimitRange rejects pods", "Namespace is being deleted"} <= titles
