"""The Expo push client against a fake HTTP layer."""

from typing import Any

import pytest
import requests
from django.test import override_settings

from notifications import expo


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeHttp:
    """Records every POST and answers each with the next of `responses`."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[FakeResponse] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(expo.requests, "post", fake.post)
    return fake


def message(n: int) -> expo.PushMessage:
    return expo.PushMessage(to=f"ExponentPushToken[{n:08d}]", title="T", body="B", data={"r": n})


def test_send_returns_tickets_in_order_and_batches_by_100(http: FakeHttp) -> None:
    http.responses.extend(
        [
            FakeResponse(200, {"data": [{"status": "ok", "id": f"id-{i}"} for i in range(100)]}),
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "status": "error",
                            "message": "not registered",
                            "details": {"error": "DeviceNotRegistered"},
                        }
                    ]
                },
            ),
        ]
    )

    tickets = expo.send([message(i) for i in range(101)])

    assert [len(call["json"]) for call in http.calls] == [100, 1]
    assert http.calls[0]["url"] == expo.SEND_URL
    assert http.calls[0]["timeout"] == 10
    assert http.calls[0]["json"][0]["data"] == {"r": 0}
    assert tickets[0] == expo.Result(ok=True, id="id-0")
    assert tickets[100] == expo.Result(ok=False, error=expo.DEVICE_NOT_REGISTERED)


@override_settings(EXPO_ACCESS_TOKEN="expo-access-not-secret")
def test_the_access_token_is_sent_when_configured(http: FakeHttp) -> None:
    http.responses.append(FakeResponse(200, {"data": [{"status": "ok", "id": "x"}]}))
    expo.send([message(1)])
    assert http.calls[0]["headers"]["Authorization"] == "Bearer expo-access-not-secret"


def test_no_authorization_header_without_a_token(http: FakeHttp) -> None:
    http.responses.append(FakeResponse(200, {"data": [{"status": "ok", "id": "x"}]}))
    expo.send([message(1)])
    assert "Authorization" not in http.calls[0]["headers"]


def test_a_request_level_error_raises_with_expos_code_only(http: FakeHttp) -> None:
    http.responses.append(
        FakeResponse(400, {"errors": [{"code": "PUSH_TOO_MANY_RECEIPTS", "message": "..."}]})
    )
    with pytest.raises(expo.ExpoError, match=r"^PUSH_TOO_MANY_RECEIPTS$"):
        expo.send([message(1)])


def test_a_server_error_without_a_body_raises(http: FakeHttp) -> None:
    http.responses.append(FakeResponse(502, ValueError("not json")))
    with pytest.raises(requests.HTTPError):
        expo.send([message(1)])


def test_a_ticket_count_mismatch_raises(http: FakeHttp) -> None:
    http.responses.append(FakeResponse(200, {"data": []}))
    with pytest.raises(expo.ExpoError):
        expo.send([message(1)])


def test_receipts_by_ticket_id(http: FakeHttp) -> None:
    http.responses.append(
        FakeResponse(
            200,
            {
                "data": {
                    "a": {"status": "ok"},
                    "b": {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                }
            },
        )
    )

    found = expo.receipts(["a", "b", "c"])

    assert http.calls[0]["url"] == expo.RECEIPTS_URL
    assert http.calls[0]["json"] == {"ids": ["a", "b", "c"]}
    assert found == {
        "a": expo.Result(ok=True),
        "b": expo.Result(ok=False, error=expo.DEVICE_NOT_REGISTERED),
    }
