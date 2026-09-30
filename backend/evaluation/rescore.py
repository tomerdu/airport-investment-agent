"""Re-score saved live answers offline. Costs nothing.

The first Phase 9 live run scored three of six cases FAIL. On inspection all
three were defects in the expectation matchers, not agent failures — a negated
mention counted as a claim, and "can't"/"leaves no" missed lists written with
"cannot"/"leave no". The matchers were corrected; this re-runs the scoring over
the saved answers so the corrected verdicts are reproducible without paying for
the calls again.

    python -m evaluation.rescore
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .agent_eval_cases import cases


def main() -> int:
    path = Path("evaluation/live_results.json")
    if not path.exists():
        print(f"no saved results at {path}")
        return 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    by_id = {c.id: c for c in cases()}

    print("Re-scoring saved live answers with the corrected matchers\n")
    print(f"{'case':<26}{'original':<10}{'corrected':<11}changed")
    changed = 0
    out = []
    for rec in saved["cases"]:
        case = by_id[rec["id"]]
        text = "\n\n".join(t.get("answer", "") for t in rec["turns"])
        unmet = [f.describe() for f in case.required_facts if not f.holds(text)]
        violated = [f.describe() for f in case.forbidden_claims
                    if not f.holds(text)]
        verdict = "PASS" if not unmet and not violated else "FAIL"
        moved = verdict != rec["verdict"]
        changed += bool(moved)
        print(f"{rec['id']:<26}{rec['verdict']:<10}{verdict:<11}"
              f"{'<-- matcher fixed' if moved else ''}")
        for u in unmet:
            print(f"    still unmet   : {u}")
        for v in violated:
            print(f"    still violated: {v}")
        out.append({**rec, "verdict_corrected": verdict,
                    "unmet_corrected": unmet, "violated_corrected": violated})

    passed = sum(1 for r in out if r["verdict_corrected"] == "PASS")
    print(f"\ncorrected: {passed}/{len(out)} PASS  "
          f"({changed} verdict(s) changed by the matcher fix)")

    saved["cases"] = out
    saved["rescored"] = True
    path.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    print(f"written back to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
