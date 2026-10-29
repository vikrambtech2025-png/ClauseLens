"""Print one training example so the message format can be eyeballed.

Run from the repo root: ``python scripts/inspect_dataset.py``

Note: this file must never live at the repository root. A module named
``inspect.py`` shadows the standard library module of the same name and breaks
pytest, streamlit and anything else that imports ``inspect``.
"""

import json
from pathlib import Path

DATASET = Path(__file__).resolve().parents[1] / "data" / "dataset" / "train.jsonl"

d = json.loads(DATASET.open("r", encoding="utf-8").readline())
print(list(d.keys()))
print("msgs:", len(d["messages"]))
for m in d["messages"]:
    print(m["role"], len(m["content"]))
print("---")
print(d["messages"][-1]["content"][:200])
