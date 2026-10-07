"""Tencent Cloud DNSPod adapter using the official API v20210323 SDK."""

from __future__ import annotations

from certbot import errors
from tencentcloud.common.credential import Credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.dnspod.v20210323 import dnspod_client, models

from certbot_dns_alias.providers.base import DNSProvider, TxtRecord
from certbot_dns_alias.providers.pagination import PageTracker, record_identity, zone_identity
from certbot_dns_alias.providers.safety import ProviderAPIError, quiet_sdk_call


class TencentDNSProvider(DNSProvider):
    name = "tencent"
    page_size = 100

    def __init__(self, secret_id: str, secret_key: str, *, token: str | None = None) -> None:
        http = HttpProfile(endpoint="dnspod.tencentcloudapi.com", reqTimeout=30)
        self.client = dnspod_client.DnspodClient(
            Credential(secret_id, secret_key, token),
            "",
            ClientProfile(httpProfile=http),
        )

    def _call(self, operation: str, request):
        try:
            with quiet_sdk_call():
                return getattr(self.client, operation)(request)
        except TencentCloudSDKException as exc:
            raise ProviderAPIError("Tencent DNSPod", operation, exc.get_code()) from None

    def list_zones(self) -> list[str]:
        zones = []
        offset = 0
        tracker = PageTracker("Tencent DNSPod zone query", self.page_size)
        while True:
            request = models.DescribeDomainListRequest()
            request.Type = "ALL"
            request.Offset = offset
            request.Limit = self.page_size
            body = self._call("DescribeDomainList", request)
            items = body.DomainList or []
            total = body.DomainCountInfo.DomainTotal if body.DomainCountInfo else None
            complete = tracker.accept(
                total, [zone_identity(getattr(item, "Name", None)) for item in items]
            )
            zones.extend(item.Name for item in items)
            offset += len(items)
            if complete:
                return zones

    def list_txt_records(self, zone: str, name: str) -> list[TxtRecord]:
        records = []
        offset = 0
        tracker = PageTracker("Tencent DNSPod TXT record query", self.page_size)
        while True:
            request = models.DescribeRecordListRequest()
            request.Domain = zone
            request.SubDomain = name
            request.RecordType = "TXT"
            request.Offset = offset
            request.Limit = self.page_size
            request.ErrorOnEmpty = "no"
            # Older API/SDK versions raise this code for an empty result.
            # Do not confuse it with a missing zone or a permission failure.
            try:
                body = self._call("DescribeRecordList", request)
            except errors.PluginError as exc:
                if getattr(exc, "code", None) == "ResourceNotFound.NoDataOfRecord":
                    if offset == 0:
                        return []
                    raise errors.PluginError(
                        "Tencent DNSPod returned an incomplete TXT record list"
                    ) from None
                raise
            items = body.RecordList or []
            info = body.RecordCountInfo
            if info is None or type(info.ListCount) is not int or info.ListCount != len(items):
                raise errors.PluginError("Tencent DNSPod returned invalid record count metadata")
            complete = tracker.accept(
                info.TotalCount,
                [record_identity(getattr(item, "RecordId", None), integer=True) for item in items],
            )
            records.extend(
                TxtRecord(str(item.RecordId), item.Name, item.Value)
                for item in items
                if item.Type == "TXT"
                and item.Name.lower() == name.lower()
                and item.Status == "ENABLE"
                and item.LineId == "0"
            )
            offset += len(items)
            if complete:
                return records

    def create_txt_record(self, zone: str, name: str, value: str, ttl: int) -> str:
        request = models.CreateRecordRequest()
        request.Domain = zone
        request.SubDomain = name
        request.RecordType = "TXT"
        request.RecordLine = "默认"
        request.RecordLineId = "0"
        request.Value = value
        request.TTL = ttl
        body = self._call("CreateRecord", request)
        if not body.RecordId:
            raise errors.PluginError("Tencent DNSPod did not return a new TXT record ID")
        return str(body.RecordId)

    def delete_txt_record(self, zone: str, record_id: str) -> None:
        request = models.DeleteRecordRequest()
        request.Domain = zone
        request.RecordId = int(record_id)
        try:
            self._call("DeleteRecord", request)
        except errors.PluginError as exc:
            if getattr(exc, "code", None) != "ResourceNotFound.NoDataOfRecord":
                raise
