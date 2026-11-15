# LargeAnnotate — Scripts

Utilities for RayStation DICOM extraction, patient batch management, verified file transfers, and DICOM metadata collection.

## Requirements

- **RayStation:** v2025 SP1 for `Raystation/Extraction.py`; execute it using the Python environment provided with RayStation.
- **Standalone Python scripts:** Python **3.11 or later** (3.11 recommended; compatibility must be checked on each workstation).
- **Operating systems:** Windows for the RayStation and NAS transfer scripts; Linux/Windows for metadata extraction.
- **Python packages:** `openpyxl` for Excel batch management and RayStation extraction; `pydicom` for DICOM metadata extraction.

Install packages in the environment used to run the corresponding script:

```bash
python -m pip install "openpyxl>=3.1,<4" "pydicom>=2.4,<4"
```

RayStation uses its own scripting environment; install `openpyxl` there only if it is not already available. The file-transfer script uses the standard Python library.

## Included scripts

| Script | Purpose |
| --- | --- |
| `Raystation/Extraction.py` | Extract planning CT, clinical RTSTRUCT and RTPLAN from RayStation. |
| `Gestion_Extractions/gestion_lots_starage.py` | Prepare the next patient batch and track patients requiring re-extraction. |
| `Gestion_Extractions/transfer_patients.py` | Transfer exported patient files between NAS locations, verify SHA-256 checksums and optionally remove verified source copies. |
| `TheraPanacea/extract_metadata.py` | Extract available CT and RTPLAN metadata to a CSV for the planned ART-PLAN contour regeneration and Metrics processing pipeline on Flamingo. |

## Notes

- Adapt paths in the scripts to the local workstation and storage layout.
- The ART-PLAN + Metrics pipeline on Flamingo is **not yet fully automated**; storage constraints and a recent cluster outage have affected deployment.
- The metadata script leaves unavailable or ambiguous values blank; it does not infer tumor location or treatment technique from unverified DICOM descriptions.
- Do not publish patient data, exported DICOM files, access credentials or local configuration files in this repository.
