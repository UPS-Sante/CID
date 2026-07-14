# Centre d'Intelligence des Données - MSPP, République du Tchad

Tableau de bord de suivi des priorités sanitaires nationales et pipeline de
consolidation des données de routine, adossés au dossier Drive du CID.

## Fonctionnement

Les directions techniques déposent leurs fichiers mensuels dans le Drive.
Chaque nuit, une action GitHub télécharge l'arborescence, normalise les
tableurs au format long (source, période, province, indicateur, valeur) et
publie `data/consolide.json`. Le tableau de bord (`index.html`, servi par
GitHub Pages) lit ce fichier au chargement : les indicateurs couverts par la
base réelle affichent les valeurs réelles, les autres restent en mode
démonstration, et le bandeau d'en-tête indique dans quel mode on se trouve.

## Fichiers

| Fichier | Rôle |
|---|---|
| `index.html` | tableau de bord (Pages) |
| `referentiel.json` | provinces, variantes d'écriture, mois |
| `config_sources.json` | dossier Drive → lecteur de format |
| `correspondance_indicateurs.json` | métrique du tableau de bord → libellé réel |
| `scripts/drive_sync.py` | téléchargement Drive (compte de service) |
| `scripts/normaliser.py` | conversion en format long, validation |
| `tests/test_normaliser.py` | suite de tests (`python tests/test_normaliser.py`) |
| `data/` | fichiers produits par la synchronisation |

Les trois fichiers JSON de configuration se modifient sans toucher au code :
une variante d'écriture de province, un nouveau domaine, une nouvelle
correspondance d'indicateur sont des éditions d'une ligne.

La mise en place complète (compte de service, secrets, workflow) est décrite
dans `NOTICE.md`.
