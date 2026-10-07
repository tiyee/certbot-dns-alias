# certbot-dns-alias

[English](README.md) | 简体中文

Certbot DNS-01 插件，通过 CNAME 委托在阿里云 DNS、腾讯云 DNSPod、Cloudflare 或 GoDaddy 管理 TXT 验证记录。

**支持 Certbot 3.x 和 5.x**，同一份插件可在这两个版本系列的宿主环境中使用。

提供两个安装名称：`certbot-dns-alias` 和 `certbot-dns-delegation`。
后者是安装别名，会自动安装同版本的主包；两种方式都使用 Certbot 认证器 `dns-alias`。

## 工作方式

在业务域名的 DNS 中预先创建 CNAME：

```dns
_acme-challenge.example.com.  300 IN CNAME example-com.delegate.example.net.
```

`delegate.example.net` 托管在阿里云、腾讯云、Cloudflare 或 GoDaddy。插件自动跟随 CNAME 链，在最终目标
`example-com.delegate.example.net` 添加本次挑战的 TXT 值，等待 DNS 传播，完成后按记录 ID 清理。
业务域名可以由任意 DNS 服务商托管；插件只需要目标托管区域的 API 凭据。

- 支持多级 CNAME、泛域名、一个证书包含多个域名。
- 支持阿里云、腾讯云、Cloudflare、GoDaddy 单独使用，或 `auto` 模式在一次申请中使用任意组合。
- 根据托管区域列表做最长 DNS 后缀匹配，支持 `example.co.uk` 和独立托管的子域，匹配包含标签边界。
- 区域列表及 TXT 查询支持 API 分页；可以显式配置区域以跳过自动枚举。
- 每个 TXT 值单独创建，保留同名记录的其他值；复用已有的相同有效 TXT 时不删除原记录。
- 清理使用创建时保存的目标区域和记录 ID；CNAME 发生变化也不会改删其他区域。
- CNAME 环路、超深链、DNS 超时、权限错误和区域归属冲突会产生明确错误。

支持 Python **3.9–3.14**、**Certbot 3.x 和 5.x**，具体组合如下：

| Python | Certbot 3.x | Certbot 5.x |
| --- | --- | --- |
| 3.9 | 支持 | 上游要求 Python ≥3.10 |
| 3.10–3.13 | 支持 | 支持 |
| 3.14 | 旧版 josepy 无法导入 | 支持 |

三个官方服务商 SDK 和包内 GoDaddy REST 客户端均随插件安装。

## 安装

在宿主 Certbot 所在的 Python 环境中安装插件：

```bash
python -m pip install certbot-dns-alias
certbot plugins --text
```

也可使用安装别名，两者任选其一即可：

```bash
python -m pip install certbot-dns-delegation
```

安装别名后，认证器仍为 `dns-alias`，参数和凭据键仍使用 `dns-alias` / `dns_alias` 前缀。

其中 `python` 必须是运行宿主 Certbot 的解释器。也可用 uv 明确指定宿主环境：

```bash
uv pip install --python /path/to/certbot-venv/bin/python certbot-dns-alias
```

插件按 Python 版本声明宿主依赖：Python 3.9 使用 Certbot 3，Python 3.14 使用 Certbot 5，
Python 3.10–3.13 可使用 Certbot 3 或 5。
已有宿主满足兼容约束时，pip 默认的依赖升级策略只在必要时升级依赖。
如果需要严格保持宿主版本，可在安装时显式固定实际版本。
**所有 Certbot 3 宿主都必须使用 `certbot3` extra**，它将 ACME 限定为 3.x，
并将 pyOpenSSL 限定在 25 以下。旧版 josepy 需要已移除的 `X509Req` API，
Python 3.10–3.13 上也需要此约束。例如宿主为 `3.0.0`：

```bash
python -m pip install 'certbot==3.0.0' 'certbot-dns-alias[certbot3]'
```

安装别名支持相同选项：`certbot-dns-delegation[certbot3]`。
Certbot 5 的默认安装不会施加上述旧版 TLS 依赖上限。
这个 extra 用于兼容旧宿主，并不能修复旧依赖的安全问题；条件允许时，应使用仍受维护的
Certbot 5 宿主。该 extra 的约束适用于 Python 3.9–3.13；Python 3.14 仅支持 Certbot 5。

如部署环境还固定了 ACME、pyOpenSSL 等依赖，应同时使用该环境的 constraints 文件。
插件与 Certbot 必须处于同一 Python 环境；已有 snap/docker 版 Certbot 时，需按对应安装方式
将插件加入宿主运行环境，在其他虚拟环境中安装不会被该宿主发现。

## 凭据配置

使用不带 INI section 的 `key = value` 格式。完整示例见 [examples](examples)。
复制示例后填入密钥，并设置权限：

```bash
cp examples/tencent.ini credentials.ini
chmod 600 credentials.ini
```

### 阿里云

```ini
dns_alias_provider = aliyun
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = delegate.example.net
```

可选项：`dns_alias_aliyun_region_id`（默认 `cn-hangzhou`）、
`dns_alias_aliyun_security_token`（临时 STS 凭据）。固定使用公共端点 `alidns.aliyuncs.com`。

### 腾讯云 DNSPod

```ini
dns_alias_provider = tencent
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = delegate.example.net
```

可选项：`dns_alias_tencent_token`（临时会话凭据）。使用腾讯云 API v20210323 和
`dnspod.tencentcloudapi.com`，不使用旧版 DNSPod Token API 或国际版端点。

### Cloudflare

```ini
dns_alias_provider = cloudflare
dns_alias_cloudflare_api_token = YOUR_API_TOKEN
```

使用具有委托区域 `Zone:DNS:Edit` 和 `Zone:Zone:Read` 权限的 API Token。
插件使用 `https://api.cloudflare.com/client/v4`，自动枚举 Token 可见的区域。
不支持 Global API Key。

要跳过区域枚举并免除 `Zone:Zone:Read` 权限，可提供区域名称与 ID 的对应关系：

```ini
dns_alias_cloudflare_zone_ids = delegate.example.net:0123456789abcdef0123456789abcdef
```

将占位值替换为 Cloudflare 控制台中的 32 位 Zone ID，多个 `区域名称:ID` 用逗号分隔。
配置后仅允许列表中的区域。可通过 `dns_alias_cloudflare_zones` 进一步限制，
其中每个区域名称都必须在 `dns_alias_cloudflare_zone_ids` 中有对应 ID。
只配置 Cloudflare 区域名称而没有 ID 会报错，因为跳过枚举需要预先提供 ID。

Cloudflare TXT 的 TTL 支持 `1`（自动）或 60–86400 秒（Enterprise 最低 30 秒），
插件默认 `600` 符合要求。委托 CNAME 应设为 DNS-only，以便公共解析器跟随；
Cloudflare 代理或 CNAME flattening 可能隐藏 CNAME。

### GoDaddy

```ini
dns_alias_provider = godaddy
dns_alias_godaddy_api_token = YOUR_GODADDY_PAT
dns_alias_godaddy_zones = delegate.example.net
```

插件使用包内 GoDaddy Domains v3 客户端，以 Personal Access Token（PAT）访问
`https://api.godaddy.com`，不支持旧版 API key/secret 凭据。
查询需要 `domains.domain:read`，写入需要 `domains.dns:update`。
TXT TTL 必须为 600–86400 秒，插件默认 `600` 符合要求。

`dns_alias_godaddy_zones` 是可选的区域白名单，配置后跳过已注册域名枚举。
独立委托子域或不在账号已注册域名列表中的 DNS 区域应使用此设置。
省略时，插件完整枚举已注册域名，并逐一验证 DNS API 访问及区域根部的 SOA 记录。
只有存在根部 SOA 的候选区域才可用于路由。API 失败或分页不完整会终止发现，
不会缓存部分结果；账号内包含 DNS 不可访问的域名时，应显式配置区域列表。
请确保委托区域由 GoDaddy 的权威域名服务器提供解析。

可选项：`dns_alias_godaddy_ote = true` 使用 `https://api.ote-godaddy.com` 和独立 OTE 凭据。
默认值为 `false`（生产环境）。此设置选择 GoDaddy API 环境，与 Certbot 的
`--staging` ACME 环境相互独立。

### 同时使用多家服务商

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
dns_alias_godaddy_api_token = YOUR_GODADDY_PAT
dns_alias_godaddy_zones = gd-delegate.example.net
```

例如：

```dns
_acme-challenge.example.com.      300 IN CNAME example-com.ali-delegate.example.net.
_acme-challenge.api.example.org.  300 IN CNAME api-example-org.tencent-delegate.example.org.
```

随后在同一命令中传入 `-d example.com -d api.example.org` 即可。`auto` 至少需要一组完整密钥，
也可以只配置一家。如果同一最长匹配区域同时属于多个服务商，插件会报错；通过调整显式区域列表
或选择单一 `provider` 消除歧义。

所有 `*_zones` 均为可选项，多个区域以逗号分隔，例如 `example.net, example.co.uk`。
配置后只允许这些区域，不再调用对应的域名枚举 API；填写云平台实际托管区域名称，
不要填写完整 TXT 主机名。Cloudflare 显式区域列表还需按上述说明提供 ID。
Cloudflare 的两个区域配置均省略时，或其他服务商未配置 `*_zones` 时，自动枚举当前凭据可见的区域。
GoDaddy 从已注册域名开始发现，并按上述说明验证 DNS API 访问。
每家服务商目前支持一个凭据账号。

## 申请和续期

先创建 CNAME，并确保公共 DNS 可以解析；第一次申请可以使用测试环境：

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

测试通过后去掉 `--staging` 申请正式证书。域名和邮箱应替换为自己的值。
使用宿主环境中的 `certbot` 执行命令。
Certbot 默认写入 `/etc/letsencrypt`、`/var/lib/letsencrypt` 和 `/var/log/letsencrypt`，
运行账号需要相应权限；也可使用 `--config-dir`、`--work-dir` 和 `--logs-dir` 指定目录。

Certbot 保存认证器和凭据文件的绝对路径，后续可使用同一环境续期：

```bash
certbot renew --dry-run
certbot renew
```

凭据文件需长期保留，临时凭据过期前需更新。按部署方式配置定时续期及证书部署 hook。

### 可配置参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--dns-alias-credentials` | 必填 | 凭据 INI 路径 |
| `--dns-alias-propagation-seconds` | `60` | 全部 TXT 添加后，统一等待的传播时间 |
| `--dns-alias-ttl` | `600` | 创建 TXT 时的 TTL，需满足云套餐限制 |
| `--dns-alias-cname-max-depth` | `8` | 允许的最大 CNAME 链接数 |
| `--dns-alias-dns-timeout` | `10` | 每次 CNAME 查询的总超时，秒 |
| `--dns-alias-dns-retries` | `2` | 超时或无可用 nameserver 时额外重试次数 |
| `--dns-alias-resolvers` | 系统 DNS | 逗号分隔的 DNS 服务器 IPv4/IPv6 地址 |
| `--dns-alias-require-cname` | 关闭 | 原始挑战名称没有 CNAME 时拒绝写入 |

没有 CNAME 时，默认允许直接在原始挑战名称所属托管区域创建 TXT。只允许委托验证时启用
`--dns-alias-require-cname`。解析器使用绝对 DNS 名称，禁止系统 search suffix 扩展。
NXDOMAIN / 无 CNAME 表示链终点；超时、SERVFAIL 等解析失败不会当作链终点。

TTL 与传播等待时间不同。第一次创建目标主机可能受 DNS 负缓存影响，必要时提高传播等待时间。
不同业务域名应使用不同委托主机名；普通域名与其泛域名会共用 `_acme-challenge` 名称，
插件会保留本次申请需要的多个 TXT 值。插件不会更新整个 TXT RRset。

创建状态保存在当前 Certbot 进程内；进程被强制终止、创建成功但响应丢失或删除失败时，可能留有 TXT，
需要在委托区域手动清理。清理失败会记录警告并继续清理其他挑战。

## API 权限

阿里云需要：

- `alidns:DescribeDomainRecords`
- `alidns:AddDomainRecord`
- `alidns:DeleteDomainRecord`
- 未配置 `dns_alias_aliyun_zones` 时，还需要 `alidns:DescribeDomains`

腾讯云需要：

- `dnspod:DescribeRecordList`
- `dnspod:CreateRecord`
- `dnspod:DeleteRecord`
- 未配置 `dns_alias_tencent_zones` 时，还需要 `dnspod:DescribeDomainList`

Cloudflare API Token 需要：

- `Zone:DNS:Edit`，用于查询、创建及删除 TXT 记录
- 未配置 `dns_alias_cloudflare_zone_ids` 时，还需要 `Zone:Zone:Read`

将 Token 的区域资源限制为委托区域。参考
[Cloudflare DNS 记录](https://developers.cloudflare.com/api/python/resources/dns/subresources/records/methods/create/) 和
[区域枚举](https://developers.cloudflare.com/api/python/resources/zones/methods/list/)。

GoDaddy Personal Access Token 需要：

- `domains.domain:read`，用于已注册域名枚举和 DNS 记录查询
- `domains.dns:update`，用于单独创建和删除 TXT 记录

插件通过包内 REST 客户端使用 Domains v3，参考
[GoDaddy DNS 文档](https://developer.godaddy.com/en/docs/api-users/domains/manage/dns)。
清理返回 404 时，只有完整查询已保存区域的 TXT，并确认已保存记录 ID 不存在后，才视为成功。
区域不可访问或不存在时仍保留清理错误，后续可以重试。

可根据云平台支持的资源范围将权限限制在委托区域。API 字段与权限参考
[阿里云 AddDomainRecord](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-adddomainrecord)、
[阿里云 DescribeDomains](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-describedomains) 和
[腾讯云 DescribeRecordList](https://cloud.tencent.com/document/api/1427/56166)。

## GoDaddy REST 客户端

包内包含使用 `httpx2` 的同步 GoDaddy Domains v3 客户端。
GoDaddy Certbot provider 使用此客户端，也可直接调用其 API 方法。
客户端使用 Personal Access Token（PAT），查询需要 `domains.domain:read`，
写入需要 `domains.dns:update`。v3 不接受旧版 API key/secret 凭据。
参考 [GoDaddy DNS 文档](https://developer.godaddy.com/en/docs/api-users/domains/manage/dns)。

```python
from certbot_dns_alias.sdk.godaddy import GoDaddyClient

with GoDaddyClient("YOUR_GODADDY_PAT") as client:
    records = client.list_records("example.com", type="TXT", name="_acme-challenge")
```

`list_domains()` 和 `get_domain()` 返回域名标识、状态与域名服务器。
`list_records()` 返回 `DNSRecord` 对象，包含 `record_id`、相对名称 `name`、`type`、
`data` 和 `ttl`。`create_record(zone, record)` 与
`replace_record(zone, record_id, record)` 接受 `DNSRecord`，并返回服务端记录；
`delete_record(zone, record_id)` 仅删除该 ID 对应的记录。TXT TTL 必须为 600–86400 秒。
枚举已注册域名并不能证明该域名的 DNS 托管在 GoDaddy。

客户端完整读取全部分页，否则抛出异常；连接超时为 10 秒，读取、写入和连接池等待超时为
30 秒，不自动重试或跟随重定向。异常继承 Certbot 的 `PluginError`，不包含敏感 API 消息。
客户端删除请求返回 404 时仍抛出 `GoDaddyAPIError`；Certbot provider 会验证区域访问和记录缺失后再接受。
测试环境使用 `ote=True` 和独立 OTE 凭据。使用后应关闭客户端，建议通过上下文管理器管理。
写入成功后响应丢失可能留下记录，因此不要盲目重试写入。
Python 3.9 安装 HTTPX2 2.0；Python 3.10–3.14 可使用更新的 2.x 版本。

## License

MIT，见 [LICENSE](LICENSE)。
