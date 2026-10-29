"""Measure SAF-T import throughput on a synthetic file: generate, parse and validate, import (batched INSERTs, because COPY
FROM is refused under row-level security). Writes reports/import-throughput.json. Needs `make up`. Reseeds the database."""

import json
import platform
import time
from datetime import date

from dclabaccount import exceptions, generator, saft
from dclabaccount.config import ROOT
from dclabaccount.db import Router
from dclabaccount.imports import import_saft

TRANSACTIONS = 50_000


def main() -> None:
    exceptions.sync()
    corpus = generator.seed(generator.TEST_SPECS)
    tenant = corpus.tenants["alfa-lda"]
    started = time.perf_counter()
    content = saft.generate(tenant["name"], tenant["nif"], 2025, 3, TRANSACTIONS)
    generate_s = time.perf_counter() - started
    started = time.perf_counter()
    parsed, problems = saft.check(content, nif=tenant["nif"])
    check_s = time.perf_counter() - started
    router = Router()
    try:
        result = import_saft(router, corpus.users["staff-alfa-lda"], "alfa-lda", "big.xml", content)
    finally:
        router.close()
    lines = parsed.lines
    out = {
        "date": date.today().isoformat(), "host": platform.platform(), "transactions": TRANSACTIONS, "lines": lines, "file_mb": round(len(content) / 1e6, 1),
        "problems": len(problems), "status": result.status,
        "generate_seconds": round(generate_s, 1), "parse_and_validate_seconds": round(check_s, 1), "import_total_seconds": round(result.seconds, 1),
        "lines_per_second_total": round(lines / result.seconds), "command": "python scripts/measure_import.py",
    }
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "import-throughput.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
