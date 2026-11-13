<#
.SYNOPSIS
    Build loadtest/results/summary.md: the table of the course (n°3, "tests de charge") for every
    scenario that has been run.

.DESCRIPTION
    run-loadtest.ps1 only keeps the raw files of each run. This script reads them and fills the
    columns that k6 cannot give by asking Managed Service for Prometheus (read-only, with your own
    gcloud identity) about the time window of each run:

      from <scenario>-k6.txt        throughput, latency avg/P95/P99, error rate, dropped iterations
      from <scenario>-timeline.txt  HPA CPU, ready pods, nodes (the autoscaling)
      from Prometheus               memory and CPU of the pods, server-side P95, 5xx rate, restarts

    It never creates or changes anything in the cluster.

.EXAMPLE
    .\loadtest\summarize-results.ps1
#>
param(
    [string]$ResultsDir = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "results"),
    [string]$ProjectId = "tp1-digital-score",
    [switch]$NoPrometheus
)

$ErrorActionPreference = "Stop"
# badkeys and malformed are not in this table: their result is the list of checks of the k6 summary.
$scenarios = "ramp", "sustained", "spike", "failover", "flood"

function ConvertTo-Milliseconds([string]$text) {
    if ($text -match '^([\d.]+)ms$') { return [double]$Matches[1] }
    if ($text -match '^([\d.]+)s$') { return [double]$Matches[1] * 1000 }
    # microseconds: the "µ" is garbled by the console encoding, whatever it became
    if ($text -match '^([\d.]+).{1,3}s$') { return [double]$Matches[1] / 1000 }
    return [double]::NaN
}

function Format-Number($value, [int]$digits = 0) {
    if ($null -eq $value -or [double]::IsNaN([double]$value)) { return "n/a" }
    return ([double]$value).ToString("F$digits", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Read-K6Summary([string]$path) {
    $lines = Get-Content $path
    $summary = @{}
    foreach ($line in $lines) {
        if ($line -match '^\s*http_reqs\.+:\s+(\d+)\s+([\d.]+)/s') { $summary.Requests = [int]$Matches[1]; $summary.Rate = [double]$Matches[2] }
        elseif ($line -match '^\s*http_req_failed\.+:\s+([\d.]+)%') { $summary.FailedPercent = [double]$Matches[1] }
        elseif ($line -match '^\s*dropped_iterations\.+:\s+(\d+)') { $summary.Dropped = [int]$Matches[1] }
        elseif ($line -match '^\s*http_req_duration\.+:\s+(.*)$' -and -not $summary.ContainsKey("Avg")) {
            $trend = $Matches[1]
            foreach ($key in "avg", "med", "max", "p\(90\)", "p\(95\)", "p\(99\)") {
                if ($trend -match "(?<![\w(])$key=(\S+)") { $summary[$key -replace '\\', ''] = ConvertTo-Milliseconds $Matches[1] }
            }
            $summary.Avg = $summary["avg"]
        }
        elseif ($line -match '^\s*vus_max\.+:\s+\d+\s+min=\d+\s+max=(\d+)') { $summary.VusMax = [int]$Matches[1] }
    }
    if (-not $summary.ContainsKey("Dropped")) { $summary.Dropped = 0 }
    return $summary
}

# Ramp only: latency per load level, from the "{ slice:rate_NNN }" lines of the k6 summary. The
# first characters of those lines are garbled by the console encoding, so only the tag is matched.
function Read-Slices([string]$path) {
    $slices = foreach ($line in (Get-Content $path)) {
        if ($line -match '\{ slice:rate_(\d+) \}\.*:\s+(.*)$') {
            $slice = [ordered]@{ Rate = [int]$Matches[1] }
            $trend = $Matches[2]
            foreach ($key in "avg", "med", "max", "p\(90\)", "p\(95\)", "p\(99\)") {
                if ($trend -match "(?<![\w(])$key=(\S+)") { $slice[$key -replace '\\', ''] = ConvertTo-Milliseconds $Matches[1] }
            }
            [pscustomobject]$slice
        }
    }
    return @($slices)
}

function Read-Timeline([string]$path) {
    $rows = foreach ($line in (Get-Content $path | Select-Object -Skip 1)) {
        if ($line -match '^(\S+)\s+(\d+)/(\d+)\s+(?:(\d+)\s+)?(\d+)/(\d+)\s+(\d+)\s*$') {
            [pscustomobject]@{
                Time = $Matches[1]; Hpa = [int]$Matches[2]
                Cpu = $(if ($Matches[4]) { [int]$Matches[4] } else { $null })
                Ready = [int]$Matches[5]; Pods = [int]$Matches[6]; Nodes = [int]$Matches[7]
            }
        }
    }
    return @($rows)
}

# --- Prometheus (read-only) -------------------------------------------------------------------
$token = $null
if (-not $NoPrometheus) { $token = (gcloud auth print-access-token 2>$null) }
$gmp = "https://monitoring.googleapis.com/v1/projects/$ProjectId/location/global/prometheus/api/v1"

function Invoke-Gmp([string]$endpoint, [hashtable]$body) {
    if (-not $token) { return $null }
    try {
        $r = Invoke-RestMethod -Uri "$gmp/$endpoint" -Method Post -Headers @{ Authorization = "Bearer $token" } -Body $body -TimeoutSec 60
        return $r.data.result
    }
    catch { Write-Warning ("Prometheus: " + $_.Exception.Message); return $null }
}

function Get-RangeValues([string]$query, [long]$start, [long]$end) {
    $result = Invoke-Gmp "query_range" @{ query = $query; start = $start; end = $end; step = 15 }
    if (-not $result) { return @() }
    return @($result[0].values | ForEach-Object { [double]$_[1] } | Where-Object { -not [double]::IsNaN($_) })
}

function Get-InstantValue([string]$query, [long]$at) {
    $result = Invoke-Gmp "query" @{ query = $query; time = $at }
    if (-not $result -or $result.Count -eq 0) { return [double]::NaN }
    return [double]$result[0].value[1]
}

function ConvertTo-UnixSeconds([datetime]$utc) { return [DateTimeOffset]::new([datetime]::SpecifyKind($utc, [DateTimeKind]::Utc)).ToUnixTimeSeconds() }

# --- one row per scenario -----------------------------------------------------------------------
$rows = @()
foreach ($scenario in $scenarios) {
    $k6File = Join-Path $ResultsDir "$scenario-k6.txt"
    $timelineFile = Join-Path $ResultsDir "$scenario-timeline.txt"
    if (-not (Test-Path $k6File) -or -not (Test-Path $timelineFile)) { continue }
    if ((Get-Content $k6File -Raw) -notmatch "http_reqs") { Write-Warning "${scenario}: the k6 summary is not complete yet, skipped"; continue }

    $k6 = Read-K6Summary $k6File
    $timeline = Read-Timeline $timelineFile
    $row = [ordered]@{ Scenario = $scenario; K6 = $k6; Timeline = $timeline }

    $day = (Get-Item $timelineFile).LastWriteTime.Date
    $start = [datetime]::ParseExact(("{0:yyyy-MM-dd} {1}" -f $day, $timeline[0].Time), "yyyy-MM-dd HH:mm:ss", $null).ToUniversalTime()
    $end = [datetime]::ParseExact(("{0:yyyy-MM-dd} {1}" -f $day, $timeline[-1].Time), "yyyy-MM-dd HH:mm:ss", $null).ToUniversalTime().AddSeconds(15)
    $row.Window = "{0:HH:mm:ss}-{1:HH:mm:ss} (heure locale)" -f $start.ToLocalTime(), $end.ToLocalTime()
    $s = ConvertTo-UnixSeconds $start; $e = ConvertTo-UnixSeconds $end; $w = [int]($e - $s)

    $row.Prom = @{}
    if ($token) {
        $mem = Get-RangeValues 'avg(container_memory_working_set_bytes{namespace="dss", container="api"}) / 1048576' $s $e
        $cpu = Get-RangeValues 'sum(rate(container_cpu_usage_seconds_total{namespace="dss", container="api"}[1m]))' $s $e
        $thr = Get-RangeValues 'sum(rate(api_requests_total{route="/score", status="200"}[1m]))' $s $e
        $row.Prom.MemAvg = $(if ($mem.Count) { ($mem | Measure-Object -Average).Average } else { [double]::NaN })
        $row.Prom.MemMax = $(if ($mem.Count) { ($mem | Measure-Object -Maximum).Maximum } else { [double]::NaN })
        $row.Prom.CpuAvg = $(if ($cpu.Count) { ($cpu | Measure-Object -Average).Average } else { [double]::NaN })
        $row.Prom.CpuMax = $(if ($cpu.Count) { ($cpu | Measure-Object -Maximum).Maximum } else { [double]::NaN })
        $row.Prom.ThroughputMax = $(if ($thr.Count) { ($thr | Measure-Object -Maximum).Maximum } else { [double]::NaN })
        $row.Prom.P95 = 1000 * (Get-InstantValue "histogram_quantile(0.95, sum by (le) (increase(api_latency_seconds_bucket{route=`"/score`"}[${w}s])))" $e)
        $row.Prom.Err5xx = 100 * (Get-InstantValue "sum(increase(api_requests_total{status=~`"5..`"}[${w}s])) / sum(increase(api_requests_total[${w}s]))" $e)
        $row.Prom.Restarts = Get-InstantValue "sum(increase(kube_pod_container_status_restarts_total{namespace=`"dss`", container=`"api`"}[${w}s]))" $e
    }
    $rows += [pscustomobject]$row
}

if ($rows.Count -eq 0) { throw "No finished scenario found in $ResultsDir (need <scenario>-k6.txt and <scenario>-timeline.txt)." }

# --- markdown -----------------------------------------------------------------------------------
$out = New-Object System.Collections.Generic.List[string]
$out.Add("# Resultats des tests de charge")
$out.Add("")
$out.Add("Genere par ``loadtest/summarize-results.ps1`` le $(Get-Date -Format 'yyyy-MM-dd HH:mm') a partir des fichiers de ce dossier et de Prometheus.")
$out.Add("")
$out.Add("## Tableau (format du cours numero 3)")
$out.Add("")
$out.Add("| Scenario | Debit moyen k6 (req/s) | Debit max servi (req/s) | Latence moyenne (ms) | P95 (ms) | P99 (ms) | Taux d'erreur (%) | Iterations perdues | CPU moyen (% de la demande) | Memoire moyenne par pod (Mo) | Observations |")
$out.Add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
foreach ($r in $rows) {
    $cpuPct = ($r.Timeline | Where-Object { $null -ne $_.Cpu } | Measure-Object -Property Cpu -Average).Average
    $out.Add(("| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8} | {9} | |" -f $r.Scenario,
            (Format-Number $r.K6.Rate 1), (Format-Number $r.Prom.ThroughputMax 1), (Format-Number $r.K6.Avg 0),
            (Format-Number $r.K6["p(95)"] 0), (Format-Number $r.K6["p(99)"] 0), (Format-Number $r.K6.FailedPercent 2),
            $r.K6.Dropped, (Format-Number $cpuPct 0), (Format-Number $r.Prom.MemAvg 0)))
}
$out.Add("")
$out.Add("## Detail par scenario")
foreach ($r in $rows) {
    $maxPods = ($r.Timeline | Measure-Object -Property Pods -Maximum).Maximum
    $minReady = ($r.Timeline | Measure-Object -Property Ready -Minimum).Minimum
    $maxNodes = ($r.Timeline | Measure-Object -Property Nodes -Maximum).Maximum
    $firstNodes = $r.Timeline[0].Nodes
    $maxCpu = ($r.Timeline | Where-Object { $null -ne $_.Cpu } | Measure-Object -Property Cpu -Maximum).Maximum
    $out.Add("")
    $out.Add("### $($r.Scenario)")
    $out.Add("")
    $out.Add("- Fenetre de mesure : $($r.Window)")
    $out.Add("- Requetes : $($r.K6.Requests) ; en echec (k6) : $(Format-Number $r.K6.FailedPercent 2) % ; iterations perdues : $($r.K6.Dropped) ; VUs max : $($r.K6.VusMax)")
    $out.Add("- Latence cote client (k6) : moyenne $(Format-Number $r.K6.Avg 0) ms, mediane $(Format-Number $r.K6['med'] 0) ms, P90 $(Format-Number $r.K6['p(90)'] 0) ms, P95 $(Format-Number $r.K6['p(95)'] 0) ms, P99 $(Format-Number $r.K6['p(99)'] 0) ms, max $(Format-Number $r.K6['max'] 0) ms")
    $out.Add("- Autoscaling : pods de $($r.Timeline[0].Pods) a $maxPods, **minimum de pods prets : $minReady**, noeuds de $firstNodes a $maxNodes, CPU du HPA maximal : $(Format-Number $maxCpu 0) % de la demande")
    if ($token) {
        $out.Add("- Cote serveur (Prometheus) : debit max servi $(Format-Number $r.Prom.ThroughputMax 1) req/s, P95 $(Format-Number $r.Prom.P95 0) ms, reponses 5xx $(Format-Number $r.Prom.Err5xx 2) %, redemarrages de conteneurs $(Format-Number $r.Prom.Restarts 0)")
        $out.Add("- Ressources : CPU total moyen $(Format-Number $r.Prom.CpuAvg 2) coeur(s), max $(Format-Number $r.Prom.CpuMax 2) ; memoire moyenne par pod $(Format-Number $r.Prom.MemAvg 0) Mo, max $(Format-Number $r.Prom.MemMax 0) Mo")
    }
    $eventsFile = Join-Path $ResultsDir "$($r.Scenario)-events.txt"
    if (Test-Path $eventsFile) { $out.Add("- Panne provoquee : " + ((Get-Content $eventsFile | Select-Object -First 2) -join " ; ")) }
}
foreach ($r in $rows) {
    $slices = Read-Slices (Join-Path $ResultsDir "$($r.Scenario)-k6.txt")
    if ($slices.Count -eq 0) { continue }
    $out.Add("")
    $out.Add("## Latence par niveau de charge ($($r.Scenario))")
    $out.Add("")
    $out.Add("Chaque ligne regroupe les requetes envoyees pendant 15 s ; le debit est celui **demande** a ce moment (au-dela de la saturation, l'API en sert moins). Latence mesuree par k6.")
    $out.Add("")
    $out.Add("| Debit demande (req/s) | Moyenne (ms) | Mediane (ms) | P90 (ms) | P95 (ms) | P99 (ms) | Max (ms) |")
    $out.Add("|---:|---:|---:|---:|---:|---:|---:|")
    foreach ($s in $slices) {
        $out.Add(("| {0} | {1} | {2} | {3} | {4} | {5} | {6} |" -f $s.Rate, (Format-Number $s.avg 0), (Format-Number $s.med 0),
                (Format-Number $s.'p(90)' 0), (Format-Number $s.'p(95)' 0), (Format-Number $s.'p(99)' 0), (Format-Number $s.max 0)))
    }
}
$out.Add("")
$out.Add("## Limites de la mesure")
$out.Add("")
$out.Add("- Le generateur de charge (k6) tourne sur les memes noeuds que l'API et consomme un peu de leur CPU.")
$out.Add("- Les durees sont plus courtes que celles du cours (3 a 4 minutes au lieu de 30).")
$out.Add("- Textes synthetiques. Le CPU du tableau est la moyenne du HPA (pourcentage du CPU demande, 1 CPU par pod).")
$out.Add("- Le 'debit moyen' est celui de toute la fenetre ; le 'debit max servi' est le meilleur debit de 200 observe par Prometheus sur 1 minute.")
$out.Add("- Latence k6 : mesuree par le client, file d'attente reseau et attente avant l'API comprises ; P95 serveur : mesuree par l'API.")

$summaryPath = Join-Path $ResultsDir "summary.md"
$out | Set-Content -Path $summaryPath -Encoding UTF8
Write-Host "Summary written to $summaryPath"
