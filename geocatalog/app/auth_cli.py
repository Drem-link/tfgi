import argparse
import getpass
import hmac
import os
import re
import secrets
from pathlib import Path

from argon2 import PasswordHasher

MIN_PASSWORD_LENGTH = 8


def write_secret_file(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(value)
        secret_file.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create files for the geocatalog Kubernetes auth Secret.")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output_dir.chmod(0o700)
    username = input("Admin username: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", username):
        parser.error("username must contain 1-128 ASCII letters, digits, dots, underscores, @ or hyphens")
    password = getpass.getpass(f"Admin password (at least {MIN_PASSWORD_LENGTH} characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if not hmac.compare_digest(password.encode(), confirmation.encode()):
        parser.error("passwords do not match")
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > 1024:
        parser.error(f"password must be {MIN_PASSWORD_LENGTH}-1024 characters")
    values = {
        "AUTH_USERNAME": username,
        "AUTH_PASSWORD_HASH": PasswordHasher().hash(password),
        "SESSION_SECRET": secrets.token_urlsafe(48),
    }
    created = []
    try:
        for name, value in values.items():
            path = args.output_dir / name
            write_secret_file(path, value)
            created.append(path)
    except FileExistsError as exc:
        for path in created:
            path.unlink()
        parser.error(f"refusing to overwrite existing secret file: {exc.filename}")
    print(f"Created protected auth secret files in {args.output_dir}")


if __name__ == "__main__":
    main()
