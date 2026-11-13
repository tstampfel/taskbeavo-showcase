"""Seeds the launch service area, Dubrovnik (docs/PLAN.md: Dubrovnik first).

The polygon is a deliberately rough outline of the City of Dubrovnik on the mainland:
Orašac and Zaton in the west, Rijeka dubrovačka and Mokošica, Gruž, Lapad, the Old Town
and Ploče, out to Lokrum. Cavtat (Konavle) and Župa dubrovačka are outside. Staff refine
it on the map in the admin; this migration doesn't overwrite an edited area.
"""

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import migrations

# (longitude, latitude), WGS84, closed ring.
DUBROVNIK = (
    (17.950, 42.700),
    (17.970, 42.715),
    (18.050, 42.710),
    (18.120, 42.690),
    (18.140, 42.655),
    (18.135, 42.630),
    (18.110, 42.620),
    (18.060, 42.640),
    (17.990, 42.685),
    (17.950, 42.700),
)


def seed(apps, schema_editor):
    ServiceArea = apps.get_model("areas", "ServiceArea")
    ServiceArea.objects.get_or_create(
        slug="dubrovnik",
        defaults={
            "name": "Dubrovnik",
            "country_code": "HR",
            "active": True,
            "polygon": MultiPolygon(Polygon(DUBROVNIK), srid=4326),
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("areas", "ServiceArea").objects.filter(slug="dubrovnik").delete()


class Migration(migrations.Migration):
    dependencies = [("areas", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
