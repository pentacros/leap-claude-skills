#!/usr/bin/env python3
"""Read a KEY=VALUE from a .env file when it isn't already in the environment.

The scripts used to read os.environ only, which assumed someone had run
`source .env` in a shell first. A non-technical user pastes the key into the file
and never opens a terminal, so the file has to be read directly or the key
silently does nothing.

Deliberately dumb: no shell expansion, no exports, no code execution. Existing
environment variables always win, so a real export still overrides the file.
"""
import os

SEARCH = (".env", "../.env", "../../.env")


def load(name, start=None):
    """Return the value of `name` from the environment, else from a nearby .env."""
    val = os.environ.get(name)
    if val:
        return val
    base = os.path.abspath(start or os.getcwd())
    for rel in SEARCH:
        path = os.path.normpath(os.path.join(base, rel))
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, raw = line.partition("=")
                    if key.strip() != name:
                        continue
                    v = raw.strip().strip('"').strip("'")
                    if v and not v.startswith("your-key"):   # ignore the placeholder
                        os.environ[name] = v                 # so child calls inherit it
                        return v
        except OSError:
            continue
    return None
