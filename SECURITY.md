# Security policy

This policy covers `certbot-dns-alias` and its metadata-only installation alias,
`certbot-dns-delegation`. Both packages use the same implementation and release version.

## Reporting a vulnerability

Email the project maintainer at **tiyee@live.com** with the subject
`[certbot-dns-alias] Security report`. Do not report unpatched vulnerabilities in a public
GitHub issue. For ordinary bugs without security implications, use the
[issue tracker](https://github.com/tiyee/certbot-dns-alias/issues).

Include the package version, Python and Certbot versions, affected provider, impact, and
reproduction steps using dummy credentials and domains. Redact API keys, tokens, account
identifiers, private keys, and ACME validation values from logs and screenshots. Do not send
live credentials or perform tests against DNS zones you do not control.

The maintainer will assess the report and coordinate remediation and disclosure with the
reporter. This volunteer project does not promise a fixed response or remediation deadline.
If you receive no response, follow up through the same private email channel.

## Supported releases

| Release line | Security maintenance |
| --- | --- |
| Current development version | Reports accepted; fixes may change before release |
| Latest stable 1.x release (currently 1.0.0) | Target for security fixes |
| Older patch versions and 0.x test releases | Upgrade to the latest supported release; no promised backports |

Version 1.0.0 is the current stable release. See [CHANGELOG.md](CHANGELOG.md) for changes and upgrade
notes. The Python/Certbot compatibility table in [README.md](README.md) describes functional
compatibility; it is not a guarantee that every third-party dependency is free of advisories.
Certbot 4 is supported on Python 3.9–3.14 without the legacy Certbot 3 TLS constraints.

## Dependency auditing

CI runs [pip-audit](https://github.com/pypa/pip-audit) against the runtime and development
dependencies installed by `uv sync --locked` on each supported Python version, 3.9–3.14.
The auditor runs in an isolated Python 3.13 tool environment and scans the frozen package
versions without resolving or upgrading them. The local editable package is excluded.
Each job uploads its dependency inventory and JSON audit report as a
`dependency-audit-py<version>` artifact, retained for 30 days, including when the audit fails.

Findings or audit errors on Python 3.10–3.14 fail CI and gate PyPI publishing through the
reusable CI workflow. Python 3.9 audits are non-blocking because its locked development host
uses Certbot 3 TLS constraints and dependencies whose newer fixes require Python 3.10.
Confirmed findings produce a warning, with every advisory listed in the log and job summary.
Tool errors and missing, invalid, or incomplete reports remain explicit errors, non-blocking
only on Python 3.9. Reports still require review. No
advisories are suppressed. This check covers the locked development environments, not every
dependency combination allowed by the published package or every deployment environment.

## Legacy Certbot 3 environments

Certbot 3 support requires the `certbot3` extra and `pyOpenSSL>=24.3,<25` because its josepy dependency
uses the removed `X509Req` API. These constraints can retain older cryptography dependencies
with known security advisories. The locked Python 3.9 development environment uses this legacy
host series; Python 3.9 also supports Certbot 4 without the legacy TLS constraints.
Use a supported Python version with Certbot 5 for new deployments when possible.

The 2026-10-07 release audit found advisories in a Certbot 3 environment containing pyOpenSSL
24.3.0 and cryptography 44.0.3. Examples include
[CVE-2026-26007](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2)
(cryptography SECT curve validation) and
[CVE-2026-27448](https://github.com/pyca/pyopenssl/security/advisories/GHSA-vp96-hxj8-p424)
(a pyOpenSSL server-name callback). These examples are not an exhaustive advisory inventory.
Their presence alone does not establish exploitability through this plugin's DNS-01 path;
other software sharing the environment may use the affected features.

Legacy compatibility does not promise fixes or backports for upstream TLS libraries. A
dependency finding must be assessed against the installed version and reachable behavior.
Do not bypass dependency constraints or globally patch third-party libraries to force an
upgrade. An environment requiring newer TLS dependencies should migrate to Certbot 5 through
a tested upgrade. Any future support-range change will be documented in the changelog.

## Operational scope

Use credentials restricted to the delegated DNS zones and required API operations. Keep the
credentials file private and available to the renewal process. Treat the ability to change
delegated ACME TXT records as certificate-issuance authority for the delegating domains.

Creation ownership is kept in memory. Forced termination, lost API responses, and cleanup
failures can leave TXT records behind. Review residual records before removing them so that
another active issuance is not disrupted. The plugin does not provide a persistent DNS audit
trail or coordination between independent Certbot processes.
