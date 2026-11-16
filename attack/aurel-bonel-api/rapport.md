# Rapport d'attaque adversariale : API d'Aurel Bonel

Cible : `POST http://34.54.213.190/score` (clé API fournie par son propriétaire, non reproduite ici).
Attaque : exemple adversarial par perturbation de texte, qui garde le sens pour un lecteur humain.

## Protocole

| | |
|---|---|
| Échantillon | 200 commentaires du jeu de test Jigsaw : 100 réellement toxiques, 100 réellement propres, graine 0 |
| Perturbation | La moitié des mots de 4 lettres ou plus : homoglyphe cyrillique, leetspeak, lettres échangées ou doublées, espace de largeur nulle |
| Verdict de la cible | Son propre champ `toxic` (le seuil est le sien, inconnu de l'attaquant) |
| Volume | 400 requêtes envoyées une par une, 50 ms de pause entre deux : une mesure, pas un test de charge |
| Résultats bruts | [`summary.json`](summary.json) |

Réponse de la cible : `{"score": 100.0, "toxic": true, "model": "lstm", "latency_ms": 10.57}`.

## Résultats

| Métrique | Avant (textes originaux) | Après (textes perturbés) | Variation |
|---|---:|---:|---:|
| Précision | 85,3 % | 74,3 % | −11,0 points |
| Rappel | 93,0 % | 84,0 % | **−9,0 points** |
| Taux de faux positifs | 16,0 % | 29,0 % | **+13,0 points** |
| Exactitude | 88,5 % | 77,5 % | −11,0 points |
| Score moyen | 57,2 | 60,8 | +3,6 |
| Latence mesurée côté client (moyenne) | 55,4 ms | 53,8 ms | inchangée |
| Latence mesurée côté client (P95) | 106,4 ms | 94,0 ms | inchangée |
| Latence rapportée par le serveur (moyenne) | 25,7 ms | 28,3 ms | +2,6 ms |

**10 des 100 commentaires toxiques ont échappé à la détection** une fois perturbés, alors qu'ils étaient signalés avant.

## Interprétation

- **L'attaque fonctionne** : le rappel perd 9 points. Un attaquant fait passer environ un commentaire toxique sur dix.
- **La perturbation dégrade aussi les commentaires propres** : le taux de faux positifs passe de 16 % à 29 %. L'attaque
  n'aide donc pas seulement l'attaquant, elle fait bloquer à tort des commentaires légitimes, ce qui dégrade la
  précision du système pour tous ses utilisateurs.
- **Hypothèse, non vérifiée** (seules les sorties de l'API sont visibles, pas son code) : le pipeline traite
  probablement les mots inconnus d'une façon qui fait monter le score. Le score moyen augmente de 3,6 points sur
  l'ensemble de l'échantillon, ce qui est cohérent avec des mots corrompus qui tirent le score vers le haut au lieu
  d'être ignorés. Une lecture du code ou du vocabulaire serait nécessaire pour trancher.
- **Le niveau de base favorise le rappel** : 93 % de rappel pour 16 % de faux positifs sur des textes non perturbés. Ce
  réglage rend l'API plus sensible à tout ce qui fait monter le score.
- **Latence** : pas d'effet mesurable. La latence côté client est dominée par le réseau (environ 55 ms, contre 26 ms de
  traitement côté serveur) ; la petite hausse côté serveur (+2,6 ms) est dans le bruit d'une mesure sur 200 requêtes.

## Limites de cette mesure

- 200 commentaires, une seule graine, un seul taux de perturbation (50 %) : les écarts de quelques points ne sont pas
  tous significatifs. Le passage de 16 à 29 faux positifs sur 100 commentaires propres est le résultat le plus net ;
  la baisse de rappel (93 → 84) repose sur 9 commentaires.
- La vérité terrain est celle du jeu Jigsaw. Si le modèle cible a été entraîné autrement, ses « erreurs » sont
  mesurées par rapport à cette référence, pas à la sienne.
- Un seul passage : pas de mesure de la variabilité entre exécutions.

## Recommandation

Si le modèle est un LSTM à vocabulaire fixe, deux corrections côté serveur neutralisent la plupart de ces
perturbations : retirer les caractères invisibles (espace de largeur nulle, etc.) avant le nettoyage du texte, puis
remplacer tout mot inconnu du vocabulaire par le mot du vocabulaire situé à une édition de distance (suppression,
transposition, substitution ou insertion). Chaque technique de l'attaque ne change qu'un caractère par mot, ce qui la
rend récupérable par cette correction.
