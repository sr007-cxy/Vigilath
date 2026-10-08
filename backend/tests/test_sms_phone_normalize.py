"""Phone number normalization: domestic 11-digit numbers unchanged, international numbers normalized to E.164; each provider's number format.

On 2026-09-15 the login page added a country/area code selector (like the Qwen login page). Existing users' users.phone is 11
plain digits, and the domestic form must not change by a single character; international numbers are uniformly "+<country code><number>".
"""
import pytest

from geo.services import sms_service as sms


class Test国内号:
    @pytest.mark.parametrize("raw", ["13812345678", "+8613812345678", "008613812345678", "86 138 1234 5678", "138-1234-5678"])
    def test_各种写法都归一到11位(self, raw):
        assert sms.normalize_phone(raw) == "13812345678"

    @pytest.mark.parametrize("raw", ["12812345678", "1381234567", "138123456789", "", "abc"])
    def test_非法国内号(self, raw):
        assert sms.normalize_phone(raw) is None

    def test_不算国际号(self):
        assert not sms.is_international("13812345678")
        assert sms._e164("13812345678") == "+8613812345678"


class Test国际号:
    @pytest.mark.parametrize("raw,want", [
        ("+852 6123 4567", "+85261234567"),
        ("+1 (415) 555-0123", "+14155550123"),
        ("00852 61234567", "+85261234567"),
        ("+81 90-1234-5678", "+819012345678"),
    ])
    def test_归一到E164(self, raw, want):
        assert sms.normalize_phone(raw) == want
        assert sms.is_international(want)

    @pytest.mark.parametrize("raw", ["+1 234", "+0123456789", "+1234567890123456"])
    def test_非法国际号(self, raw):
        assert sms.normalize_phone(raw) is None

    def test_腾讯云要带加号的E164(self):
        assert sms._e164("+85261234567") == "+85261234567"

    def test_国际号用国际签名模板_没配就退回国内那对(self, monkeypatch):
        monkeypatch.setattr(sms.settings, "SMS_SIGN_NAME", "国内签名")
        monkeypatch.setattr(sms.settings, "SMS_TEMPLATE_ID", "SMS_1")
        monkeypatch.setattr(sms.settings, "SMS_INTL_SIGN_NAME", "")
        monkeypatch.setattr(sms.settings, "SMS_INTL_TEMPLATE_ID", "")
        assert sms._sms_sign_and_template("+85261234567") == ("国内签名", "SMS_1")
        monkeypatch.setattr(sms.settings, "SMS_INTL_SIGN_NAME", "Intl")
        monkeypatch.setattr(sms.settings, "SMS_INTL_TEMPLATE_ID", "SMS_9")
        assert sms._sms_sign_and_template("+85261234567") == ("Intl", "SMS_9")
        assert sms._sms_sign_and_template("13812345678") == ("国内签名", "SMS_1")
