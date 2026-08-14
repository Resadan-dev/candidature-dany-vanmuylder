# Use Case 1 : Le bagage fantôme

## Résumé

La cause racine est l’utilisation d’un préfixe non unique comme clé du buffer : deux scans partageant leurs quatre premiers chiffres ne peuvent pas y coexister.

Le second écrase silencieusement le premier avant sa persistance.

Le correctif remplace ce dictionnaire par une liste d’événements, puis sécurise les flushs concurrents par un échange atomique du buffer, deux locks et une transaction SQLite.

Les six tests automatisés passent. Les garanties de bout en bout (ACK RabbitMQ, idempotence et buffer durable) restent hors périmètre.

## Réflexions préalables et diagnostic

Je donne le dossier à Claude desktop et je demande :

> Traduis en français l'énoncé et explique-moi, en développeur naïf du domaine en question, la problématique et les termes techniques (PLC ? BHS ? SC- ? Convoyeur ?) et l'architecture présentée.

Je peux lire et comprendre en anglais, mais autant comprendre le problème dans ma langue maternelle, je serai aussi bien plus pertinent dans mes échanges avec l'IA générative.

Cela dit, après avoir compris la problématique et l'architecture, ma première réflexion est celle-ci :

L’incident concerne environ 5 bagages sur 15 000, soit près de 0,033 %.

Il est donc rare, même si son impact opérationnel reste important puisqu’il peut conduire à des recherches manuelles et à des vols manqués.

À ce stade, je ne dois pas conclure que le problème est forcément logiciel. Même dans un use case de développement, je me place en situation réelle : il pourrait aussi s’agir d’un problème physique, technique ou humain.

Comme je n'ai que quelques heures, je demande à Claude et à Codex, de manière indépendante, de résoudre la problématique.

Pendant qu'ils tournent, prochain réflexe de mon côté : ouvrir les logs.

Les blocs contenant les bagages fantômes sont clairement mis en avant. Cependant, leurs compteurs présentent une anomalie :

```text
2026-08-11 10:22:15.800 [INFO] Flushed 10 bags to database
# Only 10 flushed but 11 were received! Bag 8594031301 lost.
```

Il y a 10 lignes de réception au-dessus de ce commentaire, pas 11. Si `8594031301` a été perdu, seuls 9 événements auraient dû être persistés.

Les logs semblent donc illustratifs, incomplets ou issus d’une autre version. Je fonde la démonstration sur le code, les événements JSON et la reproduction locale.

Par contre, le message au-dessus de ces lignes me semble révélateur :

```text
# Note: Two bags with same prefix "8594" arrived within 5ms
2026-08-11 10:22:14.112 [INFO] Received scan: tag=8594031301 location=SC-101
2026-08-11 10:22:14.117 [INFO] Received scan: tag=8594031302 location=SC-102
```

Deux bagages peuvent légitimement partager un préfixe. Mon hypothèse devient donc qu’une clé de regroupement non unique est utilisée à tort comme identité d’événement.

Voyons le code...

Le buffer est un dictionnaire :

```
self._pending_bags = {}
```

La clé utilisée pour y stocker un événement est construite à partir des quatre premiers chiffres du `tag_id` :

```
def _generate_batch_key(self, event: BagEvent) -> str:
    return event.tag_id[:4]
```

L’événement est ensuite affecté directement à cette clé :

```
batch_key = self._generate_batch_key(event)
self._pending_bags[batch_key] = event
```

Pour les deux événements observés, l’exécution revient donc à faire :

```
self._pending_bags["8594"] = event_8594031301
self._pending_bags["8594"] = event_8594031302
```

La seconde affectation remplace la première. Au moment du flush, seul `8594031302` est encore présent dans le buffer.

La cause racine est confirmée : une clé de regroupement non unique est utilisée comme clé d’un dictionnaire censé conserver chaque événement.

Les scanners sont différents (`SC-101` et `SC-102`), mais cela n’empêche pas la collision : la localisation n’entre pas dans le calcul de la clé.

Entre-temps, j'ai lancé le script Python pour reproduire le défaut :

```text
python .\bag_tracker.py
12:50:07.309 [INFO] Database initialized
12:50:07.311 [INFO] Received scan: tag=0123456789 location=SC-101
12:50:07.311 [INFO] Received scan: tag=0123456790 location=SC-101
12:50:07.311 [INFO] Received scan: tag=0123456791 location=SC-102
12:50:07.315 [INFO] Flushed 1 bags to database
Total bags in database: 1
```

Cela confirme le diagnostic : les trois tags partagent le préfixe `0123`, et un seul événement est conservé.

[Schéma 1 : Root cause and correction](diagrams/01-root-cause-and-fix.md)

Les analyses indépendantes de Claude et Codex aboutissent à la même cause. Elles signalent aussi d’autres risques que je vérifie avant d’en intégrer une partie au correctif.

Remarque : les réponses me permettent aussi de comprendre qu'il s'agit effectivement d'un problème logiciel, vu que c'est situé entre la réception par le service et l’écriture en base.

Deuxième remarque : je n'implémente pas tout ce que l'IA a relevé, car je sortirais du périmètre et du délai de deux à trois heures. Ces éléments restent utiles pour comprendre les limites de la solution.

## Hypothèses

- Chaque scan valide doit produire un enregistrement, même pour un `tag_id` déjà rencontré.
- Le code et `sample_events.json` font foi lorsque les compteurs des logs sont incohérents.
- Le correctif cible l’instance unique et le simulateur fournis ; l’intégration RabbitMQ réelle reste hors périmètre.

## Correctif

La cause racine étant l’utilisation d’un dictionnaire indexé par les quatre premiers chiffres du `tag_id`, le correctif direct consiste à remplacer ce dictionnaire par une liste d’événements :

```
self._pending_events: list[BagEvent] = []
```

Chaque scan est ensuite ajouté indépendamment :

```
self._pending_events.append(event)
```

Ce choix correspond au modèle fonctionnel décrit dans l’architecture : chaque scan doit produire un enregistrement, y compris plusieurs scans du même bagage.

Le seuil de batch compte désormais les événements réels :

```
batch_is_full = len(self._pending_events) >= self._batch_size
```

Avec cette correction :

- deux bagages partageant un préfixe sont conservés ;
- deux scans du même bagage sont conservés ;
- un batch de dix éléments représente réellement dix événements.

## Les autres points pris en compte

Le remplacement du dictionnaire corrige la cause racine.

Mais le code original présente également un risque de race condition entre `_on_message()`, qui modifie le buffer, et le thread périodique qui exécute le flush.

J’ai donc ajouté deux locks :

```
self._pending_lock = threading.Lock()
self._flush_lock = threading.Lock()
```

`_pending_lock` protège les modifications du buffer. `_flush_lock` garantit qu’un seul flush est exécuté à la fois.

Lors d’un flush, le buffer est détaché atomiquement :

```
with self._pending_lock:
    events_to_flush = self._pending_events
    self._pending_events = []
```

Les nouveaux événements peuvent alors être placés dans la nouvelle liste pendant que l’ancien lot est écrit dans SQLite. Ils ne risquent plus d’être supprimés par un `clear()` tardif.

[Schéma 2 : Atomic buffer swap and lock responsibilities](diagrams/02-atomic-buffer-swap-and-locks.md)

Les insertions sont effectuées dans une transaction :

```
with sqlite3.connect(self.db_path) as connection:
    cursor = connection.cursor()
    cursor.executemany(sql, parameters)
```

Ainsi :

- si toutes les insertions réussissent, le lot est validé ;
- si une insertion échoue, l’ensemble de la transaction est annulé.

En cas d’échec, le lot est replacé devant les événements reçus entre-temps :

```
self._pending_events = events_to_flush + self._pending_events
```

[Schéma 3 : SQLite transaction and buffer restoration](diagrams/03-transaction-and-buffer-restoration.md)

Enfin, `time.monotonic()` est utilisé pour mesurer le délai de cinq secondes, car ce compteur n’est pas affecté par une correction de l’horloge système. Un `time.time()` reculant d’une heure au passage à l’heure d’hiver suspendrait les flushs périodiques d’autant.

### Autres améliorations

- Le `except:` silencieux est remplacé par une gestion explicite de `queue.Empty` et une journalisation des erreurs réelles.
- Le parsing gère aussi les types et timestamps invalides.
- Un `stop_event` permet d’interrompre le thread périodique au lieu de conserver une boucle `while True` sans condition d’arrêt.

## Limites actuelles

Même avec ce correctif, certaines garanties de production ne sont pas couvertes.

### Buffer uniquement en mémoire

Les événements sont restaurés dans le buffer après un échec SQLite, mais ce buffer reste volatil. Si le processus s’arrête brutalement avant la persistance, son contenu est perdu.

Le log indique donc précisément :

```
restored to the in-memory buffer
```

Il ne prétend pas qu’un retry durable est garanti.

### Acquittement RabbitMQ non représenté

Le `MessageQueueClient` fourni est un simulateur. Il n’expose pas les mécanismes réels d’ACK/NACK de RabbitMQ.

En production, un message devrait idéalement être acquitté uniquement après le commit SQLite :

```
message reçu
→ validation
→ commit SQLite
→ ACK RabbitMQ
```

Si le commit échoue, il faudrait émettre un NACK ou laisser le message être redistribué.

### Pas d’idempotence

RabbitMQ fournit normalement une livraison _at least once_. Un événement peut donc être livré plusieurs fois.

Le `tag_id` ne suffit pas pour dédupliquer, car il identifie le bagage et non le scan. Il faudrait idéalement un `event_id` stable (par exemple un UUID produit à la source et conservé lors des redélivraisons) accompagné d’une contrainte d’unicité :

```
CREATE UNIQUE INDEX idx_event_id
ON bag_tracking(event_id);
```

### Pas de stratégie complète de retry

Le timer retentera indirectement le flush, mais il n’existe pas de politique explicite comprenant :

- délai progressif entre les tentatives ;
- nombre maximal de tentatives ;
- distinction entre erreur transitoire et définitive ;

### Arrêt totalement coordonné

Le correctif arrête la consommation, signale le timer, puis effectue un dernier flush. Pour une fermeture parfaitement coordonnée, il faudrait aussi conserver la référence du thread et effectuer un `join()` afin d’attendre explicitement sa terminaison.

## Améliorations possibles

1. Ajouter un `event_id` et une contrainte d’unicité pour l’idempotence.
2. Acquitter RabbitMQ uniquement après le commit SQLite.
3. Mettre en place retry avec backoff.
4. Utiliser un buffer durable si la perte lors d’un crash n’est pas acceptable.

Le correctif résout la cause racine observée et sécurise les accès concurrents au buffer pendant le fonctionnement normal.

Une garantie de bout en bout nécessiterait toutefois une coordination avec les acquittements RabbitMQ, un identifiant d’événement pour l’idempotence et éventuellement un buffer durable.

## Plan de vérification

J’ai ajouté six tests automatisés couvrant les scénarios principaux :

- deux bagages partageant le même préfixe ;
- deux scans du même bagage ;
- dix événements ayant tous le même préfixe ;
- l’ensemble des événements d’exemple ;
- un échec SQLite suivi d’un rétablissement ;
- un événement arrivant pendant un flush.

Résultat :

```powershell
cd use-case-1-phantom-bag\solution
python -m pytest

6 passed
```

Le critère principal est simple : après le flush final, chaque événement valide accepté doit être présent dans SQLite, sans perte et dans son ordre de réception.

Tests supplémentaires, non implémentés mais à réaliser avant une mise en production :

- un test du flush après cinq secondes avec moins de dix événements ;
- une rafale de 100 événements en deux secondes ;
- une erreur injectée au milieu d’une transaction pour confirmer le rollback complet ;
- un arrêt du service avec des événements encore en attente.

## Supervision

Une supervision utile ne consiste pas à produire le plus de logs possible, mais à choisir des signaux structurés, exploitables et soumis à une politique de rétention adaptée.

Voici quelques options :

### Métriques applicatives

Je mesurerais :

- événements acceptés ;
- événements persistés ;
- événements rejetés ;
- événements en attente ;
- événements en cours de flush ;
- âge du plus ancien événement non persisté ;
- succès, échecs et durée des flushs.

L’invariant principal serait :

```
acceptés = persistés + en attente + en cours de flush
```

Un écart durable aurait détecté directement le défaut original.

C’est le meilleur compromis entre simplicité et efficacité.

Sa limite est que les compteurs locaux repartent à zéro au redémarrage.

### Logs structurés

Des logs `received`, `buffered`, `persisted`, `rejected` et `flush_failed` faciliteraient l’analyse d’un scan précis.

Ils sont utiles pour le diagnostic, mais moins adaptés à une détection rapide et peuvent générer un volume important.

### RabbitMQ

Les métriques natives permettraient de surveiller :

- la profondeur et l’âge de la file ;
- le nombre de consommateurs ;
- les messages non acquittés ;
- les redélivraisons.

Elles détecteraient un consommateur arrêté ou trop lent, mais pas un événement consommé puis perdu dans le tampon.

### Réconciliation de bout en bout

La solution la plus fiable serait d’attribuer un `event_id` unique à chaque scan et de vérifier périodiquement sa présence dans SQLite.

Cette option identifierait exactement chaque événement manquant et faciliterait l’idempotence.

Son principal compromis est la complexité d’intégration : elle nécessite de modifier le contrat des messages, le schéma de la base et la supervision.

### Alertes proposées

- invariant de conservation incorrect pendant plus de 30 secondes ;
- événement non persisté depuis plus de 10 secondes ;
- plusieurs flushs consécutifs en échec ;
- aucun consommateur RabbitMQ ;
- message RabbitMQ âgé de plus de 30 secondes ;
- message rejeté au parsing.

Ces seuils sont des valeurs initiales à ajuster après observation du trafic réel.

Je retiendrais donc les métriques applicatives et RabbitMQ à court terme, puis une réconciliation par `event_id` comme amélioration future offrant une véritable garantie de bout en bout.


