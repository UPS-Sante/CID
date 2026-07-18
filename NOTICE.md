# Synchronisation automatique Drive vers le tableau de bord

Ce dispositif relie le dossier Drive du CID au depot GitHub `ikabo5/CID`.
Chaque nuit a 3h UTC (4h a N'Djamena), une action GitHub telecharge les fichiers
deposes dans le Drive, les normalise au format long et publie trois fichiers dans
`data/` : la base consolidee, le catalogue des provinces, periodes et indicateurs,
et le rapport de validation. Le tableau de bord lit ensuite `data/consolide.json`.

## Mise en place, une seule fois

### 1. Creer le compte de service Google

1. Ouvrir console.cloud.google.com avec le compte Google du CID.
2. Creer un projet (nom libre, par exemple `cid-sync`).
3. Menu « API et services », « Bibliotheque » : activer **Google Drive API**.
4. Menu « Identifiants », « Creer des identifiants », « Compte de service ».
   Donner un nom, valider sans role particulier.
5. Ouvrir le compte de service cree, onglet « Cles », « Ajouter une cle »,
   « Creer une cle », format **JSON**. Un fichier se telecharge : c'est la cle.
6. Noter l'adresse e-mail du compte de service, de la forme
   `cid-sync@nomduprojet.iam.gserviceaccount.com`.

### 2. Partager le dossier Drive avec le compte de service

Dans Drive, clic droit sur le dossier CID, « Partager », coller l'adresse du
compte de service, role **Lecteur**. Le partage couvre toute l'arborescence
(DSR, PEV, TCD_SHP_LV2). Aucun autre acces n'est necessaire.

### 3. Configurer le depot GitHub

Dans `ikabo5/CID`, « Settings », « Secrets and variables », « Actions » :

- Secret `GOOGLE_SERVICE_ACCOUNT_JSON` : coller le contenu integral du fichier JSON.
- Secret `DRIVE_FOLDER_ID` : l'identifiant du dossier racine CID, soit la fin de
  son URL Drive (`1EnRzl8nfwPqAYVQVOU1_BI3hET37VoOx`).

Verifier aussi dans « Settings », « Actions », « General » que « Workflow
permissions » est sur **Read and write permissions**, sinon l'action ne pourra
pas publier les donnees.

### 4. Deposer les fichiers du present paquet dans le depot

```
scripts/drive_sync.py
scripts/normaliser.py
.github/workflows/synchronisation.yml
requirements.txt
.gitignore
```

Commit, push. Onglet « Actions », ouvrir « Synchronisation des donnees Drive »,
bouton « Run workflow » pour un premier passage manuel. Les passages suivants
sont nocturnes et silencieux : rien n'est commite si les donnees n'ont pas change.

## Regles de depot dans le Drive

Le nom du fichier porte la periode : il doit contenir le mois en toutes lettres
et l'annee (`Indicateurs SRMNIA_Avril 2026.xlsx`). La premiere feuille contient
la matrice, provinces en lignes, indicateurs en colonnes. Les libelles de
provinces tolerent les variantes d'ecriture usuelles ; tout libelle non rattache
apparait dans `data/rapport_validation.json` au lieu d'etre ignore en silence.

## Sorties

| Fichier | Contenu |
|---|---|
| `data/consolide.json` | enregistrements `{source, periode, province, indicateur, valeur}` |
| `data/catalogue.json` | listes des provinces, periodes, sources et indicateurs presents |
| `data/rapport_validation.json` | provinces inconnues, valeurs non numeriques, periodes indetectables |

En cas de doublon (meme source, periode, province, indicateur dans deux fichiers),
le dernier fichier traite fait foi.

## Tableau de bord

Le fichier `index.html` a la racine est la version de reference du tableau de
bord. Pour le publier : « Settings », « Pages », source « Deploy from a branch »,
branche `main`, dossier racine. Le site sera servi sur
`https://ikabo5.github.io/CID/`. Il fonctionne pour l'instant sur des valeurs de
demonstration ; son raccordement a `data/consolide.json` interviendra une fois le
premier passage de synchronisation effectue.

## Organisation des fichiers

| Fichier | Role | Qui le modifie |
|---|---|---|
| `referentiel.json` | provinces, variantes d'ecriture et d'indicateurs, mois | tout membre de l'equipe, sans code |
| `config_sources.json` | association dossier Drive vers lecteur | tout membre de l'equipe, sans code |
| `scripts/drive_sync.py` | telechargement Drive (91 lignes) | developpeur |
| `scripts/normaliser.py` | conversion en format long (256 lignes) | developpeur |
| `.github/workflows/synchronisation.yml` | passage nocturne | rarement |

Les deux fichiers JSON absorbent la maintenance courante. Une province ecrite
autrement dans un fichier de routine : une ligne dans `variantes` du
referentiel. Un nouveau dossier dans le Drive : rien a faire s'il suit le
format matrice, une ligne dans `config_sources.json` sinon. Le code Python ne
change que pour un format de fichier entierement nouveau.

## Extension a de nouveaux domaines

L'arborescence Drive commande tout. Chaque dossier de premier niveau constitue
une source ; la synchronisation telecharge l'integralite du contenu, quel que
soit le nombre de fichiers ou de sous-dossiers, et la normalisation lit les
formats xlsx, xls et csv, toutes feuilles comprises.

Le fichier `config_sources.json` associe chaque source a un lecteur :

```json
{
  "DSR": "matrice_provinces",
  "PEV": "matrice_provinces",
  "NUTRITION": "format_long"
}
```

Deux lecteurs sont fournis. `matrice_provinces` traite le format de routine
(provinces en lignes, indicateurs en colonnes) et sert de lecteur par defaut
pour toute source absente de la configuration : ajouter un domaine au meme
format ne demande donc aucune modification, ni de code ni de configuration.
`format_long` traite les fichiers deja structures en colonnes province,
indicateur, valeur, avec periode facultative.

Un domaine a format nouveau demande une fonction de lecture enregistree dans
le dictionnaire `LECTEURS` de `scripts/normaliser.py` et une ligne dans la
configuration. Entre-temps, les fichiers illisibles apparaissent dans le
rapport de validation avec le motif precis, feuille par feuille : rien ne se
perd sans trace.

## Limites connues

Si les fichiers RMA du PEV suivent une structure differente des deux lecteurs
fournis, le rapport de validation le signalera et un lecteur dedie sera ajoute.
Le raccordement du tableau de bord a `data/consolide.json` constitue l'etape
suivante.
