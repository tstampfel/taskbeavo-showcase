"""The Resend email backend against a fake HTTP layer."""

from typing import Any

import pytest
import requests
from django.core.mail import EmailMessage, get_connection
from django.test import override_settings

from core import resend
from core.email import Attachment, RenderedEmail, send_email

EMAIL = RenderedEmail(locale="de", subject="Betreff", html="<p>Hallo</p>", text="Hallo\n")
BACKEND = "core.resend.ResendEmailBackend"


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict[str, str]:
        return {"id": "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"}


class FakeHttp:
    """Records every POST and answers with `status_code`."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status_code = 200

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.status_code)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(resend.requests, "post", fake.post)
    return fake


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_not_secret")
def test_send_email_posts_the_message_with_its_idempotency_key(http: FakeHttp) -> None:
    send_email("ana@example.com", EMAIL, idempotency_key="outbox:1:email:v1:user:2")

    (call,) = http.calls
    assert call["url"] == resend.API_URL
    assert call["headers"]["Authorization"] == "Bearer re_test_not_secret"
    assert call["headers"]["Idempotency-Key"] == "outbox:1:email:v1:user:2"
    assert call["timeout"] == 10
    assert call["json"] == {
        "from": "TaskBeavo <no-reply@taskbeavo.hr>",
        "to": ["ana@example.com"],
        "subject": "Betreff",
        "text": "Hallo\n",
        "html": "<p>Hallo</p>",
        "headers": {"Content-Language": "de"},
    }


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_not_secret")
def test_attachments_are_sent_base64_encoded(http: FakeHttp) -> None:
    policy = Attachment("policy-de.txt", "Widerrufsbelehrung: Sie können …")

    send_email("ana@example.com", EMAIL, attachments=[policy])

    assert http.calls[0]["json"]["attachments"] == [
        {
            "filename": "policy-de.txt",
            "content": "V2lkZXJydWZzYmVsZWhydW5nOiBTaWUga8O2bm5lbiDigKY=",
        }
    ]


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_not_secret")
def test_no_idempotency_header_without_a_key(http: FakeHttp) -> None:
    send_email("ana@example.com", EMAIL)
    assert "Idempotency-Key" not in http.calls[0]["headers"]


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_not_secret")
def test_a_refused_send_raises_with_the_status_only(http: FakeHttp) -> None:
    http.status_code = 422
    with pytest.raises(resend.ResendError, match=r"^422$"):
        send_email("ana@example.com", EMAIL)


@override_settings(RESEND_API_KEY="re_test_not_secret")
def test_fail_silently_counts_only_sent_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(url: str, **kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(resend.requests, "post", broken)
    connection = get_connection(BACKEND, fail_silently=True)
    message = EmailMessage("s", "b", "a@example.com", ["b@example.com"])

    assert connection.send_messages([message]) == 0


def test_send_email_returns_the_id_resend_gave_the_email(http: FakeHttp) -> None:
    with override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_not_secret"):
        message_id = send_email("ana@example.com", EMAIL)

    assert message_id == "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"
