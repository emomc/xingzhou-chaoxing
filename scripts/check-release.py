"""Reject runtime files and recognizable private credentials in a public tree."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRECTORIES = {".backend-state", ".remote-deploy", ".venv", ".venv313", "runtime", "node_modules"}
PRIVATE_NAMES = {"api-token", "credentials.key", "cookies.txt", "cookies.json", "config.ini", "backend.json", "web-login.txt", "prod-ssh.json"}
PRIVATE_SUFFIXES = {".log", ".sqlite", ".sqlite3", ".db", ".exe", ".dll", ".pem", ".key"}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "provider-key": re.compile(rb"\b[0-9a-f]{32}\.[A-Za-z0-9]{12,}\b"),
    "openai-style-key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
}
issues = []
count = 0
for path in ROOT.rglob("*"):
    relative = path.relative_to(ROOT)
    if ".git" in relative.parts or "__pycache__" in relative.parts:
        continue
    if path.is_symlink():
        issues.append((relative.as_posix(), "symlink"))
        continue
    if not path.is_file():
        continue
    count += 1
    if set(relative.parts) & PRIVATE_DIRECTORIES or path.name in PRIVATE_NAMES or path.suffix in PRIVATE_SUFFIXES:
        issues.append((relative.as_posix(), "private-runtime-file"))
    raw = path.read_bytes()
    for label, pattern in PATTERNS.items():
        if pattern.search(raw):
            issues.append((relative.as_posix(), label))
if issues:
    for filename, reason in issues:
        print(filename, reason)
    raise SystemExit(1)
print(f"Release check passed: {count} source/documentation files; no private runtime files or recognized credentials.")
