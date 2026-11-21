"""Generate private local configuration; never print credential values."""
import argparse
import os
from pathlib import Path
import re
import secrets
import shlex


def private_write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "runtime")
    parser.add_argument("--owner", default="admin")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.owner):
        parser.error("Owner name must use letters, digits, underscore or hyphen")
    destination = args.output.resolve()
    targets = [destination / "web.env", destination / "backend" / "api-token"]
    if any(target.exists() or target.is_symlink() for target in targets):
        parser.error("Private configuration already exists; it was not overwritten")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder in ("backend", "web"):
        (destination / folder).mkdir(exist_ok=True, mode=0o700)
    owner_password = secrets.token_urlsafe(24)
    values = {"XINGGUANG_OWNER_USERNAME": args.owner,
              "XINGGUANG_OWNER_PASSWORD": owner_password,
              "XINGGUANG_STORAGE": "sqlite"}
    private_write(targets[0], "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()))
    private_write(targets[1], secrets.token_urlsafe(48) + "\n")
    print("Private configuration created. View runtime/web.env locally for the initial login.")


if __name__ == "__main__":
    main()
