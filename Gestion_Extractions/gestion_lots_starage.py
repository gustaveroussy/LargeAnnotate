# -*- coding: utf-8 -*-
"""Prepare the next RayStation batch, without modifying the RayStation script.

Requires: pip install openpyxl
Usage (after RayStation has finished): python gestion_lots_starage.py

No input dataset is uploaded with this script: adjust the paths and first row below.
"""
from __future__ import annotations

import subprocess
import sys
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

# ------------------------- CONFIGURATION -------------------------
BASE = Path(r"\\nas-01\PHY_PROC\Radiotherapie\CQ IMRT-IMAT-STEREO\ANNOTATE")
MASTER = BASE / "260615 - Patients depuis le 05112023_X110X120.xlsx"
CURRENT = BASE / "Cohorte_STARAGE.xlsx"
EXTRACTED = BASE / "Dossiers_Cliniques"
RETRY = BASE / "Patients_A_Retraiter.xlsx"
TCGA_CLINIQUE = Path(
    r"\\nas-01\TCGA_Images\data\Large_Annotate_Study\Dossiers_Cliniques"
)

TRANSFER_SCRIPT = BASE / "transfer_patients.py"

FIRST_NEXT_ROW = 40  # First row available to fill NEXT batch (adjust if row 40 was already queued)
INITIAL_AUDIT_START_ROW = 11
INITIAL_AUDIT_END_ROW = 40  # Inclusive; checked only once, without changing master order
BATCH_SIZE = 100
MASTER_COLUMN = "B"  # NIGR in the master workbook (including hidden columns)
# -----------------------------------------------------------------

PATIENT_ID = re.compile(r"^\d{9}[A-Za-z]{2}$")


def normalise(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        value = str(int(value))
    else:
        value = str(value)
    return value.replace(" ", "").strip().upper()


def read_current():
    wb = load_workbook(CURRENT, read_only=True, data_only=True)
    try:
        values = [normalise(row[0]) for row in wb.active.iter_rows(min_row=2, max_col=1, values_only=True)]
        ids = [v for v in values if v]
        if not ids:
            raise RuntimeError("Cohorte_STARAGE.xlsx ne contient aucun NIP (colonne A).")
        # Les doublons sont autorisés.
# On conserve l'ordre de première apparition.
        ids = list(dict.fromkeys(ids))
        if any(not PATIENT_ID.fullmatch(x) for x in ids):
            raise RuntimeError("Le lot actuel comporte des NIP incompatibles avec Extraction.py.")
        return ids
    finally:
        wb.close()


def read_master():
    wb = load_workbook(MASTER, read_only=True, data_only=True)
    try:
        col = ord(MASTER_COLUMN.upper()) - ord("A") + 1
        return [(i, normalise(row[0]))
                for i, row in enumerate(wb.active.iter_rows(
                    min_row=2, min_col=col, max_col=col, values_only=True), start=2)]
    finally:
        wb.close()


def signature(ids):
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def read_tracking():
    if not RETRY.exists():
        return {}, FIRST_NEXT_ROW, None, None, False
    wb = load_workbook(RETRY, read_only=True, data_only=True)
    try:
        records = {}
        if "Patients" in wb.sheetnames:
            for row in wb["Patients"].iter_rows(min_row=2, max_col=3, values_only=True):
                nip = normalise(row[0])
                if nip:
                    records[nip] = [nip, str(row[1] or ""), str(row[2] or "")]
        if "_Suivi" not in wb.sheetnames:
            raise RuntimeError("Onglet interne _Suivi manquant dans Patients_A_Retraiter.xlsx")
        sheet = wb["_Suivi"]
        return records, int(sheet["B1"].value), sheet["B2"].value, sheet["B3"].value, bool(sheet["B4"].value)
    finally:
        wb.close()

def has_export(nip):
    """Recherche un patient dans tous les emplacements d'extraction."""

    locations = [
        BASE / nip,
        EXTRACTED / nip,
        TCGA_CLINIQUE / nip,
    ]

    for folder in locations:
        if not folder.is_dir():
            continue

        try:
            if any(
                p.is_file() and p.stat().st_size > 0
                for p in folder.rglob("*")
            ):
                return True
        except OSError:
            continue

    return False


def atomic_save(wb, path):
    temp = path.with_name(path.stem + ".tmp.xlsx")
    try:
        wb.save(temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_tracking(records, next_row, last_digest, next_digest, audit_done):
    wb = Workbook()
    ws = wb.active
    ws.title = "Patients"
    ws.append(["IPP", "Motif", "Premiere_detection"])
    for record in records.values():
        ws.append(record)
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 19
    ws.column_dimensions["B"].width = 31
    ws.column_dimensions["C"].width = 23
    meta = wb.create_sheet("_Suivi")
    meta["A1"], meta["B1"] = "Prochaine_ligne_source", next_row
    meta["A2"], meta["B2"] = "Empreinte_lot_precedent", last_digest
    meta["A3"], meta["B3"] = "Empreinte_lot_courant", next_digest
    meta["A4"], meta["B4"] = "Audit_initial_lignes_11_40_termine", audit_done
    meta.sheet_state = "hidden"
    atomic_save(wb, RETRY)


def write_current(ids):
    # Preserve the existing workbook (notably the active first sheet) and its formatting.
    wb = load_workbook(CURRENT)
    ws = wb.active
    # The RayStation script reads only column A, starting from row 2.
    for cells in ws.iter_rows(min_row=2, max_col=1):
        cells[0].value = None
    ws["A1"] = "IPP"
    for i, nip in enumerate(ids, 2):
        ws.cell(i, 1, nip)
    atomic_save(wb, CURRENT)

def lancer_transfert():
    """Lance le transfert après la gestion des lots."""

    if not TRANSFER_SCRIPT.is_file():
        raise RuntimeError(
            "Script de transfert introuvable : {}".format(TRANSFER_SCRIPT)
        )

    print("\n=== TRANSFERT VERS TCGA_IMAGES ===")

    commande = [
        sys.executable,
        str(TRANSFER_SCRIPT),
        "--execute",
        "--confirm-export-finished",
        "EXPORT_TERMINE",
    ]

    resultat = subprocess.run(commande, check=False)

    if resultat.returncode != 0:
        print(
            "ATTENTION : transfert incomplet ou en erreur. "
            "Consulter les messages ci-dessus."
        )
    else:
        print("Transfert terminé sans erreur signalée.")

def run():
    for p in (MASTER, CURRENT, EXTRACTED):
        if not p.exists():
            raise RuntimeError("Chemin introuvable : {}".format(p))
    current_ids = read_current()
    current_digest = signature(current_ids)
    records, cursor, previous_digest, expected_digest, audit_done = read_tracking()
    if expected_digest and current_digest == previous_digest:
        raise RuntimeError("Le suivi a déjà avancé mais l'ancien Excel est toujours présent. "
                           "Vérifier la précédente exécution avant de relancer.")
    if expected_digest and current_digest != expected_digest:
        raise RuntimeError("Cohorte_STARAGE.xlsx a été modifié en dehors du gestionnaire. "
                           "Arrêt pour éviter un saut de patients.")
    if cursor < FIRST_NEXT_ROW:
        raise RuntimeError("Position de reprise invalide dans le suivi.")

    print("Lot courant : {} patient(s) ; prochaine ligne source : {}".format(len(current_ids), cursor))
    print("Cette opération doit être lancée UNIQUEMENT après la fin de RayStation.")
    if input("Tape FIN pour confirmer la fin de l'extraction : ").strip() != "FIN":
        print("Annulé : aucun fichier modifié.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    master_rows = read_master()
    # One-time recovery of previously untracked patients from source rows 11..40.
    # Do not add duplicates or malformed identifiers to the re-extraction list.
    initial_recovered = []
    initial_invalid = []
    if not audit_done:
        audited = set()
        for row, nip in master_rows:
            if not INITIAL_AUDIT_START_ROW <= row <= INITIAL_AUDIT_END_ROW:
                continue
            if not nip or nip in audited:
                continue
            audited.add(nip)
            if not PATIENT_ID.fullmatch(nip):
                initial_invalid.append((row, nip))
                continue
            if not has_export(nip) and nip not in records:
                records[nip] = [nip, "ABSENT_EXTRACTION_INITIALE_LIGNE_{}".format(row), now]
                initial_recovered.append(nip)
        audit_done = True
        print("Rattrapage initial lignes {}-{} : {} NIP absents, {} identifiants hors format.".format(
            INITIAL_AUDIT_START_ROW, INITIAL_AUDIT_END_ROW, len(initial_recovered), len(initial_invalid)))
        if initial_invalid:
            print("Identifiants hors format non ajoutes aux reprises : {}".format(initial_invalid))
    # Clear old retry cases resolved by a later successful extraction.
    for old_nip in list(records):
        if has_export(old_nip):
            records.pop(old_nip)
    already = set(current_ids)
    added_retries = []
    for nip in current_ids:
        if has_export(nip):
            # Never keep a previously failed patient on the retry list once exported.
            records.pop(nip, None)
        elif nip not in records:
            records[nip] = [nip, "ABSENT_DOSSIERS_CLINIQUES", now]
            added_retries.append(nip)

    # In source order, never re-use a NIP seen in the master before the cursor.
    seen = {nip for row, nip in master_rows if row < cursor and nip}
    seen.update(already)
    # An unsuccessful previous patient must not go back to the regular stream.
    seen.update(records)
    next_ids = []
    new_cursor = cursor
    invalid = 0
    for row, nip in master_rows:
        if row < cursor:
            continue
        new_cursor = row + 1
        if not nip or nip in seen:
            continue
        seen.add(nip)
        if not PATIENT_ID.fullmatch(nip):
            invalid += 1
            if nip not in records:
                records[nip] = [nip, "IDENTIFIANT_INVALIDE_LIGNE_{}".format(row), now]
            continue
        if has_export(nip):
            continue
        next_ids.append(nip)
        if len(next_ids) == BATCH_SIZE:
            break

    print("Absents du lot : {} ; nouveaux patients : {} ; identifiants source non conformes : {}"
          .format(len(added_retries), len(next_ids), invalid))
    if not next_ids:
        # Still persist the initial recovery and current batch failures.
        write_tracking(records, cursor, previous_digest, expected_digest, audit_done)
        print("Aucun prochain patient disponible : lot courant conserve, liste de reprise sauvegardee.")
        return

    # Snapshot of current Excel, for recovery. No files are deleted from the NAS.
    history = BASE / "Historique_Lots"
    history.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = history / ("Cohorte_STARAGE_" + stamp + ".xlsx")
    shutil.copy2(CURRENT, backup)
    if RETRY.exists():
        shutil.copy2(RETRY, history / ("Patients_A_Retraiter_" + stamp + ".xlsx"))
    # Record progress, then update Excel. A partial failure is detected on the next run.
    write_tracking(records, new_cursor, current_digest, signature(next_ids), audit_done)
    write_current(next_ids)
    print("OK : nouveau lot de {} NIP écrit dans {}".format(len(next_ids), CURRENT))
    print("Patients à retraiter : {} | sauvegarde : {}".format(len(records), backup))
    print("Prochaine ligne source à examiner : {}".format(new_cursor))
    lancer_transfert()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print("ERREUR : {}".format(e))
        raise SystemExit(1)
