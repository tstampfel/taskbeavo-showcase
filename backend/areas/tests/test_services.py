"""The service-area check (hard rule j) against the seeded Dubrovnik polygon."""

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from areas.models import ServiceArea
from areas.services import assert_in_service_area, point_from_lat_lng
from core.errors import ApiError

pytestmark = pytest.mark.django_db

DUBROVNIK_OLD_TOWN = (42.6407, 18.1077)
GRUZ = (42.6610, 18.0850)
OSIJEK = (45.5550, 18.6955)
VIENNA = (48.2082, 16.3738)


def assert_out_of_area(point: Point) -> None:
    with pytest.raises(ApiError) as raised:
        assert_in_service_area(point)
    assert raised.value.extensions == {"code": "OUT_OF_SERVICE_AREA"}


def square(lng: float, lat: float, size: float = 0.1) -> MultiPolygon:
    return MultiPolygon(
        Polygon(
            ((lng, lat), (lng + size, lat), (lng + size, lat + size), (lng, lat + size), (lng, lat))
        ),
        srid=4326,
    )


def test_dubrovnik_is_seeded_active_in_croatia() -> None:
    area = ServiceArea.objects.get(slug="dubrovnik")
    assert area.active
    assert area.country_code == "HR"


@pytest.mark.parametrize("lat_lng", [DUBROVNIK_OLD_TOWN, GRUZ])
def test_points_in_dubrovnik_return_the_area(lat_lng: tuple[float, float]) -> None:
    area = assert_in_service_area(point_from_lat_lng(*lat_lng))
    assert area.slug == "dubrovnik"


def test_a_croatian_point_outside_active_areas_is_out_of_area() -> None:
    assert_out_of_area(point_from_lat_lng(*OSIJEK))


def test_a_point_outside_croatia_is_out_of_area() -> None:
    assert_out_of_area(point_from_lat_lng(*VIENNA))


def test_an_active_area_outside_croatia_never_counts() -> None:
    ServiceArea.objects.create(
        name="Wien", slug="wien", country_code="AT", active=True, polygon=square(16.3, 48.15)
    )
    assert_out_of_area(point_from_lat_lng(*VIENNA))


def test_an_inactive_area_doesnt_count() -> None:
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)
    assert_out_of_area(point_from_lat_lng(*DUBROVNIK_OLD_TOWN))


def test_a_point_on_the_boundary_is_covered() -> None:
    # ST_Covers, unlike ST_Contains, includes the boundary.
    ServiceArea.objects.create(
        name="Osijek", slug="osijek", country_code="HR", active=True, polygon=square(18.6, 45.5)
    )
    area = assert_in_service_area(Point(18.6, 45.55, srid=4326))
    assert area.slug == "osijek"


def test_a_point_in_another_srid_is_transformed() -> None:
    point = point_from_lat_lng(*DUBROVNIK_OLD_TOWN)
    point.transform(3857)
    assert assert_in_service_area(point).slug == "dubrovnik"


def test_a_point_without_srid_is_read_as_wgs84() -> None:
    lat, lng = DUBROVNIK_OLD_TOWN
    assert assert_in_service_area(Point(lng, lat)).slug == "dubrovnik"


@pytest.mark.parametrize(("lat", "lng"), [(91, 18), (-91, 18), (42, 181), (42, -181)])
def test_point_from_lat_lng_refuses_impossible_coordinates(lat: float, lng: float) -> None:
    with pytest.raises(ValueError):
        point_from_lat_lng(lat, lng)


def test_point_from_lat_lng_orders_x_as_longitude() -> None:
    point = point_from_lat_lng(42.64, 18.11)
    assert (point.x, point.y, point.srid) == (18.11, 42.64, 4326)
