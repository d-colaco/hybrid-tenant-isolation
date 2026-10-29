"""The noisy-neighbour measurement: the response time of quiet tenants while one tenant runs a heavy job.

Three conditions, the same load and the same hardware limits (each PostgreSQL container has 2 CPUs and 2 GB):

* **baseline**: four quiet tenants run their dashboard query in a closed loop; nothing else runs.
* **noisy**: the same, while a heavy tenant (BigCo, millions of ledger lines in the *shared* database) runs its
  filing-deadline job in three sessions: a full scan with grouping and a window function, repeated.
* **fixed**: BigCo's ledger lines have been moved to the dedicated database (the move is measured too), and the same heavy
  job runs there while the quiet tenants stay in the shared database.

Each condition is run several times, interleaved, and every run is kept. The figures describe this laptop, this data and
this query; they show the effect of the placement, not a general speed-up. Command: ``make bench``.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

from dclabaccount import exceptions, generator, move
from dclabaccount.config import ROOT
from dclabaccount.db import Router, admin_connection
from dclabaccount.queries import QUERIES

QUIET = ("alfa-lda", "beta-sa", "gama-unip", "delta-sociedade")
THREADS_PER_QUIET_TENANT = 2
HEAVY_SESSIONS = 3
THINK_SECONDS = 0.02
WARMUP_SECONDS = 4.0
HEAVY_JOB = """
SELECT account_code, date_trunc('month', date) AS month, count(*) AS lines, sum(debit) AS debit, sum(credit) AS credit,
       sum(sum(debit - credit)) OVER (PARTITION BY account_code ORDER BY date_trunc('month', date)) AS running_balance
FROM ledger_line GROUP BY account_code, date_trunc('month', date)
"""


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


def _users() -> dict[str, UUID]:
    with admin_connection("shared") as admin:
        return {r["name"]: r["id"] for r in admin.execute("SELECT id, name FROM app.app_user")}


def run_condition(router: Router, name: str, users: dict[str, UUID], seconds: float, *, heavy: bool) -> dict:
    stop, measuring = threading.Event(), threading.Event()
    samples: list[tuple[float, float]] = []  # (seconds since the window opened, latency in ms)
    heavy_runs: list[float] = []
    opened = [0.0]
    lock = threading.Lock()

    def quiet(slug: str) -> None:
        query, user = QUERIES["dashboard"], users[f"staff-{slug}"]
        while not stop.is_set():
            started = time.perf_counter()
            with router.tenant_tx(slug, user, store="ledger") as conn:
                query.run(conn)
            ended = time.perf_counter()
            if measuring.is_set():
                with lock:
                    samples.append((started - opened[0], (ended - started) * 1000))
            time.sleep(THINK_SECONDS)

    def heavy_job() -> None:
        user = users["staff-bigco"]
        while not stop.is_set():
            started = time.perf_counter()
            with router.tenant_tx("bigco", user, store="ledger") as conn:
                conn.execute(HEAVY_JOB).fetchall()
            with lock:
                heavy_runs.append(time.perf_counter() - started)

    threads = [threading.Thread(target=quiet, args=(s,), daemon=True) for s in QUIET for _ in range(THREADS_PER_QUIET_TENANT)]
    if heavy:
        threads += [threading.Thread(target=heavy_job, daemon=True) for _ in range(HEAVY_SESSIONS)]
    for thread in threads:
        thread.start()
    time.sleep(WARMUP_SECONDS)
    opened[0] = time.perf_counter()
    measuring.set()
    time.sleep(seconds)
    measuring.clear()
    during = len(heavy_runs)
    stop.set()
    for thread in threads:
        thread.join(timeout=120)
    latencies = [ms for _, ms in samples]
    per_second: dict[int, list[float]] = defaultdict(list)
    for at, ms in samples:
        per_second[int(at)].append(ms)
    return {
        "condition": name,
        "requests": len(latencies),
        "requests_per_second": round(len(latencies) / seconds, 1),
        "p50_ms": round(percentile(latencies, 0.50), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "p99_ms": round(percentile(latencies, 0.99), 2),
        "max_ms": round(max(latencies), 2),
        "heavy_runs_completed_in_window": during if heavy else 0,
        "heavy_run_seconds_median": round(statistics.median(heavy_runs), 2) if heavy_runs else None,
        "p95_ms_per_second": [round(percentile(per_second[s], 0.95), 2) for s in range(int(seconds))],
    }


def environment(lines: int) -> dict:
    with admin_connection("shared") as admin:
        version = admin.execute("SHOW server_version").fetchone()["server_version"]
        rows = admin.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"]
    return {
        "date": date.today().isoformat(), "host": platform.platform(), "postgres": version, "containers": "2 CPUs and 2 GB each, shared_buffers 256MB",
        "bigco_lines": lines, "ledger_lines_in_shared_at_start": rows, "quiet_tenants": len(QUIET), "quiet_threads": len(QUIET) * THREADS_PER_QUIET_TENANT,
        "think_seconds": THINK_SECONDS, "heavy_sessions": HEAVY_SESSIONS,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=3_000_000)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "reports" / "noisy-neighbour.json"))
    args = parser.parse_args()
    exceptions.sync()
    if not args.skip_seed:
        specs = tuple(generator.TenantSpec(s.slug, s.name, args.lines if s.slug == "bigco" else s.lines) for s in generator.DEFAULT_SPECS)
        started = time.perf_counter()
        generator.seed(specs, progress=print)
        print(f"seeded in {time.perf_counter() - started:.0f} s")
    router = Router(pool_size=24)
    users = _users()
    runs: list[dict] = []
    try:
        env = environment(args.lines)
        for rep in range(1, args.reps + 1):
            for name, heavy in (("baseline", False), ("noisy", True)):
                print(f"rep {rep}: {name}")
                runs.append({"rep": rep, **run_condition(router, name, users, args.seconds, heavy=heavy)})
        print("moving BigCo's ledger lines to the dedicated database")
        started = time.perf_counter()
        report = move.move_stream(router, users["staff-bigco"], "bigco", "heavy")
        retired = move.retire(router, users["staff-bigco"], "bigco", "shared")
        move_seconds = round(time.perf_counter() - started, 1)
        with admin_connection("shared") as admin:
            admin.execute("VACUUM (ANALYZE) ledger_line")
        with admin_connection("heavy") as admin:
            admin.execute("ANALYZE ledger_line")
        for rep in range(1, args.reps + 1):
            for name, heavy in (("baseline-after-move", False), ("fixed", True)):
                print(f"rep {rep}: {name}")
                runs.append({"rep": rep, **run_condition(router, name, users, args.seconds, heavy=heavy)})
    finally:
        router.close()
    summary = {}
    for name in ("baseline", "noisy", "baseline-after-move", "fixed"):
        group = [r for r in runs if r["condition"] == name]
        summary[name] = {
            "p95_ms_median": round(statistics.median(r["p95_ms"] for r in group), 2),
            "p95_ms_runs": [r["p95_ms"] for r in group],
            "p99_ms_median": round(statistics.median(r["p99_ms"] for r in group), 2),
            "p50_ms_median": round(statistics.median(r["p50_ms"] for r in group), 2),
            "heavy_run_seconds_median": next((r["heavy_run_seconds_median"] for r in group if r["heavy_run_seconds_median"]), None),
        }
    result = {
        "environment": env, "seconds_per_run": args.seconds, "reps": args.reps, "summary": summary, "runs": runs,
        "move": {"lines": report.lines, "copy_seconds": report.copy_seconds, "verified": report.verified, "total_seconds_with_retire": move_seconds, "retired_lines": retired},
        "command": f"make bench (python -m dclabaccount.bench --lines {args.lines} --seconds {args.seconds:g} --reps {args.reps})",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
