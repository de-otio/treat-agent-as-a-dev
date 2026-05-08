"""`taaad register` — drive the GitHub App manifest flow.

Equivalent of v0.4's scripts/manifest-flow.py, but writes via
keyring.set_password and the new apps/<slug>.toml registry.

PEM bytes pass through this process: response → keyring.set_password
→ discarded. They are never logged, printed, written to disk, or
otherwise persisted (RUNBOOK Operating Rule 1).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import http.server
import json
import secrets as stdlib_secrets
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser

from taaad import config, github, paths, secrets

PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
    "metadata": "read",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(port: int, state: str, manifest_attr: str, holder: dict, form_action: str):
    expected_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):  # noqa: ARG002
            pass

        def _reject(self, code: int, msg: str) -> None:
            self.send_error(code, msg)

        def do_GET(self):  # noqa: N802
            host = self.headers.get("Host", "")
            if host not in expected_hosts:
                return self._reject(403, "host header mismatch")
            origin = self.headers.get("Origin", "")
            if origin and origin not in (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ):
                return self._reject(403, "origin mismatch")

            url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(url.query)
            if url.path == "/":
                # CSRF: the form auto-POSTs to GitHub on load, so reject
                # any cross-site initiator (e.g. iframe from a malicious
                # page). /callback is necessarily cross-site (redirect
                # from github.com); the `state` token below guards it.
                sec_fetch_site = self.headers.get("Sec-Fetch-Site", "")
                if sec_fetch_site == "cross-site":
                    return self._reject(403, "cross-site request rejected")
                form = (
                    "<!doctype html><html><body onload='document.f.submit()'>"
                    "<form id='f' name='f' method='post' "
                    f"action='{form_action}?state={state}'>"
                    f"<input type='hidden' name='manifest' value=\"{manifest_attr}\">"
                    "</form><p>Submitting to GitHub…</p></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(form.encode())
            elif url.path == "/callback":
                if params.get("state", [""])[0] != state:
                    return self._reject(403, "state mismatch")
                holder["code"] = params.get("code", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"App created. You can close this tab.\n")
            else:
                return self._reject(404, "not found")

    return H


def run(args: argparse.Namespace) -> int:
    if args.name:
        requested_name = args.name
    elif args.engagement:
        requested_name = f"{args.engagement}-{args.gh_user}-bot"
    else:
        print("provide --name or --engagement", file=sys.stderr)
        return 2

    config.ensure_dirs()

    port = _free_port()
    state = stdlib_secrets.token_urlsafe(16)
    redirect = f"http://localhost:{port}/callback"

    manifest = {
        "name": requested_name,
        "url": "https://example.com",
        "redirect_url": redirect,
        "public": False,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }
    manifest_attr = html.escape(json.dumps(manifest), quote=True)
    holder: dict = {}

    if args.org:
        form_action = f"https://github.com/organizations/{args.org}/settings/apps/new"
    else:
        form_action = "https://github.com/settings/apps/new"

    handler = _make_handler(port, state, manifest_attr, holder, form_action)
    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    if args.org:
        print(f"Opening browser. Click 'Create' as @{args.gh_user} (App will be owned by org @{args.org}).", file=sys.stderr)
    else:
        print(f"Opening browser. Click 'Create' as @{args.gh_user}.", file=sys.stderr)
    webbrowser.open(f"http://localhost:{port}/")

    deadline = time.time() + 300
    while "code" not in holder and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()

    if "code" not in holder:
        print("timed out waiting for browser callback (5 min)", file=sys.stderr)
        return 1

    app = github.manifest_conversion(holder["code"])
    slug = app["slug"]
    app_id = int(app["id"])
    keychain_key = config.keychain_key_for(slug)

    pem = app["pem"]
    secrets.set_pem(keychain_key, pem)
    pem = None  # discard
    secrets.restrict_macos_acl(keychain_key, paths.taaad_executable())

    cfg = config.AppConfig(
        schema_version=config.SCHEMA_VERSION,
        slug=slug,
        app_id=app_id,
        install_id=None,
        account=None,
        keychain_key=keychain_key,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    config.write_app(cfg)

    install_url = f"https://github.com/apps/{slug}/installations/new"
    print(f"\n✅ App created.\n")
    print(f"  slug:           {slug}")
    print(f"  app_id:         {app_id}")
    print(f"  keychain_key:   {keychain_key}")
    print(f"  config:         {config.app_path(slug)}")
    print(f"\nNext: install on a repo:")
    print(f"  open {install_url}")
    print(f"  taaad install {slug} --account <owner>")
    return 0
