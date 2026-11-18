# Contributing

Thanks for helping. The most valuable contributions are **new checks for failures you have actually seen**.

## Setup

The quickest start is the dev container: open the repository in VS Code and choose **Reopen in Container**, or
open it in GitHub Codespaces. It comes with the dev dependencies, Docker, kubectl, Helm, kind and MkDocs.

By hand:

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

## Adding a check

1. Pick the module in `src/kubesift/checks/` that matches the area (pods, workloads, network, nodes,
   storage, events, hygiene, lifecycle, config_refs, operations).
2. Write a function decorated with `@check(...)`:

   ```python
   @check("pod-something", "Short title", Category.WORKLOAD, Severity.HIGH)
   def something(ctx: Context) -> Iterator[Finding]:
       """One sentence: what this detects and why it matters."""
       for pod in ctx.snap.all("pods"):
           if ...:
               yield ctx.finding("pod-something", pod, "What is wrong, in plain words.",
                                 evidence=["facts copied from the object or its events"],
                                 remediation="What to do.", commands=["kubectl ..."])
   ```

   - `live_only=False` if it only needs specs (it will then also run in `kubesift lint`).
   - `risk=True` if it describes something that will hurt later rather than something broken now.
3. Add a case to `tests/fixtures.py` and a test in `tests/test_checks.py`.
4. Run `python scripts/gen_check_docs.py` to update `docs/checks.md`.

Guidelines: messages say what is wrong and why it matters; evidence is copied from the cluster, never guessed;
no check may read Secret values; a check that raises is logged and skipped, but please don't rely on that.

## Scenarios

`test-scenarios/` holds manifests that break a cluster in specific ways, and `expected.json` lists the check
that must catch each one. Add a scenario for every new live check you can reproduce on kind.
