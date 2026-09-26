"""Bring the database up from nothing: schema, source data, rules.

Called at application startup. Idempotent by brute force -- it drops and recreates
everything -- which is appropriate precisely because the database holds no state
that does not come from the CSVs or the YAML.
"""

from __future__ import annotations

import logging
import time

from app.db.ingest import IngestReport, ingest_all
from app.db.seed import seed_reference
from app.db.session import reset_schema, session_scope
from app.rules.loader import get_rules
from app.rules.schema import RulesBundle

logger = logging.getLogger(__name__)


def rebuild_database() -> tuple[RulesBundle, IngestReport]:
    started = time.perf_counter()

    # Load and validate rules BEFORE touching the database: a bad config should
    # abort startup with a clear error, not after a partial rebuild.
    rules = get_rules()

    reset_schema()
    with session_scope() as session:
        report = ingest_all(session)
        seed_reference(session, rules)

    elapsed = (time.perf_counter() - started) * 1000
    logger.info(
        "database rebuilt in %.0fms (rules_version=%s)", elapsed, rules.version
    )
    return rules, report
