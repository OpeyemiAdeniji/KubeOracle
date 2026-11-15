"""Knowledge packs: share what fixed past incidents between teams and clusters.

A pack is a YAML file with team notes (check id, optional target, note) and runbooks. Export from one
cluster's KubeSift, review it, commit it to a repo, import it anywhere. Nothing is uploaded anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kubesift import __version__, runbooks
from kubesift.store import Store

PACK_VERSION = 1


def export_pack(store: Store, runbooks_dir: str | None = None, check: str | None = None) -> dict[str, Any]:
    notes = [{"check": n["check_id"], "target": n["target"], "note": n["note"], "author": n["author"],
              "created": n["created"][:10]} for n in store.list_notes(check)]
    return {"kubesift_knowledge_pack": PACK_VERSION, "generated_by": f"kubesift {__version__}",
            "notes": notes, "runbooks": runbooks.load(runbooks_dir)}


def import_pack(store: Store, pack: dict[str, Any], runbooks_dir: str | None = None) -> dict[str, int]:
    if pack.get("kubesift_knowledge_pack") != PACK_VERSION:
        raise ValueError("not a KubeSift knowledge pack (missing kubesift_knowledge_pack: 1)")
    from kubesift.checks import REGISTRY

    existing = {(n["check_id"], n["target"], n["note"]) for n in store.list_notes()}
    added = skipped = 0
    for n in pack.get("notes") or []:
        check, note = n.get("check"), n.get("note")
        if not check or not note or check not in REGISTRY:
            skipped += 1
            continue
        if (check, n.get("target"), note) in existing:
            skipped += 1
            continue
        store.add_note(check, str(note), n.get("target"), n.get("author"))
        added += 1
    written = 0
    rbs = pack.get("runbooks") or []
    if rbs and runbooks_dir:
        path = Path(runbooks_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        (path / "imported.yaml").write_text(yaml.safe_dump(rbs, sort_keys=False), encoding="utf-8")
        written = len(rbs)
    return {"notes_added": added, "notes_skipped": skipped, "runbooks_written": written}
