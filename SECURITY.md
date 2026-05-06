# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
<https://github.com/de-otio/treat-agent-as-a-dev/security/advisories/new>

This repo's scripts touch the user's keychain, create GitHub Apps
under the user's account, and mint installation tokens. If you find a
flaw — particularly around the manifest flow, the local callback
server, or how the PEM is captured and stored — please report it
privately rather than opening a public issue. (Public issues are
disabled on this repo precisely so reports go through the private
channel.)

## Supported versions

Only the most recent tagged release is supported.
