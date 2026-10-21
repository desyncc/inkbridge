# Security

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Use GitHub's private reporting instead:
go to the repository's **Security** tab and choose **Report a vulnerability**. I'll acknowledge it as
soon as I can and keep you posted on the fix.

Useful to include: what you did, what happened, what you expected, and the version
(`inkbridge doctor` prints it, with your secrets already left out).

## What InkBridge keeps, and where

| What | Where | Protection |
| --- | --- | --- |
| Your Viwoods sign-in token | `~/.viwoods/config.json` | Plain text, readable only by your user (`0600`) |
| Gemini API key, alert address | `~/.viwoods/config.json` | Same |
| Scans of your handwriting | `~/.viwoods/cache/` and your vault | The data folder is `0700`; the vault is yours to protect |
| Your Viwoods password | Nowhere | Typed at a hidden prompt, sent to Viwoods, never stored |

Anyone who can read `config.json` can act as you on Viwoods Cloud, so keep it out of backups you
share, screenshots and bug reports. `inkbridge doctor` and `inkbridge status` are written never to
print your token, keys or alert address.

## The dashboard

The web dashboard has **no login**. It only listens on `127.0.0.1`, refuses requests that name any
other host, and refuses writes from other websites open in your browser. Don't expose it to a
network you don't trust (the Docker setup publishes it on the host's loopback only, for the same
reason). If you do open it up, put it behind something that authenticates, such as an SSH tunnel or
a reverse proxy with a login.

## If a token leaks

Treat it as compromised. Sign in again with `inkbridge login` and change your Viwoods password.
Whether that revokes the old token is up to Viwoods, not InkBridge, so don't assume it does.

## Supported versions

Fixes go into the latest release and `main`.
