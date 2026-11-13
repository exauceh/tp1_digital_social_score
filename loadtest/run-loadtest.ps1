<#
.SYNOPSIS
    Run one k6 load-test scenario against the scoring API, inside the cluster, and keep the results.

.DESCRIPTION
    Course n°3 / TP step 5. The load generator runs in its own namespace ("loadtest") so that it
    measures the Service like a real client inside the cluster does; `kubectl port-forward` is a
    single tunnel and is not suitable for a load test.

    The API key is read from the DSS_API_KEY environment variable (never pass it on the command
    line) and is stored in a Kubernetes Secret of the "loadtest" namespace only. Use -Cleanup to
    remove that namespace, and with it the key, when the tests are over.

    Results are written to loadtest/results/:
      <scenario>-k6.txt        end-of-test summary of k6 (requests/s, latency avg/p95/p99, errors)
      <scenario>-timeline.txt  every 15 s: HPA replicas and CPU, pods, nodes (to see the autoscaling)
      <scenario>-events.txt    time of the disturbance for the failover scenario

.EXAMPLE
    $env:DSS_API_KEY = "<your key>"
    .\loadtest\run-loadtest.ps1 -Scenario ramp
    .\loadtest\run-loadtest.ps1 -Scenario failover -Disrupt pod
    .\loadtest\run-loadtest.ps1 -Cleanup
#>
param(
    [ValidateSet("ramp", "sustained", "spike", "failover", "badkeys", "malformed", "flood")]
    [string]$Scenario = "ramp",

    # failover only: what is removed 90 seconds after the start
    [ValidateSet("none", "pod", "node")]
    [string]$Disrupt = "none",

    [switch]$Cleanup,

    [string]$TargetNamespace = "dss",
    [int]$SnapshotSeconds = 15
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$results = Join-Path $root "results"
$loadNamespace = "loadtest"

function Write-Step($message) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $message) }

# $ErrorActionPreference = "Stop" does not catch a failing external command such as kubectl:
# its exit code has to be checked, otherwise the script goes on as if nothing had happened.
function Assert-Success($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE): stopping." }
}

if ($Cleanup) {
    Write-Step "Deleting the namespace '$loadNamespace' (job, script and API key secret)"
    kubectl delete namespace $loadNamespace --ignore-not-found
    return
}

if (-not $env:DSS_API_KEY) { throw "Set the DSS_API_KEY environment variable first (the API key of a client)." }
New-Item -ItemType Directory -Force -Path $results | Out-Null

$context = kubectl config current-context
Write-Step "Cluster context: $context"
if ($context -notmatch "dss") { throw "Unexpected kubectl context '$context': refusing to run a load test there." }

Write-Step "Namespace, API key secret and k6 script"
kubectl create namespace $loadNamespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
Assert-Success "Creating the namespace '$loadNamespace'"
kubectl create secret generic loadtest-key -n $loadNamespace "--from-literal=API_KEY=$env:DSS_API_KEY" --dry-run=client -o yaml | kubectl apply -f - | Out-Null
Assert-Success "Creating the API key secret"
# The whole --from-file=<path> argument must be one string: with a parenthesised expression right
# after the "=", PowerShell splits it into two arguments and kubectl refuses ("got 2").
$scriptPath = Join-Path $root "k6-score.js"
kubectl create configmap k6-script -n $loadNamespace "--from-file=$scriptPath" --dry-run=client -o yaml | kubectl apply -f - | Out-Null
Assert-Success "Creating the k6 script ConfigMap"

$jobName = "k6-$Scenario"
kubectl delete job $jobName -n $loadNamespace --ignore-not-found | Out-Null
Assert-Success "Deleting the previous job"
(Get-Content (Join-Path $root "job.yaml") -Raw).Replace("SCENARIO_PLACEHOLDER", $Scenario) | kubectl apply -f - | Out-Null
Assert-Success "Creating the k6 job"

Write-Step "Waiting for the k6 pod to start"
$deadline = (Get-Date).AddSeconds(300)
do {
    $phase = kubectl get pods -n $loadNamespace -l job-name=$jobName -o jsonpath="{.items[0].status.phase}" 2>$null
    if ($phase -in "Running", "Succeeded") { break }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if ($phase -notin "Running", "Succeeded") {
    kubectl describe pod -n $loadNamespace -l job-name=$jobName | Select-Object -Last 15
    throw "The k6 pod did not start within 5 minutes (phase: '$phase'). Clean up with -Cleanup."
}

$timeline = Join-Path $results "$Scenario-timeline.txt"
$events = Join-Path $results "$Scenario-events.txt"
"time  hpa(current/desired)  cpu%  pods(ready/total)  nodes" | Set-Content $timeline
if (Test-Path $events) { Remove-Item $events }

function Get-Snapshot {
    $hpa = kubectl get hpa dss-api -n $TargetNamespace -o jsonpath="{.status.currentReplicas}/{.status.desiredReplicas} {.status.currentMetrics[0].resource.current.averageUtilization}" 2>$null
    $pods = @(kubectl get pods -n $TargetNamespace -l app=dss-api --no-headers 2>$null)
    $ready = @($pods | Where-Object { $_ -match "\s1/1\s+Running" }).Count
    $nodes = @(kubectl get nodes --no-headers 2>$null).Count
    return ("{0}  {1}  {2}/{3}  {4}" -f (Get-Date -Format "HH:mm:ss"), $hpa, $ready, $pods.Count, $nodes)
}

Write-Step "Scenario '$Scenario' started (job $jobName)"
$started = Get-Date
# Nominal duration of each scenario (see k6-score.js) plus a margin: past that, something is stuck.
$nominalSeconds = @{ ramp = 240; sustained = 180; spike = 230; failover = 240; badkeys = 90; malformed = 60; flood = 90 }
$maxSeconds = $nominalSeconds[$Scenario] + 300
$disrupted = $false
try {
while ($true) {
    Get-Snapshot | Add-Content $timeline
    $status = kubectl get job $jobName -n $loadNamespace -o jsonpath="{.status.succeeded}/{.status.failed}" 2>$null
    if ($status -match "^1/" -or $status -match "/[1-9]") { break }
    $elapsed = ((Get-Date) - $started).TotalSeconds
    if ($elapsed -gt $maxSeconds) {
        kubectl describe pod -n $loadNamespace -l job-name=$jobName | Select-Object -Last 15
        throw "Scenario '$Scenario' did not finish within $maxSeconds seconds: stopping. Clean up with -Cleanup."
    }
    if ($Scenario -eq "failover" -and $Disrupt -ne "none" -and -not $disrupted -and $elapsed -ge 90) {
        $disrupted = $true
        # Never remove the node that runs the load generator: a drain would evict k6 itself and
        # the test would end with the failure it is supposed to measure (this happened once).
        $k6Node = kubectl get pods -n $loadNamespace -l job-name=$jobName -o jsonpath="{.items[0].spec.nodeName}"
        $apiPods = @(kubectl get pods -n $TargetNamespace -l app=dss-api -o wide --no-headers | ForEach-Object {
                $columns = $_ -split "\s+"
                [pscustomobject]@{ Name = $columns[0]; Node = $columns[6] }
            })
        $target = $apiPods | Where-Object { $_.Node -ne $k6Node } | Select-Object -First 1
        if (-not $target) {
            "{0}  no API pod runs outside the node of k6 ({1}): nothing was disrupted, run the test again" -f (Get-Date -Format "HH:mm:ss"), $k6Node | Add-Content $events
            Write-Step "Every API pod shares the node of k6: no disruption, run the test again"
            Start-Sleep -Seconds $SnapshotSeconds
            continue
        }
        $podName = $target.Name
        if ($Disrupt -eq "pod") {
            "{0}  deleting pod {1}" -f (Get-Date -Format "HH:mm:ss"), $podName | Add-Content $events
            Write-Step "Deleting pod $podName"
            kubectl delete pod $podName -n $TargetNamespace --wait=false | Out-Null
        }
        else {
            $nodeName = (kubectl get pod $podName -n $TargetNamespace -o jsonpath="{.spec.nodeName}")
            "{0}  draining node {1} (hosts pod {2})" -f (Get-Date -Format "HH:mm:ss"), $nodeName, $podName | Add-Content $events
            Write-Step "Draining node $nodeName"
            # Set before the drain: whatever happens next, the node is put back in service at the end.
            $script:drainedNode = $nodeName
            # kubectl writes its warnings ("ignoring DaemonSet-managed Pods") on stderr: with
            # $ErrorActionPreference = "Stop", PowerShell would take each of them for a fatal error and
            # abort the script in the middle of the drain. The messages are only kept as text.
            $previousPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            kubectl drain $nodeName --ignore-daemonsets --delete-emptydir-data --force --timeout=120s 2>&1 | ForEach-Object { "$_" } | Add-Content $events
            $ErrorActionPreference = $previousPreference
            "{0}  drain finished, node left cordoned until the end of the test" -f (Get-Date -Format "HH:mm:ss") | Add-Content $events
        }
    }
    Start-Sleep -Seconds $SnapshotSeconds
}
}
finally {
    # Also runs when the script is interrupted or fails: never leave a node cordoned.
    if ($script:drainedNode) {
        kubectl uncordon $script:drainedNode | Out-Null
        "{0}  node {1} uncordoned" -f (Get-Date -Format "HH:mm:ss"), $script:drainedNode | Add-Content $events
    }
}

Write-Step "Scenario finished, collecting the k6 summary"
kubectl logs "job/$jobName" -n $loadNamespace | Set-Content (Join-Path $results "$Scenario-k6.txt")
Get-Snapshot | Add-Content $timeline
Get-Content (Join-Path $results "$Scenario-k6.txt") | Select-Object -Last 40
Write-Step "Results in $results"
