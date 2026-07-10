# -*- coding: utf-8 -*-
"""Convertit les fichiers de brut/ en base long format dans data/.

Organisation :
  referentiel.json      provinces, variantes d'ecriture, mois (editable a la main)
  config_sources.json   dossier Drive -> lecteur (editable a la main)
  LECTEURS ci-dessous   une fonction par format de fichier

Chaque enregistrement produit : {source, periode, province, indicateur, valeur}.
Tout ce qui ne peut pas etre rattache (province inconnue, valeur non numerique,
feuille sans matrice, periode absente du nom de fichier) part dans
data/rapport_validation.json : rien ne disparait en silence.
"""

import csv
import json
import os
import re
import unicodedata
from datetime import datetime, timezone

import openpyxl

SOURCE_DIR = os.environ.get("SOURCE_DIR", "brut")
DATA_DIR = os.environ.get("DATA_DIR", "data")

REF = json.load(open(os.environ.get("REFERENTIEL", "referentiel.json"), encoding="utf-8"))
CONFIG = os.path.exists("config_sources.json") and json.load(open("config_sources.json", encoding="utf-8")) or {}


# ------------------------------------------------------------- rattachement

def simplifier(t):
    """Minuscules, sans accents ni ponctuation : cle de comparaison."""
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", t)).strip().lower()


CLES = {simplifier(p): p for p in REF["provinces"]}
CLES.update(REF["variantes"])
TOTAUX = set(REF["libelles_total"])


def rattacher_province(libelle):
    """Rend (nom canonique, None) ou (None, libelle inconnu)."""
    cle = simplifier(libelle)
    if not cle:
        return None, None
    if cle in CLES:
        return CLES[cle], None
    if cle in TOTAUX:
        return "National", None
    for k, canonique in CLES.items():          # rattachement partiel
        if k in cle or cle in k:
            return canonique, None
    return None, str(libelle)


def extraire_periode(nom):
    """AAAA-MM depuis le nom de fichier : '2026-06', ou mois en lettres + annee."""
    base = simplifier(os.path.basename(nom))
    m = re.search(r"\b(20\d{2})[ _-]?(0?[1-9]|1[0-2])\b", base)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(20\d{2})", base)
    annee = int(m.group(1)) if m else datetime.now(timezone.utc).year
    for cle, num in sorted(REF["mois"].items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{cle}\b", base):
            return f"{annee}-{num:02d}"
    return None


def en_nombre(v):
    """float arrondi, None si vide ou 'nd', 'NON_NUMERIQUE' sinon."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    t = str(v).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".").rstrip("%")
    if not t or t.lower() in {"-", "nd", "n/d", "na", "n/a"}:
        return None
    try:
        return round(float(t), 4)
    except ValueError:
        return "NON_NUMERIQUE"


# ------------------------------------------------------ lecture des fichiers

def feuilles(chemin):
    """Liste de (nom_feuille, lignes) quel que soit le format."""
    bas = chemin.lower()
    if bas.endswith(".xlsx"):
        wb = openpyxl.load_workbook(chemin, data_only=True, read_only=True)
        out = [(ws.title, [tuple(l) for l in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
        wb.close()
        return out
    if bas.endswith(".xls"):
        import xlrd
        return [(ws.name, [tuple(ws.cell_value(i, j) if ws.cell_value(i, j) != "" else None
                                 for j in range(ws.ncols))
                           for i in range(ws.nrows)])
                for ws in xlrd.open_workbook(chemin).sheets()]
    if bas.endswith(".csv"):
        with open(chemin, newline="", encoding="utf-8-sig", errors="replace") as f:
            try:
                dialecte = csv.Sniffer().sniff(f.read(4096), delimiters=";,\t")
            except csv.Error:
                dialecte = csv.excel
            f.seek(0)
            return [("csv", [tuple(c or None for c in l) for l in csv.reader(f, dialecte)])]
    return []


# ------------------------------------------------------------------ lecteurs

def chercher_matrice(lignes):
    """Rend (ligne d'en-tete, colonne des provinces) ou (None, None)."""
    candidates = [(i, sum(1 for c in l if isinstance(c, str) and len(c.strip()) > 3))
                  for i, l in enumerate(lignes[:12])]
    candidates = [c for c in candidates if c[1] >= 3]
    if not candidates:
        return None, None
    i = max(candidates, key=lambda c: c[1])[0]
    for j in range(4):
        reconnues = sum(1 for l in lignes[i + 1: i + 31]
                        if j < len(l) and rattacher_province(l[j])[0] not in (None, "National"))
        if reconnues >= 5:
            return i, j
    return None, None


def lecteur_matrice_provinces(lignes, ctx, anomalies):
    """Provinces en lignes, indicateurs en colonnes."""
    i, j_prov = chercher_matrice(lignes)
    if i is None:
        anomalies.append({**ctx, "type": "matrice_non_reconnue"})
        return []
    indicateurs = {j: re.sub(r"\s+", " ", str(lib)).strip()
                   for j, lib in enumerate(lignes[i])
                   if j != j_prov and lib and len(str(lib).strip()) > 3}
    out = []
    for ligne in lignes[i + 1:]:
        if not ligne or all(c is None for c in ligne):
            continue
        province, inconnu = rattacher_province(ligne[j_prov] if j_prov < len(ligne) else None)
        if province is None:
            if inconnu:
                anomalies.append({**ctx, "type": "province_inconnue", "libelle": inconnu})
            continue
        for j, indicateur in indicateurs.items():
            v = en_nombre(ligne[j]) if j < len(ligne) else None
            if v == "NON_NUMERIQUE":
                anomalies.append({**ctx, "type": "valeur_non_numerique", "province": province,
                                  "indicateur": indicateur, "valeur": str(ligne[j])[:50]})
            elif v is not None:
                out.append({"source": ctx["source"], "periode": ctx["periode"],
                            "province": province, "indicateur": indicateur, "valeur": v})
    return out


def lecteur_format_long(lignes, ctx, anomalies):
    """Colonnes province, indicateur, valeur, periode facultative."""
    cols, i = None, None
    for n, ligne in enumerate(lignes[:12]):
        candidates = {simplifier(c): j for j, c in enumerate(ligne) if c}
        if {"province", "indicateur", "valeur"} <= set(candidates):
            cols, i = candidates, n
            break
    if cols is None:
        anomalies.append({**ctx, "type": "format_long_non_reconnu"})
        return []
    out = []
    for ligne in lignes[i + 1:]:
        if not ligne or all(c is None for c in ligne):
            continue
        province, inconnu = rattacher_province(ligne[cols["province"]])
        v = en_nombre(ligne[cols["valeur"]])
        if province is None:
            if inconnu:
                anomalies.append({**ctx, "type": "province_inconnue", "libelle": inconnu})
        elif v == "NON_NUMERIQUE":
            anomalies.append({**ctx, "type": "valeur_non_numerique", "valeur": str(ligne[cols["valeur"]])[:50]})
        elif v is not None:
            periode = str(ligne[cols["periode"]]).strip()[:7] if cols.get("periode") is not None and ligne[cols["periode"]] else ctx["periode"]
            out.append({"source": ctx["source"], "periode": periode, "province": province,
                        "indicateur": re.sub(r"\s+", " ", str(ligne[cols["indicateur"]])).strip(), "valeur": v})
    return out


LECTEURS = {"matrice_provinces": lecteur_matrice_provinces,
            "format_long": lecteur_format_long}


# ------------------------------------------------------------------ pilotage

def principal():
    base, anomalies = [], []
    for racine, _, fichiers in os.walk(SOURCE_DIR):
        for nom in sorted(fichiers):
            if not nom.lower().endswith((".xlsx", ".xls", ".csv")):
                continue
            chemin = os.path.join(racine, nom)
            source = os.path.relpath(racine, SOURCE_DIR).split(os.sep)[0]
            source = "RACINE" if source == "." else source
            lecteur = LECTEURS.get(CONFIG.get(source, "matrice_provinces"))
            periode = extraire_periode(chemin)
            if lecteur is None:
                anomalies.append({"fichier": chemin, "type": "lecteur_inconnu"})
                continue
            if periode is None:
                anomalies.append({"fichier": chemin, "type": "periode_indetectable"})
                continue
            print(f"Traitement : {chemin}")
            for nom_feuille, lignes in feuilles(chemin):
                ctx = {"fichier": chemin, "feuille": nom_feuille, "source": source, "periode": periode}
                base += lecteur(lignes, ctx, anomalies)

    # doublon (source, periode, province, indicateur) : le dernier fichier fait foi
    base = sorted({(e["source"], e["periode"], e["province"], e["indicateur"]): e
                   for e in base}.values(),
                  key=lambda e: (e["source"], e["periode"], e["province"], e["indicateur"]))

    os.makedirs(DATA_DIR, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sorties = {
        "consolide.json": {"genere": horodatage, "enregistrements": base},
        "catalogue.json": {"genere": horodatage,
                           "provinces": sorted({e["province"] for e in base}),
                           "periodes": sorted({e["periode"] for e in base}),
                           "sources": sorted({e["source"] for e in base}),
                           "indicateurs": sorted({e["indicateur"] for e in base})},
        "rapport_validation.json": {"genere": horodatage, "anomalies": anomalies},
    }
    for nom, contenu in sorties.items():
        with open(os.path.join(DATA_DIR, nom), "w", encoding="utf-8") as f:
            json.dump(contenu, f, ensure_ascii=False, indent=1)
    print(f"{len(base)} enregistrements, {len(anomalies)} anomalie(s).")


if __name__ == "__main__":
    principal()
