"""Prepare the delivered dashboard for Grafana's file provisioning.

dashboard.json is an "export for sharing externally": its datasources are
import variables (${DS_BHS_API}) that Grafana asks you to map by hand at
import time. File provisioning never asks that question, it needs resolved
UIDs instead.

This script therefore produces a copy where:

  - ${DS_BHS_API} and ${DS_BHS_SQL} become the provisioned UIDs,
  - __inputs and __requires are dropped, being meaningless outside a manual
    import.

Nothing else is touched: the queries, the thresholds and the layout that end
up on screen are the ones from the delivered file.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "grafana" / "dashboards" / "bhs-controlroom.json"

# The deliverable first, the embedded copy second: inside the repository we
# always start from the rendered use case 2 file; once detached from it
# (bench.ps1 package), the bench falls back on the copy it carries along.
SOURCES = (
    ROOT.parent / "use-case-2-dashboard" / "solution" / "dashboard.json",
    ROOT / "dashboard" / "bhs-controlroom.source.json",
)

# Must match the uid values in grafana/provisioning/datasources/bhs.yaml.
UIDS = {"${DS_BHS_API}": "DS_BHS_API", "${DS_BHS_SQL}": "DS_BHS_SQL"}


def main() -> None:
    source = next((path for path in SOURCES if path.exists()), None)
    if source is None:
        sys.exit("dashboard.json not found: " + ", ".join(str(s) for s in SOURCES))

    raw = source.read_text(encoding="utf-8")
    for variable, uid in UIDS.items():
        raw = raw.replace(variable, uid)

    dashboard = json.loads(raw)
    for key in ("__inputs", "__requires"):
        dashboard.pop(key, None)

    # Provisioning refuses a dashboard whose id collides with an existing one;
    # the uid is enough to identify it.
    dashboard["id"] = None

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")

    panels = len(dashboard.get("panels", []))
    print(
        f"{TARGET.relative_to(ROOT)} generated from {source.name}: "
        f"uid={dashboard.get('uid')}, {panels} panels"
    )


if __name__ == "__main__":
    main()
