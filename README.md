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

**Kubernetes (GKE Autopilot) sur Google Cloud, dans une région européenne** (`europe-west1`, à confirmer pour GKE).
L'API est empaquetée dans une image Docker (Python 3.14, PyTorch en version CPU, modèle LSTM de 25 Mo inclus), poussée
dans Artifact Registry puis lancée dans un cluster.

| Besoin | Choix | Détail |
|---|---|---|
| Machines | GKE Autopilot | Google gère les serveurs : pas de nœuds à administrer |
| Disponibilité | 2 pods, `PodDisruptionBudget` | Un pod peut tomber ou être mis à jour pendant que l'autre répond ; au moins 1 pod reste actif pendant une maintenance |
| Mise à jour | `RollingUpdate` sans indisponibilité | Un nouveau pod doit être prêt avant qu'un ancien soit retiré ; retour arrière possible avec `kubectl rollout undo` |
| Montée en charge | `HorizontalPodAutoscaler` | De 2 à 6 pods, cible de 60 % du CPU demandé |
| Dimensionnement | 1 CPU / 1 Go demandés, 2 CPU / 1,5 Go au maximum | Mémoire mesurée à environ 750 Mo en local (build PyTorch CUDA de Windows, donc un majorant pour l'image Linux CPU) |
| Réseau | `Service` interne (`ClusterIP`) | Pas d'IP publique : l'API n'a pas encore d'authentification ni de HTTPS (étape 4). Les tests passent par `kubectl port-forward` |
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

**État d'avancement.** L'image, les manifests et les commandes sont prêts. Le déploiement sur le cloud n'a pas encore
été exécuté : les résultats de la section suivante viennent d'essais en local.

## L'API

Elle est écrite avec FastAPI ([api.py](src/tp1_digital_social_score/api.py)).

| Route | Méthode | Rôle |
|---|---|---|
| `/score` | POST | Reçoit un texte, renvoie le score de 0 à 100 |
| `/health` | GET | Sonde de santé pour Kubernetes |
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
logs. Seuls l'identifiant de requête, la longueur du texte, le score et la latence sont journalisés. Des tests
automatiques vérifient ces deux garanties.



