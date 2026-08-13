# -*- coding: utf-8 -*-
"""Construit surveillance.json a partir de l'export DHIS2 de surveillance
epidemiologique integree (IDS) depose dans le dossier Drive du COUSP.

L'export porte des taux mensuels par province : taux d'attaque ou taux
d'incidence selon la maladie, et taux de letalite. Il ne porte aucun effectif
de cas ni de deces. Le script ne convertit rien, ne complete rien et
n'extrapole rien : il structure les valeurs telles qu'exportees, releve les
periodes non couvertes et consigne les anomalies sans les corriger.

Sortie : surveillance.json

Environnement :
  BRUT  repertoire des fichiers telecharges depuis Drive (defaut : brut)
"""

import csv
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

BRUT = os.environ.get("BRUT", "brut")
DOSSIER_COUSP = os.environ.get("DOSSIER_COUSP", "COUSP")
MOTIFS = ("surveillance", "ids")

META = {"organisationunitid", "organisationunitname", "organisationunitcode",
        "organisationunitdescription", "periodid", "periodname", "periodcode",
        "perioddescription"}

MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
        "aout", "septembre", "octobre", "novembre", "decembre"]
MOIS_AFFICHE = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
                "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

# Libelles d'affichage des maladies, rattaches a la forme simplifiee du libelle
# porte par l'export. Toute maladie absente de cette table conserve le libelle
# de l'export, nettoye mais non reecrit.
LIBELLES = {
    "chikungunya": "Chikungunya",
    "cholera": "Choléra",
    "dengue": "Dengue",
    "diphterie": "Diphtérie",
    "fievre hemoragique": "Fièvre hémorragique",
    "fievre jaune": "Fièvre jaune",
    "gastroenterite": "Gastroentérite",
    "grippe humaine": "Grippe humaine",
    "hepatite e": "Hépatite E",
    "malnutrition modere": "Malnutrition modérée",
    "malnutrition severe": "Malnutrition sévère",
    "meningite": "Méningite",
    "morsure de serpent": "Morsure de serpent",
    "mpox": "Mpox",
    "paludisme": "Paludisme",
    "pfa": "Paralysie flasque aiguë",
    "piqure de scorpion": "Piqûre de scorpion",
    "rougeole": "Rougeole",
    "tnn": "Tétanos néonatal",
    "ver de guinee": "Ver de Guinée",
}

# Maladies a declaration obligatoire retenues pour la lecture provinciale.
# Les etats nutritionnels et les envenimations restent dans le jeu de donnees
# mais sortent du tableau de veille.
MDO = ["cholera", "rougeole", "meningite", "diphterie", "fievre jaune",
       "fievre hemoragique", "hepatite e", "mpox", "pfa", "tnn",
       "ver de guinee", "chikungunya", "dengue", "paludisme"]


def simplifier(t):
    """Minuscules, sans accents ni ponctuation : cle de comparaison."""
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", t)).strip().lower()


REF = json.load(open(os.environ.get("REFERENTIEL", "referentiel.json"),
                     encoding="utf-8"))
CLES = {simplifier(p): p for p in REF["provinces"]}
CLES.update(REF["variantes"])
TOTAUX = set(REF["libelles_total"])


def rattacher_province(libelle):
    """Rend le nom canonique, 'National' pour l'agregat pays, ou None."""
    cle = simplifier(libelle)
    if not cle:
        return None
    if cle in CLES:
        return CLES[cle]
    if cle in TOTAUX:
        return "National"
    for k, canonique in CLES.items():
        if k in cle or cle in k:
            return canonique
    return None


def trouver_export():
    """Retient l'export de surveillance le plus recent depose par le COUSP."""
    def collecter(base):
        trouves = []
        for racine, _, fichiers in os.walk(base):
            for f in fichiers:
                if not f.lower().endswith(".csv"):
                    continue
                if any(m in simplifier(f) for m in MOTIFS):
                    chemin = os.path.join(racine, f)
                    trouves.append((os.path.getmtime(chemin), chemin))
        return trouves

    cible = os.path.join(BRUT, DOSSIER_COUSP)
    trouves = collecter(cible) if os.path.isdir(cible) else []
    if not trouves:
        trouves = collecter(BRUT)
    if not trouves:
        return None
    return max(trouves)[1]


def lire_colonne(nom):
    """Rend (cle maladie, metrique) pour une colonne d'indicateur.

    Les libelles de l'export melangent les espaces et la casse : 'IDS_ Taux
    d'attaque_Rougeole', 'IDS_Taux de letalité_Rougeole', 'IDS_Taux de
    létalité _dengue'. La lecture se fait sur la forme simplifiee.
    """
    brut = nom.strip().strip('"')
    corps = re.sub(r"^ids[ _]*", "", brut, flags=re.I).strip()
    if "_" not in corps:
        return None, None
    metrique_brute, maladie = corps.rsplit("_", 1)
    m = simplifier(metrique_brute)
    if "letalite" in m:
        metrique = "let"
    elif "attaque" in m:
        metrique = "att"
    elif "incidence" in m:
        metrique = "inc"
    else:
        return None, None
    return simplifier(maladie), metrique


def nombre(v):
    if v is None:
        return None
    v = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def principal():
    chemin = trouver_export()
    if not chemin:
        print("Aucun export de surveillance trouve dans %s." % BRUT)
        return 1

    with open(chemin, encoding="utf-8-sig", newline="") as fh:
        lignes = list(csv.DictReader(fh))
    if not lignes:
        print("Export vide.")
        return 1

    colonnes = [c for c in lignes[0].keys() if c and c not in META]
    schema = {}
    for c in colonnes:
        maladie, metrique = lire_colonne(c)
        if maladie:
            schema[c] = (maladie, metrique)

    anomalies = []
    inconnues = sorted({c for c in colonnes if c not in schema})
    for c in inconnues:
        anomalies.append({"type": "colonne_non_lue", "detail": c})

    # ------------------------------------------------------------- structure
    maladies = {}
    for maladie, metrique in schema.values():
        m = maladies.setdefault(maladie, {"code": maladie, "att": False,
                                          "inc": False, "let": False})
        m[metrique] = True

    periodes, unites_brutes = {}, {}
    valeurs = {}

    for ligne in lignes:
        code = str(ligne.get("periodcode") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        annee, mois = int(code[:4]), int(code[4:])
        if not 1 <= mois <= 12:
            anomalies.append({"type": "periode_illisible", "detail": code})
            continue
        cle_periode = "%04d-%02d" % (annee, mois)
        periodes.setdefault(cle_periode, {"code": cle_periode,
                                          "libelle": "%s %d" % (MOIS_AFFICHE[mois - 1], annee),
                                          "renseigne": 0})

        brut_unite = ligne.get("organisationunitname")
        unite = rattacher_province(brut_unite)
        if not unite:
            unites_brutes.setdefault(str(brut_unite), 0)
            unites_brutes[str(brut_unite)] += 1
            continue

        for colonne, (maladie, metrique) in schema.items():
            v = nombre(ligne.get(colonne))
            if v is None:
                continue
            periodes[cle_periode]["renseigne"] += 1
            if metrique == "let" and v > 100:
                anomalies.append({
                    "type": "letalite_hors_bornes", "periode": cle_periode,
                    "unite": unite, "maladie": maladie, "valeur": v,
                    "detail": "taux de létalité supérieur à 100 pour cent",
                })
            if v < 0:
                anomalies.append({
                    "type": "valeur_negative", "periode": cle_periode,
                    "unite": unite, "maladie": maladie, "valeur": v,
                })
            (valeurs.setdefault(cle_periode, {})
                    .setdefault(unite, {})
                    .setdefault(maladie, {}))[metrique] = v

    for u, n in sorted(unites_brutes.items()):
        anomalies.append({"type": "unite_non_rattachee", "detail": u,
                          "lignes": n})

    # ----------------------------------------------------- couverture et ND
    presentes = {u for p in valeurs.values() for u in p} - {"National"}
    absentes = [p for p in REF["provinces"] if p not in presentes]

    attendu = len(schema) * (len(REF["provinces"]) + 1)
    liste_periodes = []
    for cle in sorted(periodes):
        p = periodes[cle]
        p["couverte"] = p["renseigne"] > 0
        p["attendu"] = attendu
        p["taux"] = round(100 * p["renseigne"] / attendu, 1) if attendu else 0
        liste_periodes.append(p)

    couvertes = [p["code"] for p in liste_periodes if p["couverte"]]
    derniere = couvertes[-1] if couvertes else None

    # La derniere periode couverte est signalee partielle lorsque le mois
    # courant n'est pas acheve a la date de construction du fichier.
    maintenant = datetime.now(timezone.utc)
    partielle = derniere == "%04d-%02d" % (maintenant.year, maintenant.month)
    if partielle:
        for p in liste_periodes:
            if p["code"] == derniere:
                p["partielle"] = True

    for p in liste_periodes:
        if not p["couverte"]:
            anomalies.append({"type": "periode_non_couverte", "periode": p["code"],
                              "detail": "aucune valeur, affichage ND"})
    for u in absentes:
        anomalies.append({"type": "unite_absente_export", "unite": u,
                          "detail": "province du référentiel absente de l'export, affichage ND"})

    for cle, m in sorted(maladies.items()):
        if not (m["att"] or m["inc"]):
            anomalies.append({"type": "metrique_manquante", "maladie": cle,
                              "detail": "létalité exportée sans taux d'attaque ni taux d'incidence"})
        if not m["let"]:
            anomalies.append({"type": "metrique_manquante", "maladie": cle,
                              "detail": "taux d'attaque exporté sans taux de létalité"})

    anomalies.append({
        "type": "unite_de_mesure_non_documentee",
        "detail": "l'export ne porte ni dénominateur ni base de calcul des taux, "
                  "les ordres de grandeur diffèrent d'une maladie à l'autre et "
                  "les valeurs ne sont pas comparables entre maladies",
    })

    sortie = {
        "meta": {
            "source": "Export DHIS2, surveillance intégrée des maladies",
            "fichier": os.path.basename(chemin),
            "granularite": "mensuelle",
            "niveau": "province",
            "nature": "taux, aucun effectif de cas ni de décès",
            "debut": liste_periodes[0]["code"] if liste_periodes else None,
            "fin": liste_periodes[-1]["code"] if liste_periodes else None,
            "derniere_periode_couverte": derniere,
            "derniere_periode_partielle": bool(partielle),
            "provinces_attendues": len(REF["provinces"]),
            "provinces_presentes": len(presentes),
            "provinces_absentes": absentes,
            "actualise": maintenant.strftime("%Y-%m-%d %H:%M UTC"),
        },
        "periodes": liste_periodes,
        "provinces": REF["provinces"],
        "maladies": [
            {
                "code": cle,
                "libelle": LIBELLES.get(cle, cle.capitalize()),
                "incidence": "attaque" if m["att"] else ("incidence" if m["inc"] else None),
                "letalite": m["let"],
                "mdo": cle in MDO,
            }
            for cle, m in sorted(maladies.items(), key=lambda x: LIBELLES.get(x[0], x[0]))
        ],
        "valeurs": valeurs,
        "anomalies": anomalies,
    }

    with open("surveillance.json", "w", encoding="utf-8") as fh:
        json.dump(sortie, fh, ensure_ascii=False, separators=(",", ":"))

    print("Surveillance actualisee depuis %s : %d periodes dont %d couvertes, "
          "%d maladies, %d provinces sur %d, %d anomalies."
          % (os.path.basename(chemin), len(liste_periodes), len(couvertes),
             len(maladies), len(presentes), len(REF["provinces"]), len(anomalies)))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
