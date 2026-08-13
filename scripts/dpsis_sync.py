# -*- coding: utf-8 -*-
"""Construit dpsis_qualite.json a partir du releve de completude et de
promptitude depose par la DPSIS dans le dossier Drive du meme nom.

Le classeur porte une ligne par province et par mois, de janvier 2020 a
decembre 2026, avec les taux declares et les volumes de rapports attendus,
saisis et saisis dans le delai. Le script structure ces valeurs sans les
corriger.

Deux precautions gouvernent la lecture. Les periodes non couvertes portent
zero et non une cellule vide : la couverture est donc etablie sur le volume
de rapports effectivement saisis, pas sur la presence d'une valeur. Les
denominateurs sont constants sur les quatre-vingt-quatre periodes, ils
refletent la carte sanitaire courante projetee sur tout l'historique et ne
sont pas des effectifs de periode.

Sortie : dpsis_qualite.json

Environnement :
  BRUT  repertoire des fichiers telecharges depuis Drive (defaut : brut)
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

import xlrd

BRUT = os.environ.get("BRUT", "brut")
DOSSIER_DPSIS = os.environ.get("DOSSIER_DPSIS", "DPSIS")
MOTIFS = ("promptitude", "completude", "dpsis")

MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
        "aout", "septembre", "octobre", "novembre", "decembre"]
MOIS_AFFICHE = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
                "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

# Systemes lus dans le classeur. 'comp' et 'promp' portent le nom exact de la
# colonne, None lorsque la metrique n'est pas exportee pour ce systeme.
SYSTEMES = [
    {"code": "rma_global", "libelle": "RMA global", "source": "DHIS2",
     "perimetre": "Ensemble des formations sanitaires",
     "comp": "Taux de complétude globale", "promp": "Taux de promptitude globale"},
    {"code": "rma_cs", "libelle": "RMA centres de santé", "source": "DHIS2",
     "perimetre": "Centres de santé",
     "comp": "Taux de complétude CS", "promp": "Taux de promptitude CS"},
    {"code": "rma_hdhp", "libelle": "RMA hôpitaux de district et provinciaux", "source": "DHIS2",
     "perimetre": "Hôpitaux de district et hôpitaux provinciaux",
     "comp": "Taux de complétude_HD/HP", "promp": "Taux de promptitude HD/HP"},
    {"code": "rma_hn", "libelle": "RMA hôpitaux nationaux", "source": "DHIS2",
     "perimetre": "Hôpitaux nationaux, N'Djamena et Ouaddaï uniquement",
     "comp": "Taux de complétude_HN", "promp": "Taux de promptitude HN"},
    {"code": "rma_global_2025", "libelle": "RMA global, référentiel 2025", "source": "DHIS2",
     "perimetre": "Série parallèle calculée sur un autre dénominateur",
     "comp": "Taux de complétude globale_2025", "promp": "Taux de promptitude globale 2025"},
    {"code": "ids", "libelle": "Surveillance intégrée", "source": "DHIS2 · IDS",
     "perimetre": "Rapportage hebdomadaire de surveillance",
     "comp": "IDS_Taux de Complétude", "promp": "IDS_Taux de Promptitude"},
    {"code": "vih", "libelle": "VIH", "source": "DHIS2 · PSLS",
     "perimetre": "Rapportage des sites de prise en charge",
     "comp": "VIH_Taux de Complétude", "promp": None},
    {"code": "pev_cs", "libelle": "PEV, rapportage de routine", "source": "DHIS2 · PEV",
     "perimetre": "Centres de santé, activités de vaccination de routine",
     "comp": "PEV_Taux de Complétude CS", "promp": "PEV_Taux de Promptitude CS"},
    {"code": "pev_polio", "libelle": "PEV, campagne polio", "source": "DHIS2 · PEV",
     "perimetre": "Rapportage des campagnes de vaccination contre la poliomyélite",
     "comp": "Pev_Complétude campagne polio", "promp": None},
    {"code": "pev_rougeole", "libelle": "PEV, campagne rougeole", "source": "DHIS2 · PEV",
     "perimetre": "Rapportage des campagnes de vaccination contre la rougeole",
     "comp": "Pev_Taux de rapportage compagne  Rougeole", "promp": None},
]

# Volumes de rapports, colonne du classeur rattachee a une cle courte.
VOLUMES = {
    "cs_attendus": "Nombre de rapports CS attendus",
    "cs_saisis": "Nombre de rapports CS saisis",
    "cs_delai": "Nombre de rapports saisis dans le délai CS",
    "hdhp_attendus": "Nombre de rapports HD/HP attendus",
    "hdhp_saisis": "Nombre de rapports HD/HP saisis",
    "hdhp_delai": "Nombre de rapports saisis dans le délai HD/HP",
    "rapports_globaux": "Nombre des rapports actuels globale",
}

# Elements de carte sanitaire, constants sur tout l'historique du classeur.
CARTE = {
    "districts": "Nombre des Districts Sanitaire",
    "hopitaux_district": "Nombre des Hopitaux District",
    "hopitaux_provinciaux": "Nombre des Hopitaux provinciaux",
    "hopitaux_nationaux": "Nombre des Hopitaux Nationaux",
    "centres_sante_pev": "PEV-Nombre de centre de santé",
}


def simplifier(t):
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", t)).strip().lower()


REF = json.load(open(os.environ.get("REFERENTIEL", "referentiel.json"),
                     encoding="utf-8"))
CLES = {simplifier(p): p for p in REF["provinces"]}
CLES.update(REF["variantes"])
TOTAUX = set(REF["libelles_total"])


def rattacher_province(libelle):
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


def trouver_classeur():
    def collecter(base):
        trouves = []
        for racine, _, fichiers in os.walk(base):
            for f in fichiers:
                if not f.lower().endswith((".xls", ".xlsx")):
                    continue
                if any(m in simplifier(f) for m in MOTIFS):
                    chemin = os.path.join(racine, f)
                    trouves.append((os.path.getmtime(chemin), chemin))
        return trouves

    cible = os.path.join(BRUT, DOSSIER_DPSIS)
    trouves = collecter(cible) if os.path.isdir(cible) else []
    if not trouves:
        trouves = collecter(BRUT)
    return max(trouves)[1] if trouves else None


def periode(libelle):
    """'Janvier 2026' rend '2026-01'."""
    parties = simplifier(libelle).split()
    if len(parties) != 2 or parties[0] not in MOIS:
        return None
    return "%04d-%02d" % (int(parties[1]), MOIS.index(parties[0]) + 1)


def principal():
    chemin = trouver_classeur()
    if not chemin:
        print("Aucun releve DPSIS trouve dans %s." % BRUT)
        return 1

    sh = xlrd.open_workbook(chemin).sheet_by_index(0)
    entete = None
    for r in range(min(10, sh.nrows)):
        ligne = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
        if "organisationunitname" in ligne and "periodname" in ligne:
            entete = {v: k for k, v in enumerate(ligne) if v}
            depart = r + 1
            break
    if not entete:
        print("En-tete introuvable dans %s." % os.path.basename(chemin))
        return 1

    def val(ligne, nom):
        k = entete.get(nom)
        if k is None:
            return None
        v = str(ligne[k]).strip()
        if v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    anomalies = []
    valeurs, volumes, carte = {}, {}, {}
    periodes = {}
    compteur_systeme = {s["code"]: 0 for s in SYSTEMES}
    unites_vues = set()
    inconnues = {}

    for r in range(depart, sh.nrows):
        ligne = [sh.cell_value(r, c) for c in range(sh.ncols)]
        brut_unite = str(ligne[entete["organisationunitname"]]).strip()
        if not brut_unite:
            continue
        cle_p = periode(str(ligne[entete["periodname"]]))
        if not cle_p:
            anomalies.append({"type": "periode_illisible",
                              "detail": str(ligne[entete["periodname"]])})
            continue
        unite = rattacher_province(brut_unite)
        if not unite:
            inconnues[brut_unite] = inconnues.get(brut_unite, 0) + 1
            continue
        unites_vues.add(unite)

        annee, mois = int(cle_p[:4]), int(cle_p[5:])
        periodes.setdefault(cle_p, {"code": cle_p,
                                    "libelle": "%s %d" % (MOIS_AFFICHE[mois - 1], annee),
                                    "unites": 0})

        vol = {k: val(ligne, nom) for k, nom in VOLUMES.items()}
        # Couverture etablie sur le volume saisi, les periodes vides portant zero.
        couverte = (vol.get("rapports_globaux") or 0) > 0
        if not couverte:
            continue
        periodes[cle_p]["unites"] += 1

        volumes.setdefault(cle_p, {})[unite] = {k: v for k, v in vol.items() if v is not None}
        carte.setdefault(unite, {k: val(ligne, nom) for k, nom in CARTE.items()})

        bloc = {}
        for s in SYSTEMES:
            c = val(ligne, s["comp"]) if s["comp"] else None
            p = val(ligne, s["promp"]) if s["promp"] else None
            if c is None and p is None:
                continue
            compteur_systeme[s["code"]] += 1
            e = {}
            if c is not None:
                e["comp"] = round(c, 2)
                if c > 100:
                    anomalies.append({"type": "completude_hors_bornes", "periode": cle_p,
                                      "unite": unite, "systeme": s["code"], "valeur": round(c, 2),
                                      "detail": "taux de complétude supérieur à 100 pour cent"})
            if p is not None:
                e["promp"] = round(p, 2)
                if p > 100:
                    anomalies.append({"type": "promptitude_hors_bornes", "periode": cle_p,
                                      "unite": unite, "systeme": s["code"], "valeur": round(p, 2),
                                      "detail": "taux de promptitude supérieur à 100 pour cent"})
            if c is not None and p is not None and p > c:
                anomalies.append({"type": "promptitude_superieure_completude", "periode": cle_p,
                                  "unite": unite, "systeme": s["code"],
                                  "completude": round(c, 2), "promptitude": round(p, 2),
                                  "detail": "un rapport saisi dans le délai est nécessairement un rapport saisi"})
            bloc[s["code"]] = e

        # Recoupement des taux declares avec les volumes exportes.
        att, sai = vol.get("cs_attendus"), vol.get("cs_saisis")
        dec = val(ligne, "Taux de complétude CS")
        if att and sai is not None and dec is not None:
            calcul = round(100 * sai / att, 2)
            if abs(calcul - dec) > 0.5:
                anomalies.append({"type": "taux_non_recalculable", "periode": cle_p,
                                  "unite": unite, "systeme": "rma_cs",
                                  "declare": dec, "recalcule": calcul,
                                  "detail": "le taux déclaré ne correspond pas au rapport des volumes exportés"})
        # Un delai de saisie egal au volume saisi indique que la promptitude
        # n'a pas ete mesuree sur la periode, elle recopie la completude.
        delai = vol.get("cs_delai")
        if att and sai and delai is not None and delai == sai:
            anomalies.append({"type": "promptitude_non_mesuree", "periode": cle_p,
                              "unite": unite, "systeme": "rma_cs",
                              "detail": "tous les rapports saisis sont comptés dans le délai, "
                                        "la promptitude recopie la complétude"})
        if att and sai is not None and sai > att:
            anomalies.append({"type": "saisis_superieurs_attendus", "periode": cle_p,
                              "unite": unite, "saisis": sai, "attendus": att,
                              "detail": "le nombre de rapports saisis dépasse le nombre attendu"})

        if bloc:
            valeurs.setdefault(cle_p, {})[unite] = bloc

    for u, n in sorted(inconnues.items()):
        anomalies.append({"type": "unite_non_rattachee", "detail": u, "lignes": n})

    liste_periodes = []
    for cle in sorted(periodes):
        p = periodes[cle]
        p["couverte"] = p["unites"] > 0
        p["taux"] = round(100 * p["unites"] / (len(REF["provinces"]) + 1), 1)
        liste_periodes.append(p)
    couvertes = [p["code"] for p in liste_periodes if p["couverte"]]
    derniere = couvertes[-1] if couvertes else None

    maintenant = datetime.now(timezone.utc)
    if derniere == "%04d-%02d" % (maintenant.year, maintenant.month):
        for p in liste_periodes:
            if p["code"] == derniere:
                p["partielle"] = True

    presentes = sorted(unites_vues - {"National"})
    absentes = [p for p in REF["provinces"] if p not in presentes]
    for u in absentes:
        anomalies.append({"type": "unite_absente_export", "unite": u,
                          "detail": "province du référentiel absente du relevé, affichage ND"})

    systemes = []
    for s in SYSTEMES:
        n = compteur_systeme[s["code"]]
        entree = {k: s[k] for k in ("code", "libelle", "source", "perimetre")}
        entree["completude"] = bool(s["comp"])
        entree["promptitude"] = bool(s["promp"])
        entree["renseigne"] = n
        if n == 0:
            entree["reserve"] = "Colonne présente dans le relevé mais jamais renseignée."
            anomalies.append({"type": "systeme_non_renseigne", "systeme": s["code"],
                              "detail": s["libelle"] + " : colonne exportée sans aucune valeur"})
        systemes.append(entree)

    anomalies.append({
        "type": "denominateurs_constants",
        "detail": "les volumes attendus et les effectifs de carte sanitaire sont identiques "
                  "sur les quatre-vingt-quatre périodes du relevé, ils reflètent la situation "
                  "courante projetée sur tout l'historique et ne sont pas des effectifs de période",
    })
    anomalies.append({
        "type": "zero_non_distinguable",
        "detail": "le relevé porte zéro et non une cellule vide pour les périodes sans "
                  "rapportage, la couverture est donc établie sur le volume de rapports saisis",
    })
    anomalies.append({
        "type": "series_globales_paralleles",
        "detail": "deux séries globales coexistent, calculées sur des dénominateurs distincts, "
                  "elles ne sont pas substituables et ne doivent pas être moyennées ensemble",
    })

    sortie = {
        "meta": {
            "source": "Relevé DPSIS de complétude et de promptitude",
            "fichier": os.path.basename(chemin),
            "granularite": "mensuelle",
            "niveau": "province",
            "debut": liste_periodes[0]["code"] if liste_periodes else None,
            "fin": liste_periodes[-1]["code"] if liste_periodes else None,
            "derniere_periode_couverte": derniere,
            "provinces_attendues": len(REF["provinces"]),
            "provinces_presentes": len(presentes),
            "provinces_absentes": absentes,
            "actualise": maintenant.strftime("%Y-%m-%d %H:%M UTC"),
        },
        "periodes": liste_periodes,
        "provinces": REF["provinces"],
        "systemes": systemes,
        "carte_sanitaire": carte,
        "valeurs": valeurs,
        "volumes": volumes,
        "anomalies": anomalies,
    }

    with open("dpsis_qualite.json", "w", encoding="utf-8") as fh:
        json.dump(sortie, fh, ensure_ascii=False, separators=(",", ":"))

    print("Qualite DPSIS actualisee depuis %s : %d periodes couvertes sur %d, "
          "%d provinces sur %d, %d systemes dont %d renseignes, %d anomalies."
          % (os.path.basename(chemin), len(couvertes), len(liste_periodes),
             len(presentes), len(REF["provinces"]), len(systemes),
             sum(1 for s in systemes if s["renseigne"]), len(anomalies)))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
