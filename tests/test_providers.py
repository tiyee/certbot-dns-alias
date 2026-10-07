import json
import logging
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alibabacloud_alidns20150109 import models as aliyun_models
from certbot import errors
from Tea.exceptions import TeaException
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.dnspod.v20210323 import models as tencent_models

from certbot_dns_alias.providers.aliyun import AliyunDNSProvider
from certbot_dns_alias.providers.base import ZoneRouter
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


def zones_response(provider, names, total, *, page=1):
    if provider.name == "aliyun":
        return SimpleNamespace(
            body=dto(
                aliyun_models.DescribeDomainsResponseBody,
                {
                    "TotalCount": total,
                    "PageNumber": page,
                    "PageSize": provider.page_size,
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


def records_response(provider, items, total, *, page=1):
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
                    "PageNumber": page,
                    "PageSize": provider.page_size,
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
        zones_response(cloud, ["sub.example.co.uk"], 3, page=2),
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
        records_response(cloud, [record(3, value="second-value")], 3, page=2),
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
def test_permission_errors_are_not_swallowed_and_messages_are_redacted(cloud, kind, caplog):
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
    assert exc.value.code == "UnauthorizedOperation"
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__
    assert "sensitive" not in "".join(
        traceback.format_exception(type(exc.value), exc.value, exc.value.__traceback__)
    )
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("certbot_dns_alias.tests").debug(
            "Provider operation failed",
            exc_info=(type(exc.value), exc.value, exc.value.__traceback__),
        )
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize("code", [None, "unsafe code\nsecret", "x" * 81])
def test_invalid_sdk_error_codes_are_not_displayed(cloud, code):
    operation(cloud, "create").side_effect = sdk_error(cloud, code)
    with pytest.raises(errors.PluginError, match="UnknownError") as caught:
        cloud.create_txt_record("example.com", "host", "token", 600)
    assert caught.value.code == "UnknownError"
    assert "sensitive" not in str(caught.value)


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


def collection_response(cloud, kind, ids, total, *, page=1):
    if kind == "zones":
        return zones_response(cloud, [f"zone{item}.example.com" for item in ids], total, page=page)
    return records_response(cloud, [record(item) for item in ids], total, page=page)


def list_collection(cloud, kind):
    return cloud.list_zones() if kind == "zones" else cloud.list_txt_records("example.com", "host")


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize("total", [10, 101])
def test_empty_production_size_page_never_establishes_completeness(cloud, kind, total):
    cloud.page_size = 100
    operation(cloud, kind).return_value = collection_response(cloud, kind, [], total)
    with pytest.raises(errors.PluginError, match="incomplete"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize("first_count", [1, 100])
def test_truncated_production_size_collection_fails(cloud, kind, first_count):
    cloud.page_size = 100
    call = operation(cloud, kind)
    call.side_effect = [
        collection_response(cloud, kind, list(range(1, first_count + 1)), 101),
        collection_response(cloud, kind, [], 101, page=2),
    ]
    with pytest.raises(errors.PluginError, match="incomplete"):
        list_collection(cloud, kind)
    # Page-number pagination must not skip the missing part of a short page.
    assert call.call_count == (1 if cloud.name == "aliyun" and first_count == 1 else 2)


@pytest.mark.parametrize("kind", ["zones", "records"])
def test_complete_production_size_collection(cloud, kind):
    cloud.page_size = 100
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, list(range(1, 101)), 101),
        collection_response(cloud, kind, [101], 101, page=2),
    ]
    assert len(list_collection(cloud, kind)) == 101


@pytest.mark.parametrize("kind", ["zones", "records"])
def test_short_page_requires_complete_receipt(cloud, kind):
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, [1], 3),
        collection_response(cloud, kind, [2, 3], 3, page=2),
    ]
    if cloud.name == "aliyun":
        with pytest.raises(errors.PluginError, match="incomplete"):
            list_collection(cloud, kind)
    else:
        assert len(list_collection(cloud, kind)) == 3
        maps = [request_map(call.args[0]) for call in operation(cloud, kind).call_args_list]
        assert [item["Offset"] for item in maps] == [0, 1]


@pytest.mark.parametrize("kind", ["zones", "records"])
def test_truncated_nonempty_final_page_fails(cloud, kind):
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, [1, 2], 4),
        collection_response(cloud, kind, [3], 4, page=2),
        collection_response(cloud, kind, [], 4, page=3),
    ]
    with pytest.raises(errors.PluginError, match="incomplete"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize("total", [None, -1, True, 1.5, "1"])
def test_malformed_pagination_totals_fail(cloud, kind, total):
    operation(cloud, kind).return_value = collection_response(cloud, kind, [1], total)
    with pytest.raises(errors.PluginError, match="invalid pagination total"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize("total", [0, 2, 4])
def test_changing_totals_fail(cloud, kind, total):
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, [1, 2], 3),
        collection_response(cloud, kind, [3], total, page=2),
    ]
    with pytest.raises(errors.PluginError, match="changed.*total"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize("pages", [[[1, 1]], [[1, 2], [1]], [[1, 2], [1, 2]]])
def test_duplicate_identities_do_not_count_toward_completeness(cloud, kind, pages):
    total = sum(len(page) for page in pages)
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, ids, total, page=index)
        for index, ids in enumerate(pages, 1)
    ]
    with pytest.raises(errors.PluginError, match="duplicate"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize(("ids", "total"), [([1], 0), ([1, 2], 1), ([1, 2, 3], 3)])
def test_pagination_overshoots_fail(cloud, kind, ids, total):
    operation(cloud, kind).return_value = collection_response(cloud, kind, ids, total)
    with pytest.raises(errors.PluginError, match="too many"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("identity", [None, "", "invalid zone", "*.example.com"])
def test_invalid_zone_identities_fail(cloud, identity):
    operation(cloud, "zones").return_value = zones_response(cloud, [identity], 1)
    with pytest.raises(errors.PluginError, match="invalid zone identity"):
        cloud.list_zones()


@pytest.mark.parametrize(
    "names", [["Example.COM", "example.com."], ["例子.测试", "xn--fsqu00a.xn--0zwm56d"]]
)
def test_equivalent_zone_identities_are_duplicates(cloud, names):
    operation(cloud, "zones").return_value = zones_response(cloud, names, 2)
    with pytest.raises(errors.PluginError, match="duplicate"):
        cloud.list_zones()


@pytest.mark.parametrize("identity", [None, "", "bad", 0, -1, False, "0", "-1"])
def test_invalid_record_ids_fail_even_when_record_would_be_filtered(cloud, identity):
    operation(cloud, "records").return_value = records_response(
        cloud, [record(identity, name="unrelated")], 1
    )
    with pytest.raises(errors.PluginError, match="invalid record ID"):
        cloud.list_txt_records("example.com", "host")


def test_incomplete_zone_discovery_is_never_cached(cloud):
    call = operation(cloud, "zones")
    call.side_effect = [
        zones_response(cloud, ["example.com", "other.net"], 3),
        zones_response(cloud, [], 3, page=2),
        zones_response(cloud, ["example.com", "other.net"], 3),
        zones_response(cloud, ["sub.example.com"], 3, page=2),
    ]
    router = ZoneRouter({cloud.name: cloud})
    with pytest.raises(errors.PluginError, match="incomplete"):
        router.find("host.sub.example.com")
    assert router._loaded_zones is None
    assert router.find("host.sub.example.com") == (cloud, "sub.example.com")
    assert call.call_count == 4


def test_incomplete_txt_query_prevents_writes_and_cleanup_state(cloud, authenticator):
    authenticator._router = ZoneRouter({cloud.name: cloud}, {cloud.name: ["delegate.example.net"]})
    operation(cloud, "records").side_effect = [
        records_response(cloud, [record(1), record(2)], 3),
        records_response(cloud, [], 3, page=2),
    ]
    with pytest.raises(errors.PluginError, match="incomplete"):
        authenticator._perform("example.com", "_acme-challenge.example.com", "new-value")
    operation(cloud, "create").assert_not_called()
    assert not authenticator._leases
    assert not authenticator._challenges


@pytest.mark.parametrize("cloud", ["aliyun"], indirect=True)
@pytest.mark.parametrize("kind", ["zones", "records"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page_number", None),
        ("page_number", 0),
        ("page_number", True),
        ("page_number", "1"),
        ("page_number", 2),
        ("page_size", None),
        ("page_size", 0),
        ("page_size", True),
        ("page_size", "2"),
        ("page_size", 3),
    ],
)
def test_aliyun_rejects_invalid_page_metadata(cloud, kind, field, value):
    response = collection_response(cloud, kind, [1], 1)
    setattr(response.body, field, value)
    operation(cloud, kind).return_value = response
    with pytest.raises(errors.PluginError, match="page metadata"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("cloud", ["aliyun"], indirect=True)
@pytest.mark.parametrize("kind", ["zones", "records"])
def test_aliyun_requires_the_requested_page_number(cloud, kind):
    operation(cloud, kind).side_effect = [
        collection_response(cloud, kind, [1, 2], 3),
        collection_response(cloud, kind, [3], 3, page=1),
    ]
    with pytest.raises(errors.PluginError, match="page metadata"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("cloud", ["aliyun"], indirect=True)
def test_aliyun_numeric_record_id_variants_are_duplicates(cloud):
    operation(cloud, "records").return_value = records_response(
        cloud, [record("001"), record("1")], 2
    )
    with pytest.raises(errors.PluginError, match="duplicate"):
        cloud.list_txt_records("example.com", "host")


@pytest.mark.parametrize("cloud", ["tencent"], indirect=True)
@pytest.mark.parametrize("value", [None, -1, 0, True, "1", 2])
def test_tencent_validates_unfiltered_list_count(cloud, value):
    response = records_response(cloud, [record(1, name="unrelated")], 1)
    response.RecordCountInfo.ListCount = value
    operation(cloud, "records").return_value = response
    with pytest.raises(errors.PluginError, match="record count metadata"):
        cloud.list_txt_records("example.com", "host")


@pytest.mark.parametrize("cloud", ["tencent"], indirect=True)
@pytest.mark.parametrize("kind", ["zones", "records"])
def test_tencent_requires_count_metadata(cloud, kind):
    response = collection_response(cloud, kind, [], 0)
    setattr(response, "DomainCountInfo" if kind == "zones" else "RecordCountInfo", None)
    operation(cloud, kind).return_value = response
    with pytest.raises(errors.PluginError, match="invalid"):
        list_collection(cloud, kind)


@pytest.mark.parametrize("cloud", ["tencent"], indirect=True)
def test_tencent_rejects_string_record_ids(cloud):
    operation(cloud, "records").return_value = records_response(cloud, [record("1")], 1)
    with pytest.raises(errors.PluginError, match="invalid record ID"):
        cloud.list_txt_records("example.com", "host")


@pytest.mark.parametrize("cloud", ["tencent"], indirect=True)
@pytest.mark.parametrize("filtered", [False, True])
def test_tencent_late_empty_api_code_is_incomplete(cloud, authenticator, filtered):
    call = operation(cloud, "records")
    call.side_effect = [
        records_response(
            cloud, [record(1, name="other" if filtered else "customer"), record(2)], 3
        ),
        sdk_error(cloud, "ResourceNotFound.NoDataOfRecord"),
    ]
    authenticator._router = ZoneRouter({cloud.name: cloud}, {cloud.name: ["delegate.example.net"]})
    with pytest.raises(errors.PluginError, match="incomplete"):
        authenticator._perform("example.com", "_acme-challenge.example.com", "new-value")
    operation(cloud, "create").assert_not_called()
    assert not authenticator._leases
    assert not authenticator._challenges
