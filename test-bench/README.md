# Test bench — console de simulation

> **Bonus, hors périmètre de l'exercice.** Les deux use cases sont livrés dans
> leurs dossiers respectifs ; ce bench a été construit après coup, pour vérifier
> que le dashboard proposé fonctionne vraiment et pour le montrer dans des
> situations qu'une capture d'écran ne peut pas raconter.

Pile Docker qui reproduit localement les deux sources de l'énoncé, avec une
console web permettant de piloter la simulation : trafic, pannes, et activation
du bug des bagages fantômes du use case 1.

![Console de simulation](images/simulation-console.png)
*La console, ici sur le scénario « Morning rush ». À gauche les sept scénarios,
au centre ce que le dashboard doit afficher, à droite le bug du use case 1 et le
cycle journalier. Le dashboard Grafana s'ouvre à côté, sur son propre port.*

Elle sert à deux choses : vérifier que
[`dashboard.json`](../use-case-2-dashboard/solution/dashboard.json) s'importe et
s'affiche réellement, et rendre le comportement du dashboard observable —
bannière `DATA STALE`, seuils de couleur, bagages fantômes qui s'accumulent.

Les deux captures du use case 2
([nominale](../use-case-2-dashboard/solution/images/dashboard-nominal.png),
[panne](../use-case-2-dashboard/solution/images/dashboard-stale.png)) sortent
d'ici.

## Démarrer

### Avant tout

- **Docker doit tourner.** Sous Windows, ouvrir Docker Desktop et attendre que
  la baleine soit stable dans la barre des tâches. Sur Linux,
  `sudo systemctl start docker`.
- **Python doit être installé** : il prépare le dashboard et choisit les ports.
  Rien d'autre, aucune dépendance à installer (`pip` n'est pas utilisé).
- `pytest` uniquement si l'on veut lancer `test` ; tout le reste s'en passe.

### En partant d'un clone

Rien de particulier : la commande de lancement ci-dessous suffit, depuis ce
dossier. Trois choses n'existent pas dans le dépôt et sont fabriquées au
premier lancement, ce qui est normal et voulu :

| Absent du dépôt | Créé par | Contenu |
|---|---|---|
| `.env` | `up` | Mots de passe aléatoires et ports libres de la machine |
| `state/` | la console | `sim.env`, l'état courant de la simulation |
| `grafana/dashboards/` | `scripts/prepare-dashboard.py` | Copie du dashboard livré, datasources résolues |

Deux points valent d'être connus :

- **Passer par le script, pas par `docker compose up` directement.** C'est lui
  qui génère le `.env` et prépare le dashboard ; sans cette étape, Grafana
  démarre avec un dossier de provisioning vide et n'affiche aucun dashboard.
- **Fins de ligne.** Un `.gitattributes` à la racine du dépôt force les `.sh`,
  `.sql` et `.yml` en LF. Sans lui, un clone sous Windows récupérerait des
  scripts en CRLF, que bash refuse d'exécuter — l'erreur est déroutante, elle
  ressemble à un fichier manquant. Si `./bench.sh` échoue avec un message
  inattendu, c'est la première chose à vérifier :

  ```bash
  file bench.sh
  ```

  La réponse doit mentionner « ASCII text », sans « CRLF line terminators ».

### Lancer

Se placer dans ce dossier, puis lancer **une seule commande**, celle de sa
plateforme. Les deux scripts sont équivalents, commande pour commande.

| Système | Commande |
|---|---|
| **Windows**, PowerShell | `.\bench.ps1 up` |
| **macOS** et **Linux** | `bash bench.sh up` |
| **Windows**, Git Bash ou WSL | `bash bench.sh up` |

Deux frictions propres à chaque plateforme, et leur parade :

- **Windows** — si PowerShell répond que « l'exécution de scripts est désactivée
  sur ce système », c'est la politique d'exécution. Sans rien changer aux
  réglages de la machine :

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\bench.ps1 up
  ```

- **macOS et Linux** — `bash bench.sh` plutôt que `./bench.sh` : un fichier
  fraîchement cloné n'a pas toujours le bit exécutable, et cette forme s'en
  passe. `chmod +x bench.sh` une fois pour toutes fonctionne aussi.

Aucune autre dépendance : le script n'utilise que Docker, Python et `curl`,
présents partout, et évite volontairement les outils GNU (`shuf`, `realpath`)
absents de macOS.

C'est tout : `up` monte les cinq services, dont le simulateur. **Il n'y a rien
à lancer séparément.** Le script s'occupe du reste — il génère un `.env` avec
des mots de passe aléatoires, choisit des ports libres si 3000, 8080 ou 8090
sont déjà pris sur la machine, provisionne Grafana, puis attend que tout
réponde avant d'afficher les deux URL :

```
Console   : http://localhost:8090
Dashboard : http://localhost:3001/d/bhs-controlroom/salle-de-controle?kiosk&refresh=5s
```

Le **premier** lancement télécharge environ 2 Go d'images et prend quelques
minutes, SQL Server étant le plus lourd. Les suivants prennent une trentaine de
secondes. Tous les ports sont publiés sur `127.0.0.1` uniquement.

Ensuite :

```bash
bash bench.sh console      # ouvre la console dans le navigateur
```

### Arrêter et reprendre

| Besoin | Commande | Effet |
|---|---|---|
| Faire une pause | `stop` puis `start` | Les conteneurs s'arrêtent, **les données restent**. Reprise en ~20 s |
| Repartir de zéro | `down` puis `up` | Volumes supprimés, base re-seedée |

Après un redémarrage de la machine, il suffit de relancer Docker puis
`bash bench.sh start` — ou `up`, qui fait la même chose en vérifiant tout.

### Le simulateur

C'est le service `simulator` : un conteneur qui, **toutes les 5 secondes**, lit
les cibles calculées par la console dans `state/sim.env` et amène la table
`bag_tracking` vers cet état. Il démarre avec la pile et tourne en continu ;
il n'y a ni script à lancer ni processus à surveiller.

Pour vérifier qu'il travaille :

```bash
docker logs -f bhs-simulator
```

Il reste silencieux en régime normal — il ne parle que pour signaler une purge
de fantômes ou un tick en échec. Le vrai contrôle est `bash bench.sh status`,
qui montre côte à côte ce que la console demande et ce que la base contient.

Si le dashboard ne bouge pas alors que la console répond, c'est le simulateur
qu'il faut regarder en premier :

```bash
docker compose restart simulator
```

### Toutes les commandes

| Commande | Effet |
|---|---|
| `up` | Démarre la pile et attend que tout réponde |
| `stop` / `start` | Pause et reprise, sans perdre les données |
| `console` / `dashboard` | Ouvre la console, ou le dashboard en mode kiosk |
| `scenario <nom>` | Applique un scénario sans passer par l'UI |
| `verify` | Joue tous les scénarios et contrôle les deux sources |
| `status` | État des services et des deux sources |
| `test` | Tests unitaires du modèle de simulation |
| `package` | Copie autonome du bench, dashboard embarqué |
| `logs` | 60 dernières lignes de tous les services |
| `down` | Arrêt **et suppression des volumes** |

## Tester

### Automatiquement

```bash
bash bench.sh verify
```

Environ deux minutes. Chaque scénario est appliqué, puis les deux sources sont
interrogées directement — la bannière et le compteur de bagages viennent de
SQL, le code HTTP de l'API mockée — et comparées à ce que la console annonce.
La sortie attendue :

```
    SCENARIO         BANNER   ACTIVE   API      RESULT
    normal           OK       142      200      OK
    morning_rush     OK       377      200      OK
    system_jam       OK       577      200      OK
    night_idle       IDLE     0        200      OK
    tracker_outage   STALE    142      200      OK
    api_outage       OK       142      503      OK
    build-up 2 -> 6 phantoms       OK
    purge -> 0 phantom left         OK
```

C'est le contrôle qui a le plus de valeur : il ne vérifie pas que la console
affiche ce qu'elle a calculé, mais que la base et l'API ont réellement bougé.

`bash bench.sh test` complète avec les 36 tests unitaires du modèle (formules,
seuils, cycle journalier, validation des entrées).

### À la main

Ouvrir la console et le dashboard côte à côte, puis, dans cet ordre :

1. **Morning rush** — le séjour passe orange *avant* le nombre de bagages :
   le dashboard signale la congestion avant l'engorgement.
2. **Night — idle** — la bannière passe au **bleu**. Rien ne bouge, mais rien
   n'est en retard.
3. **Tracker outage** — bannière **rouge**. Même absence d'inserts, mais des
   bagages encore en vol : cette fois c'est une panne. C'est la paire 2/3 qui
   justifie la logique à trois états.
4. **BHS API outage** — les quatre panneaux API tombent en erreur, la bannière
   reste **verte**. Elle est calculée en SQL, elle ne dépend pas de l'API.
5. **Phantom storm**, puis attendre — le compteur de fantômes grimpe et franchit
   le seuil rouge à 25. *Purge phantoms* le ramène à zéro.
6. **Auto-play** — la journée défile en deux minutes : nuit calme, pic du matin,
   décrue. Le dashboard s'anime seul.

Chaque carte annonce ce qu'elle va produire, il n'y a donc rien à deviner.

## Les scénarios

| Scénario | Ce qu'il démontre |
|---|---|
| **Normal operations** | Les chiffres des captures : 142 bagages, 4,2 min, tout vert |
| **Morning rush** | Le séjour franchit 8 min avant que le volume ne franchisse 300 : le dashboard alerte sur la congestion avant sur le stock |
| **System jam** | Les deux compteurs au rouge, et un bagage au-delà de 120 min (cellule grise) |
| **Night — idle** | Bannière **bleue**. Rien ne bouge, mais rien n'est en retard : ce n'est pas une panne |
| **Tracker outage** | Bannière **rouge**. Mêmes inserts arrêtés, mais des bagages encore en vol : là, c'est une panne |
| **BHS API outage** | Tous les panneaux API en erreur, **bannière verte**. C'est la raison pour laquelle elle est adossée à SQL |
| **Phantom storm** | Le bug du use case 1 activé : le compteur de fantômes grimpe et franchit le seuil rouge |

Les deux paires `Night / Tracker outage` et `API outage` sont les plus utiles à
montrer : elles justifient des choix d'architecture que le README du use case 2
ne fait qu'affirmer.

## Le modèle des bagages fantômes

Le switch « prefix-collision bug » ne truque pas un compteur, il rejoue le
défaut de `bag_tracker.py` : un buffer `dict` de 10 événements, vidé toutes les
5 secondes, indexé sur `tag_id[:4]`. Deux scans partageant leurs quatre
premiers chiffres dans la même fenêtre, et le second écrase le premier.

C'est un paradoxe des anniversaires sur 10 000 préfixes :

```
k            = min(10, débit × scans_par_bagage × 5 / 3600)
scans perdus = débit × scans_par_bagage × (k − 1) / 20000  par heure
fantômes     = scans perdus ÷ scans_par_bagage
```

La perte croît donc avec le **carré** du débit : invisible la nuit, coûteuse au
pic du matin. Sur une journée complète, le modèle strande environ 7 bagages sur
33 000, là où l'énoncé rapporte ~5 sur 15 000. Le même ordre de grandeur, sans
que rien n'ait été calé pour y tomber : c'est ce qui rend la démonstration
défendable.

Le bouton *Purge phantoms* fait ce que fait le correctif : il ferme les
bagages restés ouverts, en leur écrivant l'événement `LOADED` que le scan perdu
aurait dû produire. Il ne supprime aucune ligne — une trace d'audit ne s'efface
pas pour faire baisser un compteur.

**Le temps est comprimé ×720** : un tick du simulateur avance le système d'une
heure simulée. Sans cela, le compteur de fantômes mettrait une journée de
travail à bouger. C'est affiché dans la console, sous le graphe du cycle.

## Architecture

La console calcule, le simulateur applique.

```
console (:8090)  ──écrit──>  state/sim.env  ──lu par──>  mock-api (:8080)
  UI + modèle                 KEY=VALUE                  simulator (tick 5 s)
  + cycle 24 h                                                 │ sqlcmd
                                                               ▼
                                                          SQL Server ──> Grafana (:3001)
```

| Service | Image | Rôle |
|---|---|---|
| `sqlserver` | `mssql/server:2019-latest` | Table `bag_tracking` du schéma de `data_sources.md` |
| `init` | `mssql-tools` | Crée le schéma et les données fictives, puis sort |
| `mock-api` | `python:3.12-alpine` | Les trois endpoints REST, sans dépendance |
| `console` | `python:3.12-alpine` | UI, modèle de simulation, cycle journalier |
| `simulator` | `mssql-tools` | Amène `bag_tracking` vers les cibles, toutes les 5 s |
| `grafana` | `grafana/grafana:9.5.15` | Grafana + Infinity 2.11.4, dashboard provisionné |

`mock-api` porte l'alias réseau `api.nce-bhs.local` : le dashboard livré est
importé sans qu'une seule de ses URL soit retouchée.

### Ce qui est simulé, et comment

Les deux sources sont fabriquées, mais **pas de la même manière**, et
l'asymétrie est volontaire.

**L'API est une façade.** `mock-api/app.py` ne stocke rien et n'interroge
aucune base : à chaque requête HTTP, il lit `state/sim.env` et calcule sa
réponse. `bags_in_system` est recopié depuis l'état, les horodatages sont
dérivés de l'heure courante (`entry_time = maintenant − séjour`), le débit
horaire applique la courbe journalière au taux d'arrivée. C'est une fonction
pure : même état, même JSON. Dans l'énoncé l'API est un système tiers dont on
ne connaît que le contrat — il n'y a donc rien à simuler de sa mécanique
interne, seulement ses réponses.

**La base est réelle.** Le simulateur exécute de véritables `INSERT` dans une
vraie table SQL Server, et le dashboard fait tourner dessus les requêtes de
[`queries.sql`](../use-case-2-dashboard/solution/queries.sql), sans adaptation.
Le compteur SQL est un vrai `COUNT` sur de vraies lignes, la bannière un vrai
`DATEDIFF` sur un vrai `created_at`.

C'est ce qui fait la valeur du bench : la partie du dossier qui engage sa
crédibilité — déduplication par `tag_id`, `GETUTCDATE()` contre `GETDATE()`,
seuil des 6 heures — s'exécute pour de bon. Un mock des deux côtés n'aurait
prouvé que la mise en page.

Conséquence directe : l'écart API − SQL affiché par le dashboard est **mesuré**,
pas décrété. La console demande à l'API d'annoncer un chiffre et au simulateur
d'amener la base au même chiffre ; s'ils ne s'accordent pas à l'écran, c'est
que quelque chose n'a pas convergé. Un écart négatif fugace après un changement
de scénario est normal : l'API répond instantanément, le simulateur met un tick
de 5 s à rattraper.

Deux points de conception valent d'être signalés :

- **Toute la logique est dans `console/simulation.py`**, un module sans
  entrées-sorties, donc testable seul (`bash bench.sh test`). Le simulateur,
  lui, n'applique que des nombres déjà calculés et validés.
- **`state/sim.env` est lu, jamais exécuté.** Le fichier est écrit depuis une
  interface web ; un `source` dans le simulateur transformerait un curseur en
  shell. Côté console, chaque valeur est bornée ou comparée à une liste fermée
  avant d'être écrite.

## Remarques

- `docker compose ps` montre `init` en `exited (0)` : c'est normal, ce service
  fait son travail puis rend la main.
- Le viewport des captures du use case 2 est 1100 × 655, soit la hauteur exacte
  de la grille Grafana (17 unités, 638 px) plus ses marges.
- Le bench est mono-état : une seule base, un seul Grafana, un seul `sim.env`.
  Deux navigateurs ouverts sur la console voient donc la même chose, et le
  dernier réglage gagne.
- `.env`, `state/`, `dist/` et `grafana/dashboards/` sont générés, non versionnés.
