# Déploiement sur Kubernetes (GKE Autopilot) : clés API dans Secret Manager, accès par IAM

# 0. Variables à adapter
$PROJECT_ID = "tp1-digital-score"
$REGION     = "europe-west1"
$REPO       = "dss-api"
$CLUSTER     = "dss-cluster-2" # NOUVEAU cluster : noeuds avec un compte de service dédié et restreint
$OLD_CLUSTER = "dss-cluster"   # premier cluster : noeuds avec le compte Compute par défaut (rôle editor)
$SECRET     = "dss-api-keys"   # nom du secret dans Secret Manager
$VERSION    = "v2"             # v1 = sans authentification ; v2 = clé API lue dans Secret Manager
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

# 6. Nouveau cluster, avec un compte de service dédié pour les noeuds. Le premier cluster utilise le compte
#    Compute par défaut, qui a le rôle "editor" sur tout le projet ; ce compte ne peut pas être changé sur un
#    cluster existant, d'où la création d'un nouveau cluster. Le compte dédié n'a que les droits de journaliser,
#    d'envoyer des métriques et de lire les images du dépôt.
$NODE_SA = "dss-nodes@$PROJECT_ID.iam.gserviceaccount.com"
#    Les trois commandes suivantes sont DÉJÀ FAITES sur votre projet (compte et droits vérifiés) : inutile de les relancer.
gcloud iam service-accounts create dss-nodes --display-name="Noeuds GKE du projet Digital Social Score"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$NODE_SA" --role=roles/container.defaultNodeServiceAccount
gcloud artifacts repositories add-iam-policy-binding $REPO --location=$REGION --member="serviceAccount:$NODE_SA" --role=roles/artifactregistry.reader
#    À faire : créer le cluster avec ce compte (Autopilot, région UE)
gcloud container clusters create-auto $CLUSTER --region=$REGION --release-channel=regular --service-account=$NODE_SA
gcloud container clusters get-credentials $CLUSTER --region=$REGION      # kubectl pointe maintenant vers le NOUVEAU cluster
kubectl config current-context                                            # doit se terminer par _dss-cluster-2
kubectl cluster-info

# 7. Secret Manager : stocker les clés API (les hashs, pas les clés)
#    --replication-policy=user-managed + --locations : les données restent dans la région choisie (UE)
gcloud secrets create $SECRET --replication-policy=user-managed --locations=$REGION --labels=app=dss
$API_KEYS | gcloud secrets versions add $SECRET --data-file=-

# 8. IAM : l'application ne peut lire QUE ce secret
#    Workload Identity (toujours actif sur Autopilot) : le compte de service Kubernetes dss-api du namespace
#    dss est identifié par Google Cloud sans fichier de clé. On lui donne le rôle de lecture sur ce seul
#    secret, pas sur le projet. Cette identité est la même sur tous les clusters du projet : la liaison ne
#    dépend pas du cluster, et le nouveau cluster en profite sans autre réglage.
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

# 10b. Quand le nouveau cluster fonctionne (étape 10 réussie), supprimer l'ancien et nettoyer kubectl.
#      Il ne sert plus à rien : l'API n'était joignable que depuis le cluster, et il tourne en v1, sans authentification.
gcloud container clusters delete $OLD_CLUSTER --region=$REGION
kubectl config delete-context "gke_${PROJECT_ID}_${REGION}_${OLD_CLUSTER}"

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

# 12. Mettre à jour le code (v3) et revenir en arrière si besoin
$VERSION = "v3"
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
