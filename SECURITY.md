# Security policy

## Supported version

Security fixes target the latest Alpha source on `main`. A previously published
portable ZIP does not receive fixes automatically; use a release or update that
explicitly includes the fix.

## Deployment boundary

Video Translator is a single-user desktop application. Use the supplied launcher,
which binds to `127.0.0.1` and ignores proxy headers. The application also rejects
non-loopback clients, unexpected Host headers and cross-origin browser requests.
LAN access, reverse proxies and public hosting are unsupported in this Alpha.
Do not expose the application or its model servers to an untrusted network.

Local programs running as your user are trusted: this application has no login
or isolation from other processes on your computer. Local inference providers
intentionally use loopback URLs; optional remote provider URLs and credentials
are configured by the local user. Review a provider's privacy policy before use.
Online Edge TTS sends dubbing text to Microsoft; other remote providers receive
the text or audio needed for the requested operation.

Uploaded media is stored under unique names, with an 8 GiB per-file limit.
Clone samples have a 64 MiB limit. Served uploads and outputs cannot execute
scripts on the application origin. File extensions are an admission check,
not proof that media is safe: only process files you trust, keep upstream native
decoders/runtimes updated, and avoid running the application as administrator.
Media processing is not sandboxed.

Configuration, API keys, uploaded media, outputs and task history stay on disk.
Secret values are masked in configuration responses; `config.yaml` is not
encrypted. Protect the application directory and do not share an installed
directory containing personal data or user-installed dependencies as a release.

## Reporting a vulnerability

Do not open a public issue containing an unfixed vulnerability. If GitHub Private
Vulnerability Reporting is enabled, use **Security → Report a vulnerability** and
include reproduction steps, affected versions and expected impact.

If that entry is unavailable, open an issue without vulnerability details to
request a private contact method. Do not include real API keys, personal videos,
subtitle content or task history in a report.
