"""Pure functions of the recommendation chain -- size bucketing, count clamping, public contact info.

These three directly determine the recommendations users see: wrong bucket -> wrong match score; wrong clamp -> "100 by default" changes;
wrong contact assembly -> may expose unsubscribed/unreachable contacts (a compliance problem).

Run:
    venv/bin/python -m pytest tests/sales_agent/test_recommend_helpers.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.constants import DEFAULT_RECOMMENDATIONS, MAX_RECOMMENDATIONS
from agents.sales_agent.service import (
    _bucket_for_employee_count,
    _clamp_limit,
    _company_size_bucket_distance,
    _normalize_company_size_bucket,
    _preferred_target_size_buckets_for_seller,
    _public_contact_text,
)


# --- Employee count -> bucket ---

@pytest.mark.parametrize("count,expected", [
    (1, "1-10 名员工"),
    (10, "1-10 名员工"),
    (11, "11-100 名员工"),
    (100, "11-100 名员工"),
    (101, "101-500 名员工"),
    (500, "101-500 名员工"),
    (501, "501-1000 名员工"),
    (1000, "501-1000 名员工"),
    (1001, "1001+ 名员工"),
    (99999, "1001+ 名员工"),
    (0, ""),
])
def test_bucket_for_employee_count(count, expected):
    assert _bucket_for_employee_count(count) == expected


# --- Free text -> bucket ---

@pytest.mark.parametrize("text,expected", [
    ("101-500 名员工", "101-500 名员工"),
    ("50人", "11-100 名员工"),
    ("200-300人", "101-500 名员工"),
    ("1000人以上", "1001+ 名员工"),
    ("小微企业", "1-10 名员工"),
    ("创业团队", "1-10 名员工"),
    ("中小企业", "11-100 名员工"),
    ("中大型", "501-1000 名员工"),
    ("集团型", "1001+ 名员工"),
    ("待补充", ""),
    ("", ""),
    ("不限规模", ""),
])
def test_normalize_company_size_bucket(text, expected):
    assert _normalize_company_size_bucket(text) == expected


@pytest.mark.parametrize("label", [
    "1-10 名员工", "11-100 名员工", "101-500 名员工", "501-1000 名员工", "1001+ 名员工",
])
def test_official_options_round_trip(label):
    """The five options of the target size card must normalize unchanged.

    Regression: "1-10" is a substring of "11-100" / "501-1000", and substring matching once ran before exact matching,
    swallowing those two options into "1-10 employees" and miscomputing match scores overall (fixed 2026-08-22).
    """
    assert _normalize_company_size_bucket(label) == label


def test_bucket_distance():
    assert _company_size_bucket_distance("1-10 名员工", "1-10 名员工") == 0
    assert _company_size_bucket_distance("1-10 名员工", "11-100 名员工") == 1
    assert _company_size_bucket_distance("1-10 名员工", "1001+ 名员工") == 4
    assert _company_size_bucket_distance("不认识", "1-10 名员工") is None


def test_preferred_target_buckets():
    assert _preferred_target_size_buckets_for_seller("101-500 名员工") == [
        "101-500 名员工", "501-1000 名员工", "11-100 名员工",
    ]
    assert _preferred_target_size_buckets_for_seller("不限规模") == []


# --- Recommendation count clamping ---

def test_clamp_limit_default_is_100():
    assert _clamp_limit(None) == DEFAULT_RECOMMENDATIONS == 100
    assert _clamp_limit(0) == 100


def test_clamp_limit_bounds():
    assert _clamp_limit(30) == 30
    assert _clamp_limit(999) == MAX_RECOMMENDATIONS
    assert _clamp_limit(-5) == 1


def test_clamp_limit_survives_garbage():
    assert _clamp_limit("abc") == DEFAULT_RECOMMENDATIONS


# --- Public contact info ---

def _contact(**kw):
    base = {"email": "", "phone": "", "contact_form_url": "", "consent_status": "public", "is_reachable": True}
    base.update(kw)
    return base


def test_contact_text_joins_public_values():
    company = {"contacts": [_contact(email="sales@example.com", phone="13800000000")]}
    assert _public_contact_text(company) == "sales@example.com、13800000000"


@pytest.mark.parametrize("consent", ["unsubscribed", "blacklisted", "do_not_contact"])
def test_contact_text_skips_blocked_consent(consent):
    company = {"contacts": [_contact(email="no@example.com", consent_status=consent)]}
    assert _public_contact_text(company) == "未发现公开公司级联系方式"


def test_contact_text_skips_unreachable():
    company = {"contacts": [_contact(email="no@example.com", is_reachable=False)]}
    assert _public_contact_text(company) == "未发现公开公司级联系方式"


def test_contact_text_form_urls():
    one = {"contacts": [_contact(contact_form_url="https://a.com/contact")]}
    assert _public_contact_text(one) == "官网联系表单"
    two = {"contacts": [
        _contact(contact_form_url="https://a.com/contact"),
        _contact(contact_form_url="https://a.com/support"),
    ]}
    assert _public_contact_text(two) == "官网联系表单 ×2"


def test_contact_text_falls_back_to_company_level():
    assert _public_contact_text({"contacts": [], "email": "hi@example.com"}) == "hi@example.com"


def test_contact_text_empty():
    assert _public_contact_text({}) == "未发现公开公司级联系方式"
