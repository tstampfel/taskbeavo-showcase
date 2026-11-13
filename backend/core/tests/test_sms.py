"""SMS backends: the test outbox, the console and Infobip against a fake HTTP layer."""

import logging
from typing import Any

import pytest
import requests
from django.test import override_settings

from core import infobip, sms
from core.sms import SmsMessage, SmsSendError, mask_phone

MESSAGE = SmsMessage(to="+385921234567", text="123456 je vaš TaskBeavo kôd.", sender="TaskBeavo")
INFOBIP = {
    "SMS_BACKEND": "core.infobip.InfobipSmsBackend",
    "INFOBIP_BASE_URL": "https://example.api.infobip.com",
    "INFOBIP_API_KEY": "test-key",
    "INFOBIP_SENDER": "TaskBeavo",
}


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = 200
        self.group = "PENDING"
        self.error: Exception | None = None

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        body = {"messages": [{"to": "385921234567", "status": {"groupName": self.group}}]}
        return FakeResponse(self.status_code, body)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(infobip.requests, "post", fake.post)
    return fake


@pytest.fixture(autouse=True)
def _fresh_backends():
    sms._backend.cache_clear()
    sms.outbox.clear()
    yield
    sms._backend.cache_clear()
    sms.outbox.clear()


def test_mask_shows_country_and_last_digits_only() -> None:
    assert mask_phone("+385921234567") == "+385 ••• 567"
    assert mask_phone("+4915123456789") == "+49 ••• 789"
    assert mask_phone("garbage") == "•••"


def test_tests_keep_messages_in_the_outbox() -> None:
    sms.send_sms(MESSAGE.to, MESSAGE.text, MESSAGE.sender)

    assert sms.outbox == [MESSAGE]


@override_settings(SMS_BACKEND="core.sms.ConsoleSmsBackend")
def test_console_masks_the_number(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        sms.send_sms(MESSAGE.to, MESSAGE.text, MESSAGE.sender)

    assert "123456" in caplog.text
    assert "921234567" not in caplog.text


def test_sender_is_alphanumeric_except_for_north_america() -> None:
    assert sms.sender_for("HR") == "TaskBeavo"
    assert sms.sender_for("AR") == "TaskBeavo"
    # US and Canadian carriers drop alphanumeric senders: no SMS until one is configured.
    assert sms.sender_for("US") is None
    assert sms.sender_for("CA") is None
    with override_settings(INFOBIP_SENDER_BY_REGION={"US": "+15550100"}):
        assert sms.sender_for("US") == "+15550100"
        assert sms.sender_for("CA") is None


@override_settings(**INFOBIP)
def test_infobip_sends_one_message(http: FakeHttp) -> None:
    sms.send_sms(MESSAGE.to, MESSAGE.text, MESSAGE.sender)

    [call] = http.calls
    assert call["url"] == "https://example.api.infobip.com/sms/2/text/advanced"
    assert call["headers"]["Authorization"] == "App test-key"
    assert call["json"] == {
        "messages": [
            {
                "from": "TaskBeavo",
                "destinations": [{"to": "385921234567"}],
                "text": MESSAGE.text,
            }
        ]
    }
    assert call["timeout"] == 10


@pytest.mark.parametrize(
    ("status_code", "group", "error"),
    [
        (401, "PENDING", None),
        (200, "REJECTED", None),
        (200, "PENDING", requests.ConnectionError("https://x/385921234567 refused")),
    ],
)
@override_settings(**INFOBIP)
def test_infobip_failures_raise_without_the_number(
    http: FakeHttp, status_code: int, group: str, error: Exception | None
) -> None:
    http.status_code, http.group, http.error = status_code, group, error

    with pytest.raises(SmsSendError) as raised:
        sms.send_sms(MESSAGE.to, MESSAGE.text, MESSAGE.sender)

    assert "921234567" not in str(raised.value)
    assert raised.value.__cause__ is None
