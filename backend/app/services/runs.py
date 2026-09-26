"""Get-or-create an engine run.

A run is identified by (as_of_date, rules_version), so the same question asked
twice is answered from storage rather than recomputed. That identity is also what
makes the as_of date picker in the UI cheap: the first request for a date pays for
the evaluation, every later one is a read.

This is the seam where the deployment model would change without the engine
noticing. Here the API evaluates on demand. In production a scheduled job would
write the nightly run and the API would only ever read -- ``ensure_run`` becomes a
lookup that raises instead of computing, and nothing downstream changes.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repository import find_run, load_patient_records, persist_run
from app.engine.run import evaluate
from app.rules.loader import get_rules
from app.rules.schema import RulesBundle

logger = logging.getLogger(__name__)

# Serialises run creation within this process. Two requests arriving together for
# a new date would otherwise both evaluate the population and race to insert.
# The UNIQUE constraint on (as_of_date, rules_version) is the real guarantee --
# this lock just avoids doing the work twice in the common case. Across multiple
# processes the constraint does the job and the loser falls back to reading.
_creation_lock = threading.Lock()


def ensure_run(session: Session, as_of: date, rules: RulesBundle | None = None) -> int:
    rules = rules or get_rules()

    run_id = find_run(session, as_of, rules.version)
    if run_id is not None:
        return run_id

    with _creation_lock:
        # Re-check inside the lock: another request may have created it while we
        # waited.
        run_id = find_run(session, as_of, rules.version)
        if run_id is not None:
            return run_id

        started = time.perf_counter()
        records = load_patient_records(session)
        result = evaluate(records, rules, as_of)
        duration_ms = int((time.perf_counter() - started) * 1000)

        try:
            run_id = persist_run(session, result, duration_ms, len(records))
            session.commit()
        except IntegrityError:
            # Another process won the race. Its results are equivalent to ours by
            # definition -- same data, same rules, same date -- so use them.
            session.rollback()
            run_id = find_run(session, as_of, rules.version)
            if run_id is None:
                raise
            logger.info("lost run creation race for %s; using existing run", as_of)
            return run_id

        logger.info(
            "created run %d for as_of=%s in %dms (%d tasks)",
            run_id,
            as_of,
            duration_ms,
            len(result.tasks),
        )
        return run_id
