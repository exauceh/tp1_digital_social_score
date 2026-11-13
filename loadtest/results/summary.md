# Resultats des tests de charge

Genere par `loadtest/summarize-results.ps1` le 2026-11-13 14:51 a partir des fichiers de ce dossier et de Prometheus.

## Tableau (format du cours numero 3)

| Scenario | Debit moyen k6 (req/s) | Debit max servi (req/s) | Latence moyenne (ms) | P95 (ms) | P99 (ms) | Taux d'erreur (%) | Iterations perdues | CPU moyen (% de la demande) | Memoire moyenne par pod (Mo) | Observations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ramp | 60.6 | 108.6 | 1040 | 3500 | 3950 | 0.00 | 427 | 48 | 412 | |
| sustained | 60.0 | 60.1 | 44 | 109 | 351 | 0.00 | 0 | 50 | 360 | |
| spike | 131.5 | 220.5 | 2720 | 4760 | 5450 | 0.00 | 3246 | 54 | 456 | |
| failover | 38.8 | 38.2 | 1640 | 7400 | 8580 | 0.78 | 279 | 24 | 418 | |
| flood | 149.5 | 149.4 | 72 | 212 | 351 | 0.00 | 0 | 61 | 402 | |

## Detail par scenario

### ramp

- Fenetre de mesure : 15:26:25-15:30:57 (heure locale)
- Requetes : 14722 ; en echec (k6) : 0.00 % ; iterations perdues : 427 ; VUs max : 460
- Latence cote client (k6) : moyenne 1040 ms, mediane 273 ms, P90 3020 ms, P95 3500 ms, P99 3950 ms, max 4590 ms
- Autoscaling : pods de 2 a 6, **minimum de pods prets : 2**, noeuds de 3 a 3, CPU du HPA maximal : 105 % de la demande
- Cote serveur (Prometheus) : debit max servi 108.6 req/s, P95 4257 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 1.06 coeur(s), max 2.40 ; memoire moyenne par pod 412 Mo, max 438 Mo

### sustained

- Fenetre de mesure : 14:14:37-14:18:05 (heure locale)
- Requetes : 10801 ; en echec (k6) : 0.00 % ; iterations perdues : 0 ; VUs max : 50
- Latence cote client (k6) : moyenne 44 ms, mediane 26 ms, P90 72 ms, P95 109 ms, P99 351 ms, max 729 ms
- Autoscaling : pods de 2 a 3, **minimum de pods prets : 2**, noeuds de 4 a 4, CPU du HPA maximal : 73 % de la demande
- Cote serveur (Prometheus) : debit max servi 60.1 req/s, P95 114 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 1.14 coeur(s), max 1.61 ; memoire moyenne par pod 360 Mo, max 369 Mo

### spike

- Fenetre de mesure : 14:53:02-14:57:18 (heure locale)
- Requetes : 30253 ; en echec (k6) : 0.00 % ; iterations perdues : 3246 ; VUs max : 800
- Latence cote client (k6) : moyenne 2720 ms, mediane 2940 ms, P90 4200 ms, P95 4760 ms, P99 5450 ms, max 6950 ms
- Autoscaling : pods de 5 a 6, **minimum de pods prets : 5**, noeuds de 5 a 6, CPU du HPA maximal : 108 % de la demande
- Cote serveur (Prometheus) : debit max servi 220.5 req/s, P95 4927 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 2.72 coeur(s), max 5.80 ; memoire moyenne par pod 456 Mo, max 466 Mo

### failover

- Fenetre de mesure : 14:44:59-14:48:02 (heure locale)
- Requetes : 9321 ; en echec (k6) : 0.78 % ; iterations perdues : 279 ; VUs max : 285
- Latence cote client (k6) : moyenne 1640 ms, mediane 35 ms, P90 5480 ms, P95 7400 ms, P99 8580 ms, max 9410 ms
- Autoscaling : pods de 3 a 3, **minimum de pods prets : 2**, noeuds de 5 a 5, CPU du HPA maximal : 35 % de la demande
- Cote serveur (Prometheus) : debit max servi 38.2 req/s, P95 51 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 0.59 coeur(s), max 0.97 ; memoire moyenne par pod 418 Mo, max 421 Mo
- Panne provoquee : 14:46:35  draining node gke-dss-std-default-pool-661c9ff3-jzpp (hosts pod dss-api-759bb59f5f-sdcrf) ; node/gke-dss-std-default-pool-661c9ff3-jzpp cordoned

### flood

- Fenetre de mesure : 14:24:53-14:27:01 (heure locale)
- Requetes : 13501 ; en echec (k6) : 0.00 % ; iterations perdues : 0 ; VUs max : 100
- Latence cote client (k6) : moyenne 72 ms, mediane 44 ms, P90 165 ms, P95 212 ms, P99 351 ms, max 827 ms
- Autoscaling : pods de 4 a 6, **minimum de pods prets : 4**, noeuds de 4 a 4, CPU du HPA maximal : 88 % de la demande
- Cote serveur (Prometheus) : debit max servi 149.4 req/s, P95 219 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 2.02 coeur(s), max 3.57 ; memoire moyenne par pod 402 Mo, max 410 Mo

## Latence par niveau de charge (ramp)

Chaque ligne regroupe les requetes envoyees pendant 15 s ; le debit est celui **demande** a ce moment (au-dela de la saturation, l'API en sert moins). Latence mesuree par k6.

| Debit demande (req/s) | Moyenne (ms) | Mediane (ms) | P90 (ms) | P95 (ms) | P99 (ms) | Max (ms) |
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
| 94 | 339 | 224 | 857 | 915 | 1280 | 1410 |
| 106 | 1250 | 1110 | 1960 | 2100 | 2300 | 2370 |
| 119 | 1780 | 2120 | 2520 | 2570 | 2720 | 2990 |
| 131 | 2060 | 2400 | 3370 | 3530 | 3730 | 3770 |
| 144 | 2930 | 3180 | 3890 | 4050 | 4460 | 4590 |

## Limites de la mesure

- Le generateur de charge (k6) tourne sur les memes noeuds que l'API et consomme un peu de leur CPU.
- Les durees sont plus courtes que celles du cours (3 a 4 minutes au lieu de 30).
- Textes synthetiques. Le CPU du tableau est la moyenne du HPA (pourcentage du CPU demande, 1 CPU par pod).
- Le 'debit moyen' est celui de toute la fenetre ; le 'debit max servi' est le meilleur debit de 200 observe par Prometheus sur 1 minute.
- Latence k6 : mesuree par le client, file d'attente reseau et attente avant l'API comprises ; P95 serveur : mesuree par l'API.
