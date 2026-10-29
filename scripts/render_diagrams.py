"""Render demo/diagrams/*.mmd to SVG (light and dark) and PNG with Mermaid CLI.

Needs Node (``npx`` fetches @mermaid-js/mermaid-cli) and a Chromium. It uses the headless shell that Playwright
installed (``playwright install chromium``); set ``CHROME`` to use another one.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS = ROOT / "demo" / "diagrams"


def chrome() -> str:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    found = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")))
    if not found:
        sys.exit("no Chromium found: run `playwright install chromium` or set CHROME")
    return found[-1]


def render(source: Path, target: Path, config: Path, puppeteer: Path, *, background: str) -> None:
    command = ["npx", "-y", "@mermaid-js/mermaid-cli", "-p", str(puppeteer), "-c", str(config), "-b", background, "-i", str(source), "-o", str(target)]
    if target.suffix == ".png":
        command += ["-s", "2"]
    subprocess.run(command, check=True, env={**os.environ, "PUPPETEER_SKIP_DOWNLOAD": "1"}, capture_output=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        puppeteer = Path(tmp) / "puppeteer.json"
        puppeteer.write_text(json.dumps({"executablePath": chrome(), "args": ["--no-sandbox"]}))
        for source in sorted(DIAGRAMS.glob("*.mmd")):
            render(source, source.with_suffix(".svg"), DIAGRAMS / "mermaid-light.json", puppeteer, background="white")
            render(source, source.with_name(source.stem + "-dark.svg"), DIAGRAMS / "mermaid-dark.json", puppeteer, background="transparent")
            render(source, source.with_suffix(".png"), DIAGRAMS / "mermaid-light.json", puppeteer, background="white")
            print("rendered", source.name)


if __name__ == "__main__":
    main()
