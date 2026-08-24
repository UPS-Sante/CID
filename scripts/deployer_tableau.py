# -*- coding: utf-8 -*-
"""Publie le tableau de bord depuis le dossier Drive de deploiement.

Le compte de service deja en place pour la synchronisation des donnees sert
aussi ici. Le script cherche un sous-dossier de deploiement dans le Drive du
CID, retient la version la plus recente de chaque fichier attendu, la controle,
et ne l'ecrit dans le depot que si elle passe tous les controles.

Un fichier refuse laisse la version en ligne intacte : le tableau de bord ne
peut pas etre remplace par une version tronquee ou invalide.

Environnement requis :
  GOOGLE_SERVICE_ACCOUNT_JSON  cle JSON du compte de service (secret GitHub)
  DRIVE_FOLDER_ID              identifiant du dossier racine CID
  DOSSIER_DEPLOIEMENT          nom du sous-dossier, par defaut "Deploiement"
  VERIFICATION_SEULE           "1" pour controler sans rien ecrire
"""

import json
import os
import subprocess
import sys
import tempfile

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

API = "https://www.googleapis.com/drive/v3"
DOSSIER = "application/vnd.google-apps.folder"

NOM_DOSSIER = os.environ.get("DOSSIER_DEPLOIEMENT", "Deploiement")
VERIFICATION_SEULE = os.environ.get("VERIFICATION_SEULE", "") == "1"

# Seuls ces fichiers peuvent etre publies, et chacun selon son controle.
# Deposer autre chose dans le dossier Drive reste sans effet sur le depot.
ATTENDUS = {
    "index.html": "html",
    "seuils_rag.json": "json",
    "referentiel.json": "json",
    "correspondance_indicateurs.json": "json",
    "sao_league.json": "json",
    "config_sources.json": "json",
}

# Bornes de taille par type, garde-fou contre un fichier vide ou aberrant.
BORNES = {"html": (200_000, 5_000_000), "json": (20, 2_000_000)}

# Marqueurs de structure : leur absence signale un fichier qui n'est pas le
# tableau de bord, ou dont une partie a ete perdue en cours de route.
MARQUEURS_HTML = [
    "<!DOCTYPE html", "</html>", 'id="main"',
    "function renderMain", "PROVINCE_PATHS", "CID_PLAFOND",
]
BALISES_UNIQUES = ["html", "body", "style", "script"]


def session():
    infos = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        infos, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


def lister(s, parent):
    """Contenu direct d'un dossier, pagination comprise."""
    elements, jeton = [], None
    while True:
        params = {"q": f"'{parent}' in parents and trashed = false",
                  "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                  "pageSize": 200, "supportsAllDrives": "true",
                  "includeItemsFromAllDrives": "true"}
        if jeton:
            params["pageToken"] = jeton
        r = s.get(f"{API}/files", params=params)
        r.raise_for_status()
        corps = r.json()
        elements += corps.get("files", [])
        jeton = corps.get("nextPageToken")
        if not jeton:
            return elements


def telecharger(s, identifiant):
    r = s.get(f"{API}/files/{identifiant}",
              params={"alt": "media", "supportsAllDrives": "true"}, stream=True)
    r.raise_for_status()
    return r.content


def controler_html(octets):
    """Controles de structure et de syntaxe du tableau de bord."""
    motifs = []
    try:
        texte = octets.decode("utf-8")
    except UnicodeDecodeError:
        return ["le fichier n'est pas en UTF-8"]

    debut = texte.lstrip()[:40].lower()
    if not debut.startswith("<!doctype html"):
        motifs.append("le fichier ne commence pas par une declaration HTML")
    if not texte.rstrip().endswith("</html>"):
        motifs.append("le fichier ne se termine pas par </html>, signe d'une troncature")

    for m in MARQUEURS_HTML:
        if m not in texte:
            motifs.append(f"marqueur absent : {m}")

    for b in BALISES_UNIQUES:
        ouv, fer = texte.count("<" + b), texte.count("</" + b + ">")
        if ouv != fer:
            motifs.append(f"balises {b} desequilibrees : {ouv} ouvrantes, {fer} fermantes")

    # Controle de syntaxe du script : une erreur ici rendrait le tableau
    # de bord muet, l'analyse statique de Node l'attrape avant publication.
    i, j = texte.find("<script>"), texte.rfind("</script>")
    if i == -1 or j <= i:
        motifs.append("aucun bloc script exploitable")
    else:
        chemin = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                             encoding="utf-8") as f:
                f.write(texte[i + len("<script>"):j])
                chemin = f.name
            r = subprocess.run(["node", "--check", chemin],
                               capture_output=True, text=True)
            if r.returncode != 0:
                ligne = next((l for l in (r.stderr or "").splitlines()
                              if "Error" in l or "error" in l), "erreur de syntaxe")
                motifs.append("script invalide : " + ligne.strip()[:160])
        except FileNotFoundError:
            motifs.append("Node absent du poste, controle de syntaxe impossible")
        finally:
            if chemin and os.path.exists(chemin):
                os.unlink(chemin)
    return motifs


def controler_json(octets):
    try:
        valeur = json.loads(octets.decode("utf-8"))
    except UnicodeDecodeError:
        return ["le fichier n'est pas en UTF-8"]
    except json.JSONDecodeError as e:
        return [f"JSON invalide : {e}"]
    if not isinstance(valeur, (dict, list)) or not valeur:
        return ["JSON vide ou de forme inattendue"]
    return []


def controler(nom, octets):
    genre = ATTENDUS[nom]
    mini, maxi = BORNES[genre]
    motifs = []
    if len(octets) < mini:
        motifs.append(f"taille de {len(octets)} octets, en deca du plancher de {mini}")
    elif len(octets) > maxi:
        motifs.append(f"taille de {len(octets)} octets, au dela du plafond de {maxi}")
    else:
        motifs += controler_html(octets) if genre == "html" else controler_json(octets)
    return motifs


def main():
    s = session()
    racine = os.environ["DRIVE_FOLDER_ID"]

    dossiers = [e for e in lister(s, racine)
                if e["mimeType"] == DOSSIER and e["name"].strip().lower() == NOM_DOSSIER.lower()]
    if not dossiers:
        print(f"Aucun sous-dossier « {NOM_DOSSIER} » dans le Drive du CID. Rien a publier.")
        return 0
    if len(dossiers) > 1:
        print(f"Plusieurs sous-dossiers « {NOM_DOSSIER} » : lever l'ambiguite dans le Drive.")
        return 1

    fichiers = [e for e in lister(s, dossiers[0]["id"]) if e["mimeType"] != DOSSIER]
    if not fichiers:
        print(f"Le dossier « {NOM_DOSSIER} » est vide. Rien a publier.")
        return 0

    # Une nouvelle version se depose a cote de l'ancienne : on retient la plus
    # recente de chaque nom, l'historique Drive reste consultable.
    retenus = {}
    for f in fichiers:
        nom = f["name"].strip()
        if nom not in ATTENDUS:
            print(f"  ignore   {nom} (hors de la liste des fichiers publiables)")
            continue
        if nom not in retenus or f.get("modifiedTime", "") > retenus[nom].get("modifiedTime", ""):
            retenus[nom] = f
    if not retenus:
        print("Aucun fichier publiable dans le dossier.")
        return 0

    publies, refuses = [], []
    for nom, f in sorted(retenus.items()):
        octets = telecharger(s, f["id"])
        motifs = controler(nom, octets)
        if motifs:
            refuses.append(nom)
            print(f"  REFUSE   {nom} ({len(octets)} octets, depose le {f.get('modifiedTime', 'date inconnue')})")
            for m in motifs:
                print(f"             {m}")
            continue
        if os.path.exists(nom) and open(nom, "rb").read() == octets:
            print(f"  inchange {nom}")
            continue
        if VERIFICATION_SEULE:
            print(f"  valide   {nom} ({len(octets)} octets), non ecrit : verification seule")
            continue
        with open(nom, "wb") as sortie:
            sortie.write(octets)
        publies.append(nom)
        print(f"  publie   {nom} ({len(octets)} octets, depose le {f.get('modifiedTime', 'date inconnue')})")

    if refuses:
        print("\nPublication interrompue : " + ", ".join(refuses)
              + ". La version en ligne reste inchangee.")
        return 1
    print("\n" + (", ".join(publies) + " ecrit(s) dans le depot." if publies
                  else "Aucun changement a publier."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
