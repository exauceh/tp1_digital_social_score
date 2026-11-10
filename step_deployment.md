# Déploiement sur Kubernetes (GKE Autopilot)

# 0. Variables à adapter
$PROJECT_ID = "mon-projet"
$REGION     = "europe-west1"   # région UE : à confirmer pour GKE et Artifact Registry
$REPO       = "dss-api"
$CLUSTER    = "dss-cluster"
$IMAGE      = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/dss-lstm:v1"

# 1. Connexion, projet, API, plugin d'authentification de kubectl pour GKE
gcloud auth login
gcloud config set project $PROJECT_ID
gcloud services enable container.googleapis.com artifactregistry.googleapis.com
gcloud components install gke-gcloud-auth-plugin   # absent de votre machine : nécessaire à kubectl

# 2. Dépôt d'images dans la même région que le cluster
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION --description="Digital Social Score"
gcloud auth configure-docker "$REGION-docker.pkg.dev"

# 3. Construire et tester l'image en local
Set-Content score.json '{"text": "You are a stupid idiot"}'
docker build -t $IMAGE .
docker run --rm -p 8080:8080 $IMAGE

#    dans un autre terminal (ou ouvrez http://localhost:8080/docs dans le navigateur) :
curl.exe -s http://localhost:8080/health
curl.exe -s -X POST http://localhost:8080/score -H "Content-Type: application/json" -d "@score.json"

# 4. Pousser l'image
docker push $IMAGE

# 5. Créer le cluster Autopilot (Google gère les machines) et connecter kubectl
gcloud container clusters create-auto $CLUSTER --region=$REGION --release-channel=regular
gcloud container clusters get-credentials $CLUSTER --region=$REGION
kubectl cluster-info

# 6. Déployer : kustomize assemble les manifests du dossier k8s/, puis le texte IMAGE_PLACEHOLDER
#    est remplacé par votre image avant l'envoi à Kubernetes
kubectl kustomize k8s | ForEach-Object { $_ -replace 'IMAGE_PLACEHOLDER', $IMAGE } | kubectl apply -f -
kubectl rollout status deployment/dss-api -n dss --timeout=10m
kubectl get pods,svc,hpa,pdb -n dss
#    Si un pod reste en ImagePullBackOff : le compte de service des noeuds doit pouvoir lire
#    Artifact Registry (rôle roles/artifactregistry.reader) ; voir `kubectl describe pod -n dss`

# 7. Tester sans exposer l'API sur Internet : tunnel local vers le Service
#    (laissez cette commande tourner et utilisez un autre terminal)
kubectl port-forward svc/dss-api 8081:80 -n dss
curl.exe -s http://localhost:8081/health
curl.exe -s -X POST http://localhost:8081/score -H "Content-Type: application/json" -d "@score.json"

# 8. Observer
kubectl logs -l app=dss-api -n dss --tail=20      # vérifier qu'aucun texte de commentaire n'apparaît
kubectl top pods -n dss                           # mémoire et CPU réels : ajuster k8s/deployment.yaml
kubectl get hpa -n dss                            # autoscaling (2 à 6 pods, cible 60 % de CPU)
kubectl describe pod -l app=dss-api -n dss        # événements, sondes, redémarrages

# 9. Mettre à jour (v2) et revenir en arrière si besoin
$IMAGE_V2 = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/dss-lstm:v2"
docker build -t $IMAGE_V2 .
docker push $IMAGE_V2
kubectl kustomize k8s | ForEach-Object { $_ -replace 'IMAGE_PLACEHOLDER', $IMAGE_V2 } | kubectl apply -f -
kubectl rollout status deployment/dss-api -n dss
kubectl rollout undo deployment/dss-api -n dss    # retour à la version précédente

# 10. Éteindre après les tests (un cluster et ses pods coûtent tant qu'ils existent)
kubectl delete namespace dss
gcloud container clusters delete $CLUSTER --region=$REGION
gcloud artifacts repositories delete $REPO --location=$REGION
