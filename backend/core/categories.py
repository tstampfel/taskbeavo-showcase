"""Task categories (docs/PLAN.md: five launch categories in Dubrovnik).

Shared by the waitlist (areas) and, from BEAV-85, by tasks and helper profiles. Rides,
medical and personal care are deliberately not categories (trust-safety).
"""

from enum import Enum

import strawberry
from django.db import models


class TaskCategory(models.TextChoices):
    GARDEN = "GARDEN", "Garden"
    ERRANDS = "ERRANDS", "Grocery and pharmacy runs"
    REPAIRS = "REPAIRS", "Small repairs"
    CLEANING = "CLEANING", "Light cleaning"
    CHECK_IN = "CHECK_IN", "Check-in visits"


@strawberry.enum(name="TaskCategory", description="What kind of help a task asks for.")
class TaskCategoryEnum(Enum):
    GARDEN = TaskCategory.GARDEN.value
    ERRANDS = TaskCategory.ERRANDS.value
    REPAIRS = TaskCategory.REPAIRS.value
    CLEANING = TaskCategory.CLEANING.value
    CHECK_IN = TaskCategory.CHECK_IN.value
