"""Who should be charged -- ops acting on someone's behalf is never charged.

When ops clicks "publish", the platform pays itself (acting for customers, internal placements). Charging current_user literally
would drain ops' wallet with customers' placements and book the charge to the wrong person.

This directly decides the launch steps: **existing accounts don't need bulk top-ups** -- ops publishes as usual,
and regular users with insufficient balance get a 402 prompting top-up.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo.api import rwj_channel


def _user(role: str):
    return SimpleNamespace(id=1, role=role, email="x@y.z", name="x")


@pytest.fixture(autouse=True)
def charge_on(monkeypatch):
    monkeypatch.setenv("RWJ_CHARGE_ENABLED", "1")


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_staff_never_pays(role):
    assert rwj_channel._should_charge(_user(role)) is False


def test_normal_user_pays():
    assert rwj_channel._should_charge(_user("user")) is True


def test_no_user_no_charge():
    """Background threads / scheduled jobs have no current_user -- can't charge someone out of thin air."""
    assert rwj_channel._should_charge(None) is False


def test_switch_off_disables_everyone(monkeypatch):
    monkeypatch.setenv("RWJ_CHARGE_ENABLED", "0")
    assert rwj_channel._should_charge(_user("user")) is False


def test_charge_is_on_by_default(monkeypatch):
    """On by default. Off = users publish free, platform pays; only for temporary use while troubleshooting."""
    monkeypatch.delenv("RWJ_CHARGE_ENABLED", raising=False)
    assert rwj_channel._charge_enabled() is True
