import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alibabacloud_alidns20150109 import models as aliyun_models
from certbot import errors
from Tea.exceptions import TeaException
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.dnspod.v20210323 import models as tencent_models

from certbot_dns_alias.providers.aliyun import AliyunDNSProvider
from certbot_dns_alias.providers.tencent import TencentDNSProvider

OPERATIONS = {
    "aliyun": {
        "zones": "describe_domains_with_options",
        "records": "describe_domain_records_with_options",
        "create": "add_domain_record_with_options",
        "delete": "delete_domain_record_with_options",
    },
    "tencent": {
        "zones": "DescribeDomainList",
        "records": "DescribeRecordList",
        "create": "CreateRecord",
        "delete": "DeleteRecord",
    },
}


def dto(cls, data):
    obj = cls()
    if hasattr(obj, "from_map"):
        return obj.from_map(data)
    obj.from_json_string(json.dumps(data))
    return obj


def zones_response(provider, names, total):
    if provider.name == "aliyun":
        return SimpleNamespace(
            body=dto(
                aliyun_models.DescribeDomainsResponseBody,
                {
                    "TotalCount": total,
                    "Domains": {"Domain": [{"DomainName": name} for name in names]},
                },
            )
        )
    return dto(
        tencent_models.DescribeDomainListResponse,
        {
            "DomainCountInfo": {"DomainTotal": total, "AllTotal": 1000},
            "DomainList": [{"Name": name} for name in names],
        },
    )


def records_response(provider, items, total):
    if provider.name == "aliyun":
        items = [dict(item, RR=item["Name"], Line="default") for item in items]
        for item in items:
            del item["Name"]
            del item["LineId"]
        return SimpleNamespace(
            body=dto(
                aliyun_models.DescribeDomainRecordsResponseBody,
                {
                    "TotalCount": total,
                    "DomainRecords": {"Record": items},
                },
            )
        )
    return dto(
        tencent_models.DescribeRecordListResponse,
        {
            "RecordCountInfo": {"TotalCount": total, "ListCount": len(items)},
            "RecordList": items,
        },
    )


def record(id_, name="host", value="token", type_="TXT", status="ENABLE", line="0"):
    return {
        "RecordId": id_,
        "Name": name,
        "Value": value,
        "Type": type_,
        "Status": status,
        "LineId": line,
    }


def sdk_error(provider, code):
    if provider.name == "aliyun":
        return TeaException({"code": code, "message": "sensitive request details"})
    return TencentCloudSDKException(code, "sensitive request details")


def request_map(request):
    if hasattr(request, "to_map"):
        return request.to_map()
    return {
        key: value
        for key, value in json.loads(request.to_json_string()).items()
        if value is not None
    }


@pytest.fixture(params=["aliyun", "tencent"])
def cloud(request, monkeypatch):
    sdk = Mock()
    if request.param == "aliyun":
        monkeypatch.setattr("certbot_dns_alias.providers.aliyun.Client", Mock(return_value=sdk))
        provider = AliyunDNSProvider("id", "secret", security_token="session")
    else:
        monkeypatch.setattr(
            "certbot_dns_alias.providers.tencent.dnspod_client.DnspodClient",
            Mock(return_value=sdk),
        )
        provider = TencentDNSProvider("id", "secret", token="session")
    provider.page_size = 2
    return provider


def operation(cloud, name):
    return getattr(cloud.client, OPERATIONS[cloud.name][name])


def test_all_zone_pages(cloud):
    call = operation(cloud, "zones")
    call.side_effect = [
        zones_response(cloud, ["example.com", "example.co.uk"], 3),
        zones_response(cloud, ["sub.example.co.uk"], 3),
    ]
    assert cloud.list_zones() == ["example.com", "example.co.uk", "sub.example.co.uk"]
    maps = [request_map(item.args[0]) for item in call.call_args_list]
    if cloud.name == "aliyun":
        assert [item["PageNumber"] for item in maps] == [1, 2]
        assert all(item["PageSize"] == 2 for item in maps)
    else:
        assert [item["Offset"] for item in maps] == [0, 2]
        assert all(item["Type"] == "ALL" and item["Limit"] == 2 for item in maps)


def test_all_txt_pages_and_exact_host_filter(cloud):
    call = operation(cloud, "records")
    call.side_effect = [
        records_response(cloud, [record(1), record(2, name="other-host")], 3),
        records_response(cloud, [record(3, value="second-value")], 3),
    ]
    found = cloud.list_txt_records("example.com", "host")
    assert [(item.id, item.value) for item in found] == [("1", "token"), ("3", "second-value")]
    maps = [request_map(item.args[0]) for item in call.call_args_list]
    if cloud.name == "aliyun":
        assert maps[1] == {
            "DomainName": "example.com",
            "RRKeyWord": "host",
            "Type": "TXT",
            "SearchMode": "EXACT",
            "PageNumber": 2,
            "PageSize": 2,
        }
    else:
        assert maps[1] == {
            "Domain": "example.com",
            "SubDomain": "host",
            "RecordType": "TXT",
            "Offset": 2,
            "Limit": 2,
            "ErrorOnEmpty": "no",
        }


def test_only_active_txt_on_default_line_can_be_reused(cloud):
    call = operation(cloud, "records")
    data = [record(1, status="DISABLE"), record(2, type_="CNAME")]
    call.return_value = records_response(cloud, data, 2)
    assert cloud.list_txt_records("example.com", "host") == []
    if cloud.name == "tencent":
        call.return_value = records_response(cloud, [record(1, line="10")], 1)
        assert cloud.list_txt_records("example.com", "host") == []


def test_empty_lists(cloud):
    operation(cloud, "zones").return_value = zones_response(cloud, [], 0)
    operation(cloud, "records").return_value = records_response(cloud, [], 0)
    assert cloud.list_zones() == []
    assert cloud.list_txt_records("example.com", "@") == []


@pytest.mark.parametrize("kind", ["zones", "records"])
def test_incomplete_pagination_fails(cloud, kind):
    operation(cloud, kind).return_value = (
        zones_response(cloud, [], 10) if kind == "zones" else records_response(cloud, [], 10)
    )
    with pytest.raises(errors.PluginError, match="incomplete"):
        if kind == "zones":
            cloud.list_zones()
        else:
            cloud.list_txt_records("example.com", "host")


def test_create_and_delete_use_real_sdk_request_schema(cloud):
    call = operation(cloud, "create")
    if cloud.name == "aliyun":
        call.return_value = SimpleNamespace(
            body=dto(
                aliyun_models.AddDomainRecordResponseBody,
                {"RecordId": "123"},
            )
        )
    else:
        call.return_value = dto(tencent_models.CreateRecordResponse, {"RecordId": 123})
    assert cloud.create_txt_record("example.com", "@", "token", 600) == "123"
    actual = request_map(call.call_args.args[0])
    if cloud.name == "aliyun":
        assert actual == {
            "DomainName": "example.com",
            "RR": "@",
            "Type": "TXT",
            "Value": "token",
            "TTL": 600,
            "Line": "default",
        }
        assert call.call_args.args[1].autoretry is False
    else:
        assert actual == {
            "Domain": "example.com",
            "SubDomain": "@",
            "RecordType": "TXT",
            "Value": "token",
            "TTL": 600,
            "RecordLine": "默认",
            "RecordLineId": "0",
        }
    cloud.delete_txt_record("example.com", "123")
    delete = request_map(operation(cloud, "delete").call_args.args[0])
    assert delete == (
        {"RecordId": "123"}
        if cloud.name == "aliyun"
        else {
            "Domain": "example.com",
            "RecordId": 123,
        }
    )


def test_create_requires_record_id(cloud):
    body = (
        SimpleNamespace(record_id=None)
        if cloud.name == "aliyun"
        else SimpleNamespace(RecordId=None)
    )
    operation(cloud, "create").return_value = (
        SimpleNamespace(body=body) if cloud.name == "aliyun" else body
    )
    with pytest.raises(errors.PluginError, match="record ID"):
        cloud.create_txt_record("example.com", "host", "token", 600)


@pytest.mark.parametrize("kind", ["zones", "records", "create", "delete"])
def test_permission_errors_are_not_swallowed_and_messages_are_redacted(cloud, kind):
    operation(cloud, kind).side_effect = sdk_error(cloud, "UnauthorizedOperation")
    with pytest.raises(errors.PluginError, match="UnauthorizedOperation") as exc:
        if kind == "zones":
            cloud.list_zones()
        elif kind == "records":
            cloud.list_txt_records("example.com", "host")
        elif kind == "create":
            cloud.create_txt_record("example.com", "host", "token", 600)
        else:
            cloud.delete_txt_record("example.com", "123")
    assert "sensitive" not in str(exc.value)


def test_already_deleted_record_is_success(cloud):
    code = (
        "InvalidRecordId.NotFound" if cloud.name == "aliyun" else "ResourceNotFound.NoDataOfRecord"
    )
    operation(cloud, "delete").side_effect = sdk_error(cloud, code)
    cloud.delete_txt_record("example.com", "123")


def test_tencent_empty_record_api_code(cloud):
    if cloud.name != "tencent":
        operation(cloud, "records").side_effect = sdk_error(
            cloud, "ResourceNotFound.NoDataOfRecord"
        )
        with pytest.raises(errors.PluginError):
            cloud.list_txt_records("example.com", "host")
        return
    operation(cloud, "records").side_effect = sdk_error(cloud, "ResourceNotFound.NoDataOfRecord")
    assert cloud.list_txt_records("example.com", "host") == []
