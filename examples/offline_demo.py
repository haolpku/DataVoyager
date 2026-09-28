"""Exercise real HTML extraction, storage and lineage without network or models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataflowwebagent.agents.Obtainer.datamixer.operators import run_pipeline
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore

# Self-contained authored fixtures, not fetched pages or simulated model output.
PAGES = [
    ("python-functions", "Python functions", """
    <html><head><title>Python functions</title></head><body><main>
    <h1>Python functions</h1><p>A Python function can return a value using the
    return statement. A function that reaches its end without a return statement
    returns None. Parameters let a caller pass inputs into the function.</p>
    <pre>def add(a, b):\n    return a + b</pre>
    </main><script>do_not_include_this_script()</script></body></html>"""),
    ("python-lists", "Python lists", """
    <html><head><title>Python lists</title></head><body><main>
    <h1>Python lists</h1><p>A Python list stores an ordered sequence of objects.
    Lists are mutable. The append method adds one item at the end of a list.
    Indexing starts at zero, and a negative index counts from the end.</p>
    <pre>values = [1, 2]\nvalues.append(3)</pre>
    </main></body></html>"""),
]


def run_demo(warehouse: Path) -> dict:
    store = DataStore.init(warehouse)
    try:
        dataset = store.catalog.resolve_dataset("offline_l1")
        if dataset is None:
            dataset = store.catalog.add_dataset(name="offline_l1", source="authored_fixture")
        records = [{"content": {"html": html, "title": title,
                                 "url": f"https://example.invalid/{slug}"},
                    "source_uri": f"https://example.invalid/{slug}", "domain": "code"}
                   for slug, title, html in PAGES]
        store.ingest_records(dataset, records, defaults={"quality_level": "L1"}, decontaminate=False)
        result = run_pipeline(store, {
            "name": "offline_html_to_text",
            "source": {"dataset": "offline_l1", "filter": "quality_level = 'L1'"},
            "operators": [{"name": "webpage_to_pt",
                           "args": {"engine": "legacy", "min_chars": 80},
                           "output": {"dataset": "offline_l2", "quality_level": "L2",
                                      "stage": "pretrain", "modality": "text"}}],
        })
        counts = {name: store.catalog.count(dataset_id=store.catalog.resolve_dataset(name))
                  for name in ("offline_l1", "offline_l2")}
        report = {"mode": "offline", "warehouse": str(warehouse.resolve()),
                  "counts": counts, "pipeline": result.to_dict(),
                  "scope": "Authored HTML to L2 text only; no web acquisition or QA generation."}
        (warehouse / "offline_report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", type=Path, default=Path("runs/offline-demo"))
    print(json.dumps(run_demo(parser.parse_args().warehouse), indent=2))
