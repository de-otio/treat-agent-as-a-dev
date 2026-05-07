#!/usr/bin/env python3
"""
List installations of a GitHub App, by account.

Reads the PEM from the OS secret store, mints a short-lived App JWT
in memory, calls GET /app/installations, and prints
`INSTALL_ID=<id>` (and friends) on stdout in shell-eval format. PEM
bytes are never echoed; the JWT is held in memory only.

Use this in Step 3 instead of `gh api /repos/.../installation`,
which requires the App JWT and so 401s with a user PAT.

usage: python3 scripts/list-installs.py \\
  --slug <app-slug> --app-id <id> --account <owner>
"""
import argparse, binascii, os, platform, subprocess, sys, time
import jwt, requests


def read_pem(slug: str) -> str:
    keychain_key = f"github-app-{slug}-pem"
    sysname = platform.system()
    if sysname == "Darwin":
        out = subprocess.run(
            ["security", "find-generic-password", "-s", keychain_key, "-w"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # `security ... -w` returns ASCII hex when the stored value
        # contains newlines (which any PEM does). Decode transparently.
        if out and all(c in "0123456789abcdefABCDEF" for c in out):
            return binascii.unhexlify(out).decode()
        return out
    elif sysname == "Linux":
        if subprocess.run(["sh", "-c", "command -v secret-tool"],
                          capture_output=True).returncode == 0:
            return subprocess.run(
                ["secret-tool", "lookup", "service", keychain_key],
                capture_output=True, text=True, check=True,
            ).stdout
        if subprocess.run(["sh", "-c", "command -v pass"],
                          capture_output=True).returncode == 0:
            return subprocess.run(
                ["pass", "show", keychain_key],
                capture_output=True, text=True, check=True,
            ).stdout
        sys.exit("install libsecret-tools (secret-tool) or pass")
    elif sysname == "Windows":
        store = os.path.expandvars(r"%USERPROFILE%\.secrets")
        ps = (
            f"$Secure = Import-Clixml '{store}\\{keychain_key}.xml';"
            "ConvertFrom-SecureString $Secure -AsPlainText"
        )
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, check=True,
        ).stdout
    else:
        sys.exit(f"unsupported OS: {sysname}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug",    required=True)
    ap.add_argument("--app-id",  required=True)
    ap.add_argument("--account", required=True,
                    help="filter to installs under this account login")
    args = ap.parse_args()

    pem = read_pem(args.slug)
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 9 * 60, "iss": args.app_id},
        pem, algorithm="RS256",
    )
    pem = None  # discard

    r = requests.get(
        "https://api.github.com/app/installations",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10,
    )
    r.raise_for_status()
    app_jwt = None  # discard

    for inst in r.json():
        if inst["account"]["login"] == args.account:
            print(f"INSTALL_ID={inst['id']}")
            print(f"INSTALL_ACCOUNT={inst['account']['login']}")
            return
    sys.exit(f"no installation found under account '{args.account}'")


if __name__ == "__main__":
    main()
