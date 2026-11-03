"""Importing this module registers every ORM model on BaseModel.metadata.

Alembic autogenerate (see migrations/env.py) relies on this to detect
schema changes. Add a line here whenever a new entity package gains models.
"""

import events.models  # noqa: F401
import venues.models  # noqa: F401
