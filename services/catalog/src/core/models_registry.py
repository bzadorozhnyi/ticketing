"""Importing this module registers every ORM model on BaseModel.metadata.

Alembic autogenerate (see migrations/env.py) relies on this to detect
schema changes. Add a line here whenever a new models module appears.
"""

import events.models.event  # noqa: F401
import events.models.section_price  # noqa: F401
import venues.models.seat  # noqa: F401
import venues.models.section  # noqa: F401
import venues.models.venue  # noqa: F401
