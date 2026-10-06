# certbot-dns-alias

支持 CNAME 委托的 Certbot DNS-01 插件，可在阿里云 DNS 或腾讯云 DNSPod 自动添加和清理 TXT 验证记录。

## 工作原理

将业务域名的 `_acme-challenge` 记录通过 CNAME 指向集中管理的验证域名：

```dns
_acme-challenge.example.com.  300 IN CNAME example-com.delegate.example.net.
```

其中，`delegate.example.net` 托管在阿里云或腾讯云。申请证书时，插件：

1. 跟随 `_acme-challenge.example.com` 的 CNAME 链，找到最终目标。
2. 根据最终目标所属的托管区域，选择对应的云服务商。
3. 在 `example-com.delegate.example.net` 添加本次验证需要的 TXT 值。
4. 等待 DNS 传播，由证书颁发机构完成 DNS-01 验证。
5. 按创建时保存的记录 ID 清理临时 TXT。

业务域名可以由任意 DNS 服务商托管，只需提供最终目标区域的 API 凭据。
支持多级 CNAME、泛域名和多域名证书，也支持一次申请中的不同域名分别委托给阿里云和腾讯云。
插件按 TXT 值分别管理记录，保留同名记录中的其他值；复用已有的相同有效记录时，不会删除该原有记录。

## 安装

需要 Python 3.10+ 和 Certbot 3.x–5.x。插件与 Certbot 必须安装在同一 Python 环境中。

使用 uv 安装 Certbot 和插件：

```bash
uv tool install --with certbot-dns-alias certbot
```

或者在已有 Certbot 的 Python 虚拟环境中安装：

```bash
python -m pip install certbot-dns-alias
```

确认插件可用：

```bash
certbot plugins --text
```

## 凭据配置

创建 `credentials.ini`，使用不带 section 的 `key = value` 格式，并设置文件权限：

```bash
chmod 600 credentials.ini
```

### 阿里云

```ini
dns_alias_provider = aliyun
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = delegate.example.net
```

可选配置：`dns_alias_aliyun_region_id`（默认 `cn-hangzhou`）和
`dns_alias_aliyun_security_token`（临时 STS 凭据）。

### 腾讯云 DNSPod

```ini
dns_alias_provider = tencent
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = delegate.example.net
```

使用腾讯云 SecretId / SecretKey，可通过 `dns_alias_tencent_token` 配置临时会话凭据。

### 同时使用阿里云和腾讯云

```ini
dns_alias_provider = auto
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = ali-delegate.example.net
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = tencent-delegate.example.org
```

为不同业务域名设置对应的委托目标：

```dns
_acme-challenge.example.com.      300 IN CNAME example-com.ali-delegate.example.net.
_acme-challenge.api.example.org.  300 IN CNAME api-example-org.tencent-delegate.example.org.
```

在申请命令中同时传入 `-d example.com -d api.example.org` 即可。
`auto` 至少需要一组完整的服务商密钥，也允许只配置一家。

`*_zones` 为可选配置，多个区域以逗号分隔。填写云平台实际托管区域名称，
例如 `delegate.example.net`，而不是完整 TXT 主机名。
配置后只使用列出的区域；省略时自动查询当前凭据可见的区域。
插件选择最长匹配的 DNS 后缀；同一匹配区域同时出现在两家服务商中时，
需调整区域列表或选择单一服务商以消除歧义。

### API 权限

阿里云凭据需要以下操作权限：

- `alidns:DescribeDomainRecords`
- `alidns:AddDomainRecord`
- `alidns:DeleteDomainRecord`
- 未配置 `dns_alias_aliyun_zones` 时，还需要 `alidns:DescribeDomains`

腾讯云凭据需要以下操作权限：

- `dnspod:DescribeRecordList`
- `dnspod:CreateRecord`
- `dnspod:DeleteRecord`
- 未配置 `dns_alias_tencent_zones` 时，还需要 `dnspod:DescribeDomainList`

## 申请证书

先配置 CNAME，并确认公共 DNS 可以解析，然后运行：

```bash
certbot certonly \
  --authenticator dns-alias \
  --dns-alias-credentials /path/to/credentials.ini \
  --dns-alias-require-cname \
  --dns-alias-propagation-seconds 120 \
  --non-interactive --agree-tos --email admin@example.com \
  -d example.com -d '*.example.com'
```

将凭据路径、邮箱和域名替换为实际值。首次使用可添加 `--staging` 验证配置，
配置确认后去掉该参数申请正式证书。
普通域名和其泛域名共用 `_acme-challenge` 名称，插件会同时保留各自需要的 TXT 值。

### 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--dns-alias-credentials` | 必填 | 凭据 INI 文件路径 |
| `--dns-alias-propagation-seconds` | `60` | 全部 TXT 添加后的传播等待时间，秒 |
| `--dns-alias-ttl` | `600` | TXT 记录 TTL，需满足云套餐限制 |
| `--dns-alias-cname-max-depth` | `8` | 最大 CNAME 链接数 |
| `--dns-alias-dns-timeout` | `10` | 单次 DNS 查询总超时，秒 |
| `--dns-alias-dns-retries` | `2` | DNS 超时或无可用服务器时的额外重试次数 |
| `--dns-alias-resolvers` | 系统 DNS | 逗号分隔的 DNS 服务器 IPv4/IPv6 地址 |
| `--dns-alias-require-cname` | 关闭 | 原始挑战名称没有 CNAME 时拒绝写入 |

不启用 `--dns-alias-require-cname` 时，也允许直接在原始挑战名称所属的托管区域添加 TXT。
TTL 与传播等待时间不同；首次创建验证主机可能受 DNS 负缓存影响，必要时提高传播等待时间。

## 续期

Certbot 会保存认证器配置和凭据文件的绝对路径，使用同一运行环境执行：

```bash
certbot renew --dry-run
certbot renew
```

凭据文件需长期保留，临时凭据应在过期前更新。运行账号需要有权访问 Certbot 的配置、工作和日志目录。
如果 Certbot 进程被强制终止或云端清理失败，委托区域可能留有本次 TXT 记录，可手动删除。
