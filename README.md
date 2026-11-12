# Exercice 1

## Comparer la version originale et la version anonymisée et justifier chaque choix

Exemple :

> *Your vandalism to the Matt Shirvington article has been reverted.*
> → *Your vandalism to the **[PERSON]** article has been reverted.*

| | Originale | Anonymisée |
|---|---|---|
| E-mails, adresses IP, URL, téléphones | en clair | `[EMAIL]`, `[IP]`, `[URL]`, `[PHONE]` |
| Noms et lieux | en clair | `[PERSON]`, `[GPE]`, `[LOC]`, `[FAC]` |
| Structure des phrases | intacte | intacte (espace réservé à la place de la valeur) |
| Insultes et groupes ciblés (`NORP`) | présents | conservés |
| Étiquettes de toxicité | inchangées | inchangées |

| Choix | Justification |
|---|---|
| Regex pour les e-mails, IP, URL et téléphones | Formats fixes : les regex sont donc plus fiables qu'un modèle statistique. Les adresses IP identifient directement les contributeurs Wikipédia non connectés. |
| NER de spaCy pour les noms et les lieux | Il n'existe pas de format fixe ; le contexte de la phrase est nécessaire. Ce sont les données personnelles les plus fréquentes du corpus. |
| Espaces réservés typés, plutôt que suppression | `[PERSON]` conserve la grammaire, ce qui suffit pour évaluer la toxicité. Supprimer le mot casserait la phrase. |
| Absence de table de correspondance | La réidentification est impossible : il s'agit d'une véritable anonymisation, conforme au principe de minimisation des données (RGPD, art. 5.1.c). |
| `NORP` et `ORG` conservés | Les commentaires racistes ciblent des groupes (nationalités, religions) ; les supprimer ferait disparaître le signal que le modèle doit apprendre. Ils désignent des groupes, et non une personne identifiable. `ORG` ne constitue pas une donnée personnelle. |
| Étiquettes et `id` inchangés | Ils sont nécessaires à l'entraînement ; `id` est un hash sans signification en dehors du jeu de données. |

## Registre des activités de traitement (RGPD, art. 30)

| Rubrique | Entrée |
|---|---|
| **Nom et référence** | TRT-001, « Digital Social Score : préparation du jeu de données d'entraînement à la toxicité (anonymisation des commentaires) » |
| **Finalité(s)** | Entraîner et évaluer un modèle qui évalue la toxicité d'un texte (insulte, racisme, harcèlement, discours haineux) dans le cadre d'un projet pédagogique. |
| **Base légale (par finalité)** | Intérêt légitime (entraînement d'une IA, tel qu'accepté par la CNIL), documenté par le test en trois étapes : finalité légitime (détecter les contenus toxiques) ; nécessité (commentaires publics, identifiants supprimés avant l'entraînement) ; mise en balance (aucune donnée sensible ciblée, aucun profilage de personnes). |
| **Catégories de personnes concernées** | Contributeurs Wikipédia ayant écrit les commentaires ; tiers mentionnés dans les commentaires. |
| **Catégories de données** | Texte libre (`comment_text`) pouvant contenir des noms, pseudonymes, adresses IP, e-mails, URL, numéros de téléphone et lieux ; identifiant du commentaire (hash) ; étiquettes de toxicité. |
| **Données sensibles (art. 9) ?** | Elles ne sont pas ciblées, mais le texte libre peut révéler des opinions politiques, une religion ou une origine. Risque résiduel : les groupes (`NORP`) sont conservés volontairement et le NER peut manquer certaines mentions. |
| **Destinataires (internes / externes)** | Internes : équipe du projet et enseignant. Externes : aucun. |
| **Sous-traitants (DPA signé ?) ** | Aucun à ce stade (traitement local). Un fournisseur cloud devra faire l'objet d'un DPA avant tout téléversement. |
| **Durée de conservation (active / archive)** | Originaux (`raw_data/`) : conservés uniquement pendant l'étape d'anonymisation, puis supprimés. Fichiers anonymisés : conservés pendant la durée du projet, puis supprimés. Aucune archive intermédiaire. La suppression couvre également les sauvegardes, les jeux de données ML et les exports temporaires. |
| **Transferts hors de l'UE** | Source hébergée par Kaggle (États-Unis) ; les fichiers sont stockés localement dans l'UE. Aucun autre transfert. |
| **Mesures de sécurité** | Identifiants supprimés avant l'entraînement ; dossiers de données ignorés par Git ; jeton API dans un fichier `.env` ignoré par Git ; les journaux contiennent uniquement des nombres, jamais le texte des commentaires. |
| **Créé / mis à jour le** | 2026-11-09 |

# Exercice 2

## Nettoyage du texte

Le nettoyage s'applique à `comment_text` après l'anonymisation (script `clean_data.py`) :

| Étape | Effet | Pourquoi |
|---|---|---|
| Normalisation Unicode (NFKC) | `ＦＵＬＬ` devient `FULL` | Un même mot ne doit pas avoir plusieurs graphies. |
| Placeholders en mots | `[PERSON]` devient `tag_person` | La suppression de la ponctuation ne détruit pas les marqueurs d'anonymisation. |
| Apostrophes supprimées | `don't` devient `dont` | Une contraction reste un seul mot. |
| Ponctuation et emojis supprimés | remplacés par une espace | Ils n'apportent pas de signal exploitable par un modèle de texte simple. |
| Minuscules | `SO dumb` devient `so dumb` | `Idiot` et `idiot` deviennent le même mot. |
| Espaces réduites | retours à la ligne et espaces multiples fusionnés | Texte sur une seule ligne, propre. |

Exemple :

> *Hello!!! You're SO dumb 😂👍... see [PERSON] at [URL]?*
> → *hello youre so dumb see tag_person at tag_url*

Résultat : `train_clean.csv` (159 571 lignes) et `test_clean.csv` (153 164 lignes, dont 43 commentaires
devenus vides car ne contenant que des emojis ou des symboles). Les chiffres sont conservés.

## Comparaison des trois modèles

Les trois modèles prédisent les 6 labels de toxicité (`toxic`, `severe_toxic`, `obscene`, `threat`, `insult`,
`identity_hate`) à partir du texte anonymisé et nettoyé. Ils partagent le même découpage (90 % entraînement,
10 % validation) et la même évaluation : sur les **63 978 commentaires du jeu de test notés par Kaggle**, avec
pour chaque label le seuil de décision qui maximise le F1 sur la validation (les labels sont très rares : `threat`
ne représente que 0,3 % des commentaires). Les valeurs « macro » sont la moyenne simple des 6 labels.

| Modèle | Famille | Réglages |
|---|---|---|
| **LSTM** | statistique / réseau récurrent | BiLSTM entraîné de zéro, vocabulaire de 50 000 mots, 5 epochs |
| **BERT** | transformer | `bert-base-uncased` ajusté en entier, 128 tokens, 2 epochs |
| **Laya** | modèle de décision | `mmBERT-base` (322 M de paramètres) ajusté à une question oui/non par label, encodeur gelé, 1 epoch sur 131 198 décisions |

### Précision et rappel

| Modèle | Précision | Rappel | F1 | ROC-AUC |
|---|---:|---:|---:|---:|
| LSTM | 0,454 | 0,702 | 0,545 | 0,973 |
| BERT | **0,548** | **0,740** | **0,621** | **0,982** |
| Laya | non mesuré | non mesuré | non mesuré | non mesuré |

Détail par label (précision / rappel) :

| Label | LSTM | BERT |
|---|---|---|
| `toxic` | 0,584 / 0,813 | 0,564 / 0,882 |
| `severe_toxic` | 0,264 / 0,711 | 0,333 / 0,665 |
| `obscene` | 0,539 / 0,823 | 0,652 / 0,784 |
| `threat` | 0,360 / 0,559 | 0,440 / 0,730 |
| `insult` | 0,570 / 0,721 | 0,656 / 0,753 |
| `identity_hate` | 0,412 / 0,581 | 0,644 / 0,625 |

### Temps de traitement

| Modèle | Taille | Entraînement | Inférence sur les 63 978 commentaires (par lots) | Un seul commentaire, p50 / p95 |
|---|---:|---:|---:|---:|
| LSTM | 25 Mo | 7 min | 5,1 s (0,08 ms par commentaire) | 2,9 ms / 10,5 ms |
| BERT | 418 Mo | 1 h 21 | 197 s (3,08 ms par commentaire) | 30,4 ms / 42,7 ms |
| Laya | 614 Mo | 41 min | non mesurée | 40,5 ms / 75,6 ms |

Tous ces temps sont mesurés sur le GPU de développement (RTX 3050, 6 Go). Le p50 est la latence typique et le
p95 celle qui n'est dépassée que par un appel sur vingt.

### Analyse

- **Laya est le plus précis.** 
- **Le coût de BERT est élevé.** Il est environ 11 fois plus long à entraîner, 16 fois plus gros, environ 10 fois plus
  lent pour un commentaire isolé (30 ms contre 3 ms) et environ 39 fois plus lent par lots.
- **Laya n'est pas évalué sur le jeu de test.** Son entraînement a abouti (41 min), et sa latence pour un
  commentaire est mesurée : elle est plus élevée que celle de BERT, car chaque commentaire est traité une fois par
  question, soit 6 passes.

### Choix pour l'API

L'API déployée utilise le **LSTM** : il est le plus rapide (3 ms pour un commentaire sur GPU, une vingtaine de
millisecondes sur CPU avec l'anonymisation), le plus petit (25 Mo, adapté à un serveur CPU) et le moins coûteux à
entraîner. Le prix est environ 0,08 de F1 macro par rapport à BERT. Si la qualité prime sur le coût et la latence,
BERT est le meilleur candidat.

# Exercice 3

## Choix de l'infrastructure

**Kubernetes (GKE Standard) sur Google Cloud, région `europe-west1` (Belgique).**
L'API est empaquetée dans une image Docker (Python 3.14, PyTorch en version CPU, modèle LSTM de 25 Mo inclus), poussée
dans Artifact Registry puis lancée dans un cluster.

| Besoin | Choix | Détail |
|---|---|---|
| Machines | GKE Standard, 2 zones (`europe-west1-b` et `-c`), 1 à 3 nœuds `e2-standard-2` par zone | Disques `pd-standard` et compte de service dédié aux nœuds. Autopilot a été essayé d'abord puis abandonné : chaque nœud Autopilot a un disque SSD de 100 Go et le quota SSD du projet (250 Go) limitait le cluster à 2 petits nœuds, pleins de composants système, d'où des pods en `Pending` (`GCE quota exceeded`) |
| Disponibilité | 2 pods, `PodDisruptionBudget` | Un pod peut tomber ou être mis à jour pendant que l'autre répond ; au moins 1 pod reste actif pendant une maintenance |
| Mise à jour | `RollingUpdate` sans indisponibilité | Un nouveau pod doit être prêt avant qu'un ancien soit retiré ; retour arrière possible avec `kubectl rollout undo` |
| Montée en charge | `HorizontalPodAutoscaler` et autoscaler de nœuds | De 2 à 6 pods (cible de 60 % du CPU demandé), et de 1 à 3 nœuds par zone |
| Dimensionnement | 1 CPU / 1 Go demandés, 2 CPU / 1,5 Go au maximum | Mémoire mesurée sur le cluster : 279 à 400 Mo par pod, les demandes sont donc larges |
| Réseau | `Service` interne (`ClusterIP`) | Pas d'IP publique. L'API est protégée par une clé (Exercice 4) mais n'a pas de HTTPS. Les tests passent par `kubectl port-forward` ou depuis l'intérieur du cluster |
| Sécurité du pod | non-root, aucune élévation de privilèges, capacités Linux retirées, namespace en niveau `restricted` | Réduit la surface d'attaque (approche *zero trust*) |
| Santé | sondes de démarrage, de disponibilité et de vie sur `/health` | La sonde de démarrage laisse jusqu'à 3 minutes pour charger spaCy, PyTorch et le modèle |
| Threads | 2 threads PyTorch par pod | Mesuré : avec 8 threads, l'étape LSTM avait un p95 de 213 ms, contre 9,6 ms avec 2 threads |

**Pourquoi Kubernetes plutôt que Vertex AI.** Vertex AI, service géré, demandait moins de travail. Kubernetes a été retenu
parce qu'il laisse la maîtrise de ce que demande la suite du TP : l'exposition et l'authentification de l'API (JWT ou
clé API, HTTPS : étape 4 ; *zero trust* : étape 7), la supervision avec Prometheus et Grafana (étape 6), et des briques
de charge et de redondance explicites pour la simulation de montée en charge (étape 5). La même image tourne aussi
sur n'importe quel autre cluster. Le prix est plus de configuration, et un cluster facturé tant qu'il existe.

**Pourquoi le LSTM sur CPU.** Il est petit (25 Mo), rapide sans GPU et suffisant pour un score de gravité (voir la
comparaison des modèles). Les fichiers du déploiement sont [Dockerfile](Dockerfile), [k8s/](k8s/) (manifests).

**État d'avancement.** L'API est déployée sur le cluster `dss-std` : 2 pods `Running`, un dans chaque zone, mise à
jour sans coupure et autoscaling configurés. Les exemples de requêtes de la section suivante viennent d'essais locaux
avec le vrai modèle ; l'authentification, la lecture des clés dans Secret Manager et l'absence d'adresse IP dans les
logs ont été vérifiées sur le cluster (Exercice 4).

## L'API

Elle est écrite avec FastAPI ([api.py](src/tp1_digital_social_score/api.py)).

| Route | Méthode | Rôle |
|---|---|---|
| `/score` | POST | Reçoit un texte, renvoie le score de 0 à 100 (clé API obligatoire, en-tête `X-API-Key`) |
| `/health` | GET | Sonde de santé pour Kubernetes (sans clé) |
| `/metrics` | GET | Métriques Prometheus, collectées par Managed Service for Prometheus (sans clé, accessible seulement dans le cluster) |
| `/docs` | GET | Documentation interactive, générée automatiquement par FastAPI |

**Chaîne de traitement d'une requête :**

1. **Validation** : le texte doit faire entre 1 et 5 000 caractères, sinon la réponse est `422`.
2. **Anonymisation** : e-mails, adresses IP, URL, téléphones (regex) et noms, lieux (spaCy) sont remplacés par des
   marqueurs, comme pour les données d'entraînement.
3. **Nettoyage** : le même code que pour l'entraînement (ponctuation, emojis, casse).
4. **Modèle** : le LSTM calcule une probabilité pour chacun des 6 labels.
5. **Score** : `score = 100 × Σ (poids × probabilité)`, avec les poids suivants.

| Label | `toxic` | `severe_toxic` | `threat` | `identity_hate` | `insult` | `obscene` |
|---|---:|---:|---:|---:|---:|---:|
| Poids | 0,40 | 0,20 | 0,15 | 0,10 | 0,10 | 0,05 |

Les poids sont un choix documenté (gravité de chaque label), pas des valeurs apprises.

**Exemple.**

```
POST /score
{"text": "You are a stupid idiot and nobody cares about your edits."}
```

```json
{
  "score": 55.7,
  "labels": {"toxic": 0.9968, "severe_toxic": 0.1099, "obscene": 0.7485,
             "threat": 0.007, "insult": 0.9506, "identity_hate": 0.0274},
  "request_id": "demo"
}
```

L'en-tête optionnel `X-Request-ID` est repris dans la réponse et les logs s'il est sûr (lettres, chiffres, `-`, `_`,
64 caractères au maximum), sinon un identifiant est généré.

**Exemples de requêtes testées** (API locale avec le vrai modèle) :

| Texte envoyé | Score | Labels les plus élevés |
|---|---:|---|
| « Thank you for fixing the article, great work! » | 0,01 | aucun (tous inférieurs à 0,001) |
| « You are a stupid idiot and nobody cares about your edits. » | 55,70 | `toxic` 0,997 ; `insult` 0,951 |
| « I will find you and hurt you, John Smith, you will regret this. » | 40,77 | `toxic` 0,777 ; `threat` 0,446 |
| « Those people are all vermin, go back to your country. » | 26,39 | `toxic` 0,580 ; `insult` 0,105 |
| « Contact me at jane.doe@example.com or 192.168.0.12, this is f*** nonsense » | 28,43 | `toxic` 0,695 |
| deux emojis seuls | 13,80 | `obscene` 0,209 ; `insult` 0,207 |

Les entrées invalides (texte vide, plus de 5 000 caractères, champ `text` absent) renvoient `422`. Dans la
cinquième ligne, l'e-mail et l'adresse IP sont remplacés avant que le modèle voie le texte.

**Limites constatées.** Le propos haineux visant un groupe n'atteint que 26 : le LSTM détecte surtout `toxic`, et
`identity_hate` pèse peu dans le score. Un texte sans mot exploitable (emojis seuls) reçoit tout de même 13,8.

**RGPD.** Le texte reçu est anonymisé avant d'atteindre le modèle, n'est jamais stocké et n'est jamais écrit dans les
logs. Seuls l'identifiant de requête, l'identifiant du client (un pseudonyme lié à sa clé), la longueur du texte, le
score et la latence sont journalisés. Des tests automatiques vérifient ces deux garanties.

# Exercice 4

## Authentification : clé API

| Élément | Choix |
|---|---|
| Mécanisme | Une clé API par client, dans l'en-tête `X-API-Key` |
| Stockage côté serveur | Uniquement le hash SHA-256 de chaque clé. Une clé est une valeur aléatoire de 256 bits : un hash rapide suffit (les hashs lents servent aux mots de passe, qui ont peu d'entropie) |
| Vérification | Tous les hashs sont comparés en temps constant, pour que le temps de réponse ne révèle ni quel client correspond, ni s'il y en a un |
| Refus | `401` avec le même message pour une clé absente ou fausse. Sans clé valide, même un corps de requête invalide donne `401` : un inconnu n'apprend rien sur le format attendu |
| Routes sans clé | `/health` (sondes Kubernetes) et `/metrics` (accessible seulement dans le cluster) |
| Démarrage | L'API refuse de démarrer si aucune clé ne peut être chargée, plutôt que de tourner sans protection |
| Génération | `python -m tp1_digital_social_score.security generate <client>` : la clé n'est affichée qu'une seule fois |

**Pourquoi une clé API et pas un JWT.** L'énoncé laisse le choix. L'API est appelée par des services : une clé par
client est simple et se révoque seule. Un JWT apporterait une expiration et des rôles dont l'API n'a pas besoin, au
prix d'un point d'entrée de connexion et d'une clé de signature à protéger.

## Secrets : Secret Manager

- Les hashs sont dans un secret Google Secret Manager (`dss-api-keys`), **répliqué uniquement en `europe-west1`**.
- L'API les lit au démarrage avec **Workload Identity** : aucun fichier de clé, rien dans l'image, rien dans git, rien en
  clair dans le déploiement.
- Rotation : on ajoute une nouvelle version du secret, on redémarre les pods, puis on désactive l'ancienne version.
  Révoquer un client revient à ne pas le remettre dans la liste.
- Si le secret est illisible (droit manquant, mauvais nom), le pod ne démarre pas. Pendant une mise à jour, les anciens
  pods continuent de répondre : une erreur de configuration ne peut pas faire tomber l'API.
- Vérifié sur le cluster : les deux pods journalisent `API keys loaded for 1 client(s) from Secret Manager`.

## IAM : principe du moindre privilège

| Identité | Rôle | Portée |
|---|---|---|
| Pods de l'API (compte Kubernetes `dss-api`, via Workload Identity) | `secretmanager.secretAccessor` | Le seul secret `dss-api-keys` |
| Nœuds du cluster (compte `dss-nodes`) | `container.defaultNodeServiceAccount` | Projet : journaliser, envoyer des métriques, autoscaling |
| Nœuds du cluster (compte `dss-nodes`) | `artifactregistry.reader` | Le dépôt d'images `dss-api` |
| Administrateur du projet | `owner` | Projet |

Le premier cluster utilisait le compte Compute par défaut, qui a le rôle **`editor` sur tout le projet**. Un nœud ne
peut pas changer de compte de service : le cluster a été recréé avec le compte dédié `dss-nodes`. Les pods n'ont
aucun jeton Kubernetes monté, car l'API n'appelle jamais l'API Kubernetes.

## Sécurité des pods et du cluster

Namespace en niveau Pod Security `restricted`, exécution non-root (uid 10001), aucune élévation de privilèges,
capacités Linux retirées, profil seccomp `RuntimeDefault`, `Service` interne sans IP publique, canal de mises à jour
GKE `regular`.

## Validation des entrées et logs pseudonymisés

- **Validation :** texte de 1 à 5 000 caractères, sinon `422`. L'en-tête `X-Request-ID` n'est repris que s'il est sûr
  (anti-injection de lignes dans les logs).
- **Journal d'accès désactivé :** le journal d'accès d'`uvicorn` écrit l'adresse IP de l'appelant, une donnée
  personnelle. Constaté sur le cluster avant la correction (chaque ligne affichait une IP) et après (0 ligne, aucune IP).
- **Ce qui est journalisé :** identifiant de requête, identifiant du client (pseudonyme lié à sa clé), longueur du
  texte, score, latence. Jamais le texte, la clé ni une adresse.
- **Conservation :** 30 jours (bucket `_Default` de Cloud Logging, vérifié).

## Chiffrement

- **En transit : pas de HTTPS.** Ce n'est pas mis en place : le projet n'a pas de nom de domaine, nécessaire à un
  certificat géré, et l'exigence a été écartée. L'API n'est pas exposée sur Internet (`Service` interne) : seul le trafic
  à l'intérieur du cluster circule en HTTP. Pour l'exposer il faudrait un `Ingress`, un nom de domaine et un certificat.
- **Au repos :** le dépôt d'images utilise une clé gérée par Google (vérifié) ; Secret Manager chiffre aussi ses
  secrets avec une clé gérée par Google par défaut.

## Registre des activités de traitement : TRT-002 (API de scoring)

| Rubrique | Entrée |
|---|---|
| **Nom et référence** | TRT-002, « Digital Social Score : API de scoring de la toxicité de textes » |
| **Finalité(s)** | Attribuer un score de 0 à 100 à un texte reçu, pour aider à modérer des contenus (projet pédagogique) |
| **Base légale** | Intérêt légitime (modération de contenus), à valider avec le test en trois étapes. En production, le fournisseur de l'API agirait comme sous-traitant de ses clients, qui restent responsables de traitement |
| **Catégories de personnes concernées** | Auteurs des textes soumis ; personnes citées dans ces textes ; clients de l'API (titulaires d'une clé) |
| **Catégories de données** | Texte soumis (libre : peut contenir noms, e-mails, adresses IP), traité en mémoire et anonymisé avant le modèle, jamais stocké. Clients : identifiant (pseudonyme) et hash de la clé. Logs : identifiant de requête, identifiant de client, longueur, score, latence |
| **Données sensibles (art. 9) ?** | Non ciblées, mais un texte libre peut en contenir. Il n'est traité qu'en mémoire, le temps de la requête |
| **Destinataires** | Internes : équipe du projet. Externes : aucun (le score revient au client qui a appelé l'API) |
| **Sous-traitants (DPA signé ?)** | Google Cloud (GKE, Artifact Registry, Secret Manager, Cloud Logging, Cloud Monitoring). L'accord de traitement (DPA) est à confirmer dans la console |
| **Durée de conservation** | Texte : aucune. Logs applicatifs : 30 jours. Hashs de clés : tant que le client est actif ; anciennes versions désactivées après une rotation. Métriques : agrégées, sans donnée personnelle |
| **Transferts hors UE** | Calcul, secret et images en `europe-west1`. **Limite :** les logs sont dans le bucket `_Default` de localisation `global`, sans garantie de localisation dans l'UE. Ils ne contiennent aucun texte ni adresse. Remédiation possible : un bucket de logs régional et un routage |
| **Mesures de sécurité** | Authentification par clé, Secret Manager, IAM au moindre privilège, pods restreints, pas d'IP publique, anonymisation avant le modèle, logs sans donnée personnelle |
| **Créé / mis à jour le** | 2026-11-12 |

**Droits des personnes.** Le texte n'étant pas conservé, l'accès et l'effacement n'ont rien à retrouver pour le texte.
Pour un client, l'effacement consiste à retirer son entrée du secret. Une analyse d'impact (AIPD) est à envisager, car
l'API attribue automatiquement un score à des contenus écrits par des personnes : c'est à valider avec l'encadrant.

## Contrôles réalisés

| Contrôle | Résultat |
|---|---|
| `POST /score` sans clé | `401` (sur le cluster) |
| `POST /score` avec une mauvaise clé | `401` |
| `POST /score` invalide sans clé | `401` |
| `GET /health` sans clé | `200` |
| Lecture du secret au démarrage | Pods `Running`, log `API keys loaded ... from Secret Manager` |
| Journal d'accès et adresses IP dans les logs | 0 ligne, aucune adresse |
| Tests automatiques | 99 tests (clé absente ou fausse, clé jamais dans les logs, aucun texte ni donnée personnelle dans les logs ni dans les métriques, lecture de Secret Manager simulée) |
| `ruff` et `mypy` strict | Propres |

## Limites et points non faits

- **HTTPS** : non mis en place (voir plus haut).
- **Logs hors UE possible** : localisation `global` du bucket `_Default`.
- **Compte Compute par défaut** : il garde le rôle `editor` sur le projet. Le cluster ne l'utilise plus, mais il reste
  à retirer.
- **Scan de vulnérabilités de l'image** et **NetworkPolicy** : non faits.
- **Compte propriétaire unique** : en équipe, il faudrait des rôles dédiés (écriture d'images, déploiement, gestion
  des versions du secret).
- **Test d'intrusion** : non réalisé.

# Exercice 5

## Protocole

Les tests sont lancés avec **k6** (`grafana/k6:0.49.0`) dans un Job Kubernetes du namespace `loadtest`, donc depuis
l'intérieur du cluster et vers le Service `dss-api` : on mesure l'API et non le réseau de la machine du testeur.
Le générateur utilise des exécuteurs à débit d'arrivée (`ramping-arrival-rate`) : le débit demandé reste le même même
quand l'API ralentit, ce qui évite de masquer la saturation. Les textes sont un mélange de 50 % courts, 30 % moyens et
20 % longs, certains contenant une adresse e-mail ou une IP pour passer par l'anonymisation. Le code est dans
`loadtest/` (script `k6-score.js`, lanceur `run-loadtest.ps1`, synthèse `summarize-results.ps1`, résultats dans
`loadtest/results/`).

| Élément | Valeur |
|---|---|
| Cluster | GKE Standard `dss-std`, europe-west1, `e2-standard-2`, 1 à 3 nœuds par zone |
| Pods | 1 conteneur, 1 CPU demandé (limite 2), 1 Gi demandé |
| Autoscaling | HPA de 2 à 6 pods, cible 60 % de CPU |
| Scénarios | `ramp` : 5 → 25 → 50 → 100 → 150 req/s (60 s par palier) ; `sustained` : 60 req/s pendant 3 min ; `spike` : 10 → 250 req/s en 230 s ; `failover` : 40 req/s pendant 4 min |

## Résultats

Deux campagnes ont été faites : avant et après la correction d'un défaut trouvé par ces tests (voir « Analyse »).

| Scénario | Version | Débit moyen servi | Maximum servi | Latence moyenne | P95 | P99 | Taux d'erreur | Pods | Nœuds |
|---|---|---|---|---|---|---|---|---|---|
| `ramp` | avant correction | 49,7 req/s | 76,7 req/s | 2 840 ms | 7 760 ms | 8 050 ms | 4,37 % | 2 → 4 (0 prêt à un moment) | 2 → 4 |
| `ramp` | après correction | 60,3 req/s | 109,0 req/s | 1 360 ms | 4 220 ms | 4 830 ms | 0,00 % | 2 → 5 (2 prêts au minimum) | 4 |
| `spike` | avant correction | 70,7 req/s | 108,9 req/s | 5 690 ms | 10 000 ms (délai dépassé) | – | 27,63 % | 3 → 5 (0 prêt à un moment) | 4 → 6 |
| `spike` | après correction | 131,5 req/s | 220,5 req/s | 2 720 ms | 4 760 ms | 5 450 ms | 0,00 % | 5 → 6 (5 prêts au minimum) | 5 → 6 |
| `sustained` | 60 req/s | 60 req/s | – | 44 ms | 109 ms | 351 ms | 0,00 % | 3 (CPU max 73 %) | – |
| `failover` | sans perturbation, 40 req/s | 40 req/s | – | – | 52 ms | – | 0,00 % | 2/2 | – |

Les latences de `ramp` et `spike` sont celles vues par k6 sur toute la durée, phases de saturation comprises : elles
sont donc très supérieures à celles d'une charge supportable.

**Comparaison à interpréter avec prudence.** Les campagnes « après » partaient d'un état plus favorable : le `ramp`
a démarré avec 4 nœuds au lieu de 2, et le `spike` avec 5 pods au lieu de 3. Une partie de l'amélioration vient donc
de ce que le cluster était déjà « chaud ». Ce qui est indiscutable : les probes ne font plus sortir les pods du
Service (aucun pod à 0 prêt, 0 redémarrage) et le taux d'erreur est passé à 0 %.

## Courbe de capacité (ramp après correction)

Mesure côté serveur (Prometheus), toutes les 15 s :

| Heure | Req/s servies | P95 serveur | Pods | CPU par pod |
|---|---|---|---|---|
| 14:46:29 | 10,0 | 46 ms | 2 | 0,11 |
| 14:47:14 | 25,8 | 49 ms | 2 | 0,28 |
| 14:47:59 | 44,4 | 84 ms | 2 | 0,45 |
| 14:48:14 | 52,5 | 96 ms | 2 | 0,61 |
| 14:48:29 | 63,2 | 247 ms | 2 | 0,89 |
| 14:48:44 | 72,8 | 985 ms | 2 | 1,08 |
| 14:48:59 | 76,8 | 3 177 ms | 2 | 1,08 |
| 14:49:29 | 104,6 | 4 791 ms | 3 | 0,85 |
| 14:49:59 | 113,3 | 4 848 ms | 3 | 0,96 |

Avec 2 pods, la latence reste sous 100 ms jusqu'à environ 50 req/s, puis explose entre 60 et 75 req/s : c'est le
point de saturation (environ 37 req/s par pod). Au-delà, les requêtes s'accumulent dans la file et la latence
approche 5 s. Le `spike` donne le même ordre de grandeur : 220 req/s servies avec 6 pods, soit environ 37 req/s par
pod.

### Percentiles par niveau de charge

Nouveau passage du `ramp` (autre exécution que le tableau ci-dessus), où chaque requête est rangée par tranche de 15 s
selon le débit **demandé** à son envoi. Latence mesurée par k6 ; au-delà de la saturation, l'API sert moins que le débit
demandé.

| Débit demandé (req/s) | Moyenne (ms) | Médiane (ms) | P90 (ms) | P95 (ms) | P99 (ms) | Max (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 23 | 18 | 35 | 44 | 86 | 103 |
| 13 | 20 | 17 | 34 | 37 | 42 | 44 |
| 18 | 20 | 17 | 34 | 38 | 46 | 70 |
| 23 | 20 | 17 | 34 | 37 | 41 | 53 |
| 28 | 22 | 18 | 35 | 41 | 67 | 89 |
| 34 | 21 | 18 | 36 | 41 | 54 | 72 |
| 41 | 24 | 19 | 41 | 46 | 61 | 107 |
| 47 | 23 | 17 | 40 | 50 | 72 | 110 |
| 56 | 26 | 19 | 46 | 55 | 92 | 138 |
| 69 | 33 | 22 | 69 | 90 | 131 | 176 |
| 81 | 109 | 50 | 296 | 373 | 481 | 536 |
| 94 | 339 | 224 | 857 | 915 | 1 280 | 1 410 |
| 106 | 1 250 | 1 110 | 1 960 | 2 100 | 2 300 | 2 370 |
| 119 | 1 780 | 2 120 | 2 520 | 2 570 | 2 720 | 2 990 |
| 131 | 2 060 | 2 400 | 3 370 | 3 530 | 3 730 | 3 770 |
| 144 | 2 930 | 3 180 | 3 890 | 4 050 | 4 460 | 4 590 |

La latence est plate (P95 de 37 à 55 ms) jusqu'à environ 55 req/s, monte doucement jusqu'à 70 req/s (P95 90 ms), puis
casse entre 70 et 80 req/s : à 81 req/s le P95 est de 373 ms, au-dessus du seuil de 300 ms, et il atteint 2 s à
106 req/s. Cela correspond aux environ 37 req/s par pod mesurés plus haut. Le HPA ajoute des pods pendant le test, ce
qui retarde un peu la saturation observée. Chaque tranche contient peu de requêtes aux faibles débits (environ 100 à
200) : les P99 y sont peu fiables.

## Analyse

**Ce qui tient.** À 60 req/s (`sustained`), l'API est confortable : P95 109 ms, P99 351 ms, aucune erreur, CPU de
l'HPA à 73 %, 3 pods. Tant que la charge reste sous environ 35 req/s par pod, le seuil de 300 ms de l'objectif de
latence est respecté.

**Ce qui limite : un pod ne dépasse pas environ 1 cœur.** Chaque pod sature vers 1 CPU alors que la limite est de 2.
Le modèle et spaCy sont protégés par un verrou unique (ils ne sont pas garantis thread-safe) et Python n'exécute
qu'un thread à la fois (GIL) : donner plus de CPU à un pod ne sert à rien. Le levier est de multiplier les pods ou
les processus.

**L'autoscaling est trop lent pour un pic.** Le HPA, réglé à 60 % de CPU, ne réagit qu'une fois le pod déjà saturé.
Ajouter un pod prend 1 à 2,5 minutes : mesure du CPU, puis, si aucun nœud n'a de place, environ 65 s pour que le
cluster ajoute un nœud, puis démarrage du pod (chargement du modèle et de spaCy). Pendant ce temps, les requêtes
attendent. C'est ce qui explique les P95 de plusieurs secondes lors du `ramp` et du `spike`, même sans erreur.

**Défaut mis en évidence par le test, puis corrigé.** Avant correction, `/health` était une route synchrone qui
partageait le pool de 40 threads avec `/score`. Sous charge, la probe de disponibilité dépassait son délai de 2 s : le
pod sortait du Service, et la probe de vivacité le redémarrait (1, 3 puis 1 redémarrages ; jusqu'à 0 pod prêt). C'est
ce qui produisait 4,37 % (`ramp`) et 27,63 % (`spike`) d'erreurs. Les routes `/health` et `/metrics` sont maintenant
`async` : elles répondent immédiatement, sans attendre le pool. Un test automatique le vérifie (60 requêtes `/score`
bloquées, `/health` et `/metrics` doivent répondre en moins de 3 s ; il échouait avant la correction). Après la
correction : 0 redémarrage, 0 % d'erreur.

**Ressources.** La mémoire mesurée est de 360 à 474 Mo par pod pour 1 Gi demandé : la demande est trop haute et coûte
des nœuds inutiles. Le CPU (environ 1 cœur par pod à saturation, 5,8 cœurs au total pour 6 pods) confirme que la
limite de 2 CPU n'est jamais utilisée.

## Reprise après panne (failover)

**Non réalisé.** Le scénario `failover` a seulement été exécuté sans perturbation (40 req/s, 0 erreur, P95 52 ms,
2 pods prêts) : cela ne prouve rien sur la tolérance à la panne. Ce que la configuration permet d'attendre, sans
l'avoir mesuré : 2 pods minimum répartis sur 2 zones, un PodDisruptionBudget (`minAvailable: 1`) et un déploiement
progressif sans indisponibilité. Pour le mesurer :

```powershell
$env:DSS_API_KEY = "<clé d'un client>"
.\loadtest\run-loadtest.ps1 -Scenario failover -Disrupt pod    # supprime un pod après 90 s
.\loadtest\run-loadtest.ps1 -Scenario failover -Disrupt node   # vide le nœud d'un pod après 90 s
.\loadtest\summarize-results.ps1
.\loadtest\run-loadtest.ps1 -Cleanup
```

## Limites de la mesure

- Un seul passage par scénario : pas de répétition, donc pas de marge d'erreur.
- Comparaison avant/après biaisée en faveur de « après » (cluster déjà chaud, voir plus haut).
- Textes synthétiques, mélange 50/30/20 : le trafic réel peut être différent.
- Le générateur tourne dans le même cluster que l'API et peut partager des nœuds avec elle.
- Les redémarrages ne sont pas disponibles dans Prometheus (pas de `kube-state-metrics` exploitable) ; ils ont été
  relevés avec `kubectl`.
- Pas de test d'endurance ni de test depuis l'extérieur du cluster.

## Améliorations proposées

| Problème observé | Amélioration | Effet attendu |
|---|---|---|
| Un pod sature à environ 37 req/s (verrou + GIL) | 2 processus par pod ou plus de pods, avec des demandes de CPU adaptées | Environ 2 fois la capacité par pod |
| Montée en charge trop lente | `minReplicas` à 3 ou 4, cible CPU à 40-50 %, `behavior.scaleUp` plus agressif | Moins de saturation pendant le démarrage des pods |
| Latence de plusieurs secondes sous saturation | Limiter la file et répondre `429` avec `Retry-After` au lieu de tout accepter | Réponse rapide et explicite plutôt qu'une attente de 5 s |
| Mêmes textes rescorés | **Cache** (Redis/Memorystore) des scores, clé = empreinte du texte anonymisé, durée de vie courte (RGPD : jamais le texte lui-même) | Moins de calcul pour les textes répétés ; gain à mesurer |
| Exposition de l'API | **Load balancer** (Ingress/Gateway ou LB interne) devant le Service, avec contrôles de santé et limitation de débit par client | Répartition entre zones et protection contre les excès d'un client |
| Perte d'un nœud ou d'une zone | **Redondance** : `topologySpreadConstraints` entre zones et nœuds, au moins 3 pods, PDB conservé | Une panne ne retire qu'une partie de la capacité |
| 1 Gi demandé pour 0,4 à 0,5 Go utilisé | Réduire la demande mémoire à environ 640 Mi (limite 1 Gi) | Moins de nœuds, moins de coût |
