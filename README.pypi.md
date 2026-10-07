# certbot-dns-alias

English | [Simplified Chinese](https://github.com/tiyee/certbot-dns-alias/blob/master/README.zh-CN.md)

A Certbot DNS-01 plugin with CNAME delegation that automatically creates and cleans up
TXT validation records on Alibaba Cloud DNS, Tencent Cloud DNSPod, or Cloudflare.

**Supports Certbot 3.x and 5.x** with a single plugin codebase for both host versions.

Two installation names are available: `certbot-dns-alias` and `certbot-dns-delegation`.
The latter is an installation alias that automatically installs the same version of the main
package. Both use the Certbot authenticator `dns-alias`.

## How it works

Delegate your domain's `_acme-challenge` record to a centrally managed validation domain
using a CNAME:

```dns
_acme-challenge.example.com.  300 IN CNAME example-com.delegate.example.net.
```

Host `delegate.example.net` on Alibaba Cloud, Tencent Cloud, or Cloudflare. When requesting
a certificate, the plugin:

1. Follows the CNAME chain from `_acme-challenge.example.com` to its final target.
2. Selects the cloud provider that manages the target's zone.
3. Adds the required TXT value at `example-com.delegate.example.net`.
4. Waits for DNS propagation so the certificate authority can complete DNS-01 validation.
5. Cleans up the temporary TXT record using the record ID saved at creation time.

The original domain can use any DNS provider; only API credentials for the final target's
managed zone are required. Multiple CNAME hops, wildcards, and multiple domains in one
certificate are supported. Domains in the same request can delegate to different cloud
providers. TXT values are managed individually, preserving other values at the same name.
Existing valid records with the same value are reused and never deleted by the plugin.

## Installation

Supports Python **3.9–3.14** and **Certbot 3.x and 5.x** in the following combinations:

| Python | Certbot 3.x | Certbot 5.x |
| --- | --- | --- |
| 3.9 | Supported | Upstream requires Python ≥3.10 |
| 3.10–3.13 | Supported | Supported |
| 3.14 | Older josepy cannot be imported | Supported |

The plugin is loaded by your Certbot host and must be installed in the same Python environment.

Install in the Python environment containing Certbot:

```bash
python -m pip install certbot-dns-alias
```

Alternatively, use the installation alias. Choose either package:

```bash
python -m pip install certbot-dns-delegation
```

With the alias installed, the authenticator is still `dns-alias`, and options and credential
keys still use the `dns-alias` / `dns_alias` prefixes.

Here, `python` must be the interpreter running your Certbot host. With uv, specify the host
environment explicitly:

```bash
uv pip install --python /path/to/certbot-venv/bin/python certbot-dns-alias
```

Host dependency requirements depend on the Python version: Python 3.9 uses Certbot 3,
Python 3.14 uses Certbot 5, and Python 3.10–3.13 can use Certbot 3 or 5.
To preserve an exact host version, pin it explicitly during installation. For example,
for a `3.0.0` host:

```bash
python -m pip install 'certbot==3.0.0' certbot-dns-alias
```

Use your deployment's constraints file to preserve any other host dependency requirements.

Check that the plugin is available:

```bash
certbot plugins --text
```

## Credentials

Create `credentials.ini` with `key = value` entries and no INI section. Restrict file permissions:

```bash
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
`dns_alias_aliyun_security_token` (temporary STS credentials).

### Tencent Cloud DNSPod

```ini
dns_alias_provider = tencent
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = delegate.example.net
```

Use Tencent Cloud SecretId / SecretKey credentials. Set `dns_alias_tencent_token` for temporary
session credentials.

### Cloudflare

```ini
dns_alias_provider = cloudflare
dns_alias_cloudflare_api_token = YOUR_API_TOKEN
```

Use an API Token with `Zone:DNS:Edit` and `Zone:Zone:Read` for the delegated zones.
The plugin uses `https://api.cloudflare.com/client/v4` and discovers zones visible to the token.
Global API keys are not supported.

To skip zone discovery and avoid the `Zone:Zone:Read` permission, provide zone-name:ID pairs:

```ini
dns_alias_cloudflare_zone_ids = delegate.example.net:0123456789abcdef0123456789abcdef
```

Replace the placeholder with the 32-character Zone ID from the Cloudflare dashboard.
Separate multiple pairs with commas. This mapping restricts eligible zones to the listed names.
You may further restrict them with `dns_alias_cloudflare_zones`; every listed name must have
an entry in `dns_alias_cloudflare_zone_ids`. Explicit Cloudflare zone names without IDs are
rejected because skipping discovery requires their IDs.

Cloudflare TXT record TTL is `1` (automatic) or 60–86400 seconds (30-second minimum for Enterprise).
The plugin's default `600` is valid. Keep delegation CNAME records in DNS-only mode so public
resolvers can follow them; Cloudflare proxying or CNAME flattening can hide the CNAME.

### Using multiple providers

```ini
dns_alias_provider = auto
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = ali-delegate.example.net
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = tencent-delegate.example.org
dns_alias_cloudflare_api_token = YOUR_API_TOKEN
dns_alias_cloudflare_zone_ids = cf-delegate.example.net:0123456789abcdef0123456789abcdef
```

Configure a delegation target for each domain:

```dns
_acme-challenge.example.com.      300 IN CNAME example-com.ali-delegate.example.net.
_acme-challenge.api.example.org.  300 IN CNAME api-example-org.tencent-delegate.example.org.
```

Pass both `-d example.com -d api.example.org` in the certificate request.
The `auto` mode requires at least one complete set of provider credentials; configuring only
one provider is also valid.

The `*_zones` settings are optional. Separate multiple zones with commas, and use actual zone
names hosted by the cloud provider, such as `delegate.example.net`, rather than complete TXT
hostnames. An explicit list restricts eligible zones; otherwise, the plugin discovers all zones
visible to the credentials. Cloudflare uses `dns_alias_cloudflare_zone_ids` to skip discovery;
explicit `dns_alias_cloudflare_zones` also requires IDs as described above. The plugin selects
the longest matching DNS suffix. If the same matching zone belongs to multiple providers,
adjust the zone lists or select a single provider to resolve the ambiguity.

### API permissions

Alibaba Cloud credentials require:

- `alidns:DescribeDomainRecords`
- `alidns:AddDomainRecord`
- `alidns:DeleteDomainRecord`
- `alidns:DescribeDomains` when `dns_alias_aliyun_zones` is not configured

Tencent Cloud credentials require:

- `dnspod:DescribeRecordList`
- `dnspod:CreateRecord`
- `dnspod:DeleteRecord`
- `dnspod:DescribeDomainList` when `dns_alias_tencent_zones` is not configured

Cloudflare API Tokens require:

- `Zone:DNS:Edit` for listing, creating, and deleting TXT records
- `Zone:Zone:Read` when `dns_alias_cloudflare_zone_ids` is not configured

Restrict the token's zone resources to the delegated zones. See
[Cloudflare DNS records](https://developers.cloudflare.com/api/python/resources/dns/subresources/records/methods/create/)
and [zone discovery](https://developers.cloudflare.com/api/python/resources/zones/methods/list/).

## Requesting a certificate

Configure the CNAME first and ensure it resolves through public DNS, then run:

```bash
certbot certonly \
  --authenticator dns-alias \
  --dns-alias-credentials /path/to/credentials.ini \
  --dns-alias-require-cname \
  --dns-alias-propagation-seconds 120 \
  --non-interactive --agree-tos --email admin@example.com \
  -d example.com -d '*.example.com'
```

Replace the credentials path, email address, and domains with your own values.
Add `--staging` for an initial test, then remove it to request a production certificate.
A domain and its wildcard share the same `_acme-challenge` name; the plugin preserves all TXT
values needed for both challenges.

### Common options

| Option | Default | Description |
| --- | --- | --- |
| `--dns-alias-credentials` | Required | Path to the credentials INI file |
| `--dns-alias-propagation-seconds` | `60` | Propagation wait in seconds after all TXT records are ready |
| `--dns-alias-ttl` | `600` | TXT record TTL; must satisfy the cloud plan's limits |
| `--dns-alias-cname-max-depth` | `8` | Maximum number of CNAME hops |
| `--dns-alias-dns-timeout` | `10` | Total timeout in seconds for each DNS query |
| `--dns-alias-dns-retries` | `2` | Additional retries after a timeout or unavailable nameservers |
| `--dns-alias-resolvers` | System DNS | Comma-separated IPv4/IPv6 DNS server addresses |
| `--dns-alias-require-cname` | Disabled | Reject writes when the original challenge name has no CNAME |

Without `--dns-alias-require-cname`, the plugin may also create TXT records directly in the
managed zone of the original challenge name. TTL and propagation wait are different settings.
Creating a validation hostname for the first time may encounter DNS negative caching;
increase the propagation wait if needed.

## Renewal

Certbot saves the authenticator configuration and the absolute credentials file path.
Run renewal in the same environment:

```bash
certbot renew --dry-run
certbot renew
```

Keep the credentials file available and refresh temporary credentials before they expire.
The account running Certbot needs access to its configuration, work, and log directories.
Creation state is stored only in the current Certbot process. Forced termination, a lost
response after a successful creation, or a cleanup failure may leave TXT records behind in
the delegated zone; remove these manually.

## GoDaddy REST client (SDK only)

The package includes an internal synchronous GoDaddy Domains v3 client using `httpx2`.
GoDaddy is not yet a selectable Certbot provider; `dns_alias_provider = godaddy` is not supported.
The client uses a Personal Access Token (PAT), with `domains.domain:read` for queries and
`domains.dns:update` for writes. Legacy API key/secret credentials are not accepted by v3.
See [GoDaddy DNS documentation](https://developer.godaddy.com/en/docs/api-users/domains/manage/dns).

```python
from certbot_dns_alias.sdk.godaddy import GoDaddyClient

with GoDaddyClient("YOUR_GODADDY_PAT") as client:
    records = client.list_records("example.com", type="TXT", name="_acme-challenge")
```

`list_domains()` and `get_domain()` return domain identities, status, and nameservers.
`list_records()` returns `DNSRecord` objects, including `record_id`, relative `name`, `type`,
`data`, and `ttl`. `create_record(zone, record)` and
`replace_record(zone, record_id, record)` accept a `DNSRecord` and return the server's record;
`delete_record(zone, record_id)` deletes only that ID. TXT TTLs must be 600–86400 seconds.
Registered domain enumeration does not establish that a domain's DNS is hosted on GoDaddy.

The client reads all pages or raises, uses a 10-second connection timeout and 30-second
read/write/pool timeouts, and performs no automatic retries or redirects. Errors subclass
Certbot's `PluginError` and omit sensitive API messages. A delete returning 404 remains a
`GoDaddyAPIError` for the future provider to classify. Use `ote=True` with separate OTE credentials
for the test environment. Close the client after use, preferably with a context manager.
Lost responses after successful writes can leave records behind; do not blindly retry writes.
Python 3.9 installs HTTPX2 2.0; Python 3.10–3.14 can use newer 2.x releases.
