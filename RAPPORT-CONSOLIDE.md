# Rapport consolidé final — Exercice Red Team sur les API étudiantes

**Date :** 2026-11-16, 10h21 → 12h06 · **3 vagues + 2 re-runs** · **9 équipes attaquées**
**Cadre :** lab pédagogique consentant — scope signé HMAC par run, clés API scellées,
plafonds absolus (100k req / 128 conc / 90-600 s), kill switches, journal chaîné,
aucune exfiltration, aucun tir destructeur (aucun marqueur d'intégrité exposé).
**Outil :** Red Team IA — reconnaissance GLM 5.3 Flash + 18 sondes déterministes
(web.*, 7 sondes IA llm.*, impact.load/crash/destroy, nmap/nuclei/sqlmap).

**Convention de lecture** : ⚔️ = attaque réellement exécutée avec preuve mesurée ;
🔍 = faiblesse détectée sans exploitation ; « confirmed » = preuve reproduite au rejeu.

---

## Synthèse générale

- **29 findings confirmés** sur 9 équipes — **le DoS a réussi sur 100 % des cibles**.
- **Deux échecs de détection côté étudiants** : aucune équipe n'a réagi pendant les
  fenêtres d'attaque — monitoring insuffisant (pas d'alerte CPU/429/5xx) et, pour
  certains, attaque absorbée à l'edge (LB) sans atteindre les pods.
- **Deux incidents majeurs** : l'hôte de Loris à terre ~10 min (pression soutenen) ;
  Arianne révèle score + seuil + modèle dans chaque réponse (constaté par recon, non
  reproduit in-chain — son API rejette strictement les champs inattendus).
- **Tous les services attaqués sont revenus opérationnels** après les tirs.

---

## Résultats par étudiant

### Rémi (ALLAIN) — `http://34.140.180.53` · vague 5 · 4 findings
- ⚔️ HIGH — DoS : **2749 requêtes / 91,7 s** à concurrence 128 sur son endpoint, latence
  dégradée sous charge, kill switch non déclenché (service qui rame sans mourir)
- 🔍 MEDIUM — En-têtes manquants (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)
- 🔍 MEDIUM — Aucune limitation de débit ; 🔍 LOW — aucun WAF
- **Remédiation** : en-têtes, rate-limiting par clé/IP (il a DÉJÀ réagi en déployant une
  redirection HTTP→HTTPS pendant l'exercice — le cycle de réponse fonctionne), puis
  re-tir de validation.

### Eliana / Damondgwende (+ Situ, Jean-philippe) — `https://136-81-204-100.sslip.io`
- vagues 1+4+5 cumulées · **4 findings** (le run « allain » des logs est leur cible)
- ⚔️ HIGH — DoS : **43 685 requêtes ACCEPTÉES en 4 min (~180 req/s)**, p95 4276 ms contre
  référence 56 ms (**×76**) — service saturé mais répondeur, 0 % d'erreurs
- 🔍 MEDIUM — En-têtes manquants ; 🔍 MEDIUM — aucune limitation observable ; 🔍 LOW — WAF
- **Remédiation** : le limiter ne protège pas la latence → throttling progressif ;
  en-têtes ; surveiller le taux de requêtes entrantes (l'attaque était visible là).

### Wissam (ABDELWAHED) — tunnel trycloudflare · **5 findings — la cible la plus exposée**
- ⚔️ HIGH — **Endpoint d'inférence accessible SANS authentification** : `POST /predict`
  sans jeton → HTTP 200
- 🔍 MEDIUM — **Scores par classe exacts exposés** (`{"scores_par_classe": {...}}`) :
  la précondition directe de Model Extraction, Membership Inference et Model Inversion
- ⚔️ HIGH — DoS (kill switch déclenché — service cassé sous charge)
- 🔍 MEDIUM — En-têtes manquants
- **Remédiation** : authentification obligatoire (sa tunnel est une surface nue, sans
  LB ni limiter), ne renvoyer que la décision, en-têtes.

### Loris (CAZAUX) — bert · **5 findings + incident majeur**
- ⚔️ HIGH — **Modèle extractible par requêtes** : 400 échantillons en 12 s
  (**2005 requêtes/min, zéro 429**) — frontière de décision échantillonnée sans frein
- ⚔️ HIGH — DoS (latence dégradée sous charge)
- 🔍 MEDIUM — **Rate-limiting présent mais saturable** (429 en masse, dégradation maintenue)
- 🔍 MEDIUM — En-têtes manquants ; 🔍 LOW — aucun WAF
- 🔥 **INCIDENT** : après le tir soutenu de laya (100k inférences réelles, clé exemptée),
  **son hôte entier (bert+laya+lstm) est tombé ~10 min** (LB sans backend sain) puis
  récupéré par l'orchestrateur. La preuve est dans SA console GKE (fenêtre 10h36-10h52 :
  OOMKilled / CrashLoopBackOff / restarts) — s'il n'y voit rien, son monitoring a un trou.
- **Remédiation** : memory limits + graceful degradation, limiter AUSSI sur le POST
  authentifié, en-têtes, et vérifier les événements GKE de la fenêtre d'attaque.

### Arianne (MAFO) — `http://34.163.37.11` · vague 5 · 0 findings in-chain
- 🔍 (reconnaissance, 3 requêtes) — chaque réponse expose **le score exact (2.5), le
  seuil de détection (31.3), le modèle (`bert-int8-v1`) et sa latence interne** : le
  contournement de détection (Adversarial Example) est trivial avec le seuil connu
- ⚠️ Son API **rejette strictement les champs inattendus** (422) : les sondes IA avec
  payload générique n'ont pas atteint l'inférence — correctif outil prévu (utiliser le
  champ déclaré de l'OpenAPI), le tir complet sera rejoué après
- `GET /cle` protégé (401) ✓ ; `/health` public ✓
- **Remédiation** : ne plus exposer score/seuil/modele dans la réponse (ne renvoyer que
  la décision), et poser le monitoring — la fenêtre d'attaque a coïncidé avec un service
  injoignable, à rejouer après redéploiement stable.

### Titouan et Cléo (ROBE) — `http://136.82.246.21` (`POST /model`) · vague 6 · 4 findings
- ⚔️ HIGH — DoS sur **leur vrai endpoint d'inférence** (`POST /model`, découvert via
  l'OpenAPI) : 7712 requêtes ACCEPTÉES en 91,7 s, p50 1397 ms contre référence 126 ms
  (**×11**) — première équipe dont la charge soutenue a atteint le modèle directement
- 🔍 MEDIUM — **Modèle extractible mais freiné** : 30 échantillons en 4 s puis **429 en
  masse** — leur limiter a réduit l'extraction à 489/min (défense partielle : le
  substituted reste entraînable, juste plus lent)
- 🔍 MEDIUM — En-têtes manquants (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)
- 🔍 LOW — Aucun WAF ; **xmlrpc.php exposé** (amplification DoS possible)
- **Remédiation** : en-têtes, désactiver xmlrpc.php, et étendre le limiter — il a tenu
  30 requêtes puis a cédé sur la latence pendant le DoS.
- *Note : leur service flappait lors de la vague 5 — la vague 6 avec détection de
  stabilité (3 sondes consécutives) a permis un audit propre.*

### Pierre-Dennick — `https://136.82.152.12.sslip.io` (`POST /v1/analyze`) · vague 6 · 2 findings
- ⚔️ MEDIUM — DoS : **15 923 requêtes ACCEPTÉES en 90 s (~176 req/s soutenus)**, p50
  400 ms contre référence 151 ms (**×2,6**) — kill switch jamais déclenché : son service
  a ENCAISSÉ la charge sans casser (le plus solide du lab avec Rémi), mais la dégradation
  est mesurable
- 🔍 MEDIUM — En-têtes manquants (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)
- Les sondes `llm.*` n'ont rien détecté sur son endpoint (robustesse correcte ou champ
  de payload non couvert — à qualifier avec lui)
- **Remédiation** : en-têtes ; surveiller le taux de requêtes pendant les fenêtres d'audit.

### Arianne (MAFO) — `http://34.163.37.11` · vague 5 · 0 findings in-chain
- 🔍 (reconnaissance, 3 requêtes) — chaque réponse expose **le score exact (2.5), le
  seuil de détection (31.3), le modèle (`bert-int8-v1`) et sa latence interne** : le
  contournement de détection (Adversarial Example) est trivial avec le seuil connu
- ⚠️ Deux obstacles au tir complet : (1) son API **rejette strictement les champs
  inattendus** (422) — corrigé dans l'outil (les sondes lisent le champ déclaré de
  l'OpenAPI) ; (2) **son service était arrêté pendant les deux fenêtres de re-tir**
  (timeouts continus 14h14-14h26)
- `GET /cle` protégé (401) ✓ ; `/health` public ✓
- **Remédiation** : ne plus exposer score/seuil/modele dans la réponse (ne renvoyer que
  la décision), redéployer stable, puis re-tir (la commande est prête, l'outil parle
  maintenant son schéma).

### Exaucé (HOUNDONOUGBO) — 4 findings
- ⚔️ HIGH — DoS (kill switch immédiat)
- 🔍 MEDIUM — Aucune limitation observable ; 🔍 MEDIUM — en-têtes manquants ; 🔍 LOW — WAF
- **Remédiation** : idem — le service casse dès la montée en concurrence.

### Florentin (BIAOU) — 4 findings
- ⚔️ HIGH — DoS (kill switch immédiat)
- 🔍 MEDIUM — Aucune limitation observable ; 🔍 MEDIUM — en-têtes manquants ; 🔍 LOW — WAF
- **Remédiation** : idem. (Bonus : son API n'expose pas de /docs public — bonne pratique.)

### Aurel (ADISSN) — 2 findings
- ⚔️ HIGH — DoS (kill switch immédiat)
- 🔍 MEDIUM — En-têtes manquants (CSP, HSTS ; X-Frame-Options/X-Content-Type-Options
  partiellement présents — bien)
- **Remédiation** : terminer les en-têtes + dimensionnement.

---

## Ce que chaque étudiant devait voir (et n'a pas vu)

| Signal | Où | Pourquoi invisible |
|---|---|---|
| CPU des pods en plateau | Console GKE / Cloud Monitoring (natif) | Personne ne regardait pendant la fenêtre |
| Taux de requêtes + 429 | Ingress / logs du limiter | Non instrumenté |
| Latence p50/p95 | Histogrammes applicatifs | Non exportés |
| Service down (Loris) | `kubectl get pods` / événements GKE | Fenêtre 10h36-10h52 à vérifier rétroactivement |

**La règle du jeu** : la preuve de chaque attaque est dans leur rapport d'audit
(volumes, statuts, latences chiffrés) — leur travail consiste à retrouver la trace
correspondante dans **leur** monitoring, corriger, puis faire rejouer le tir.

## Non couvert par les sondes HTTP (attaques d'entraînement)

Data Poisoning, Backdoor, Gradient Leakage, Data Drift → volet DevSecOps :
provenance des données, sécurisation du pipeline CI/CD, monitoring de dérive.

## Rejeux de validation (après correctifs)

Chaque équipe : `uv run redteam run --mode crew --target <son-url> --impact --destructive
--api-key <clé> --attestation "lab etu"` — les DoS doivent tenir ou être atténués, et
le delta avant/après fait foi. Pour Arianne : corriger d'abord l'exposition du seuil.
