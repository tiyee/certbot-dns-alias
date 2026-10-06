# certbot-dns-alias

Certbot DNS-01 插件，通过 CNAME 委托在阿里云 DNS 或腾讯云 DNSPod 管理 TXT 验证记录。

Certbot DNS-01 authentication with CNAME delegation, supporting Alibaba Cloud DNS and Tencent Cloud DNSPod.

## 工作方式

在业务域名的 DNS 中预先创建 CNAME：

```dns
_acme-challenge.example.com.  300 IN CNAME example-com.delegate.example.net.
```

`delegate.example.net` 托管在阿里云或腾讯云。插件自动跟随 CNAME 链，在最终目标
`example-com.delegate.example.net` 添加本次挑战的 TXT 值，等待 DNS 传播，完成后按记录 ID 清理。
业务域名可以由任意 DNS 服务商托管；插件只需要目标托管区域的 API 凭据。

- 支持多级 CNAME、泛域名、一个证书包含多个域名。
- 支持阿里云、腾讯云单独使用，或 `auto` 模式在一次申请中同时使用两者。
- 根据托管区域列表做最长 DNS 后缀匹配，支持 `example.co.uk` 和独立托管的子域，匹配包含标签边界。
- 区域列表及 TXT 查询支持 API 分页；可以显式配置区域以跳过自动枚举。
- 每个 TXT 值单独创建，保留同名记录的其他值；复用已有的相同有效 TXT 时不删除原记录。
- 清理使用创建时保存的目标区域和记录 ID；CNAME 发生变化也不会改删其他区域。
- CNAME 环路、超深链、DNS 超时、权限错误和区域归属冲突会产生明确错误。

Python **3.10+**，Certbot **3.x–5.x**。两个云服务商 SDK 均随插件安装。

## 安装

### 本地开发（uv）

```bash
uv sync --locked
uv run certbot plugins --text
uv run certbot --help dns-alias
```

`uv.lock` 已纳入版本管理，`uv sync` 会创建 `.venv` 并以 editable 模式安装插件。

### 从 PyPI 安装（本项目发布后）

在宿主 Certbot 所在的 Python 环境中安装插件：

```bash
python -m pip install certbot-dns-alias
certbot plugins --text
```

其中 `python` 必须是运行宿主 Certbot 的解释器。也可用 uv 明确指定宿主环境：

```bash
uv pip install --python /path/to/certbot-venv/bin/python certbot-dns-alias
```

插件声明 `certbot>=3.0,<6`，表示支持的宿主版本范围。同一份插件可供 Certbot 3.x 和 5.x 使用。
已有宿主满足兼容约束时，pip 默认的依赖升级策略只在必要时升级依赖。
如果需要严格保持宿主版本，可在安装时显式固定实际版本，例如宿主为 `3.0.0`：

```bash
python -m pip install 'certbot==3.0.0' certbot-dns-alias
```

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

### 同时使用两家云服务商

```ini
dns_alias_provider = auto
dns_alias_aliyun_access_key_id = YOUR_ACCESS_KEY_ID
dns_alias_aliyun_access_key_secret = YOUR_ACCESS_KEY_SECRET
dns_alias_aliyun_zones = ali-delegate.example.net
dns_alias_tencent_secret_id = YOUR_SECRET_ID
dns_alias_tencent_secret_key = YOUR_SECRET_KEY
dns_alias_tencent_zones = tencent-delegate.example.org
```

例如：

```dns
_acme-challenge.example.com.      300 IN CNAME example-com.ali-delegate.example.net.
_acme-challenge.api.example.org.  300 IN CNAME api-example-org.tencent-delegate.example.org.
```

随后在同一命令中传入 `-d example.com -d api.example.org` 即可。`auto` 至少需要一组完整密钥，
也可以只配置一家。如果同一最长匹配区域同时属于两个服务商，插件会报错；通过调整显式区域列表
或选择单一 `provider` 消除歧义。

所有 `*_zones` 均为可选项，多个区域以逗号分隔，例如 `example.net, example.co.uk`。
配置后只允许这些区域，不再调用对应的域名枚举 API；填写云平台实际托管区域名称，
不要填写完整 TXT 主机名。未配置时自动枚举当前凭据可见的区域。
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
使用宿主环境中的 `certbot` 执行命令；在仓库开发环境中验证时可使用 `uv run certbot`。
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

可根据云平台支持的资源范围将权限限制在委托区域。API 字段与权限参考
[阿里云 AddDomainRecord](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-adddomainrecord)、
[阿里云 DescribeDomains](https://www.alibabacloud.com/help/en/dns/api-alidns-2015-01-09-describedomains) 和
[腾讯云 DescribeRecordList](https://cloud.tencent.com/document/api/1427/56166)。

## 测试和构建

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=certbot_dns_alias --cov-report=term-missing
uv build
uv run twine check --strict dist/*
```

测试通过 mock 官方 SDK 和 DNS 响应运行，不需要真实 API 密钥，不会写入云端 DNS。
包含 Certbot 公共挑战生命周期测试、CNAME 链 / 环路 / 超时 / NXDOMAIN、区域匹配、
两个 SDK 的真实请求模型和分页、已有 TXT 保留、多挑战共享与清理失败处理。
CI 配置 Python 3.10–3.14，以及 Certbot 3.0 兼容性验证。
Certbot 3.0 的旧 ACME/josepy 依赖需要 `pyOpenSSL<25`；兼容性测试使用这一旧版本依赖组合。
`uv.lock` 锁定的是仓库开发和默认 CI 的依赖环境；发布包通过依赖范围声明宿主兼容性，
不会将开发环境锁定的 Certbot 版本强制施加到已有宿主环境。

目录结构：

```text
certbot_dns_alias/
  dns_alias.py        # Certbot Authenticator、TXT 所有权和清理状态
  dns.py              # CNAME 解析、DNS 名称和相对主机记录
  config.py           # INI 校验和服务商构建
  providers/
    base.py           # 服务商接口及区域路由
    aliyun.py         # 阿里云 OpenAPI SDK
    tencent.py        # 腾讯云 DNSPod SDK
examples/             # 三种模式的凭据示例
tests/                # 无网络单元和生命周期测试
```

创建和删除 API 不自动重试写请求，避免响应丢失后的重复创建。清理失败会记录警告并继续清理其他挑战。
创建状态保存在当前 Certbot 进程内；进程被强制终止、创建成功但响应丢失或删除失败时，可能留有 TXT，
需要在委托区域手动清理。插件不持久化密钥或挑战值，也不会通过重新解析 CNAME 来猜测待删除记录。

## 发布到 PyPI

包名为 `certbot-dns-alias`，Certbot 入口点为 `dns-alias`。

手动发布前，在 `pyproject.toml` 更新版本，再运行 `uv lock`、测试和构建。可先上传 TestPyPI：

```bash
uv build
uv publish --publish-url https://test.pypi.org/legacy/ dist/*
# 正式发布
uv publish dist/*
```

上传时配置对应的 `UV_PUBLISH_TOKEN`。发布新版本前清空旧 `dist` 产物，以免上传旧版本。

仓库包含 `.github/workflows/publish.yml`，GitHub Release 发布时会验证标签与项目版本一致
（例如版本 `0.1.1` 对应 `v0.1.1`），运行测试、构建 wheel/sdist 并通过 PyPI Trusted Publishing 上传。
需先在 PyPI 为仓库 `tiyee/certbot-dns-alias` 配置 Trusted Publisher，工作流文件名 `publish.yml`，
environment 为 `pypi`；尚未创建 PyPI 项目时可以使用 pending publisher。
GitHub 仓库中创建同名 environment，可按需要设置发布审核。预发布 Release 只构建，不上传正式 PyPI。

## License

MIT，见 [LICENSE](LICENSE)。
