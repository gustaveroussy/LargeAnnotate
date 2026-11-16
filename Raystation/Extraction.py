# -*- coding: utf-8 -*-
"""STARAGE - export CT + RTSTRUCT clinique + RTPLAN, filtre ROI Target/OrganAtRisk.

A valider dans RayStation RS2025 SP1 sur 3 patients avant utilisation large.
Aucune modification ni sauvegarde volontaire dans la base RayStation.
Aucun RTDOSE exporte.
Le RTSTRUCT est exporte sans filtrage par type de ROI.
ATTENTION : le RTPLAN peut referencer des ROI de prescription exclues du RTSTRUCT filtre ;
verifier la coherence DICOM avant toute reutilisation ou import.
Aucune dependance pydicom.
"""
import os
import csv
import openpyxl
from raystation import get_current

EXCEL_FILE = r"\\nas-01\PHY_PROC\Radiotherapie\CQ IMRT-IMAT-STEREO\ANNOTATE\Cohorte_STARAGE.xlsx"

EXPORT_ROOT = r"\\nas-01\PHY_PROC\Radiotherapie\CQ IMRT-IMAT-STEREO\ANNOTATE"

DATE_DEBUT = (2023, 9, 1)
DATE_FIN = (2026, 10, 29)
MAX_PATIENTS = None  


def read_patient_ids(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ids, seen = [], set()
    try:
        for line, row in enumerate(wb.active.iter_rows(min_row=2, max_col=1, values_only=True), 2):
            raw = row[0]
            if raw is None or not str(raw).strip():
                continue
            pid = str(raw).replace(" ", "").strip()
            if len(pid) != 11 or not pid[:9].isdigit() or not pid[9:].isalpha():
                print("ID invalide ligne {} : {}".format(line, raw))
                continue
            if pid not in seen:
                ids.append(pid)
                seen.add(pid)
    finally:
        wb.close()
    return ids


def date_tuple(exam):
    dt = exam.GetExaminationDateTime()
    return None if dt is None else (int(dt.Year), int(dt.Month), int(dt.Day))


def safe_name(value):
    return "".join("_" if c in '<>:"/\\|?*' else c for c in str(value)).strip().rstrip(".")


def choose_plan(patient):
    candidates = []
    for ci, case in enumerate(patient.Cases):
        for pi, plan in enumerate(case.TreatmentPlans):
            try:
                if plan.Review is None or plan.Review.ApprovalStatus != "Approved" or len(plan.BeamSets) == 0:
                    continue
                bs = plan.BeamSets[0]
                exam = bs.GetPlanningExamination()
                if exam is None:
                    continue
                dt = date_tuple(exam)
                if dt is not None and DATE_DEBUT <= dt <= DATE_FIN:
                    candidates.append((dt, ci, pi, case, plan, bs, exam))
            except Exception as exc:
                print("Plan ignore : {}".format(exc))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    return (candidates[0] if candidates else None), len(candidates)


def main():
    os.makedirs(EXPORT_ROOT, exist_ok=True)
    ids = read_patient_ids(EXCEL_FILE)
    if MAX_PATIENTS is not None:
        ids = ids[:MAX_PATIENTS]
    db = get_current("PatientDB")
    log_path = os.path.join(EXPORT_ROOT, "STARAGE_CT_RTSTRUCT_RTPLAN_log.csv")
    with open(log_path, "w", newline="", encoding="utf-8-sig") as logfile:
        writer = csv.writer(logfile, delimiter=";")
        writer.writerow(["PatientID", "Statut", "Case", "Plan", "BeamSet", "CT", "Date_CT", "Filtrage_ROI", "Dossier", "Message"])
        for pid in ids:
            case_name = plan_name = bs_name = exam_name = date_str = out_folder = ""
            try:
                matches = db.QueryPatientInfo(Filter={"PatientID": pid}, UseIndexService=True)
                if len(matches) != 1:
                    raise ValueError("Nombre de correspondances patient : {}".format(len(matches)))
                patient = db.LoadPatient(PatientInfo=matches[0], AllowPatientUpgrade=True)
                chosen, n = choose_plan(patient)
                if chosen is None:
                    raise ValueError("Aucun plan approuve avec CT dans la periode")
                dt, ci, pi, case, plan, bs, exam = chosen
                case_name = str(getattr(case, "CaseName", ci))
                plan_name = str(plan.Name)
                bs_name = str(bs.DicomPlanLabel)
                exam_name = str(exam.Name)
                date_str = "{:04d}-{:02d}-{:02d}".format(*dt)
                out_folder = os.path.join(EXPORT_ROOT, safe_name(pid),
                                          safe_name("CT_RTSTRUCT_RTPLAN_{}_{}".format(plan_name, bs_name)))
                if os.path.exists(out_folder):
                    raise ValueError("Dossier deja existant, export non lance : " + out_folder)
                os.makedirs(out_folder)
                print("{} : CT {}, RTSTRUCT et RTPLAN du plan {}".format(pid, exam_name, plan_name))
                case.ScriptableDicomExport(
                    ExportFolderPath=out_folder,
                    Examinations=[exam_name],
                    RtStructureSetsReferencedFromBeamSets=[bs.BeamSetIdentifier()],
                    BeamSets=[bs.BeamSetIdentifier()],
                    DicomFilter="",
                    IgnorePreConditionWarnings=True
                )
                writer.writerow([pid, "EXPORT_APPELE", case_name, plan_name, bs_name,
                                 exam_name, date_str, "NON_FILTRE", out_folder,
                                 "Verifier CT/RTSTRUCT/RTPLAN et ROI; {} plans admissibles".format(n)])
                print("{} : export appele ; verifier les fichiers et ROI dans {}".format(pid, out_folder))
            except Exception as exc:
                print("{} : ERREUR : {}".format(pid, exc))
                writer.writerow([pid, "ERREUR", case_name, plan_name, bs_name, exam_name, date_str, "", out_folder, str(exc)])
            finally:
                logfile.flush()
    print("Journal : " + log_path)


if __name__ == "__main__":
    main()
