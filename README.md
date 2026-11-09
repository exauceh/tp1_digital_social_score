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
