# certbot-dns-alias

English | [Simplified Chinese](README.zh-CN.md)

A Certbot DNS-01 plugin that manages TXT validation records through CNAME delegation
with Alibaba Cloud DNS or Tencent Cloud DNSPod.

**Supports Certbot 3.x and 5.x** with a single plugin codebase for both host versions.

Two installation names are available: `certbot-dns-alias` and `certbot-dns-delegation`.
The latter is an installation alias that automatically installs the same version of the main
package. Both use the Certbot authenticator `dns-alias`.

## How it works

Create a CNAME record in the DNS zone of the domain you want to secure:

```dns
_acme-challenge.example.com.  300 IN CNAME example-com.delegate.example.net.
```

Host `delegate.example.net` on Alibaba Cloud or Tencent Cloud. The plugin follows the CNAME
chain, adds the challenge's TXT value at the final target `example-com.delegate.example.net`,
waits for DNS propagation, and cleans up using the saved record ID when validation finishes.
The original domain can use any DNS provider; the plugin only needs API credentials for the
target's managed zone.

- Supports multiple CNAME hops, wildcard certificates, and multiple domains in one certificate.
- Uses Alibaba Cloud or Tencent Cloud individually, or both in one request with `auto` mode.
- Selects the longest matching DNS suffix from managed zones, respecting label boundaries and
  supporting names such as `example.co.uk` and separately hosted subdomains.
- Handles API pagination for zone and TXT queries; explicit zone lists skip automatic discovery.
- Creates each TXT value separately and preserves other values at the same name. Existing valid
  records with the same value are reused and never deleted by the plugin.
- Cleans up using the zone and record ID saved at creation time, even if the CNAME later changes.
- Reports clear errors for CNAME loops, excessive chain depth, DNS timeouts, permission failures,
  and ambiguous zone ownership.

Supports Python **3.9–3.14** and **Certbot 3.x and 5.x** in the following combinations:

| Python | Certbot 3.x | Certbot 5.x |
| --- | --- | --- |
| 3.9 | Supported | Upstream requires Python ≥3.10 |
| 3.10–3.13 | Supported | Supported |
| 3.14 | Older josepy cannot be imported | Supported |

Both cloud provider SDKs are installed with the plugin.

## Installation

Install the plugin in the Python environment that runs your Certbot host:

```bash
python -m pip install certbot-dns-alias
certbot plugins --text
```

Alternatively, use the installation alias. Choose either package:

```bash
python -m pip install certbot-dns-delegation
```

With the alias installed, the authenticator is still `dns-alias`, and options and credential
keys still use the `dns-alias` / `dns_alias` prefixes.

Here, `python` must be the interpreter running your Certbot host. You can also specify the host
environment explicitly with uv:

```bash
uv pip install --python /path/to/certbot-venv/bin/python certbot-dns-alias
```

Host dependency requirements depend on the Python version: Python 3.9 uses Certbot 3,
Python 3.14 uses Certbot 5, and Python 3.10–3.13 can use Certbot 3 or 5.
When the installed host satisfies these requirements, pip's default upgrade strategy only
upgrades dependencies when needed. To preserve an exact host version, pin it explicitly during
installation. For example, for a `3.0.0` host:

```bash
python -m pip install 'certbot==3.0.0' certbot-dns-alias
```

If your deployment also pins ACME, pyOpenSSL, or other dependencies, use its constraints file.
The plugin and Certbot must share the same Python environment. For a snap or Docker installation
of Certbot, add the plugin to that host's runtime using the appropriate installation method;
installing it in another virtual environment will not make it discoverable by that host.

## Credentials

Use `key = value` entries without an INI section. Complete examples are in [examples](examples).
Copy an example, fill in your credentials, and restrict file permissions:

```bash
cp examples/tencent.ini credentials.ini
chmod 600 credentials.ini
```

### Alibaba Cloud

```ini
dns_alias_provider = aliyun
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = delegate.example.net
```

Optional settings: `dns_alias_aliyun_region_id` (default: `cn-hangzhou`) and
`dns_alias_aliyun_security_token` (temporary STS credentials). The plugin uses the public endpoint
`alidns.aliyuncs.com`.

### Tencent Cloud DNSPod

```ini
dns_alias_provider = tencent
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = delegate.example.net
```

Optional setting: `dns_alias_tencent_token` (temporary session credentials). The plugin uses
Tencent Cloud API v20210323 at `dnspod.tencentcloudapi.com`, rather than the legacy DNSPod Token
API or the international endpoint.

### Using both cloud providers

```ini
dns_alias_provider = auto
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = ali-delegate.example.net
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = tencent-delegate.example.org
```

For example:

```dns
_acme-challenge.example.com.      300 IN CNAME example-com.ali-delegate.example.net.
_acme-challenge.api.example.org.  300 IN CNAME api-example-org.tencent-delegate.example.org.
```

Then pass `-d example.com -d api.example.org` in the same certificate request. The `auto` mode
requires at least one complete set of credentials; configuring only one provider is also valid.
If the same longest matching zone belongs to both providers, the plugin reports an error.
Adjust the explicit zone lists or select a single `provider` to resolve the ambiguity.

All `*_zones` settings are optional. Separate multiple zones with commas, for example
`example.net, example.co.uk`. An explicit list restricts eligible zones and skips that provider's
domain discovery API. Use actual zone names hosted by the cloud provider, rather than complete
TXT hostnames. When omitted, the plugin discovers all zones visible to the credentials.
Currently, one credential account is supported per provider.

## Issuance and renewal

Create the CNAME first and ensure it resolves through public DNS. Use the staging environment
for an initial test:

```bash
certbot certonly \
  --authenticator dns-alias \
  --dns-alias-credentials "$(pwd)/credentials.ini" \
  --dns-alias-require-cname \
  --dns-alias-propagation-seconds 120 \
  --staging \
  --non-interactive --agree-tos --email admin@example.com \
  -d example.com -d '*.example.com'
```

After testing, remove `--staging` to request a production certificate. Replace the domains and
email address with your own values, and run `certbot` from the host environment.
By default, Certbot writes to `/etc/letsencrypt`, `/var/lib/letsencrypt`, and `/var/log/letsencrypt`.
The account running Certbot needs permission to access these directories. You can select other
locations with `--config-dir`, `--work-dir`, and `--logs-dir`.

Certbot saves the authenticator and the absolute credentials file path. Renew in the same
environment:

```bash
certbot renew --dry-run
certbot renew
```

Keep the credentials file available and refresh temporary credentials before they expire.
Configure scheduled renewal and certificate deployment hooks for your deployment.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--dns-alias-credentials` | Required | Path to the credentials INI file |
| `--dns-alias-propagation-seconds` | `60` | Propagation wait in seconds after all TXT records are ready |
| `--dns-alias-ttl` | `600` | TXT record TTL; must satisfy the cloud plan's limits |
| `--dns-alias-cname-max-depth` | `8` | Maximum number of CNAME hops |
| `--dns-alias-dns-timeout` | `10` | Total timeout in seconds for each CNAME query |
| `--dns-alias-dns-retries` | `2` | Additional retries after a timeout or unavailable nameservers |
| `--dns-alias-resolvers` | System DNS | Comma-separated IPv4/IPv6 DNS server addresses |
| `--dns-alias-require-cname` | Disabled | Reject writes when the original challenge name has no CNAME |

By default, if no CNAME exists, the plugin may create TXT records directly in the managed zone
of the original challenge name. Enable `--dns-alias-require-cname` to require delegation.
The resolver uses absolute DNS names and disables system search suffix expansion.
NXDOMAIN or no CNAME marks the end of a chain; timeouts, SERVFAIL, and other resolution failures
are treated as errors.

TTL and propagation wait are different settings. Creating a target hostname for the first time
may encounter DNS negative caching; increase the propagation wait if needed.
Use distinct delegation hostnames for different domains. A domain and its wildcard share the
same `_acme-challenge` name, and the plugin preserves all TXT values needed for the request.
It does not replace the entire TXT RRset.

Creation state is stored only in the current Certbot process. Forced termination, a lost
response after a successful creation, or a deletion failure may leave TXT records behind;
remove these manually from the delegated zone. Cleanup failures produce warnings while the
plugin continues cleaning up other challenges.

## API permissions

Alibaba Cloud requires:

- `alidns:DescribeDomainRecords`
- `alidns:AddDomainRecord`
- `alidns:DeleteDomainRecord`
- `alidns:DescribeDomains` when `dns_alias_aliyun_zones` is not configured

Tencent Cloud requires:

- `dnspod:DescribeRecordList`
- `dnspod:CreateRecord`
- `dnspod:DeleteRecord`
- `dnspod:DescribeDomainList` when `dns_alias_tencent_zones` is not configured

Restrict permissions to delegated zones where the cloud provider supports resource scoping.
For API fields and permissions, see
[Alibaba Cloud AddDomainRecord](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-adddomainrecord),
[Alibaba Cloud DescribeDomains](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-describedomains), and
[Tencent Cloud DescribeRecordList](https://cloud.tencent.com/document/api/1427/56166).

## License

MIT. See [LICENSE](LICENSE).
