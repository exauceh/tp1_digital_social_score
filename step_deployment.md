# Déploiement sur Kubernetes (GKE Standard) : clés API dans Secret Manager, accès par IAM

# 0. Variables à adapter
$PROJECT_ID = "tp1-digital-score"
$REGION     = "europe-west1"
$REPO       = "dss-api"
$CLUSTER     = "dss-std"       # cluster GKE Standard : disques pd-standard, compte de service dédié pour les noeuds
$OLD_CLUSTER = "dss-cluster-2" # cluster Autopilot bloqué par le quota de disques SSD (pods en Pending) : à supprimer
$MACHINE_TYPE = "e2-standard-2" # 2 vCPU complets, 8 Go. e2-medium coûte moins cher mais partage son coeur (ralentit sous charge)
$SECRET     = "dss-api-keys"   # nom du secret dans Secret Manager
$VERSION    = "v4"             # image déjà poussée dans le dépôt (v1 = sans authentification ; v4 = clé API lue dans Secret Manager)
$IMAGE      = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/dss-lstm:$VERSION"

# Assemble les manifests de k8s/ et remplace les deux marqueurs (image, projet) avant l'envoi à Kubernetes
function Render-Manifests($Image) {
    kubectl kustomize k8s | ForEach-Object { $_ -replace 'IMAGE_PLACEHOLDER', $Image -replace 'PROJECT_ID_PLACEHOLDER', $PROJECT_ID }
}

# 1. Connexion, projet, API, plugin d'authentification de kubectl pour GKE
gcloud auth login
gcloud config set project $PROJECT_ID
gcloud services enable container.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud components install gke-gcloud-auth-plugin   # nécessaire à kubectl

# 2. Dépôt d'images dans la même région que le cluster (déjà créé si vous avez fait la v1)
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION --description="Digital Social Score"
gcloud auth configure-docker "$REGION-docker.pkg.dev"

# 3. Générer une clé API pour un client (ici "dev")
#    Elle n'est affichée qu'UNE SEULE fois : conservez-la dans un gestionnaire de mots de passe, jamais dans git.
$generated = uv run python -I -m tp1_digital_social_score.security generate dev 2>$null
$API_KEY   = $generated[0]    # la clé, à donner au client (en-tête X-API-Key)
$API_KEYS  = $generated[1]    # l'entrée "client:hash" : seul ce hash est stocké côté serveur
#    Plusieurs clients : $API_KEYS = "$entree_dev,$entree_alice" (entrées séparées par des virgules)

# 4. Construire et tester l'image en local (en local, les clés passent par la variable API_KEYS)
Set-Content score.json '{"text": "You are a stupid idiot"}'
docker build -t $IMAGE .
docker run --rm -p 8080:8080 -e API_KEYS="$API_KEYS" $IMAGE

#    dans un autre terminal (ou ouvrez http://localhost:8080/docs dans le navigateur) :
curl.exe -s http://localhost:8080/health                                                   # 200, sans clé
curl.exe -s -X POST http://localhost:8080/score -H "Content-Type: application/json" -d "@score.json"   # 401
curl.exe -s -X POST http://localhost:8080/score -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" -d "@score.json"

# 5. Pousser l'image
docker push $IMAGE

# 6. Cluster GKE Standard.
#    Pourquoi pas Autopilot : chaque noeud Autopilot a un disque de 100 Go qui compte dans le quota SSD de la région
#    (250 Go sur ce projet), donc 2 noeuds au maximum. Les 2 noeuds créés (e2-highcpu-2, 2 Go) étaient pleins de
#    composants système et un 3e noeud, plus grand, dépassait le quota : pods en Pending, "GCE quota exceeded".
#    Ici les noeuds utilisent des disques "pd-standard", comptés à part (quota de 2 048 Go, aucun utilisé).
#
#    6a. Supprimer le cluster Autopilot bloqué (il ne sert plus à rien et coûte) et nettoyer kubectl
gcloud container clusters delete $OLD_CLUSTER --region=$REGION
kubectl config delete-context "gke_${PROJECT_ID}_${REGION}_${OLD_CLUSTER}"
#
#    6b. Compte de service dédié aux noeuds (au lieu du compte Compute par défaut, qui a le rôle "editor" sur tout
#        le projet) : il n'a que les droits de journaliser, d'envoyer des métriques et de lire les images du dépôt.
$NODE_SA = "dss-nodes@$PROJECT_ID.iam.gserviceaccount.com"
#        Les trois commandes suivantes sont DÉJÀ FAITES sur votre projet (compte et droits vérifiés) : inutile de les relancer.
gcloud iam service-accounts create dss-nodes --display-name="Noeuds GKE du projet Digital Social Score"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$NODE_SA" --role=roles/container.defaultNodeServiceAccount
gcloud artifacts repositories add-iam-policy-binding $REPO --location=$REGION --member="serviceAccount:$NODE_SA" --role=roles/artifactregistry.reader
#
#    6c. Créer le cluster : région UE, 2 zones (b et c), 1 noeud par zone au départ, jusqu'à 3 par zone
#        (6 noeuds, soit le maximum de l'autoscaler de pods qui vise 6 pods d'1 CPU).
#        --scopes=cloud-platform : l'accès réel est limité par les rôles IAM du compte, pas par les "scopes".
#        --workload-pool : active Workload Identity, utilisé pour lire Secret Manager (étape 8).
gcloud container clusters create $CLUSTER `
  --region=$REGION --node-locations="$REGION-b,$REGION-c" `
  --release-channel=regular `
  --machine-type=$MACHINE_TYPE --num-nodes=1 `
  --enable-autoscaling --min-nodes=1 --max-nodes=3 `
  --disk-type=pd-standard --disk-size=50 `
  --service-account=$NODE_SA --scopes=cloud-platform `
  --workload-pool="$PROJECT_ID.svc.id.goog" --workload-metadata=GKE_METADATA `
  --enable-ip-alias --enable-shielded-nodes
gcloud container clusters get-credentials $CLUSTER --region=$REGION      # kubectl pointe maintenant vers ce cluster
kubectl config current-context                                            # doit se terminer par _dss-std
kubectl get nodes -o wide                                                 # 2 noeuds, tous "Ready"
kubectl cluster-info

# 7. Secret Manager : stocker les clés API (les hashs, pas les clés)
#    --replication-policy=user-managed + --locations : les données restent dans la région choisie (UE)
gcloud secrets create $SECRET --replication-policy=user-managed --locations=$REGION --labels=app=dss
$API_KEYS | gcloud secrets versions add $SECRET --data-file=-

# 8. IAM : l'application ne peut lire QUE ce secret
#    Workload Identity (activé à la création du cluster avec --workload-pool) : le compte de service Kubernetes dss-api du namespace
#    dss est identifié par Google Cloud sans fichier de clé. On lui donne le rôle de lecture sur ce seul
#    secret, pas sur le projet. Cette identité est la même sur tous les clusters du projet : la liaison ne
#    dépend pas du cluster. Elle est DÉJÀ FAITE sur votre projet (vérifiée) : inutile de la refaire.
$PROJECT_NUMBER = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
$APP_IDENTITY   = "principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog/subject/ns/dss/sa/dss-api"
gcloud secrets add-iam-policy-binding $SECRET --member=$APP_IDENTITY --role=roles/secretmanager.secretAccessor
gcloud secrets get-iam-policy $SECRET            # vérifier : une seule identité, ce rôle uniquement

# 9. Déployer
Render-Manifests $IMAGE | kubectl apply -f -
kubectl rollout status deployment/dss-api -n dss --timeout=10m
kubectl get pods,svc,hpa,pdb -n dss
#    Nouveau cluster : il n'y a pas de v1 dessus, les pods v2 sont les premiers à démarrer.
#    Diagnostic si un pod ne démarre pas :
#      kubectl logs -l app=dss-api -n dss --tail=20     # "Could not read the API keys from Secret Manager (...): PermissionDenied"
#                                                       #   = l'étape 8 manque ou le nom du secret est faux
#      kubectl describe pod -n dss                      # ImagePullBackOff : le compte de service des noeuds doit pouvoir
#                                                       #   lire Artifact Registry (rôle roles/artifactregistry.reader)
#      Si les logs indiquent des identifiants introuvables : retirez la ligne automountServiceAccountToken
#      de k8s/deployment.yaml (non testé sur votre cluster).

# 10. Tester sans exposer l'API sur Internet : tunnel local vers le Service
#     (laissez cette commande tourner et utilisez un autre terminal)
kubectl port-forward svc/dss-api 8081:80 -n dss
curl.exe -s http://localhost:8081/health
curl.exe -s -X POST http://localhost:8081/score -H "Content-Type: application/json" -d "@score.json"   # 401
curl.exe -s -X POST http://localhost:8081/score -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" -d "@score.json"
kubectl logs -l app=dss-api -n dss --tail=20      # "API keys loaded ... from Secret Manager", puis "score request_id=... client=dev ..."
kubectl top pods -n dss                           # mémoire et CPU réels : ajuster k8s/deployment.yaml
kubectl get hpa -n dss                            # autoscaling (2 à 6 pods, cible 60 % de CPU)

# 11. IAM : autres droits à contrôler (principe du moindre privilège)
#     a) Noeuds du cluster : compte dédié, créé à l'étape 6.
#     b) Personnes et machines qui déploient : ne leur donnez pas "owner" ni "editor", mais le nécessaire seulement.
#        Pousser des images            : roles/artifactregistry.writer   (sur le dépôt $REPO)
#        Déployer sur le cluster       : roles/container.developer
#        Gérer les clés (ajouter une version, désactiver) : roles/secretmanager.secretVersionManager (sur le secret)
#        Lire les clés (application)   : roles/secretmanager.secretAccessor (sur le secret) : déjà fait à l'étape 8
#     c) Contrôler les droits en vigueur :
gcloud projects get-iam-policy $PROJECT_ID --flatten="bindings[].members" --format="table(bindings.role,bindings.members)"
gcloud iam service-accounts get-iam-policy $NODE_SA

# 12. Mettre à jour le code (v5) et revenir en arrière si besoin
$VERSION = "v5"
$IMAGE   = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/dss-lstm:$VERSION"
docker build -t $IMAGE .
docker push $IMAGE
Render-Manifests $IMAGE | kubectl apply -f -
kubectl rollout status deployment/dss-api -n dss
kubectl rollout undo deployment/dss-api -n dss    # retour à la version précédente

# 13. Faire tourner ou révoquer une clé
#     Chaque ajout crée une NOUVELLE VERSION du secret (l'historique est conservé). Les pods lisent
#     "latest" au démarrage : après un changement, il faut les redémarrer.
$generated    = uv run python -I -m tp1_digital_social_score.security generate dev 2>$null
$NEW_API_KEY  = $generated[0]
$NEW_API_KEYS = $generated[1]                     # pour révoquer un client, ne le remettez simplement pas dans la liste
$NEW_API_KEYS | gcloud secrets versions add $SECRET --data-file=-
kubectl rollout restart deployment/dss-api -n dss
kubectl rollout status deployment/dss-api -n dss
#     Une fois les pods redémarrés, désactivez l'ancienne version (numéro à lire dans la liste) :
gcloud secrets versions list $SECRET
gcloud secrets versions disable 1 --secret=$SECRET

# 14. Éteindre après les tests (un cluster et ses pods coûtent tant qu'ils existent)
kubectl delete namespace dss
gcloud container clusters delete $CLUSTER --region=$REGION
gcloud secrets delete $SECRET                    # supprime le secret et toutes ses versions
gcloud artifacts repositories delete $REPO --location=$REGION
gcloud iam service-accounts delete $NODE_SA      # compte des noeuds, devenu inutile

# 15. Supervision (cours n°3) : métriques Prometheus et alertes
#     L'API expose /metrics ; Managed Service for Prometheus (activé sur le cluster) les collecte grâce à k8s/podmonitoring.yaml,
#     et les alertes SLO sont dans k8s/alert-rules.yaml (appliquées avec le reste par Render-Manifests).
kubectl get podmonitoring,rules -n dss                      # ConfigurationCreateSuccess=True attendu
kubectl get podmonitoring dss-api -n dss -o jsonpath="{.status.conditions[*].type}={.status.conditions[*].status}"
#     Interroger Prometheus managé avec votre identité gcloud (lecture seule) :
$token = gcloud auth print-access-token
$q = 'sum(rate(api_requests_total[1m]))'
Invoke-RestMethod -Method Post -Uri "https://monitoring.googleapis.com/v1/projects/$PROJECT_ID/location/global/prometheus/api/v1/query" `
  -Headers @{ Authorization = "Bearer $token" } -Body @{ query = $q } | ConvertTo-Json -Depth 6
#     Requêtes utiles : histogram_quantile(0.95, sum by (le) (rate(api_latency_seconds_bucket{route="/score"}[5m])))
#                       sum by (status) (rate(api_requests_total[5m]))        kube_horizontalpodautoscaler_status_current_replicas

# 16. Tests de charge (cours n°3, TP étape 5) avec k6, exécutés DANS le cluster
#     (kubectl port-forward est un seul tunnel : il ne convient pas à un test de charge).
#     La clé API d'un client est lue dans la variable d'environnement, jamais passée en argument.
$env:DSS_API_KEY = "<clé d'un client>"
.\loadtest\run-loadtest.ps1 -Scenario ramp                  # montée progressive 5 -> 150 requêtes/s en 4 min
.\loadtest\run-loadtest.ps1 -Scenario sustained             # 60 requêtes/s pendant 3 min
.\loadtest\run-loadtest.ps1 -Scenario spike                 # pic à 250 requêtes/s (stress, autoscaling)
.\loadtest\run-loadtest.ps1 -Scenario failover -Disrupt pod # 40 requêtes/s, un pod supprimé après 90 s
.\loadtest\run-loadtest.ps1 -Scenario failover -Disrupt node # idem avec un noeud vidé (kubectl drain)
#     Résultats dans loadtest\results\ : <scénario>-k6.txt (débit, latence moyenne/P95/P99, taux d'erreur),
#     <scénario>-timeline.txt (pods, noeuds, CPU du HPA toutes les 15 s), <scénario>-events.txt (heure de la panne).
.\loadtest\run-loadtest.ps1 -Cleanup                        # supprime le namespace loadtest et la clé qu'il contient


# 17. Grafana (TP étape 6) : tableau de bord branché sur Managed Prometheus
#     Deux pods dans le namespace "monitoring" : gmp-frontend (lit Managed Prometheus, lecture seule) et Grafana.
#     Rien n'est exposé sur Internet : on y accède par kubectl port-forward.
#     a) Droit de lecture pour gmp-frontend, par Workload Identity (pas de clé) : lecture des métriques uniquement.
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog/subject/ns/monitoring/sa/gmp-frontend" `
  --role=roles/monitoring.viewer --condition=None
#     b) Namespace, puis mot de passe administrateur de Grafana (tapé ici, jamais écrit dans un fichier du dépôt)
kubectl apply -f monitoring/grafana/namespace.yaml
$grafanaPassword = Read-Host "Mot de passe admin Grafana"
kubectl create secret generic grafana-admin -n monitoring "--from-literal=password=$grafanaPassword" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable grafanaPassword
#     c) Déploiement (le nom du projet est injecté comme pour l'API)
kubectl kustomize monitoring/grafana | ForEach-Object { $_ -replace 'PROJECT_ID_PLACEHOLDER', $PROJECT_ID } | kubectl apply -f -
kubectl rollout status deployment/gmp-frontend deployment/grafana -n monitoring
#     d) Ouvrir Grafana : http://localhost:3000 (utilisateur "admin"), dossier "Digital Social Score" > "Digital Social Score - API"
kubectl port-forward svc/grafana 3000:3000 -n monitoring
#     Si les panneaux sont vides : kubectl logs deploy/gmp-frontend -n monitoring --tail=20  (PermissionDenied = étape a)

# 18. Simulations d'attaque (TP étape 6), depuis le cluster, sur notre propre API
#     Gardez le tableau de bord ouvert pendant les tests, et faites une capture à chaque fois.
$env:DSS_API_KEY = "<clé d'un client>"
.\loadtest\run-loadtest.ps1 -Scenario badkeys    # 100 req/s sans clé ou avec une fausse clé, 90 s : attendu 401 partout, alerte AuthenticationFailuresHigh
.\loadtest\run-loadtest.ps1 -Scenario malformed  # entrées invalides ou hostiles, 60 s : attendu 422, jamais de 5xx
.\loadtest\run-loadtest.ps1 -Scenario flood      # un client à 150 req/s pendant 90 s : mesure l'effet sur le service (pas de limite par client)
#     Alertes déclenchées pendant la période (lecture seule) :
$token = gcloud auth print-access-token
Invoke-RestMethod -Method Post -Uri "https://monitoring.googleapis.com/v1/projects/$PROJECT_ID/location/global/prometheus/api/v1/query" `
  -Headers @{ Authorization = "Bearer $token" } -Body @{ query = 'sum(rate(api_auth_failures_total[1m]))' } | ConvertTo-Json -Depth 6
#     Nettoyage à la fin : .\loadtest\run-loadtest.ps1 -Cleanup ; kubectl delete namespace monitoring

# 19. Exposition publique (TP n°2) : LoadBalancer, jeton /metrics et clé invitee
#     a) Rendre l'API joignable depuis l'exterieur du cluster, sans restriction de source pour l'instant
#        (k8s/service.yaml est deja en "type: LoadBalancer"). Appliquer puis relever l'IP :
kubectl apply -f k8s/service.yaml -n dss
kubectl get service dss-api -n dss
#        Remettre en ClusterIP a la fin : kubectl patch service dss-api -n dss -p '{"spec":{"type":"ClusterIP"}}'
#     b) Jeton de /metrics (secret Kubernetes, PAS Secret Manager) : /metrics reste ouvert pour
#        quiconque a l'IP tant que ce secret n'existe pas (voir METRICS_TOKEN dans security.py).
#        Generer un jeton puis creer le secret dans le namespace dss, AVANT de reappliquer
#        podmonitoring.yaml (qui exige desormais ce secret pour scraper) :
$metricsToken = uv run python -I -c "import secrets; print(secrets.token_urlsafe(32))"
kubectl create secret generic dss-metrics-token -n dss "--from-literal=token=$metricsToken" --dry-run=client -o yaml | kubectl apply -f -
Remove-Variable metricsToken
#        Puis appliquer le reste (podmonitoring.yaml branche le meme secret ; deployment.yaml lit
#        METRICS_TOKEN et redemarre les pods pour l'exiger sur /metrics) :
kubectl apply -f k8s/podmonitoring.yaml -n dss
kubectl kustomize k8s | ForEach-Object { $_ -replace 'IMAGE_PLACEHOLDER', $Image -replace 'PROJECT_ID_PLACEHOLDER', $PROJECT_ID } | kubectl apply -f -
kubectl rollout status deployment/dss-api -n dss
#        Verifier : 401 sans jeton, 200 avec, et que Grafana continue de recevoir des points.
curl.exe -s -o NUL -w "sans jeton: %{http_code}\n" "http://<IP_PUBLIQUE>/metrics"
#     c) Clé API "invitee" pour vos camarades (clé separee de "dev", a revoquer apres le TP) :
$generated     = uv run python -I -m tp1_digital_social_score.security generate guest 2>$null
$GUEST_API_KEY = $generated[0]    # a donner aux camarades (en-tête X-API-Key)
$GUEST_ENTRY   = $generated[1]
$current = gcloud secrets versions access latest --secret=$SECRET
"$current,$GUEST_ENTRY" | gcloud secrets versions add $SECRET --data-file=-
kubectl rollout restart deployment/dss-api -n dss
#        Pour revoquer en fin de TP : relire le secret, retirer l'entree "guest:...", ajouter une
#        nouvelle version (comme a l'étape 13), puis redemarrer les pods.

# 20. Automatiser le deploiement (TP2 etape 5) : Cloud Build + Artifact Registry + GKE
#     Pipeline dans cloudbuild.yaml : tests (ruff/mypy/pytest) -> build -> push -> scan de
#     vulnerabilites (Artifact Registry, bloque le build si une faille CRITICAL corrigeable est
#     trouvee) -> deploiement sur le cluster. Execute par un compte de service dedie, pas le
#     compte par defaut de Cloud Build (moindre privilege, comme pour dss-api/dss-nodes).
#     a) Activer les API necessaires et le scan automatique des images poussees
gcloud services enable cloudbuild.googleapis.com containerscanning.googleapis.com --project $PROJECT_ID
#     b) Compte de service dedie au deploiement, avec seulement les droits necessaires
gcloud iam service-accounts create dss-deployer --display-name="Cloud Build : build/push/deploy de dss-api"
$DEPLOYER_SA = "dss-deployer@$PROJECT_ID.iam.gserviceaccount.com"
gcloud artifacts repositories add-iam-policy-binding $REPO --location=$REGION `
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/container.developer
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/logging.logWriter
#        Lire les resultats du scan de vulnerabilites necessite ce role en plus :
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/containeranalysis.occurrences.viewer
#        Le compte qui LANCE le build (vous, ou le declencheur) doit pouvoir emprunter cette identite :
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA `
  --member="user:VOTRE_EMAIL" --role=roles/iam.serviceAccountUser
#     c) Connecter le depot GitHub a Cloud Build (une fois, via la console ou gcloud) puis creer
#        le declencheur : push sur main -> cloudbuild.yaml. L'installation de l'app GitHub Cloud
#        Build se fait dans la console (Cloud Build > Declencheurs > Connecter un depot) ; une fois
#        connecte, creer le declencheur en CLI :
gcloud builds triggers create github `
  --name="dss-api-deploy" --repo-name="tp1_digital_social_score" --repo-owner="exauceh" `
  --branch-pattern="^main$" --build-config="cloudbuild.yaml" `
  --service-account="projects/$PROJECT_ID/serviceAccounts/dss-deployer@$PROJECT_ID.iam.gserviceaccount.com"
#     d) models/lstm est dans .gitignore (27 Mo, pas du code source) : le checkout GitHub que
#        Cloud Build clone ne les a donc jamais. Les deposer dans un bucket prive, lu seulement
#        par dss-deployer, et le pipeline (etape "fetch-model") les recopie avant le build Docker.
gcloud storage buckets create gs://tp1-digital-score-models --location=$REGION --uniform-bucket-level-access
gcloud storage cp -r models/lstm/* gs://tp1-digital-score-models/lstm/
gcloud storage buckets add-iam-policy-binding gs://tp1-digital-score-models `
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/storage.objectViewer
#     e) Tester sans attendre un vrai push :
gcloud builds triggers run dss-api-deploy --branch=main
gcloud builds list --ongoing
#        Suivre un build precis : gcloud builds log <BUILD_ID> --stream

# 21. Commandes usuelles : Grafana et logs des pods
#     a) Lancer Grafana (tunnel local, rien n'est expose sur Internet) :
kubectl port-forward svc/grafana 3000:3000 -n monitoring
#        Puis ouvrir http://localhost:3000 (utilisateur "admin", mot de passe cree a l'etape 17b).
#     b) Logs de l'API (dss-api, namespace dss) :
kubectl logs -l app=dss-api -n dss --tail=50          # derniers logs de tous les pods
kubectl logs -l app=dss-api -n dss --tail=50 -f        # puis suivre en continu (Ctrl+C pour arreter)
kubectl get pods -n dss                                 # noms des pods, pour cibler un seul pod :
kubectl logs <nom_du_pod> -n dss --tail=100
#     c) Logs de Grafana / du collecteur Managed Prometheus (namespace monitoring) :
kubectl logs deploy/grafana -n monitoring --tail=50
kubectl logs deploy/gmp-frontend -n monitoring --tail=50
#        Le collecteur Managed Prometheus tourne dans le namespace kube-system (un pod par noeud) :
kubectl get pods -n kube-system -l app=managed-prometheus-collector
kubectl logs -n kube-system -l app=managed-prometheus-collector --tail=50
#     d) Logs persistants dans Cloud Logging (au-dela de ce que kubectl logs garde localement) :
gcloud logging read 'resource.type="k8s_container" AND resource.labels.namespace_name="dss"' `
  --limit=50 --format="table(timestamp, textPayload)" --freshness=1h
