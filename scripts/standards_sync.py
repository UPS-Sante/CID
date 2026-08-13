# -*- coding: utf-8 -*-
"""Valide un projet de standards ZR contre le referentiel des annees
anterieures, applique les corrections demontrables, et construit
standards.json, la couche de reference des denominateurs du tableau de bord.

Le principe est celui du referentiel arbitre : une valeur du projet n'est
corrigee que lorsque le referentiel anterieur etablit mecaniquement l'erreur.
Trois familles de corrections sont admises, chacune journalisee ligne a ligne.

  Propagation de libelle. Lorsque le rattachement d'un centre de sante differe
  du referentiel, que le district du projet precede alphabetiquement le
  district du referentiel dans la meme province, et que le centre n'apparait
  pas par ailleurs sous son district de reference, le rattachement du
  referentiel est retabli. Ce motif provient des cellules fusionnees du
  classeur source : la premiere ligne de chaque bloc porte seule le libelle,
  et sa perte fait heriter tout le bloc du district precedent. Le sens
  alphabetique systematique distingue ce defaut d'une reorganisation reelle,
  qui n'aurait aucune raison d'etre unidirectionnelle.

  Coherence province et district. Lorsque la province d'une ligne ne
  correspond pas a celle de son district, etablie par le referentiel et par
  les autres lignes du projet, la province est alignee sur le district, qui
  est le libelle le plus fin.

  Doublon arbitre. Lorsqu'un meme centre apparait deux fois avec des
  populations divergentes, la ligne dont la population prolonge la
  trajectoire du referentiel est conservee, l'autre est ecartee et
  consignee avec sa valeur.

Tout ecart qui ne remplit pas ces conditions est signale sans correction :
transferts de rattachement a confirmer, centres nouveaux ou disparus,
populations en rupture de trajectoire, changements de convention sur la
population cible.

Sorties : standards.json, AAAAMMJJ_ZR_standards_<annee>_corrige.csv

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

DOSSIER = os.environ.get("DOSSIER_STANDARDS",
                         os.path.join(os.environ.get("BRUT", "brut"), "STANDARDS"))
SEUIL_CROISSANCE = 0.25   # rupture de trajectoire au-dela de 25 pour cent
SEUIL_RATIO = (3.5, 5.5)  # bornes de plausibilite du ratio cible sur population


def simplifier(t):
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def nombre(v):
    v = str(v if v is not None else "").replace("\u202f", "").replace("\xa0", "")
    v = v.replace(",", "").replace(" ", "").strip()
    if v in ("", "nan", "-", "#DIV/0!", "#REF!", "#N/A"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


REF_PROVINCES = None
def canoniser_province(nom):
    """Rattache un libelle de province au nom canonique du referentiel du
    tableau de bord, pour que les jointures inter-sources ne dependent pas
    des graphies du classeur source."""
    global REF_PROVINCES
    if REF_PROVINCES is None:
        try:
            ref = json.load(open(os.environ.get("REFERENTIEL", "referentiel.json"),
                                 encoding="utf-8"))
            cles = {simplifier(x): x for x in ref["provinces"]}
            cles.update(ref.get("variantes", {}))
            REF_PROVINCES = cles
        except Exception:
            REF_PROVINCES = {}
    cle = simplifier(nom)
    if cle in REF_PROVINCES:
        return REF_PROVINCES[cle]
    for k, canonique in REF_PROVINCES.items():
        if k and (k in cle or cle in k):
            return canonique
    return str(nom).strip()


def lire_csv(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def trouver_fichiers():
    """Rend (referentiel, projet). Le referentiel porte une colonne year,
    le projet porte un en-tete Centre de Sante sans colonne year."""
    ref = projet = None
    if not os.path.isdir(DOSSIER):
        return None, None
    for f in sorted(os.listdir(DOSSIER)):
        if not f.lower().endswith(".csv"):
            continue
        chemin = os.path.join(DOSSIER, f)
        tete = lire_csv(chemin)[:12]
        plat = [simplifier(c) for ligne in tete for c in ligne]
        if "year" in plat and "zr name standards" in " ".join(plat):
            ref = chemin
        elif "centre de sante" in " ".join(plat):
            projet = chemin
    return ref, projet


# ------------------------------------------------------------ referentiel

def charger_referentiel(chemin):
    lignes = lire_csv(chemin)
    entete = {simplifier(c): i for i, c in enumerate(lignes[0]) if c.strip()}
    col = lambda n: entete.get(simplifier(n))
    ix = {k: col(v) for k, v in {
        "annee": "year", "province": "province_standards",
        "district": "district_standards", "zr": "ZR_name_standards",
        "pop": "Population_2020_standards", "cible": "0-11_pop_standards",
    }.items()}
    donnees, illisibles = [], 0
    for l in lignes[1:]:
        if len(l) <= max(v for v in ix.values() if v is not None):
            continue
        annee = nombre(l[ix["annee"]])
        zr = l[ix["zr"]].strip()
        if not annee or not zr:
            continue
        cible = nombre(l[ix["cible"]])
        if cible is None:
            illisibles += 1
        donnees.append({
            "annee": int(annee),
            "province": canoniser_province(l[ix["province"]]),
            "district": l[ix["district"]].strip(),
            "zr": zr, "nom": re.sub(r"^[A-Za-z]{2,3}_", "", zr),
            "pop": nombre(l[ix["pop"]]), "cible": cible,
        })
    return donnees, illisibles


# ------------------------------------------------------------ projet

def charger_projet(chemin):
    lignes = lire_csv(chemin)
    depart = entete = None
    for i, l in enumerate(lignes[:15]):
        cles = [simplifier(c) for c in l]
        if "centre de sante" in cles and "province" in cles:
            entete = {simplifier(c): j for j, c in enumerate(l) if c.strip()}
            depart = i + 1
            break
    if entete is None:
        return None, None, 0
    col = lambda n: entete.get(simplifier(n))
    ix = {"province": col("Province"), "district": col("District"),
          "cs": col("Centre de Santé"), "unique": col("Centre_de_santé_unique"),
          "type": col("Type"), "pop": col("Pop 2020"),
          "cible": col("Enfants 0-11 mois"), "bcg": col("BCG")}
    donnees, vides = [], 0
    for pos, l in enumerate(lignes[depart:]):
        v = lambda k: (l[ix[k]].strip() if ix[k] is not None and ix[k] < len(l) else "") or None
        cs = v("cs")
        if not cs:
            vides += 1
            continue
        donnees.append({
            "ligne": depart + pos + 1,
            "province": canoniser_province(v("province")) if v("province") else None,
            "district": v("district"),
            "cs": cs, "unique": v("unique"), "type": v("type"),
            "pop": nombre(v("pop")), "cible": nombre(v("cible")),
            "vaccins": v("bcg") is not None,
            "brut": l,
        })
    return donnees, entete, vides


# ------------------------------------------------------------ moteur

def principal():
    chemin_ref, chemin_projet = trouver_fichiers()
    if not chemin_ref:
        print("Referentiel des standards introuvable dans %s." % DOSSIER)
        return 1

    reference, cibles_illisibles = charger_referentiel(chemin_ref)
    annees_ref = sorted({r["annee"] for r in reference})
    annee_ref = annees_ref[-1]
    ref = [r for r in reference if r["annee"] == annee_ref]

    corrections, anomalies = [], []
    if cibles_illisibles:
        anomalies.append({"type": "cible_illisible_referentiel",
                          "lignes": cibles_illisibles,
                          "detail": "valeurs de population cible illisibles dans le référentiel, ignorées des totaux"})

    # index du referentiel
    par_unique = {}
    for r in ref:
        par_unique.setdefault(simplifier(r["zr"]), []).append(r)
    par_prov_nom = {}
    for r in ref:
        par_prov_nom.setdefault((simplifier(r["province"]), simplifier(r["nom"])), []).append(r)
    prov_du_district = {}
    for r in ref:
        prov_du_district.setdefault(simplifier(r["district"]), set()).add(r["province"])

    resultat = {"meta": {}, "annees": {}, "corrections": corrections,
                "anomalies": anomalies, "regles": [
        "Une correction n'est appliquée que lorsque le référentiel antérieur établit mécaniquement l'erreur : propagation alphabétique de libellé, incohérence entre la province et le district, doublon dont une seule valeur prolonge la trajectoire.",
        "Tout autre écart est signalé sans correction et reste à trancher par le programme.",
        "Les dénominateurs d'une année donnée relèvent du millésime de standards correspondant ; un calcul portant sur une année sans standards doit nommer le millésime employé.",
    ]}

    for a in annees_ref:
        bloc = [r for r in reference if r["annee"] == a]
        provs = {}
        for r in bloc:
            p = provs.setdefault(r["province"], {"ds": set(), "zr": 0, "pop": 0, "cible": 0})
            p["ds"].add(r["district"]); p["zr"] += 1
            p["pop"] += r["pop"] or 0; p["cible"] += r["cible"] or 0
        resultat["annees"][str(a)] = {
            "statut": "référentiel",
            "national": {"ds": len({r["district"] for r in bloc}), "zr": len(bloc),
                         "pop": int(sum(r["pop"] or 0 for r in bloc)),
                         "cible": int(sum(r["cible"] or 0 for r in bloc))},
            "provinces": {k: {"ds": len(v["ds"]), "districts": sorted(v["ds"]), "zr": v["zr"],
                              "pop": int(v["pop"]), "cible": int(v["cible"])}
                          for k, v in sorted(provs.items())},
        }

    # ------------------------------------------------ projet, s'il existe
    annee_projet = None
    if chemin_projet:
        m = re.search(r"(20\d\d)", os.path.basename(chemin_projet))
        annee_projet = int(m.group(1)) if m else annee_ref + 1
        projet, entete_projet, vides = charger_projet(chemin_projet)
        if projet is None:
            print("En-tete du projet introuvable dans %s." % os.path.basename(chemin_projet))
            return 1
        if vides:
            anomalies.append({"type": "lignes_vides_ecartees", "lignes": vides,
                              "detail": "lignes sans nom de centre, résidus de formules du classeur source"})

        districts_projet = {(simplifier(x["province"] or ""), simplifier(x["district"] or ""))
                            for x in projet}

        # rattachement au referentiel
        sans_ref = 0
        for x in projet:
            cands = par_unique.get(simplifier(x["unique"] or ""), [])
            if not cands:
                cands = par_prov_nom.get((simplifier(x["province"] or ""), simplifier(x["cs"])), [])
            x["ref"] = cands[0] if len(cands) == 1 else None
            if not cands:
                sans_ref += 1

        # R1 propagation de libelle sur le district
        for x in projet:
            r = x["ref"]
            if not r or simplifier(x["district"] or "") == simplifier(r["district"]):
                continue
            meme_prov = simplifier(x["province"] or "") == simplifier(r["province"])
            alpha = simplifier(x["district"] or "") < simplifier(r["district"])
            deja = any(simplifier(y["district"] or "") == simplifier(r["district"])
                       and simplifier(y["cs"]) == simplifier(x["cs"])
                       for y in projet if y is not x)
            if meme_prov and alpha and not deja:
                corrections.append({"regle": "propagation_libelle", "ligne": x["ligne"],
                                    "cs": x["cs"], "province": r["province"],
                                    "de": x["district"], "vers": r["district"]})
                x["district"] = r["district"]
            elif meme_prov:
                anomalies.append({"type": "rattachement_divergent", "ligne": x["ligne"],
                                  "cs": x["cs"], "province": x["province"],
                                  "projet": x["district"], "referentiel": r["district"],
                                  "detail": "changement de district hors motif de propagation, à confirmer par le programme"})

        # R2 coherence province et district
        for x in projet:
            provs = prov_du_district.get(simplifier(x["district"] or ""), set())
            if len(provs) == 1 and x["province"] not in provs:
                seule = next(iter(provs))
                # un bloc entier qui change de province est un transfert, pas une faute de frappe
                bloc = [y for y in projet if simplifier(y["district"] or "") == simplifier(x["district"] or "")]
                majoritaire = sum(1 for y in bloc if y["province"] == x["province"]) > len(bloc) / 2
                if majoritaire and len(bloc) > 2:
                    continue
                corrections.append({"regle": "province_alignee_sur_district", "ligne": x["ligne"],
                                    "cs": x["cs"], "district": x["district"],
                                    "de": x["province"], "vers": seule})
                x["province"] = seule

        # transferts provinciaux de blocs entiers, signales sans correction
        vus = set()
        for x in projet:
            r = x["ref"]
            if not r or x["province"] == r["province"]:
                continue
            cle = (simplifier(x["district"] or ""), r["province"], x["province"])
            if cle in vus:
                continue
            vus.add(cle)
            n = sum(1 for y in projet if y["ref"] and simplifier(y["district"] or "") == cle[0]
                    and y["ref"]["province"] == r["province"] and y["province"] == x["province"])
            if n > 2:
                anomalies.append({"type": "transfert_provincial_a_confirmer",
                                  "district": x["district"], "de": r["province"],
                                  "vers": x["province"], "centres": n,
                                  "detail": "bloc cohérent rattaché à une autre province que dans le référentiel"})

        # R3 doublons
        groupes = {}
        for x in projet:
            groupes.setdefault((simplifier(x["province"] or ""), simplifier(x["district"] or ""),
                                simplifier(x["cs"])), []).append(x)
        for cle, xs in groupes.items():
            if len(xs) == 1:
                continue
            xs.sort(key=lambda y: (y["type"] != "CS", y["ligne"]))
            r = xs[0]["ref"]
            garde = xs[0]
            if r and r["pop"]:
                def ecart(y):
                    return abs((y["pop"] or 0) / r["pop"] - 1)
                garde = min(xs, key=ecart)
            for y in xs:
                if y is garde:
                    continue
                y["ecarte"] = True
                corrections.append({"regle": "doublon_ecarte", "ligne": y["ligne"],
                                    "cs": y["cs"], "province": garde["province"],
                                    "district": garde["district"],
                                    "pop_ecartee": y["pop"], "pop_conservee": garde["pop"]})
                if y["pop"] and garde["pop"] and abs(y["pop"] - garde["pop"]) / max(garde["pop"], 1) > 0.1:
                    anomalies.append({"type": "doublon_population_divergente", "cs": y["cs"],
                                      "district": garde["district"],
                                      "valeurs": [garde["pop"], y["pop"]],
                                      "detail": "populations divergentes pour un même centre, la valeur en trajectoire du référentiel est conservée"})
        projet = [x for x in projet if not x.get("ecarte")]

        # ------------------------------------------------ controles sans correction
        ruptures = []
        for x in projet:
            r = x["ref"]
            if r and r["pop"] and x["pop"]:
                g = x["pop"] / r["pop"] - 1
                if abs(g) > SEUIL_CROISSANCE:
                    ruptures.append((x["province"], x["district"], x["cs"], r["pop"], x["pop"], round(100 * g, 1)))
        if ruptures:
            ruptures.sort(key=lambda t: -abs(t[5]))
            anomalies.append({"type": "population_en_rupture", "centres": len(ruptures),
                              "exemples": [{"cs": t[2], "district": t[1], "avant": t[3],
                                            "apres": t[4], "variation_pct": t[5]} for t in ruptures[:10]],
                              "detail": "variation de population supérieure à 25 pour cent entre le référentiel et le projet"})

        hors_ratio = sum(1 for x in projet if x["pop"] and x["cible"]
                         and not (SEUIL_RATIO[0] <= 100 * x["cible"] / x["pop"] <= SEUIL_RATIO[1]))
        if hors_ratio:
            anomalies.append({"type": "ratio_cible_hors_bornes", "centres": hors_ratio,
                              "detail": "ratio population cible sur population hors de l'intervalle de 3,5 à 5,5 pour cent"})

        pop_ref = sum(r["pop"] or 0 for r in ref)
        pop_proj = sum(x["pop"] or 0 for x in projet)
        cible_ref = sum(r["cible"] or 0 for r in ref)
        cible_proj = sum(x["cible"] or 0 for x in projet)
        if pop_ref and pop_proj:
            anomalies.append({"type": "libelle_population_perime",
                              "detail": "la colonne du projet est intitulée Pop 2020 mais porte une projection de l'année du projet, en croissance de %.1f pour cent sur le référentiel" % (100 * (pop_proj / pop_ref - 1))})
        if pop_ref and pop_proj and cible_ref and cible_proj:
            r_ref, r_proj = 100 * cible_ref / pop_ref, 100 * cible_proj / pop_proj
            if abs(r_ref - r_proj) > 0.15:
                anomalies.append({"type": "convention_cible_modifiee",
                                  "ratio_referentiel": round(r_ref, 2), "ratio_projet": round(r_proj, 2),
                                  "detail": "le ratio de population cible du projet revient à la convention antérieure à %d ; à millésime de standards différent, les couvertures calculées ne sont pas comparables" % annee_ref})

        sans_vaccins = sum(1 for x in projet if not x["vaccins"])
        if sans_vaccins:
            anomalies.append({"type": "besoins_vaccinaux_absents", "centres": sans_vaccins,
                              "detail": "centres sans besoins vaccinaux calculés dans le projet, affichage ND"})
        if sans_ref:
            anomalies.append({"type": "centres_nouveaux", "centres": sans_ref,
                              "detail": "centres du projet absents du référentiel, ajouts à confirmer"})
        cles_projet = {simplifier(x["unique"] or "") for x in projet} | \
                      {(simplifier(x["province"] or ""), simplifier(x["cs"])) for x in projet}
        disparus = [r for r in ref if simplifier(r["zr"]) not in cles_projet
                    and (simplifier(r["province"]), simplifier(r["nom"])) not in cles_projet]
        if disparus:
            anomalies.append({"type": "centres_du_referentiel_absents", "centres": len(disparus),
                              "exemples": [{"cs": r["nom"], "district": r["district"],
                                            "province": r["province"]} for r in disparus[:10]],
                              "detail": "centres du référentiel %d absents du projet" % annee_ref})

        # ------------------------------------------------ agregation du projet corrige
        provs = {}
        for x in projet:
            p = provs.setdefault(x["province"], {"ds": set(), "zr": 0, "pop": 0, "cible": 0})
            p["ds"].add(x["district"]); p["zr"] += 1
            p["pop"] += x["pop"] or 0; p["cible"] += x["cible"] or 0
        resultat["annees"][str(annee_projet)] = {
            "statut": "projet corrigé, en attente de validation par le programme",
            "national": {"ds": len({x["district"] for x in projet}), "zr": len(projet),
                         "pop": int(pop_proj), "cible": int(cible_proj)},
            "provinces": {k: {"ds": len(v["ds"]), "districts": sorted(v["ds"]), "zr": v["zr"],
                              "pop": int(v["pop"]), "cible": int(v["cible"])}
                          for k, v in sorted(provs.items())},
        }

        # ------------------------------------------------ csv corrige
        nom_sortie = "%s_ZR_standards_%d_corrige.csv" % (
            datetime.now(timezone.utc).strftime("%Y%m%d"), annee_projet)
        inv = sorted(entete_projet.items(), key=lambda kv: kv[1])
        noms_cols = [k for k, _ in inv]
        idx = {k: i for i, (k, _) in enumerate(inv)}
        with open(nom_sortie, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([c for c in noms_cols] + ["correction appliquee"])
            corr_par_ligne = {}
            for c in corrections:
                corr_par_ligne.setdefault(c["ligne"], []).append(
                    "%s : %s vers %s" % (c["regle"].replace("_", " "), c.get("de", ""), c.get("vers", "")))
            for x in sorted(projet, key=lambda y: (y["province"] or "", y["district"] or "", y["cs"])):
                l = list(x["brut"]) + [""] * (len(noms_cols) - len(x["brut"]))
                l = l[:len(noms_cols)]
                l[idx["province"]] = x["province"] or ""
                l[idx["district"]] = x["district"] or ""
                w.writerow(l + ["; ".join(corr_par_ligne.get(x["ligne"], []))])
    else:
        nom_sortie = None

    resultat["meta"] = {
        "source_referentiel": os.path.basename(chemin_ref),
        "source_projet": os.path.basename(chemin_projet) if chemin_projet else None,
        "annee_referentiel": annee_ref,
        "annee_projet": annee_projet,
        "corrections": len(corrections),
        "signalements": len(anomalies),
        "actualise": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    with open("standards.json", "w", encoding="utf-8") as fh:
        json.dump(resultat, fh, ensure_ascii=False, separators=(",", ":"))

    n = resultat["annees"].get(str(annee_projet), {}).get("national", {})
    print("Standards actualises : referentiel %s, projet %s%s. "
          "%d corrections journalisees, %d signalements."
          % (annee_ref, annee_projet or "absent",
             " (%d DS, %d ZR)" % (n.get("ds", 0), n.get("zr", 0)) if n else "",
             len(corrections), len(anomalies)))
    if nom_sortie:
        print("Projet corrige ecrit dans %s." % nom_sortie)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
