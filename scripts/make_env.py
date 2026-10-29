#!/usr/bin/env python3
"""Write .env from .env.example with a random password for each *_PASSWORD. Does nothing if .env exists."""
import pathlib
import re
import secrets
import sys

root = pathlib.Path(__file__).resolve().parent.parent
target, example = root / ".env", root / ".env.example"
if target.exists():
    sys.exit(0)
text = re.sub(r"^([A-Z_]*PASSWORD)=.*$", lambda m: f"{m.group(1)}=lab_{secrets.token_hex(12)}", example.read_text(), flags=re.M)
target.write_text(text)
target.chmod(0o600)
print(".env written with generated passwords")
