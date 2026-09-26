"""CSV -> source tables.

This stands in for the EHR feed. It is written defensively even though the supplied
files are clean, because the interesting question is not "does this dataset load"
but "what happens on the day a row is malformed". The policy here:

  * a bad row is rejected individually, with file, line number, and reason, and the
    rest of the load continues -- one unparseable birthday must not blank out a
    night's worth of care gaps;
  * a row referencing a patient who does not exist is rejected, because silently
    orphaned clinical data is worse than a loud gap;
  * exact duplicate rows are collapsed and counted (the supplied encounters.csv
    contains one: P0231 / PCP / 2024-10-02);
  * every decision is reported, not just logged, so the API can expose it.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.db.models import Diagnosis, Encounter, LabResult, Patient
from app.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class FileReport:
    filename: str
    read: int = 0
    loaded: int = 0
    duplicates: int = 0
    rejected: list[str] = field(default_factory=list)

    def reject(self, line: int, reason: str) -> None:
        self.rejected.append(f"{self.filename}:{line} {reason}")


@dataclass
class IngestReport:
    files: list[FileReport] = field(default_factory=list)

    @property
    def total_rejected(self) -> int:
        return sum(len(f.rejected) for f in self.files)

    @property
    def total_duplicates(self) -> int:
        return sum(f.duplicates for f in self.files)

    def summary(self) -> str:
        parts = [f"{f.filename}: {f.loaded}/{f.read}" for f in self.files]
        return (
            f"ingest complete [{', '.join(parts)}] "
            f"duplicates={self.total_duplicates} rejected={self.total_rejected}"
        )


def ingest_all(session: Session, data_dir: Path | None = None) -> IngestReport:
    directory = Path(data_dir or settings.data_dir)
    report = IngestReport()

    patient_ids = _ingest_patients(session, directory / "patients.csv", report)
    session.flush()  # patients must exist before their children are inserted
    _ingest_diagnoses(session, directory / "diagnoses.csv", report, patient_ids)
    _ingest_labs(session, directory / "labs.csv", report, patient_ids)
    _ingest_encounters(session, directory / "encounters.csv", report, patient_ids)

    logger.info(report.summary())
    for file_report in report.files:
        for rejection in file_report.rejected:
            logger.warning("rejected row: %s", rejection)
    return report


# --------------------------------------------------------------------------- #
# Per-file loaders
# --------------------------------------------------------------------------- #


def _ingest_patients(session: Session, path: Path, report: IngestReport) -> set[str]:
    fr = FileReport(path.name)
    report.files.append(fr)
    seen: set[str] = set()

    for line, row in _rows(path, fr):
        patient_id = (row.get("patient_id") or "").strip()
        if not patient_id:
            fr.reject(line, "missing patient_id")
            continue
        if patient_id in seen:
            # A repeated primary key is a genuine conflict, not a harmless dupe:
            # the two rows may disagree. First one wins and the clash is reported.
            fr.duplicates += 1
            fr.reject(line, f"duplicate patient_id {patient_id}")
            continue
        seen.add(patient_id)

        dob, error = _parse_date(row.get("date_of_birth"))
        if error:
            # Kept, not rejected: a patient with an unknown birthday still has
            # diagnoses and still belongs on the roster. The engine treats an
            # unknown age as failing any age floor.
            fr.reject(line, f"unparseable date_of_birth for {patient_id}; kept as null")

        session.add(
            Patient(
                patient_id=patient_id,
                first_name=_clean(row.get("first_name")) or "",
                last_name=_clean(row.get("last_name")) or "",
                date_of_birth=dob,
                gender=_clean(row.get("gender")),
                phone=_clean(row.get("phone")),
                language=_clean(row.get("language")),
                pcp_provider_name=_clean(row.get("pcp_provider_name")),
            )
        )
        fr.loaded += 1

    return seen


def _ingest_diagnoses(
    session: Session, path: Path, report: IngestReport, patient_ids: set[str]
) -> None:
    fr = FileReport(path.name)
    report.files.append(fr)
    seen: set[tuple] = set()

    for line, row in _rows(path, fr):
        patient_id = _require_patient(row, line, fr, patient_ids)
        if patient_id is None:
            continue
        icd_code = (row.get("icd_code") or "").strip().upper()
        if not icd_code:
            fr.reject(line, "missing icd_code")
            continue
        diagnosed, error = _parse_date(row.get("diagnosed_date"))
        if error:
            fr.reject(line, "unparseable diagnosed_date; kept as null")

        key = (patient_id, icd_code, diagnosed)
        if key in seen:
            fr.duplicates += 1
            continue
        seen.add(key)

        session.add(
            Diagnosis(
                patient_id=patient_id,
                icd_code=icd_code,
                description=_clean(row.get("description")),
                diagnosed_date=diagnosed,
            )
        )
        fr.loaded += 1


def _ingest_labs(
    session: Session, path: Path, report: IngestReport, patient_ids: set[str]
) -> None:
    fr = FileReport(path.name)
    report.files.append(fr)
    seen: set[tuple] = set()

    for line, row in _rows(path, fr):
        patient_id = _require_patient(row, line, fr, patient_ids)
        if patient_id is None:
            continue
        test_name = _clean(row.get("test_name"))
        if not test_name:
            fr.reject(line, "missing test_name")
            continue
        try:
            value = float((row.get("result_value") or "").strip())
        except ValueError:
            # A lab with no usable number cannot inform a threshold. Dropping it
            # makes the patient look unmonitored, which is the safe direction.
            fr.reject(line, f"non-numeric result_value {row.get('result_value')!r}")
            continue
        result_date, error = _parse_date(row.get("result_date"))
        if result_date is None or error:
            fr.reject(line, "missing or unparseable result_date")
            continue

        key = (patient_id, test_name, value, result_date)
        if key in seen:
            fr.duplicates += 1
            continue
        seen.add(key)

        session.add(
            LabResult(
                patient_id=patient_id,
                test_name=test_name,
                result_value=value,
                result_date=result_date,
            )
        )
        fr.loaded += 1


def _ingest_encounters(
    session: Session, path: Path, report: IngestReport, patient_ids: set[str]
) -> None:
    fr = FileReport(path.name)
    report.files.append(fr)
    seen: set[tuple] = set()

    for line, row in _rows(path, fr):
        patient_id = _require_patient(row, line, fr, patient_ids)
        if patient_id is None:
            continue
        specialty = _clean(row.get("specialty"))
        if not specialty:
            fr.reject(line, "missing specialty")
            continue
        encounter_date, error = _parse_date(row.get("encounter_date"))
        if encounter_date is None or error:
            fr.reject(line, "missing or unparseable encounter_date")
            continue

        # Two identical visits on one day for one specialty is a data artefact, not
        # two appointments. Left in place it would not change any result, but it
        # would inflate every "visits per patient" number downstream.
        key = (patient_id, specialty, encounter_date, _clean(row.get("provider_name")))
        if key in seen:
            fr.duplicates += 1
            continue
        seen.add(key)

        session.add(
            Encounter(
                patient_id=patient_id,
                specialty=specialty,
                encounter_date=encounter_date,
                provider_name=_clean(row.get("provider_name")),
            )
        )
        fr.loaded += 1


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _rows(path: Path, fr: FileReport) -> Iterable[tuple[int, dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"expected input file {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):  # line 1 = header
            fr.read += 1
            yield line, row


def _require_patient(
    row: dict, line: int, fr: FileReport, patient_ids: set[str]
) -> str | None:
    patient_id = (row.get("patient_id") or "").strip()
    if not patient_id:
        fr.reject(line, "missing patient_id")
        return None
    if patient_id not in patient_ids:
        fr.reject(line, f"unknown patient_id {patient_id}")
        return None
    return patient_id


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    # Several EHR exports spell "no value" differently; normalise them all to NULL.
    if text == "" or text.upper() in {"NULL", "NONE", "N/A", "NA"}:
        return None
    return text


def _parse_date(value: Any) -> tuple[date | None, bool]:
    """Return (parsed_date, had_error). A blank value is not an error."""
    text = _clean(value)
    if text is None:
        return None, False
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), False
    except ValueError:
        return None, True
