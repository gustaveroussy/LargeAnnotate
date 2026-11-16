#!/usr/bin/env python3
"""Read-only DICOM metadata component for an ART-PLAN / Metrics pipeline.

Requires pydicom >= 2.4. Supports directories arranged as:
    INPUT/PatientID/.../DICOM files
One CSV row per patient. Ambiguous or unavailable values are left blank.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

try:
    import pydicom
except ImportError:
    sys.exit("Missing dependency: install with 'python -m pip install pydicom>=2.4,<4'")

LOG = logging.getLogger("extract_metadata")
FIELDS = [
    "patient_id", "status", "ct_series_count", "rtplan_count",
    "tumor_location", "treatment_technique", "prescription_dose_gy", "number_of_fractions",
    "acquisition_date", "age_years", "sex", "ct_manufacturer", "ct_scanner_model",
    "tube_voltage_kvp", "tube_current_ma", "reconstruction_kernel",
    "contrast_enhanced", "matrix_rows", "matrix_columns", "pixel_spacing_mm",
    "slice_thickness_mm", "direct_density", "artplan_version",
    "dose_tag_source", "acquisition_date_tag_source", "notes",
]


def value(ds, tag):
    item = getattr(ds, tag, None)
    if item is None:
        return ""
    return str(item).strip()


def first(seq):
    return seq[0] if seq else None


def as_number(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def one(values):
    """Return a uniform nonblank value only; never pick an arbitrary one."""
    choices = {str(v).strip() for v in values if str(v).strip()}
    return next(iter(choices)) if len(choices) == 1 else ""


def parse_dicom_date(raw):
    if not re.fullmatch(r"\d{8}", raw or ""):
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def age_on_scan(ct):
    # Prefer the DICOM-provided age on the acquisition date.
    patient_age = value(ct, "PatientAge")
    if re.fullmatch(r"\d{3}Y", patient_age):
        return str(int(patient_age[:3]))
    birth = parse_dicom_date(value(ct, "PatientBirthDate"))
    scanned = parse_dicom_date(value(ct, "AcquisitionDate") or value(ct, "StudyDate"))
    if not birth or not scanned or scanned < birth:
        return ""
    return str(scanned.year - birth.year - ((scanned.month, scanned.day) < (birth.month, birth.day)))


def contrast(cts):
    # Presence of a positive contrast-related tag; absence is not proof of non-contrast.
    for ds in cts:
        for tag in ("ContrastBolusAgent", "ContrastBolusRoute"):
            if value(ds, tag):
                return "YES"
    return ""  # Unknown, not NO


def prescriptions(plan):
    """Unique explicitly encoded TARGET dose in Gy; never calculate from beam dose.

    DICOM: DoseReferenceSequence (300A,0010) / DoseReferenceType (300A,0020)
    / TargetPrescriptionDose (300A,0026). Some plans also provide a
    ReferencedDoseReferenceSequence nested in FractionGroupSequence; its
    target prescription dose is only used when the referenced dose reference
    is explicitly TARGET.
    """
    doses = set()
    target_numbers = set()
    for ref in getattr(plan, "DoseReferenceSequence", []):
        if value(ref, "DoseReferenceType").upper() == "TARGET":
            n = value(ref, "DoseReferenceNumber")
            if n:
                target_numbers.add(n)
            dose = as_number(getattr(ref, "TargetPrescriptionDose", None))
            if dose is not None:
                doses.add(round(dose, 6))
    for fg in getattr(plan, "FractionGroupSequence", []):
        for ref in getattr(fg, "ReferencedDoseReferenceSequence", []):
            if value(ref, "ReferencedDoseReferenceNumber") not in target_numbers:
                continue
            dose = as_number(getattr(ref, "TargetPrescriptionDose", None))
            if dose is not None:
                doses.add(round(dose, 6))
    if len(doses) == 1:
        return str(next(iter(doses))), "300A,0010/300A,0026"
    return "", ""


def fraction_count(plan):
    vals = set()
    for fg in getattr(plan, "FractionGroupSequence", []):
        n = as_number(getattr(fg, "NumberOfFractionsPlanned", None))
        if n is not None:
            vals.add(int(n))
    return str(next(iter(vals))) if len(vals) == 1 else ""


def get_dicom(path):
    try:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        return ds if value(ds, "Modality") in {"CT", "RTPLAN"} else None
    except Exception as exc:  # File may be non-DICOM, compressed, or malformed.
        LOG.debug("Skipped %s: %s", path, exc)
        return None


def extract_patient(folder, cli_version):
    row = {key: "" for key in FIELDS}
    row["patient_id"] = folder.name
    row["artplan_version"] = cli_version or ""
    cts_by_series = defaultdict(list)
    plans = []
    ids = set()
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        ds = get_dicom(path)
        if ds is None:
            continue
        pid = value(ds, "PatientID")
        if pid:
            ids.add(pid)
        if value(ds, "Modality") == "CT":
            cts_by_series[value(ds, "SeriesInstanceUID") or f"UNKNOWN:{path.name}"].append(ds)
        elif value(ds, "Modality") == "RTPLAN":
            plans.append(ds)
    row["ct_series_count"] = str(len(cts_by_series))
    row["rtplan_count"] = str(len(plans))
    notes = []
    if len(ids) > 1:
        row["status"] = "REVIEW"
        notes.append("Conflicting DICOM PatientID values")
    elif ids and folder.name not in ids:
        row["status"] = "REVIEW"
        notes.append("Folder name differs from DICOM PatientID")
    if len(cts_by_series) != 1:
        row["status"] = "REVIEW"
        notes.append("Expected exactly one CT series; no arbitrary series selected")
    else:
        cts = next(iter(cts_by_series.values()))
        ct = first(cts)
        raw_date = one([value(d, "AcquisitionDate") or value(d, "StudyDate") for d in cts])
        acquisition = parse_dicom_date(raw_date)
        row["acquisition_date"] = acquisition.isoformat() if acquisition else ""
        row["acquisition_date_tag_source"] = (
            "0008,0022" if one([value(d, "AcquisitionDate") for d in cts]) else
            "0008,0020" if acquisition else ""
        )
        row["age_years"] = one([age_on_scan(d) for d in cts])
        row["sex"] = one([value(d, "PatientSex") for d in cts])
        for field, tag in (
            ("ct_manufacturer", "Manufacturer"), ("ct_scanner_model", "ManufacturerModelName"),
            ("tube_voltage_kvp", "KVP"), ("tube_current_ma", "XRayTubeCurrent"),
            ("reconstruction_kernel", "ConvolutionKernel"), ("matrix_rows", "Rows"),
            ("matrix_columns", "Columns"), ("slice_thickness_mm", "SliceThickness"),
        ):
            row[field] = one([value(d, tag) for d in cts])
        spacing = [getattr(d, "PixelSpacing", None) for d in cts]
        row["pixel_spacing_mm"] = one(["\\".join(str(v) for v in s) if s is not None else "" for s in spacing])
        row["contrast_enhanced"] = contrast(cts)
        # A missing ContrastBolusAgent (0018,0010) is UNKNOWN, not NO.
        # Direct-density is vendor/private encoding; do not infer it from manufacturer name.
    if len(plans) == 1:
        row["prescription_dose_gy"], row["dose_tag_source"] = prescriptions(plans[0])
        row["number_of_fractions"] = fraction_count(plans[0])
    elif len(plans) > 1:
        row["status"] = "REVIEW"
        notes.append("Multiple RTPLANs: no arbitrary plan selected")
    else:
        row["status"] = "REVIEW"
        notes.append("RTPLAN not found")
    # Tumor site and 3D/IMRT/VMAT/SBRT classification require curated information.
    # Direct-density and ART-PLAN version cannot be inferred from standard CT tags.
    if not cts_by_series:
        notes.append("CT not found")
    row["status"] = row["status"] or ("OK" if len(cts_by_series) == 1 else "REVIEW")
    row["notes"] = "; ".join(notes)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Root containing patient subdirectories")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV (semicolon separated)")
    parser.add_argument("--artplan-version", default="", help="Version if independently confirmed")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    src = args.input.resolve()
    out = args.output.resolve()
    if not src.is_dir():
        parser.error(f"Input directory does not exist: {src}")
    if out == src:
        parser.error("Invalid output path")
    patients = sorted(p for p in src.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not patients:
        parser.error("No patient folders found directly under input directory")
    # Build first, then publish the output. Avoid overwriting an existing CSV inadvertently.
    if out.exists():
        parser.error(f"Output already exists: {out} (choose a new filename)")
    rows = [extract_patient(p, args.artplan_version) for p in patients]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Patients: {len(rows)}; review: {sum(r['status'] != 'OK' for r in rows)}; CSV: {out}")


if __name__ == "__main__":
    main()
