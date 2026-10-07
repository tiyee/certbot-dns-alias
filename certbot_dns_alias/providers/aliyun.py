"""Alibaba Cloud DNS adapter using the official OpenAPI SDK."""

from __future__ import annotations

from alibabacloud_alidns20150109 import models
from alibabacloud_alidns20150109.client import Client
from alibabacloud_tea_openapi.models import Config
from alibabacloud_tea_util.models import RuntimeOptions
from certbot import errors
from Tea.exceptions import TeaException, UnretryableException

from certbot_dns_alias.providers.base import DNSProvider, TxtRecord


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
            return getattr(self.client, operation)(request, self.runtime).body
        except (TeaException, UnretryableException) as exc:
            # SDK messages can contain request details. Report only the code.
            code = getattr(exc, "code", None) or type(exc).__name__
            raise errors.PluginError(f"Aliyun DNS {operation} failed ({code})") from exc

    def list_zones(self) -> list[str]:
        zones = []
        page = 1
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
            zones.extend(item.domain_name for item in items)
            if page * self.page_size >= body.total_count:
                return zones
            if not items:
                raise errors.PluginError("Aliyun DNS returned an incomplete zone list")
            page += 1

    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        records = []
        page = 1
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
            records.extend(
                TxtRecord(str(item.record_id), item.rr, item.value)
                for item in items
                if item.type == "TXT"
                and item.rr.lower() == name.lower()
                and item.status == "ENABLE"
                and item.line == "default"
            )
            if page * self.page_size >= body.total_count:
                return records
            if not items:
                raise errors.PluginError("Aliyun DNS returned an incomplete TXT record list")
            page += 1

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
            if getattr(exc.__cause__, "code", None) != "InvalidRecordId.NotFound":
                raise
