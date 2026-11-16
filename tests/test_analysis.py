from datetime import timedelta

from kubesift.analysis import engine
from kubesift.analysis.changes import detect_changes
from kubesift.analysis.engine import ask, investigate
from kubesift.analysis.llm import Provider


def test_changes_include_rollout_diff(snap):
    changes = detect_changes(snap, timedelta(hours=6))
    rollout = next(c for c in changes if c.kind == "rollout" and c.resource.name == "checkout")
    assert any("image ghcr.io/acme/checkout:1.4 -> ghcr.io/acme/checkout:1.5" in d for d in rollout.details)
    assert any("env removed DATABASE_URL" in d for d in rollout.details)
    assert rollout.rollback_command == "kubectl rollout undo deployment/checkout -n shop --to-revision=1"
    assert any(c.kind == "helm" and c.resource.name == "legacy" for c in changes)
    assert any(c.kind == "config" and c.resource.name == "checkout-flags" for c in changes)


def test_crashloop_linked_to_rollout(snap, settings):
    report = investigate(settings, snap, use_llm=False)
    crash = next(f for f in report.findings if f.check == "pod-crashloop")
    assert crash.related_changes and crash.related_changes[0].kind == "rollout"
    incident = next(i for i in report.incidents if i.root.name == "checkout")
    assert incident.likely_trigger and incident.likely_trigger.resource.name == "checkout"
    assert incident.layer in ("app", "config")


def test_node_incident_groups_pods(snap, settings):
    report = investigate(settings, snap, use_llm=False)
    node_inc = next(i for i in report.incidents if i.root.kind == "Node")
    assert node_inc.layer == "infra"
    assert any(f.resource.name == "cart-1" for f in node_inc.findings)


def test_rules_diagnosis_offers_rollback(snap, settings):
    report = investigate(settings, snap, use_llm=False)
    d = report.diagnosis
    assert d.generated_by == "rules"
    assert d.root_causes
    cmds = [c for rc in d.root_causes for c in rc.commands]
    assert any("rollout undo deployment/checkout" in c for c in cmds)


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def complete_json(self, system, user, schema):
        self.prompts.append(user)
        return self.payload


def test_llm_output_is_validated(snap, settings, monkeypatch):
    fake = FakeProvider({"summary": "Checkout is down after the 1.5 rollout.", "next_checks": [],
                         "root_causes": [
                             {"title": "made up", "explanation": "x", "confidence": 99, "evidence": ["nope"],
                              "fix_steps": [], "commands": []},
                         ]})
    monkeypatch.setattr(engine, "get_provider", lambda s: fake)
    report = investigate(settings, snap, use_llm=True)
    assert report.diagnosis.generated_by.startswith("fake")
    assert report.diagnosis.root_causes[0].confidence <= 40  # no valid evidence -> capped
    assert "DATABASE_URL" in fake.prompts[0]
    assert "postgres://db:5432" not in fake.prompts[0] or "[REDACTED" in fake.prompts[0]


def test_llm_failure_falls_back(snap, settings, monkeypatch):
    class Broken(Provider):
        name = "broken"

        def complete_json(self, *a):
            from kubesift.analysis.llm import LLMError
            raise LLMError("boom")

    monkeypatch.setattr(engine, "get_provider", lambda s: Broken())
    report = investigate(settings, snap, use_llm=True)
    assert report.diagnosis.generated_by == "rules"
    assert any("LLM analysis failed" in w for w in report.warnings)


def test_ask_focuses_on_named_workload(snap, settings):
    report, answer = ask(settings, snap, "why is checkout failing?", use_llm=False)
    assert "checkout" in answer["answer"]
    assert report.findings and all((f.owner or f.resource).name in ("checkout", "checkout-7c9a-x1", "checkout-7c9a")
                                   for f in report.findings)


def test_ask_general_topic(snap, settings):
    _, answer = ask(settings, snap, "are there any dns or network problems?", use_llm=False)
    assert answer["answer"]


def test_notes_attached(snap, settings):
    from kubesift.store import Store

    store = Store(settings.data_dir)
    store.add_note("pod-oomkilled", "Raised to 512Mi; leak fixed in 3.3", target="Deployment/shop/api")
    report = investigate(settings, snap, use_llm=False, store=store)
    oom = next(f for f in report.findings if f.check == "pod-oomkilled")
    assert any("512Mi" in n for n in oom.notes)
    rid = store.save_report(report)
    assert store.get_report(rid).cluster == "test-cluster"


def test_alert_dedupe(snap, settings):
    from kubesift.store import Store

    store = Store(settings.data_dir)
    report = investigate(settings, snap, use_llm=False)
    first = store.new_findings("c", report.findings)
    second = store.new_findings("c", report.findings)
    assert first and not second


def test_cited_evidence_ids_are_accepted(snap, settings, monkeypatch):
    base = investigate(settings, snap, use_llm=False)
    crash = next(f for f in base.findings if f.check == "pod-crashloop")
    fake = FakeProvider({"summary": "s", "next_checks": [], "root_causes": [
        {"title": "exact", "explanation": "x", "confidence": 90, "evidence_ids": [crash.fingerprint],
         "fix_steps": [], "commands": []},
        {"title": "wrapped", "explanation": "x", "confidence": 90, "evidence_ids": [f"finding {crash.fingerprint}"],
         "fix_steps": [], "commands": []},
        {"title": "invented", "explanation": "x", "confidence": 90, "evidence_ids": ["000000000000"],
         "fix_steps": [], "commands": []}]})
    monkeypatch.setattr(engine, "get_provider", lambda s: fake)
    report = investigate(settings, snap, use_llm=True)
    exact, wrapped, invented = report.diagnosis.root_causes
    assert exact.evidence == [crash.fingerprint] and exact.confidence == 90
    assert wrapped.evidence == [crash.fingerprint] and wrapped.confidence == 90
    assert invented.evidence == [] and invented.confidence == 40
    assert f'"id": "{crash.fingerprint}"' in fake.prompts[0] and '"facts"' in fake.prompts[0]


def test_ai_eval_script_scores_a_model(settings, monkeypatch):
    import importlib.util
    from pathlib import Path

    from fixtures import broken_cluster

    spec = importlib.util.spec_from_file_location("eval_ai", Path(__file__).parent.parent / "scripts" / "eval_ai.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    rules = investigate(settings, broken_cluster(), use_llm=False)
    crash = next(f for f in rules.findings if f.check == "pod-crashloop").fingerprint
    oom = next(f for f in rules.findings if f.check == "pod-oomkilled").fingerprint

    class Good(Provider):
        name = "good"

        def complete_json(self, system, user, schema):
            if "Question:" in user:
                return {"answer": "DATABASE_URL was removed", "confidence": 80, "evidence_ids": [crash],
                        "commands": [], "follow_up": []}
            return {"summary": "s", "next_checks": [], "root_causes": [
                {"title": "bad rollout", "explanation": "x", "confidence": 90, "evidence_ids": [crash],
                 "fix_steps": [], "commands": []},
                {"title": "oom", "explanation": "x", "confidence": 90, "evidence_ids": [oom],
                 "fix_steps": [], "commands": []}]}

    monkeypatch.setattr(engine, "get_provider", lambda s: Good())
    result = ev.evaluate(settings)
    assert result["coverage"] == 0.4 and result["cited"] == 1.0
    assert result["found"]["pod-crashloop"] and not result["found"]["node-not-ready"]
    assert result["questions"][0]["right_evidence"] and result["questions"][0]["mentions"]
