"""Load a skill's script from its hyphenated folder, and expose the shared
_lib on sys.path so tests can monkey-patch psx_http."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")
FIX = os.path.join(ROOT, "tests", "fixtures", "raw")

# Make _lib (psx_http, psx_fingerprint) importable before a skill is loaded.
sys.path.insert(0, os.path.join(SKILLS_DIR, "_lib"))


def load(folder: str, name: str):
    """`folder` = skill directory name (e.g. 'psx-scanner'), `name` = .py stem."""
    path = os.path.join(SKILLS_DIR, folder, name + ".py")
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_") + "_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    # Each skill does `sys.path.insert(0, os.path.join(HERE, "..", "_lib"))`
    # before importing psx_http; that still works, but we pre-inserted _lib
    # above so the import is idempotent either way.
    spec.loader.exec_module(mod)
    return mod


def ensure_fixtures_env():
    os.environ["PSX_FIXTURES_DIR"] = FIX
    # Also reflect on the already-imported module (if any).
    if "psx_http" in sys.modules:
        sys.modules["psx_http"].FIXTURES_DIR = FIX
