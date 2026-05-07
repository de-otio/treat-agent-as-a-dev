#!/usr/bin/env python3
"""
Run the GitHub App manifest flow end-to-end.

Starts a local HTTP server, opens a browser to a self-hosted page that
auto-POSTs the manifest to GitHub, waits for the developer to click
Create, captures the resulting code, exchanges it for App credentials,
and writes the PEM directly to the OS secret store.

Prints non-secret outputs (APP_ID, APP_SLUG, APP_INSTALL_URL) on
stdout in shell-eval format. Logs progress to stderr.

usage: python3 scripts/manifest-flow.py --gh-user <handle> \\
  ( --name <bot-name> | --engagement <slug> --dev <slug> )

If --name is omitted, the App is named "<engagement>-<GitHub Username>-bot".
"""
import argparse, html, http.server, json, os, platform, secrets, socket
import socketserver, subprocess, sys, threading, time, urllib.parse, webbrowser
import requests

PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
    "metadata": "read",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def store_pem(keychain_key: str, pem: str) -> None:
    sysname = platform.system()
    if sysname == "Darwin":
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", keychain_key,
             "-a", os.environ.get("USER", ""),
             "-w", pem],
            check=True,
        )
    elif sysname == "Linux":
        if subprocess.run(["sh", "-c", "command -v secret-tool"],
                          capture_output=True).returncode == 0:
            p = subprocess.Popen(
                ["secret-tool", "store", "--label", keychain_key,
                 "service", keychain_key],
                stdin=subprocess.PIPE,
            )
            p.communicate(pem.encode())
            if p.returncode != 0:
                raise RuntimeError("secret-tool store failed")
        elif subprocess.run(["sh", "-c", "command -v pass"],
                            capture_output=True).returncode == 0:
            p = subprocess.Popen(
                ["pass", "insert", "-m", "-f", keychain_key],
                stdin=subprocess.PIPE,
            )
            p.communicate(pem.encode())
            if p.returncode != 0:
                raise RuntimeError("pass insert failed")
        else:
            raise RuntimeError("install libsecret-tools (secret-tool) or pass")
    elif sysname == "Windows":
        ps_script = (
            "$Pem    = [Console]::In.ReadToEnd();"
            "$Secure = ConvertTo-SecureString $Pem -AsPlainText -Force;"
            "$Store  = \"$env:USERPROFILE\\.secrets\";"
            "New-Item -ItemType Directory -Force $Store | Out-Null;"
            f"$Secure | Export-Clixml \"$Store\\{keychain_key}.xml\""
        )
        p = subprocess.Popen(
            ["pwsh", "-NoProfile", "-Command", ps_script],
            stdin=subprocess.PIPE, text=True,
        )
        p.communicate(pem)
        if p.returncode != 0:
            raise RuntimeError("PowerShell DPAPI write failed")
    else:
        raise RuntimeError(f"unsupported OS: {sysname}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gh-user",    required=True)
    ap.add_argument("--name",       help="bot name (overrides --engagement/--dev)")
    ap.add_argument("--engagement", help="engagement slug; required unless --name is given")
    ap.add_argument("--dev",        help="developer slug; required unless --name is given")
    args = ap.parse_args()

    if args.name:
        requested_name = args.name
    elif args.engagement and args.dev:
        requested_name = f"{args.engagement}-{args.dev}-bot"
    else:
        ap.error("provide --name, or both --engagement and --dev")

    port     = free_port()
    state    = secrets.token_urlsafe(16)
    redirect = f"http://localhost:{port}/callback"

    manifest = {
        "name":                requested_name,
        "url":                 "https://example.com",
        "redirect_url":        redirect,
        "public":              False,
        "default_permissions": PERMISSIONS,
        "default_events":      [],
    }
    manifest_attr = html.escape(json.dumps(manifest), quote=True)

    holder: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw): pass

        def do_GET(self):
            url    = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(url.query)
            if url.path == "/":
                form = (
                    "<!doctype html><html><body onload='document.f.submit()'>"
                    "<form id='f' name='f' method='post' "
                    f"action='https://github.com/settings/apps/new?state={state}'>"
                    f"<input type='hidden' name='manifest' value=\"{manifest_attr}\">"
                    "</form><p>Submitting to GitHub…</p></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(form.encode())
            elif url.path == "/callback":
                if params.get("state", [""])[0] != state:
                    self.send_error(403, "state mismatch")
                    return
                holder["code"] = params.get("code", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"App created. You can close this tab.\n")
            else:
                self.send_error(404)

    server = socketserver.TCPServer(("127.0.0.1", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Opening browser. Click 'Create' as @{args.gh_user}.", file=sys.stderr)
    webbrowser.open(f"http://localhost:{port}/")

    deadline = time.time() + 300
    while "code" not in holder and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()

    if "code" not in holder:
        sys.exit("timed out waiting for browser callback (5 min)")

    r = requests.post(
        f"https://api.github.com/app-manifests/{holder['code']}/conversions",
        headers={"Accept": "application/vnd.github+json"},
        timeout=10,
    )
    r.raise_for_status()
    app = r.json()

    keychain_key = f"github-app-{app['slug']}-pem"
    pem = app["pem"]
    store_pem(keychain_key, pem)
    pem = None

    print(f"APP_ID={app['id']}")
    print(f"APP_SLUG={app['slug']}")
    print(f"APP_INSTALL_URL=https://github.com/apps/{app['slug']}/installations/new")


if __name__ == "__main__":
    main()
