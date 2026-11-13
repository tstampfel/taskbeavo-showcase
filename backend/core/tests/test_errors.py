from graphql import build_schema as build_sdl_schema

from config import graphql_limits
from config.schema import build_schema
from core import realtime
from core.errors import ErrorCode


def test_every_error_code_is_documented_in_the_schema() -> None:
    sdl = build_sdl_schema(str(build_schema(app_env="test")))
    enum = sdl.get_type("ErrorCode")

    assert enum is not None
    assert enum.description  # type: ignore[union-attr]
    values = enum.values  # type: ignore[union-attr]
    assert set(values) == {code.name for code in ErrorCode}
    for name, value in values.items():
        assert value.description, f"ErrorCode.{name} needs a description"


def test_codes_raised_by_the_api_are_error_codes() -> None:
    assert ErrorCode.RATE_LIMITED.name == graphql_limits.RATE_LIMITED
    assert ErrorCode.MUTATIONS_REQUIRE_HTTP.name == graphql_limits.MUTATIONS_REQUIRE_HTTP
    assert ErrorCode.UNAUTHENTICATED.name == realtime.UNAUTHENTICATED


def test_error_code_names_equal_their_values() -> None:
    # Clients translate `errors.<CODE>`; the wire value is the key.
    for code in ErrorCode:
        assert code.name == code.value
