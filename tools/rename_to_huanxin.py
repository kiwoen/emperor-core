# -*- coding: utf-8 -*-
"""Rename emperor-core -> huanxin-ai (display 幻炘AI).

Strategy:
  Phase 1: git mv renames (package dir, key modules, test files).
  Phase 2: content replacements in THREE passes:
    pass1: package/display/product tokens (no Emperor class word yet)
    pass2: court "Emperor/天子" -> "Sovereign"  (court-package files +
           files that import the court monarch)
    pass3: all other files -> "Emperor" -> "Huanxin"
  tests/test_court.py is a COLLISION file (imports both main and court
  Emperor); it is treated as Huanxin by the automated pass and fixed
  manually afterwards.

  Skips: .git, versions/ (gitignored runtime data), venv, data, logs,
         telemetry, build, dist, __pycache__, court runtime json.
"""
import os
import re
import subprocess
import sys

ROOT = r"E:/yuxing/AI自我进化/emperor-core-fresh/emperor-core"
os.chdir(ROOT)
DRY = "--dry-run" in sys.argv

SKIP_DIRS = {".git", "versions", ".venv", ".testdata", "data", "logs",
             "telemetry", "build", "dist", "__pycache__", ".pytest_cache",
             ".eggs", "node_modules"}
SKIP_FILES = {"genome_state.json", "memory.json", "learning_curve.json"}
TEXT_EXT = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".html",
            ".sh", ".bat", ".cfg", ".ini", ".rst", ".svg", ".css", ".js",
            ".ts", ".gitignore", ".dockerfile", ".mk"}
SPECIAL_NAMES = {"Makefile", "Dockerfile"}

# Files excluded from the Sovereign (court) consumer set even though they
# live under the court package or mention the monarch — they reference the
# MAIN evolutionary system's Emperor class, not the court monarch.
#   jarvis/court/scheduler.py -> imports `from jarvis.emperor import Emperor`
#   tests/test_court.py -> imports BOTH main and court Emperor (collision);
#       handled as Huanxin by the automated pass, fixed manually afterwards.
SOVEREIGN_EXCLUDE = {"jarvis/court/scheduler.py"}
FORCE_HUANXIN = {"tests/test_court.py"}


def should_process(path):
    parts = path.split(os.sep)
    if any(p in SKIP_DIRS for p in parts):
        return False
    base = os.path.basename(path)
    if base in SKIP_FILES:
        return False
    if "court" in parts and base.endswith(".json"):
        return False
    ext = os.path.splitext(base)[1].lower()
    if ext in TEXT_EXT or base in SPECIAL_NAMES:
        return True
    return False


def read_text(p):
    with open(p, "rb") as f:
        data = f.read()
    if b"\x00" in data[:2048]:
        return None  # binary
    return data.decode("utf-8", errors="replace")


def write_text(p, s):
    with open(p, "wb") as f:
        f.write(s.encode("utf-8"))


def git(*a):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    if r.returncode != 0:
        print("GIT FAIL", a, r.stderr)
        sys.exit(1)
    return r.stdout


def norm_rel(rel):
    """Normalize a path so it always compares against the original jarvis/ layout."""
    rel = rel.replace(os.sep, "/")
    if rel.startswith("./"):
        rel = rel[2:]
    # package was renamed jarvis -> huanxin; normalize back for rule matching
    if rel.startswith("huanxin/"):
        rel = "jarvis/" + rel[len("huanxin/"):]
    return rel


def is_sovereign(rel, text):
    """True if `Emperor` in this file refers to the COURT monarch (-> Sovereign).

    Detection runs on ORIGINAL text (before pass1 rewrites jarvis.court.emperor
    -> huanxin.court.sovereign), so signals stay stable in both dry and real runs.
    """
    rel = norm_rel(rel)
    if rel in SOVEREIGN_EXCLUDE or rel in FORCE_HUANXIN:
        return False
    is_court_pkg = "/court/" in rel
    # Precise court-monarch signals (must not match jarvis.court_api / main Emperor):
    #   - imports the court emperor module
    #   - re-exports Emperor from the court package
    #   - references the court-only SmartEmperor class
    explicit = (
        "jarvis.court.emperor" in text
        or ("from jarvis.court import" in text and "Emperor" in text)
        or "jarvis.court.Emperor" in text
        or "SmartEmperor" in text
    )
    if is_court_pkg:
        return True
    if os.path.basename(rel) in ("court_api.py", "emperor_cli.py"):
        return True
    if explicit:
        return True
    return False


# ---------- Phase 1: renames ----------
RENAMES = [
    ("jarvis", "huanxin"),
    ("huanxin/emperor.py", "huanxin/core.py"),
    ("huanxin/emperor_cli.py", "huanxin/court_cli.py"),
    ("huanxin/court/emperor.py", "huanxin/court/sovereign.py"),
]
# test_emperor*.py live in top-level tests/ (sibling of the package)
for f in sorted(os.listdir("tests")):
    if f.startswith("test_emperor") and f.endswith(".py"):
        RENAMES.append((os.path.join("tests", f),
                        os.path.join("tests", f.replace("test_emperor", "test_huanxin"))))

if DRY:
    print("== PLANNED FILE RENAMES ==")
    for a, b in RENAMES:
        print(f"  {a}  ->  {b}")
else:
    for a, b in RENAMES:
        if os.path.exists(a):
            git("mv", a, b)
        else:
            print("SKIP (missing):", a)

# ---------- collect files ----------
all_files = []
for dp, dn, fn in os.walk("."):
    dn[:] = [d for d in dn if d not in SKIP_DIRS]
    for f in fn:
        fp = os.path.join(dp, f)
        if should_process(fp):
            all_files.append(fp)
print(f"== files to scan: {len(all_files)} ==")

# ---------- pass1 rules (package / display / product) ----------
PASS1 = [
    ("J.A.R.V.I.S.", "幻炘AI"),
    ("Emperor Core", "幻炘AI"),
    ("Emperor-Core", "幻炘AI"),
    ("jarvis.emperor_cli", "huanxin.court_cli"),
    ("jarvis.court.emperor", "huanxin.court.sovereign"),
    ("jarvis.emperor", "huanxin.core"),
    ("jarvis.", "huanxin."),
    ("jarvis", "huanxin"),
    ("emperor-core", "huanxin-ai"),
    ("EMPEROR", "HUANXIN"),
    ("Jarvis", "Huanxin"),
    ("JARVIS", "HUANXIN"),
    ('"JARVIS Core Team"', '"Huanxin Core Team"'),
]


def apply_rules(s, rules):
    total = 0
    for a, b in rules:
        n = s.count(a)
        if n:
            s = s.replace(a, b)
            total += n
    return s, total


p1_total = 0
consumers = set()
for fp in all_files:
    s = read_text(fp)
    if s is None:
        continue
    s2, n = apply_rules(s, PASS1)
    p1_total += n
    if is_sovereign(fp, s):
        consumers.add(fp)
    if not DRY and n:
        write_text(fp, s2)

print(f"== pass1 replacements: {p1_total} ==")
print(f"== SOVEREIGN (court) consumers: {len(consumers)} ==")
for c in sorted(consumers):
    print(f"    {norm_rel(c)}")

# pyproject special: drop the `emperor =` script line
PPT = "pyproject.toml" if os.path.exists("pyproject.toml") else "huanxin/pyproject.toml"
if os.path.exists(PPT):
    s = read_text(PPT)
    if s:
        s2 = re.sub(r'\n\s*emperor\s*=\s*["\'][^"\']*["\']', '', s)
        if s2 != s and not DRY:
            write_text(PPT, s2)
        if s2 != s:
            print("== pyproject: removed `emperor =` script line ==")

# ---------- pass2 (consumers) / pass3 (others) ----------
PASS2 = [("Emperor", "Sovereign")]
PASS3 = [("Emperor", "Huanxin")]

p2 = p3 = 0
for fp in all_files:
    s = read_text(fp)
    if s is None:
        continue
    if fp in consumers:
        s2, n = apply_rules(s, PASS2)
        p2 += n
    else:
        s2, n = apply_rules(s, PASS3)
        p3 += n
    if not DRY and n:
        write_text(fp, s2)

print(f"== pass2 (Sovereign) replacements: {p2} ==")
print(f"== pass3 (Huanxin) replacements: {p3} ==")
print("DRY-RUN: no files written." if DRY else "DONE.")
