# -*- coding: utf-8 -*-
"""Telecharge les tableurs du dossier Drive vers brut/, en reproduisant
l'arborescence. Parcours recursif : tout nouveau fichier ou sous-dossier
est pris en compte sans modification.

Environnement requis :
  GOOGLE_SERVICE_ACCOUNT_JSON  cle JSON du compte de service (secret GitHub)
  DRIVE_FOLDER_ID              identifiant du dossier racine
"""

import json
import os
import sys

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

API = "https://www.googleapis.com/drive/v3"
DOSSIER = "application/vnd.google-apps.folder"
GSHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTENSIONS = (".xlsx", ".xls", ".csv")
DESTINATION = os.environ.get("DESTINATION", "brut")


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
                  "fields": "nextPageToken, files(id, name, mimeType, modifiedTime)",
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


def telecharger(s, fichier, cible):
    """Telechargement direct, ou export xlsx pour un Google Sheet."""
    if fichier["mimeType"] == GSHEET:
        url, params = f"{API}/files/{fichier['id']}/export", {"mimeType": XLSX}
        cible += "" if cible.endswith(".xlsx") else ".xlsx"
    else:
        url, params = f"{API}/files/{fichier['id']}", {"alt": "media", "supportsAllDrives": "true"}
    r = s.get(url, params=params, stream=True)
    r.raise_for_status()
    os.makedirs(os.path.dirname(cible), exist_ok=True)
    with open(cible, "wb") as f:
        for morceau in r.iter_content(1 << 16):
            f.write(morceau)
    return cible


def parcourir(s, dossier_id, chemin, inventaire):
    for e in lister(s, dossier_id):
        nom = "".join("_" if c in '\\/:*?"<>|' else c for c in e["name"]).strip()
        if e["mimeType"] == DOSSIER:
            parcourir(s, e["id"], os.path.join(chemin, nom), inventaire)
        elif e["mimeType"] == GSHEET or nom.lower().endswith(EXTENSIONS):
            cible = telecharger(s, e, os.path.join(DESTINATION, chemin, nom))
            inventaire.append({"nom": e["name"], "chemin": cible, "modifie": e.get("modifiedTime")})
            print(f"  {cible}")


def principal():
    racine = os.environ.get("DRIVE_FOLDER_ID") or sys.exit("DRIVE_FOLDER_ID manquant.")
    inventaire = []
    print(f"Synchronisation du dossier {racine} vers {DESTINATION}/")
    parcourir(session(), racine, "", inventaire)
    os.makedirs(DESTINATION, exist_ok=True)
    with open(os.path.join(DESTINATION, "inventaire.json"), "w", encoding="utf-8") as f:
        json.dump(inventaire, f, ensure_ascii=False, indent=2)
    print(f"{len(inventaire)} fichier(s) synchronise(s).")


if __name__ == "__main__":
    principal()
