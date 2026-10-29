"""Render the evidence frames in demo/screenshots from live runs of the lab (a small seeded corpus) and from the
recorded benchmark (reports/noisy-neighbour.json, made by ``make bench``).

Each frame is an HTML page built from the lab's own output and photographed with headless Chromium. They are evidence, not
an interface: the lab has none. Needs Playwright's Chromium (``playwright install chromium``) or CHROME; needs ``make up``.
"""

from __future__ import annotations

import glob
import html
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from dclabaccount import exceptions, generator, move, offboard, saft
from dclabaccount.config import ROOT
from dclabaccount.db import NotFound, Router, admin_connection
from dclabaccount.imports import import_saft
from dclabaccount.queries import QUERIES

OUT = ROOT / "demo" / "screenshots"
BENCH = ROOT / "reports" / "noisy-neighbour.json"

CSS = """
:root { --bg:#fcfcfb; --fg:#0b0b0b; --muted:#52514e; --line:#dcdad3; --ok:#1a7f37; --bad:#cf222e; --warn:#9a6700; --chip:#f4f3ef;
        --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; }
* { box-sizing:border-box; }
body { margin:0; background:#ecebe6; font:15px/1.45 -apple-system,"Segoe UI",Inter,Helvetica,Arial,sans-serif; color:var(--fg); }
.frame { width:1180px; padding:28px 32px; background:var(--bg); }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:20px 0 8px; } .sub { color:var(--muted); margin:0 0 18px; }
table { border-collapse:collapse; width:100%; } th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12.5px; text-transform:uppercase; letter-spacing:.03em; }
code, pre, .mono { font-family:"JetBrains Mono","DejaVu Sans Mono",Menlo,monospace; font-size:13px; }
pre { background:var(--chip); border:1px solid var(--line); border-radius:6px; padding:12px; margin:0; white-space:pre-wrap; }
.pass { color:var(--ok); font-weight:700; } .fail { color:var(--bad); font-weight:700; } .warn { color:var(--warn); font-weight:700; }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:22px; }
.foot { margin-top:16px; padding-top:10px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; }
.num { text-align:right; font-variant-numeric:tabular-nums; }
.step { padding:6px 10px; border:1px solid var(--line); border-radius:6px; margin-bottom:6px; background:var(--chip); }
"""


def esc(v) -> str:
    return html.escape(str(v))


def page(title: str, sub: str, body: str, foot: str = "") -> str:
    footer = f'<div class="foot">{foot}</div>' if foot else ""
    return f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body><div class="frame"><h1>{esc(title)}</h1><p class="sub">{sub}</p>{body}{footer}</div></body></html>'


def ok(flag: bool, yes="pass", no="FAIL") -> str:
    return f'<span class="{"pass" if flag else "fail"}">{yes if flag else no}</span>'


# --- frames ---------------------------------------------------------------------------------------


def frame_isolation(router, corpus) -> str:
    alfa, beta = corpus.tenants["alfa-lda"]["id"], corpus.tenants["beta-sa"]["id"]
    rows = ""
    for query in QUERIES.values():
        cells = []
        for slug, own in (("alfa-lda", alfa), ("beta-sa", beta)):
            with router.tenant_tx(slug, corpus.users[f"staff-{slug}"], store=query.store) as conn:
                result = query.run(conn)
            foreign = sum(1 for r in result if r["tenant_id"] != own)
            cells += [len(result), foreign]
        rows += f"<tr><td><code>{esc(query.name)}</code></td><td class='num'>{cells[0]}</td><td class='num'>{ok(cells[1] == 0, '0', str(cells[1]))}</td><td class='num'>{cells[2]}</td><td class='num'>{ok(cells[3] == 0, '0', str(cells[3]))}</td></tr>"
    leaks = 0
    with admin_connection("shared") as admin:
        admin.execute("ALTER TABLE ledger_line DISABLE ROW LEVEL SECURITY")
        try:
            for query in QUERIES.values():
                if query.store == "ledger":
                    with router.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"], store="ledger") as conn:
                        leaks += sum(1 for r in query.run(conn) if r["tenant_id"] != alfa)
        finally:
            admin.execute("ALTER TABLE ledger_line ENABLE ROW LEVEL SECURITY")
    with router.pools["shared"].connection() as conn:
        role = conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
        unprotected = conn.execute(
            "SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relname <> 'alembic_version' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
        ).fetchone()["n"]
    body = (
        "<table><tr><th>Registered query</th><th>Alfa: rows</th><th>Alfa: foreign rows</th><th>Beta: rows</th><th>Beta: foreign rows</th></tr>" + rows + "</table>"
        f"<h2>Can the test fail?</h2><p>With row-level security switched off on the ledger table, the same walk finds <b class='fail'>{leaks}</b> foreign rows, "
        f"so a green run means something. The application role: superuser <b>{role['rolsuper']}</b>, bypass <b>{role['rolbypassrls']}</b>, "
        f"tables without forced row-level security <b>{unprotected}</b>.</p>"
    )
    return page("The two-tenant test", "Every registered query runs as two tenants. None of them filters on the tenant: the database does. A new query is covered the moment it is registered.", body,
                "Source: <code>tests/test_isolation.py</code>. Synthetic data; the corpus here is small (400 lines per tenant).")


def frame_exception(router, corpus) -> str:
    entry = exceptions.load()["my_work_across_clients"]
    rita = exceptions.run(router, corpus.users["rita"], "my_work_across_clients")
    paulo = exceptions.run(router, corpus.users["paulo"], "my_work_across_clients")
    staff = exceptions.run(router, corpus.users["staff-gama-unip"], "my_work_across_clients")

    def by_tenant(result):
        counts: dict[str, int] = {}
        for r in result:
            counts[r["tenant"]] = counts.get(r["tenant"], 0) + 1
        return ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))

    with admin_connection("shared") as admin:
        use = admin.execute("SELECT exception_id, tenants, at FROM app.exception_use ORDER BY id DESC LIMIT 1").fetchone()
    refused = []
    for label, call in (
        ("an id that is not on the list", lambda: exceptions.run(router, corpus.users["rita"], "all_ledgers_unreviewed")),
        ("the statement edited after review", lambda: exceptions.run(router, corpus.users["rita"], entry.id, entries={entry.id: exceptions.ReviewedException(entry.id, entry.description, entry.sql + "\n-- extra", entry.sql_sha256, entry.reviewer, entry.reviewed_on)})),
    ):
        try:
            call()
            refused.append((label, "ran"))
        except exceptions.ExceptionNotReviewed as error:
            refused.append((label, str(error)))
    body = (
        f"<h2>The list entry, kept like code (<code>exceptions.yaml</code>)</h2><pre>id: {esc(entry.id)}\nreviewer: {esc(entry.reviewer)}   reviewed_on: {esc(entry.reviewed_on)}\nsql_sha256: {esc(entry.sql_sha256)}\n\n{esc(entry.sql.strip())}</pre>"
        "<h2>What the same statement returns for three users</h2><table><tr><th>User</th><th>Works for</th><th>Rows by client</th></tr>"
        f"<tr><td>Rita (accountant)</td><td>5 clients</td><td class='mono'>{esc(by_tenant(rita))}</td></tr>"
        f"<tr><td>Paulo (accountant)</td><td>2 clients</td><td class='mono'>{esc(by_tenant(paulo))}</td></tr>"
        f"<tr><td>Staff of one client</td><td>1 client</td><td class='mono'>{esc(by_tenant(staff))}</td></tr></table>"
        "<h2>Refused</h2><table>" + "".join(f"<tr><td>{esc(a)}</td><td class='mono'>{esc(b)}</td></tr>" for a, b in refused) + "</table>"
        f"<h2>Recorded by the database on every use (the last one: the single-client user)</h2><p class='mono'>exception_use: {esc(use['exception_id'])}, {use['tenants']} tenant{'' if use['tenants'] == 1 else 's'}, {esc(use['at'].strftime('%Y-%m-%d %H:%M:%S'))}</p>"
    )
    return page("A cross-tenant user as a reviewed exception", "An accountant works for many clients and needs one result set. That is the one place the boundary is crossed on purpose.", body,
                "Source: <code>tests/test_scope_and_pool.py</code>. Read-only; ledger lines of a dedicated tenant have no such path.")


def frame_pool(corpus) -> str:
    one = Router(pool_size=1)
    try:
        with one.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"], store="ledger") as conn:
            first = conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"]
        with one.pools["shared"].connection() as conn:
            setting = conn.execute("SELECT current_setting('app.tenant_id', true) AS t").fetchone()["t"]
            second = conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"]
        with one.tenant_tx("alfa-lda", corpus.users["staff-alfa-lda"], store="ledger", _unsafe_session_scope=True) as conn:
            conn.execute("SELECT 1")
        with one.pools["shared"].connection() as conn:
            leaked = conn.execute("SELECT count(*) AS n FROM ledger_line").fetchone()["n"]
    finally:
        one.close()
    body = (
        "<table><tr><th>Step (one pooled connection)</th><th>Setting scope</th><th>Rows seen</th><th>Verdict</th></tr>"
        f"<tr><td>Request 1 names tenant Alfa</td><td class='mono'>transaction</td><td class='num'>{first}</td><td>{ok(first == 400, 'Alfa only')}</td></tr>"
        f"<tr><td>Request 2 borrows the same connection and names no tenant</td><td class='mono'>current_setting = {esc(repr(setting))}</td><td class='num'>{second}</td><td>{ok(second == 0, 'no rows')}</td></tr>"
        f"<tr><td>Negative control: request 1 sets the tenant for the <i>session</i></td><td class='mono'>session</td><td class='num'>{leaked}</td><td>{ok(leaked == 0, 'no rows', 'LEAK: runs as Alfa')}</td></tr></table>"
    )
    return page("The tenant does not survive a pooled connection", "The tenant is set per transaction, so the next request that borrows the connection starts with none. The third row shows what a session-level setting would do.", body,
                "Source: <code>tests/test_scope_and_pool.py</code>. The negative control exists so the test is known to be able to fail.")


def chart(runs_by: dict[str, dict]) -> str:
    width, height, left, right, top, bottom = 1100, 346, 60, 30, 40, 40
    series = [("baseline", "Baseline", "var(--s1)"), ("noisy", "Heavy job on the shared database", "var(--s2)"), ("fixed", "Heavy job on its own database", "var(--s3)")]
    seconds = len(runs_by["baseline"]["p95_ms_per_second"])
    top_value = max(max(runs_by[k]["p95_ms_per_second"]) for k, _, _ in series)
    ymax = 10 ** len(str(int(top_value))) if top_value < 5 else (int(top_value / 10) + 1) * 10

    def x(i): return left + (width - left - right) * i / max(1, seconds - 1)
    def y(v): return top + (height - top - bottom) * (1 - v / ymax)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="p95 response time of quiet tenants per second, three conditions">']
    for tick in range(0, 6):
        value = ymax * tick / 5
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="var(--line)" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{y(value) + 4:.1f}" text-anchor="end" font-size="12" fill="var(--muted)">{value:g}</text>')
    for s in range(0, seconds, 5):
        parts.append(f'<text x="{x(s):.1f}" y="{height - 16}" text-anchor="middle" font-size="12" fill="var(--muted)">{s} s</text>')
    parts.append(f'<text x="{left - 48}" y="{top - 22}" font-size="12" fill="var(--muted)">p95, ms</text>')
    for key, _label, colour in series:
        values = runs_by[key]["p95_ms_per_second"]
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{x(len(values) - 1):.1f}" cy="{y(values[-1]):.1f}" r="4" fill="{colour}" stroke="var(--bg)" stroke-width="2"/>')
    parts.append("</svg>")
    legend = "".join(f'<span style="margin-right:22px"><span style="display:inline-block;width:18px;height:3px;background:{c};vertical-align:middle;margin-right:6px"></span>{esc(label)}</span>' for _, label, c in series)
    return f'<div style="margin:6px 0 10px">{legend}</div>' + "".join(parts)


def frame_noisy() -> str | None:
    if not BENCH.exists():
        return None
    data = json.loads(BENCH.read_text())

    def median_run(condition: str) -> dict:
        group = sorted((r for r in data["runs"] if r["condition"] == condition), key=lambda r: r["p95_ms"])
        return group[len(group) // 2]

    chosen = {"baseline": median_run("baseline"), "noisy": median_run("noisy"), "fixed": median_run("fixed")}
    summary = data["summary"]
    rows = ""
    for key, label in (("baseline", "Baseline: no heavy job"), ("noisy", "Heavy job on the shared database"), ("baseline-after-move", "Baseline again, after the move"), ("fixed", "Heavy job on its own database")):
        s = summary[key]
        rows += (f"<tr><td>{esc(label)}</td><td class='num'>{s['p50_ms_median']}</td><td class='num'><b>{s['p95_ms_median']}</b></td><td class='num'>{s['p99_ms_median']}</td>"
                 f"<td class='mono'>{esc(', '.join(str(v) for v in s['p95_ms_runs']))}</td><td class='num'>{s['heavy_run_seconds_median'] or '-'}</td></tr>")
    env = data["environment"]
    move_info = data["move"]
    body = (
        chart(chosen)
        + "<h2>Quiet tenants' response time (ms), median of the repetitions</h2>"
        "<table><tr><th>Condition</th><th>p50</th><th>p95</th><th>p99</th><th>p95 of each repetition</th><th>Heavy run (s)</th></tr>" + rows + "</table>"
        f"<p class='sub' style='margin-top:12px'>{env['quiet_threads']} quiet client threads on {env['quiet_tenants']} tenants, {env['heavy_sessions']} heavy sessions for BigCo ({env['bigco_lines']:,} ledger lines), "
        f"{data['seconds_per_run']:g} s per run, {data['reps']} repetitions per condition. The move of {move_info['lines']:,} lines took {move_info['copy_seconds']} s to copy ({'verified' if move_info['verified'] else 'NOT verified'}).</p>"
    )
    return page("Noisy neighbour: quiet tenants while one tenant runs a heavy job", "p95 per second of the dashboard query of four quiet tenants, one repetition per condition (the one with the median p95).", body,
                f"Source: <code>{esc(data['command'])}</code>, {esc(env['date'])}, {esc(env['postgres'])}, containers with {esc(env['containers'])}. One laptop, this data, this query: it shows the effect of the placement, not a general speed-up.")


def frame_move(rep: dict) -> str:
    rows = ""
    for step in rep["steps"]:
        detail = ", ".join((k.replace("_", " ") if v else f"NOT {k.replace('_', ' ')}") if isinstance(v, bool) else f"{k.replace('_', ' ')} {v}"
                           for k, v in step.items() if k in ("lines", "copy_seconds", "verified", "deleted", "equal", "reads_equal", "source", "target"))
        good = step.get("equal", step.get("reads_equal", step.get("verified", True)))
        rows += f"<tr><td>{esc(step['step'])}</td><td class='mono'>{esc(detail)}</td><td>{ok(bool(good))}</td></tr>"
    return page("A tenant move, rehearsed", "Copy, verify, switch, a full cycle, delete; then back again. Reads are compared with the ones taken before the move.",
                f"<table><tr><th>Step</th><th>Detail</th><th>Check</th></tr>{rows}</table>",
                "Source: <code>make move</code> (<code>dclabaccount.move.rehearse</code>) on the small corpus (2,000 lines for BigCo); the 3-million-line move is in the noisy-neighbour frame. COPY FROM is refused on a table with row-level security, so the lines go in as batched INSERTs.")


def frame_saft(router, corpus) -> str:
    user, slug = corpus.users["staff-alfa-lda"], "alfa-lda"
    tenant = corpus.tenants[slug]
    base = saft.generate(tenant["name"], tenant["nif"], 2025, 3, 60)
    steps = []
    first = import_saft(router, user, slug, "2025-03.xml", base)
    again = import_saft(router, user, slug, "2025-03-again.xml", base)
    corrected = import_saft(router, user, slug, "2025-03-v2.xml", saft.generate(tenant["name"], tenant["nif"], 2025, 3, 75, seed=2))
    steps = [("First import of March", first.status, f"{first.lines} lines"), ("The same file again", again.status, "nothing changed"),
             ("A corrected file for March", corrected.status, f"{corrected.lines} lines, replaced {corrected.replaced_lines} (not appended)")]
    bad_rows = ""
    for kind in sorted(saft.CORRUPTIONS):
        r = import_saft(router, user, slug, f"{kind}.xml", saft.corrupt(saft.generate(tenant["name"], tenant["nif"], 2025, 4, 60), kind))
        found = sorted({p.code for p in r.problems})
        bad_rows += f"<tr><td><code>{esc(kind)}</code></td><td class='mono'>{esc(', '.join(found))}</td><td>{ok(r.status == 'quarantined', 'quarantined', r.status)}</td></tr>"
    with router.tenant_tx(slug, user, store="ledger") as conn:
        april = conn.execute("SELECT count(*) AS n FROM ledger_line WHERE to_char(date, 'YYYY-MM') = '2025-04' AND batch_id IS NOT NULL").fetchone()["n"]
    count_word = {6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}.get(len(saft.CORRUPTIONS), str(len(saft.CORRUPTIONS)))
    body = (
        "<h2>Idempotent re-import</h2><table><tr><th>Step</th><th>Status</th><th>Result</th></tr>" + "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in steps) + "</table>"
        f"<h2>{count_word.capitalize()} ways a file can be bad: every one is quarantined with its reason</h2><table><tr><th>Corruption</th><th>Reasons found</th><th>Outcome</th></tr>" + bad_rows + "</table>"
        f"<p style='margin-top:10px'>Lines of April that entered the ledger from all {count_word} bad files: <b class='{'pass' if april == 0 else 'fail'}'>{april}</b>.</p>"
    )
    return page("SAF-T import: validation, quarantine, and re-import", "A file enters the ledger whole or not at all. Files are synthetic, in a subset of the SAF-T (PT) structure, and not validated against the official XSD.", body,
                "Source: <code>tests/test_saft_import.py</code>. The lab accepts only files that carry its synthetic watermark.")


def frame_offboard(router, corpus) -> str:
    user, slug = corpus.users["staff-alfa-lda"], "alfa-lda"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        try:
            offboard.erase(router, user, slug, dest)
            before = "erased"
        except offboard.OffboardRefused as error:
            before = str(error)
        manifest = offboard.export_tenant(router, user, slug, dest)
        report = offboard.erase(router, user, slug, dest)
        refusals = []
        for label, call in (("purge, no retention date set", lambda: offboard.purge(slug)),):
            try:
                call()
                refusals.append((label, "deleted"))
            except offboard.OffboardRefused as error:
                refusals.append((label, str(error)))
        offboard.set_retention(slug, date(2040, 1, 1))
        try:
            offboard.purge(slug, today=date(2039, 12, 31))
            refusals.append(("purge before the date", "deleted"))
        except offboard.OffboardRefused as error:
            refusals.append(("purge one day before the (made-up) date 2040-01-01", str(error)))
        deleted = offboard.purge(slug, today=date(2040, 1, 1))
    try:
        router.resolve(slug, user)
        entry = "still can enter"
    except NotFound:
        entry = "not found: nobody can enter an offboarded tenant"
    files = "".join(f"<tr><td><code>{esc(n)}.csv</code></td><td class='num'>{m['rows']}</td><td class='mono'>{esc(m['sha256'][:16])}...</td></tr>" for n, m in manifest["files"].items())
    kept = ", ".join(f"{k} {v}" for k, v in report.retained.items() if v)
    body = (
        f"<h2>Before an export exists</h2><p class='mono'>erase: {esc(before)}</p>"
        f"<h2>Export (manifest of row counts and hashes)</h2><table><tr><th>File</th><th class='num'>Rows</th><th>sha256</th></tr>{files}</table>"
        f"<h2>Erasure</h2><p>{report.erased_parties} parties' personal data replaced by placeholders, {report.deleted_quarantine_files} raw files deleted. Kept: <span class='mono'>{esc(kept)}</span>. Entering the tenant afterwards is refused ({esc(entry)}).</p>"
        "<h2>Purge of the accounting records</h2><table>" + "".join(f"<tr><td>{esc(a)}</td><td class='mono'>{esc(b)}</td></tr>" for a, b in refusals)
        + f"<tr><td>purge on the date</td><td class='mono'>deleted {esc(', '.join(f'{k} {v}' for k, v in deleted.items() if v))}</td></tr></table>"
    )
    return page("Offboarding: export, erasure, and the retention tension", "Personal data goes now; accounting records stay until a retention date the operator sets. The lab states no period and defaults to none.", body,
                "Source: <code>tests/test_move_offboard.py</code>. The date 2040-01-01 is made up for the demonstration, not a legal period: verify the real one with the accountant before any value is written.")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    exceptions.sync()
    router = Router()
    frames: dict[str, str] = {}
    try:
        corpus = generator.seed(generator.TEST_SPECS)
        frames["01-two-tenant-test"] = frame_isolation(router, corpus)
        frames["02-reviewed-exception"] = frame_exception(router, corpus)
        frames["03-pooled-connection"] = frame_pool(corpus)
        corpus = generator.seed(generator.TEST_SPECS)
        frames["06-saft-import"] = frame_saft(router, corpus)
        corpus = generator.seed(generator.TEST_SPECS)
        move_report = move.rehearse(out=Path(tempfile.gettempdir()) / "tenant-move.json")
        frames["05-tenant-move"] = frame_move(move_report)
        corpus = generator.seed(generator.TEST_SPECS)
        frames["07-offboarding"] = frame_offboard(router, corpus)
    finally:
        router.close()
    noisy = frame_noisy()
    if noisy:
        frames["04-noisy-neighbour"] = noisy
    executable = os.environ.get("CHROME") or sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))[-1]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        tab = browser.new_page(viewport={"width": 1240, "height": 900}, device_scale_factor=1.5)
        for name, content in sorted(frames.items()):
            tab.set_content(content)
            tab.locator(".frame").screenshot(path=str(OUT / f"{name}.png"))
            print("wrote", name)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
