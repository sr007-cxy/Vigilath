import json
from types import SimpleNamespace

from geo.api.publish_dispatch import _ctrip_location, _ctrip_topic
from geo.models.ai_telemetry import BrandProfile


def test_ctrip_location_is_preserved_in_profile_dump():
    profile = BrandProfile(ctrip_location={
        "name": "西湖风景名胜区",
        "city": "杭州",
        "address": "西湖区龙井路 1 号",
    })

    assert profile.model_dump()["ctrip_location"] == {
        "name": "西湖风景名胜区",
        "city": "杭州",
        "address": "西湖区龙井路 1 号",
    }


def test_ctrip_location_defaults_to_empty_values_for_legacy_profiles():
    assert BrandProfile().model_dump()["ctrip_location"] == {
        "name": "",
        "city": "",
        "address": "",
    }


def test_ctrip_location_snapshot_uses_structured_profile_fields():
    topic = SimpleNamespace(profile_json=json.dumps({
        "ctrip_location": {"name": " 西湖 ", "city": " 杭州 ", "address": " 龙井路1号 "},
    }))

    assert _ctrip_location(topic) == {
        "name": "西湖", "city": "杭州", "address": "龙井路1号",
    }


def test_ctrip_topic_uses_brand_profile_name():
    topic = SimpleNamespace(profile_json=json.dumps({
        "profile_name": " 香格臻品・踏雪鸡火塘火锅店 ",
        "company_short_name": "踏雪鸡火塘火锅店",
    }))

    assert _ctrip_topic(topic) == "香格臻品・踏雪鸡火塘火锅店"
