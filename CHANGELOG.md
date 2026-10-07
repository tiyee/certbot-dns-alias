# Changelog

Changes apply to both `certbot-dns-alias` and `certbot-dns-delegation` unless noted otherwise.

## Unreleased

### Added

- Certbot 4.x support on Python 3.9–3.14, including installed-wheel lifecycle tests,
  alias installation/removal checks, and dependency lower-bound checks in CI.
- Allow modern TLS dependencies on Python 3.9 Certbot 4 hosts; retain legacy TLS constraints
  through the `certbot3` extra and in the locked Python 3.9 development environment.
- Dependency audits of locked Python 3.9–3.14 environments, with retained JSON reports and
  release gating on Python 3.10–3.14; legacy Python 3.9 findings remain non-blocking.

### Changed

- Use HTTPX for the GoDaddy REST client as well as the Cloudflare SDK, removing the HTTPX2
  dependency and its separate transport stack. Custom GoDaddy transports now use
  `httpx.BaseTransport` and `httpx.MockTransport`.

## 1.0.0

### Added

- Cloudflare DNS support using scoped API tokens and optional explicit zone IDs.
- GoDaddy Domains v3 support using Personal Access Tokens, individual record IDs, and the
  bundled synchronous REST client.
- English and Simplified Chinese documentation, a security reporting policy, and this changelog.
- Python 3.9–3.14 compatibility across the documented Certbot 3 and 5 combinations.
- The `certbot3` installation extra, including through the installation alias, to retain
  compatible ACME and pyOpenSSL versions on legacy hosts.
- CI checks for the lowest resolvable direct dependencies at both Python boundaries of each
  host series, including installed-wheel tests and Certbot discovery.
- A 97.5% line-coverage floor for coverage runs, with two-decimal reporting.

### Fixed

- Use non-transitional IDNA2008 normalization so Unicode names such as `faß.de` match
  `xn--fa-hia.de` without being conflated with `fass.de`. ASCII service labels remain supported.
- Require dnspython 2.6.1 or newer to avoid the 2.6.0 truncated-UDP/TCP-fallback regression.
- Exclude Certbot and ACME 4.x from package requirements to match the supported host range.
- Require `pyOpenSSL>=24.3,<25` on legacy hosts; older releases can resolve against
  incompatible cryptography versions and prevent Certbot from importing.
- Reject incomplete or inconsistent DNS pagination instead of routing from partial results.
- Keep sensitive SDK diagnostics and exception messages out of Certbot logs and tracebacks.
- Initialize explicit DNS resolvers independently of system resolver configuration.

### Upgrade notes

- Keep the `dns-alias` authenticator, `--dns-alias-*` options, and `dns_alias_` credential keys
  in existing renewal configurations. The installation alias does not change those names.
- All Certbot 3 hosts must install the `certbot3` extra. Its legacy TLS limits provide
  compatibility, not security updates; see [SECURITY.md](SECURITY.md).
- Certbot 4 is unsupported. Pin a supported host version when upgrading; do not rely on the
  package installer to preserve a Certbot 4 environment.
- Review Unicode zone allowlists when migrating from test releases. A name containing `ß`
  now refers to its IDNA2008 A-label rather than a different name containing `ss`.
- GoDaddy Domains v3 requires a Personal Access Token; legacy API key/secret pairs do not work.

## 0.1.2

- Added the `certbot-dns-delegation` metadata-only installation alias with an exact dependency
  on the main package, and coordinated dual-package builds and publishing.
- Added installed-wheel testing across the then-supported Certbot 3/5 compatibility matrix.

## 0.1.1

- Added a focused English PyPI description covering installation and CNAME delegation.
- Updated release workflow dependencies.

## 0.1.0

- Introduced the CNAME delegation plugin for Alibaba Cloud DNS and Tencent Cloud DNSPod,
  with per-value TXT creation and cleanup by saved record ID.

Historical sections describe repository tags, not a claim that every tagged artifact remains
available on PyPI. Untagged development changes after 0.1.2 are included under 1.0.0 above.
