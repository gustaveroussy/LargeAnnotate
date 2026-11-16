#!/usr/bin/env python3
"""Verified patient folder move from PHY_PROC to TCGA_Images.

Dry run by default. Run after RayStation export has finished.
Patient folders are read both from ANNOTATE/ and ANNOTATE/Dossiers_Cliniques/.
The source is only removed after an entire patient directory has been copied,
cryptographically verified, and published to the destination.
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_SOURCE = r"\\nas-01\PHY_PROC\Radiotherapie\CQ IMRT-IMAT-STEREO\ANNOTATE"
DEFAULT_DEST = r"\\nas-01\TCGA_Images\data\Large_Annotate_Study\Dossiers_Cliniques"
PATIENT_RE = re.compile(r"^[0-9]{9}[A-Za-z]{2}$")


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.digest()


def manifest(folder):
    result = {}
    for base, _, files in os.walk(folder):
        for filename in files:
            path = Path(base) / filename
            relative = path.relative_to(folder).as_posix()
            result[relative] = (path, path.stat().st_size)
    return result


def equal_tree(left, right):
    left_files, right_files = manifest(left), manifest(right)
    if not left_files or left_files.keys() != right_files.keys():
        return False
    for rel, (left_file, left_size) in left_files.items():
        right_file, right_size = right_files[rel]
        if left_size != right_size or file_digest(left_file) != file_digest(right_file):
            return False
    return True


def eligible(folder, min_age_minutes):
    files = manifest(folder)
    if not files:
        return False, "dossier vide"
    if any(size == 0 for _, size in files.values()):
        return False, "fichier vide"
    newest = max(f.stat().st_mtime for f, _ in files.values())
    if datetime.now().timestamp() - newest < min_age_minutes * 60:
        return False, "modification trop récente"
    return True, ""


def patient_sources(source_root):
    # The current extraction can place patients in either location.
    for parent in (source_root / "Dossiers_Cliniques", source_root):
        if not parent.is_dir():
            continue
        for folder in sorted(parent.iterdir()):
            if folder.is_dir() and PATIENT_RE.fullmatch(folder.name):
                yield folder


def run(args):
    source_root = Path(args.source)
    destination_root = Path(args.destination)
    if not source_root.is_dir():
        print("ERREUR: dossier source introuvable:", source_root)
        return 2
    # Never remove data while the operator is still exporting.
    if args.execute and args.confirm_export_finished != "EXPORT_TERMINE":
        print("ERREUR: préciser --confirm-export-finished EXPORT_TERMINE après la fin de RayStation")
        return 2
    candidates = list(patient_sources(source_root))
    print("Mode:", "TRANSFERT" if args.execute else "SIMULATION")
    print("Patients détectés:", len(candidates))
    successes = blocked = 0
    for source in candidates:
        try:
            ready, reason = eligible(source, args.min_age_minutes)
            if not ready:
                print("IGNORE", source.name, reason)
                blocked += 1
                continue
            final = destination_root / source.name
            if not args.execute:
                print("A TRANSFERER" if not final.exists() else "A COMPARER", source.name, "depuis", source.parent)
                continue
            destination_root.mkdir(parents=True, exist_ok=True)
            if final.exists():
                if not equal_tree(source, final):
                    print("CONFLIT", source.name, "destination différente : aucune suppression")
                    blocked += 1
                    continue
                # Identical folder already published; verified source can be removed.
            else:
                staging = destination_root / (".__staging_" + source.name + "_" + uuid.uuid4().hex)
                try:
                    shutil.copytree(source, staging)
                    if not equal_tree(source, staging):
                        print("ECHEC VERIFICATION", source.name, "source conservée")
                        blocked += 1
                        continue
                    # Avoid replacing an existing patient's data.
                    if final.exists():
                        print("CONFLIT", source.name, "destination apparue pendant le transfert")
                        blocked += 1
                        continue
                    staging.rename(final)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            # Re-check the original immediately before deletion, detecting changes
            # that might have occurred during verification.
            if not equal_tree(source, final):
                print("CHANGEMENT SOURCE", source.name, "source conservée")
                blocked += 1
                continue
            if args.delete_source:
                shutil.rmtree(source)
                print("DEPLACE ET VERIFIE", source.name)
            else:
                print("COPIE VERIFIEE (SOURCE CONSERVEE)", source.name)
            successes += 1
        except (OSError, ValueError) as exc:
            print("ERREUR", source.name, str(exc))
            blocked += 1
    print("Terminé | traités:", successes, "| ignorés/erreurs:", blocked)
    return 1 if blocked else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--destination", default=DEFAULT_DEST)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--delete-source", action="store_true", help="Supprimer les dossiers source après vérification SHA-256")
    parser.add_argument("--confirm-export-finished", default="")
    parser.add_argument("--min-age-minutes", type=int, default=120)
    args = parser.parse_args()
    if args.delete_source and not args.execute:
        parser.error("--delete-source nécessite --execute")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
