"""Alibaba Cloud DNS adapter using the official OpenAPI SDK."""

from __future__ import annotations

from alibabacloud_alidns20150109 import models
from alibabacloud_alidns20150109.client import Client
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util.models import RuntimeOptions
from certbot import errors
from Tea.exceptions import TeaException, UnretryableException

from certbot_dns_alias.providers.base import DNSProvider, TxtRecord
from certbot_dns_alias.providers.pagination import PageTracker, record_identity, zone_identity
from certbot_dns_alias.providers.safety import ProviderAPIError, quiet_sdk_call


class AliyunDNSProvider(DNSProvider):
    name = "aliyun"
    page_size = 100

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        *,
        region_id: str = "cn-hangzhou",
        security_token: str | None = None,
    ) -> None:
        self.client = Client(
            Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                security_token=security_token,
                region_id=region_id,
                endpoint="alidns.aliyuncs.com",
            )
        )
        # Never automatically replay a write whose result may have been lost.
        self.runtime = RuntimeOptions(
            connect_timeout=10000,
            read_timeout=30000,
            autoretry=False,
        )

    def _call(self, operation: str, request):
        try:
            with quiet_sdk_call():
                return getattr(self.client, operation)(request, self.runtime).body
        except (TeaException, UnretryableException) as exc:
            # SDK messages can contain request details. Report only the code.
            code = getattr(exc, "code", None)
            raise ProviderAPIError("Aliyun DNS", operation, code) from None

    def list_zones(self) -> list[str]:
        zones = []
        page = 1
        tracker = PageTracker("Aliyun DNS zone query", self.page_size, fixed_pages=True)
        while True:
            body = self._call(
                "describe_domains_with_options",
                models.DescribeDomainsRequest(
                    page_number=page,
                    page_size=self.page_size,
                ),
            )
            items = body.domains.domain if body.domains else []
            items = items or []
            self._check_page(body, page)
            complete = tracker.accept(
                body.total_count,
                [zone_identity(getattr(item, "domain_name", None)) for item in items],
            )
            zones.extend(item.domain_name for item in items)
            if complete:
                return zones
            page += 1

    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        records = []
        page = 1
        tracker = PageTracker("Aliyun DNS TXT record query", self.page_size, fixed_pages=True)
        while True:
            body = self._call(
                "describe_domain_records_with_options",
                models.DescribeDomainRecordsRequest(
                    domain_name=zone,
                    rrkey_word=name,
                    type="TXT",
                    search_mode="EXACT",
                    page_number=page,
                    page_size=self.page_size,
                ),
            )
            items = body.domain_records.record if body.domain_records else []
            items = items or []
            self._check_page(body, page)
            complete = tracker.accept(
                body.total_count,
                [record_identity(getattr(item, "record_id", None)) for item in items],
            )
            records.extend(
                TxtRecord(str(item.record_id), item.rr, item.value)
                for item in items
                if item.type == "TXT"
                and item.rr.lower() == name.lower()
                and item.status == "ENABLE"
                and item.line == "default"
            )
            if complete:
                return records
            page += 1

    def _check_page(self, body, page: int) -> None:
        if (
            type(body.page_number) is not int
            or body.page_number != page
            or type(body.page_size) is not int
            or body.page_size != self.page_size
        ):
            raise errors.PluginError("Aliyun DNS returned invalid page metadata")

    def create_txt_record(self, zone: str, name: str, value: str, ttl: int) -> str:
        body = self._call(
            "add_domain_record_with_options",
            models.AddDomainRecordRequest(
                domain_name=zone,
                rr=name,
                type="TXT",
                value=value,
                ttl=ttl,
                line="default",
            ),
        )
        if not body.record_id:
            raise errors.PluginError("Aliyun DNS did not return a new TXT record ID")
        return str(body.record_id)

    def delete_txt_record(self, zone: str, record_id: str) -> None:
        try:
            self._call(
                "delete_domain_record_with_options",
                models.DeleteDomainRecordRequest(
                    record_id=record_id,
                ),
            )
        except errors.PluginError as exc:
            # DeleteDomainRecord takes only a record ID, not a zone name.
            if getattr(exc, "code", None) != "InvalidRecordId.NotFound":
                raise
