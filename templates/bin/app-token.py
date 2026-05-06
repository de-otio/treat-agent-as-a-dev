#!/usr/bin/env python3
# usage: app-token.py <app-id> <installation-id>
# reads PEM on stdin, prints installation token on stdout
import sys, time, jwt, requests

app_id, install_id = sys.argv[1], sys.argv[2]
pem = sys.stdin.read()

now = int(time.time())
app_jwt = jwt.encode(
    {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id},
    pem, algorithm="RS256",
)

r = requests.post(
    f"https://api.github.com/app/installations/{install_id}/access_tokens",
    headers={
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    },
    timeout=10,
)
r.raise_for_status()
print(r.json()["token"])
