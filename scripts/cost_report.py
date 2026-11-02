"""What the call log says was spent, by model and purpose. Muse Code's own usage is separate; see the session log.

python scripts/cost_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db as dbmod  # noqa: E402
from app.config import RUNS_DIR, get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    total = 0.0
    for path in sorted(RUNS_DIR.glob("*.sqlite")):
        conn = dbmod.connect(path)
        spent = conn.execute(
            "SELECT COALESCE(SUM(est_cost_usd), 0) AS c, COUNT(*) AS n FROM calls WHERE replayed = 0"
        ).fetchone()
        print(f"{path.name}: {spent['n']} live calls, ${spent['c']:.4f}")
        total += spent["c"]
        rows = conn.execute(
            "SELECT model, purpose, COUNT(*) AS n, SUM(tokens_in) AS i, SUM(tokens_cached) AS c, SUM(tokens_out) AS o, SUM(est_cost_usd) AS usd "
            "FROM calls WHERE replayed = 0 GROUP BY model, purpose ORDER BY usd DESC"
        ).fetchall()
        for r in rows:
            print(
                f"  {r['model']:28} {r['purpose']:24} n={r['n']:<4} in={r['i']:<9} cached={r['c']:<9} out={r['o']:<7} ${r['usd']:.4f}"
            )
    print(f"total ${total:.4f} of the ${settings.budget_total:.0f} budget, across local databases")


if __name__ == "__main__":
    main()
