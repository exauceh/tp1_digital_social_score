# Resultats des tests de charge

Genere par `loadtest/summarize-results.ps1` le 2026-11-12 14:38 a partir des fichiers de ce dossier et de Prometheus.

## Tableau (format du cours numero 3)

| Scenario | Debit moyen k6 (req/s) | Debit max servi (req/s) | Latence moyenne (ms) | P95 (ms) | P99 (ms) | Taux d'erreur (%) | Iterations perdues | CPU moyen (% de la demande) | Memoire moyenne par pod (Mo) | Observations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ramp | 49.7 | 76.7 | 2840 | 7760 | 8050 | 4.37 | 2728 | 54 | 425 | |
| sustained | 60.0 | 60.1 | 44 | 109 | 351 | 0.00 | 0 | 50 | 360 | |
| spike | 70.7 | 108.9 | 5690 | 10000 | 10000 | 27.63 | 17238 | 52 | 388 | |
| failover | 40.0 | 40.0 | 27 | 52 | 87 | 0.00 | 0 | 41 | 409 | |

## Detail par scenario

### ramp

- Fenetre de mesure : 14:04:20-14:08:53 (heure locale)
- Requetes : 12421 ; en echec (k6) : 4.37 % ; iterations perdues : 2728 ; VUs max : 600
- Latence cote client (k6) : moyenne 2840 ms, mediane 553 ms, P90 7610 ms, P95 7760 ms, P99 8050 ms, max 8650 ms
- Autoscaling : pods de 2 a 4, **minimum de pods prets : 0**, noeuds de 2 a 4, CPU du HPA maximal : 107 % de la demande
- Cote serveur (Prometheus) : debit max servi 76.7 req/s, P95 9159 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 0.99 coeur(s), max 2.23 ; memoire moyenne par pod 425 Mo, max 475 Mo

### sustained

- Fenetre de mesure : 14:14:37-14:18:05 (heure locale)
- Requetes : 10801 ; en echec (k6) : 0.00 % ; iterations perdues : 0 ; VUs max : 50
- Latence cote client (k6) : moyenne 44 ms, mediane 26 ms, P90 72 ms, P95 109 ms, P99 351 ms, max 729 ms
- Autoscaling : pods de 2 a 3, **minimum de pods prets : 2**, noeuds de 4 a 4, CPU du HPA maximal : 73 % de la demande
- Cote serveur (Prometheus) : debit max servi 60.1 req/s, P95 114 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 1.14 coeur(s), max 1.61 ; memoire moyenne par pod 360 Mo, max 369 Mo

### spike

- Fenetre de mesure : 14:18:21-14:22:36 (heure locale)
- Requetes : 16261 ; en echec (k6) : 27.63 % ; iterations perdues : 17238 ; VUs max : 800
- Latence cote client (k6) : moyenne 5690 ms, mediane 6420 ms, P90 9990 ms, P95 10000 ms, P99 10000 ms, max 10070 ms
- Autoscaling : pods de 3 a 5, **minimum de pods prets : 0**, noeuds de 4 a 6, CPU du HPA maximal : 94 % de la demande
- Cote serveur (Prometheus) : debit max servi 108.9 req/s, P95 10000 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 1.21 coeur(s), max 2.23 ; memoire moyenne par pod 388 Mo, max 439 Mo

### failover

- Fenetre de mesure : 14:30:02-14:34:33 (heure locale)
- Requetes : 9600 ; en echec (k6) : 0.00 % ; iterations perdues : 0 ; VUs max : 50
- Latence cote client (k6) : moyenne 27 ms, mediane 21 ms, P90 45 ms, P95 52 ms, P99 87 ms, max 306 ms
- Autoscaling : pods de 2 a 2, **minimum de pods prets : 2**, noeuds de 6 a 6, CPU du HPA maximal : 50 % de la demande
- Cote serveur (Prometheus) : debit max servi 40.0 req/s, P95 50 ms, reponses 5xx 0.00 %, redemarrages de conteneurs n/a
- Ressources : CPU total moyen 0.80 coeur(s), max 1.13 ; memoire moyenne par pod 409 Mo, max 424 Mo

## Limites de la mesure

- Le generateur de charge (k6) tourne sur les memes noeuds que l'API et consomme un peu de leur CPU.
- Les durees sont plus courtes que celles du cours (3 a 4 minutes au lieu de 30).
- Textes synthetiques. Le CPU du tableau est la moyenne du HPA (pourcentage du CPU demande, 1 CPU par pod).
- Le 'debit moyen' est celui de toute la fenetre ; le 'debit max servi' est le meilleur debit de 200 observe par Prometheus sur 1 minute.
- Latence k6 : mesuree par le client, file d'attente reseau et attente avant l'API comprises ; P95 serveur : mesuree par l'API.
