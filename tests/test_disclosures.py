import pytest

from tracker.worker.disclosures import parse_details, reached_countries


PAGE_ID = "120111814386760"


def details_payload(with_errors=True, with_transparency=True):
    node = {
        "advertiser": {"page": {"id": PAGE_ID}},
        "transparency_by_location": {
            "eu_transparency": {
                "eu_total_reach": 25,
                "age_country_gender_reach_breakdown": [
                    {
                        "country": {"country_code": "DE"},
                        "age_gender_breakdowns": [
                            {"age_range": "18-24", "male": 10, "female": 15, "unknown": 0}
                        ],
                    }
                ],
            },
            "uk_transparency": {
                "total_reach": 4,
                "age_country_gender_reach_breakdown": [
                    {
                        "country": {"country_code": "UK"},
                        "age_gender_breakdowns": [
                            {"age_range": "25-34", "male": 1, "female": 3, "unknown": 0}
                        ],
                    }
                ],
            },
        },
    }
    if not with_transparency:
        node["transparency_by_location"] = None
    payload = {"data": {"ad_library_main": {"ad_details": node}}}
    if with_errors:
        payload["errors"] = [
            {
                "message": "partial field failure",
                "code": 2334017,
                "path": [
                    "ad_library_main",
                    "ad_details",
                    "advertiser",
                    "ad_library_page_info",
                    "page_spend",
                ],
            }
        ]
    return payload


def test_parse_details_accepts_unrelated_partial_graphql_error():
    parsed = parse_details(details_payload(), PAGE_ID)

    assert reached_countries(parsed) == ["DE", "GB"]
    assert parsed["regions"][0]["reach"] == 25
    assert parsed["regions"][1]["reach"] == 4


def test_parse_details_rejects_response_without_ad_details():
    payload = {
        "errors": [{"message": "request failed", "path": ["ad_library_main", "ad_details"]}],
        "data": {"ad_library_main": {"ad_details": None}},
    }

    with pytest.raises(ValueError, match="Meta 详情接口返回错误"):
        parse_details(payload, PAGE_ID)


def test_parse_details_rejects_response_without_required_disclosure():
    with pytest.raises(ValueError, match="缺少地区披露字段"):
        parse_details(details_payload(with_transparency=False), PAGE_ID)
