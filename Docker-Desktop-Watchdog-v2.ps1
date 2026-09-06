#requires -version 5.1
<#
.SYNOPSIS
    Docker Desktop Watchdog v2 fuer Windows 10

.DESCRIPTION
    Prueft:
      - Docker Desktop Windows Service (com.docker.service)
      - WSL-Service dynamisch: WslService oder LxssManager
      - Hyper-V Compute Service (vmcompute), falls vorhanden
      - Host Network Service (hns), falls vorhanden
      - docker-desktop WSL Distribution
      - Docker Desktop Prozesse
      - Docker Engine mehrfach per "docker info"

    Eine Reparatur wird erst ausgeloest, wenn die Docker Engine mehrfach
    hintereinander nicht erreichbar ist oder ein zwingender Docker-Dienst fehlt.

.NOTES
    Als Administrator ausfuehren.
#>

[CmdletBinding()]
param(
    [int]$DockerStartupTimeout = 90,
    [int]$CheckIntervalSeconds = 3,
    [int]$EngineCheckRetries = 3,
    [int]$EngineRetryDelaySeconds = 5,
    [switch]$ForceRepair
)

$ErrorActionPreference = "Stop"

$DockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$LogDir = Join-Path $env:ProgramData "DockerDesktopWatchdog"
$LogFile = Join-Path $LogDir "watchdog.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param(
        [string]$Message,
        [ValidateSet("INFO","WARN","ERROR","OK")]
        [string]$Level = "INFO"
    )

    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Test-Administrator {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ServiceIfExists {
    param([string]$Name)
    return Get-Service -Name $Name -ErrorAction SilentlyContinue
}

function Ensure-ServiceRunning {
    param(
        [string]$Name,
        [switch]$Optional
    )

    $svc = Get-ServiceIfExists -Name $Name

    if (-not $svc) {
        if ($Optional) {
            Write-Log "Dienst '$Name' ist nicht vorhanden - wird uebersprungen." "INFO"
            return $true
        }

        Write-Log "Erforderlicher Dienst '$Name' wurde nicht gefunden." "ERROR"
        return $false
    }

    if ($svc.Status -eq "Running") {
        Write-Log "Dienst '$Name' laeuft." "OK"
        return $true
    }

    Write-Log "Dienst '$Name' ist $($svc.Status). Starte ihn..." "WARN"

    try {
        Start-Service -Name $Name -ErrorAction Stop
        $svc.WaitForStatus("Running", [TimeSpan]::FromSeconds(20))
        Write-Log "Dienst '$Name' laeuft." "OK"
        return $true
    }
    catch {
        if ($Optional) {
            Write-Log "Optionaler Dienst '$Name' konnte nicht gestartet werden: $($_.Exception.Message)" "WARN"
            return $true
        }

        Write-Log "Dienst '$Name' konnte nicht gestartet werden: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

function Get-WslServiceName {
    if (Get-Service -Name "WslService" -ErrorAction SilentlyContinue) {
        return "WslService"
    }

    if (Get-Service -Name "LxssManager" -ErrorAction SilentlyContinue) {
        return "LxssManager"
    }

    return $null
}

function Test-DockerEngine {
    try {
        $null = & docker info 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Test-DockerEngineWithRetries {
    param(
        [int]$Retries,
        [int]$DelaySeconds
    )

    for ($i = 1; $i -le $Retries; $i++) {
        if (Test-DockerEngine) {
            Write-Log "docker info erfolgreich (Versuch $i/$Retries)." "OK"
            return $true
        }

        if ($i -lt $Retries) {
            Write-Log "docker info fehlgeschlagen (Versuch $i/$Retries). Neuer Versuch in $DelaySeconds Sekunden..." "WARN"
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    Write-Log "docker info ist nach $Retries Versuchen weiterhin fehlgeschlagen." "ERROR"
    return $false
}

function Get-DockerDesktopWslState {
    try {
        $output = & wsl.exe -l -v 2>$null

        if ($LASTEXITCODE -ne 0) {
            return "ERROR"
        }

        $line = $output | Where-Object {
            ($_ -replace "`0","") -match "docker-desktop"
        } | Select-Object -First 1

        if (-not $line) {
            return "MISSING"
        }

        $clean = ($line -replace "`0","").Trim()

        if ($clean -match "\bRunning\b") {
            return "RUNNING"
        }

        if ($clean -match "\bStopped\b") {
            return "STOPPED"
        }

        return "UNKNOWN"
    }
    catch {
        return "ERROR"
    }
}

function Stop-DockerDesktopProcesses {
    Write-Log "Beende Docker-Desktop-Prozesse..." "WARN"

    $processNames = @(
        "Docker Desktop",
        "com.docker.backend",
        "com.docker.proxy"
    )

    foreach ($name in $processNames) {
        Get-Process -Name $name -ErrorAction SilentlyContinue |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 2
}

function Repair-DockerDesktop {
    Write-Log "Docker-Desktop-Reparatur wird gestartet." "WARN"

    Stop-DockerDesktopProcesses

    Write-Log "Fahre WSL vollstaendig herunter..." "INFO"
    try {
        & wsl.exe --shutdown 2>$null
    }
    catch {
        Write-Log "WSL shutdown meldete einen Fehler: $($_.Exception.Message)" "WARN"
    }

    Start-Sleep -Seconds 3

    $services = @(
        @{ Name = "vmcompute";          Optional = $true  },
        @{ Name = "hns";                Optional = $true  },
        @{ Name = "com.docker.service"; Optional = $false }
    )

    foreach ($entry in $services) {
        $ok = Ensure-ServiceRunning -Name $entry.Name -Optional:$entry.Optional
        if (-not $ok -and -not $entry.Optional) {
            return $false
        }
    }

    $wslServiceName = Get-WslServiceName

    if ($wslServiceName) {
        Write-Log "Erkannter WSL-Dienst: $wslServiceName" "INFO"
        $null = Ensure-ServiceRunning -Name $wslServiceName -Optional
    }
    else {
        Write-Log "Kein separater WSL-Dienst gefunden. Verwaltung erfolgt ueber wsl.exe." "INFO"
    }

    if (-not (Test-Path $DockerDesktopExe)) {
        Write-Log "Docker Desktop wurde nicht gefunden: $DockerDesktopExe" "ERROR"
        return $false
    }

    Write-Log "Starte Docker Desktop..." "INFO"
    Start-Process -FilePath $DockerDesktopExe

    Write-Log "Warte maximal $DockerStartupTimeout Sekunden auf die Docker Engine..." "INFO"

    $deadline = (Get-Date).AddSeconds($DockerStartupTimeout)

    while ((Get-Date) -lt $deadline) {
        if (Test-DockerEngine) {
            Write-Log "Docker Engine ist wieder erreichbar." "OK"

            $wslState = Get-DockerDesktopWslState
            Write-Log "docker-desktop WSL Status: $wslState" "INFO"

            return $true
        }

        Start-Sleep -Seconds $CheckIntervalSeconds
    }

    Write-Log "Docker Engine reagiert nach $DockerStartupTimeout Sekunden weiterhin nicht." "ERROR"
    return $false
}

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

Write-Log "============================================================"
Write-Log "Docker Desktop Watchdog v2 gestartet."

if (-not (Test-Administrator)) {
    Write-Log "Das Skript muss als Administrator gestartet werden." "ERROR"
    exit 10
}

Write-Log "Windows: $([Environment]::OSVersion.VersionString)"
Write-Log "PowerShell: $($PSVersionTable.PSVersion)"

$problemDetected = $false
$hardFailure = $false

# Docker-Service pruefen
$dockerService = Get-ServiceIfExists "com.docker.service"

if (-not $dockerService) {
    Write-Log "com.docker.service wurde nicht gefunden." "ERROR"
    $problemDetected = $true
    $hardFailure = $true
}
elseif ($dockerService.Status -ne "Running") {
    Write-Log "com.docker.service Status: $($dockerService.Status)" "WARN"
    $problemDetected = $true
}
else {
    Write-Log "com.docker.service laeuft." "OK"
}

# Infrastruktur-Dienste nur informativ pruefen
foreach ($svcName in @("vmcompute","hns")) {
    $svc = Get-ServiceIfExists $svcName

    if ($svc) {
        if ($svc.Status -eq "Running") {
            Write-Log "$svcName laeuft." "OK"
        }
        else {
            Write-Log "$svcName Status: $($svc.Status)" "WARN"
        }
    }
}

# Dynamischen WSL-Service pruefen
$wslServiceName = Get-WslServiceName

if ($wslServiceName) {
    $wslSvc = Get-ServiceIfExists $wslServiceName
    Write-Log "Erkannter WSL-Dienst: $wslServiceName" "INFO"

    if ($wslSvc.Status -eq "Running") {
        Write-Log "$wslServiceName laeuft." "OK"
    }
    else {
        Write-Log "$wslServiceName Status: $($wslSvc.Status). Dies allein loest keine Reparatur aus." "WARN"
    }
}
else {
    Write-Log "Kein WslService/LxssManager gefunden. WSL wird direkt ueber wsl.exe geprueft." "INFO"
}

# WSL docker-desktop Status
$wslState = Get-DockerDesktopWslState
Write-Log "docker-desktop WSL Status: $wslState" "INFO"

# Engine ist die entscheidende Gesundheitspruefung
$engineHealthy = Test-DockerEngineWithRetries -Retries $EngineCheckRetries -DelaySeconds $EngineRetryDelaySeconds

if (-not $engineHealthy) {
    $problemDetected = $true
}

if ($ForceRepair) {
    Write-Log "-ForceRepair wurde angegeben." "WARN"
    $problemDetected = $true
}

if ($problemDetected) {
    if (-not $engineHealthy) {
        Write-Log "Docker Engine ist nicht erreichbar. Reparatur wird ausgefuehrt." "WARN"
    }
    elseif ($hardFailure) {
        Write-Log "Kritischer Docker-Dienst fehlt. Reparatur wird ausgefuehrt." "WARN"
    }
    elseif ($ForceRepair) {
        Write-Log "Erzwungene Reparatur wird ausgefuehrt." "WARN"
    }
    else {
        Write-Log "Nicht-kritische Abweichung erkannt, Engine arbeitet jedoch. Kein Neustart." "INFO"
        exit 0
    }

    if (Repair-DockerDesktop) {
        Write-Log "Docker Desktop wurde erfolgreich wiederhergestellt." "OK"
        exit 0
    }
    else {
        Write-Log "Automatische Wiederherstellung war nicht erfolgreich." "ERROR"
        Write-Log "Docker Logs pruefen: $env:LOCALAPPDATA\Docker\log" "INFO"
        exit 20
    }
}
else {
    Write-Log "Alle kritischen Docker-Komponenten arbeiten normal. Kein Neustart notwendig." "OK"
    exit 0
}

# SIG # Begin signature block
# MIIFkgYJKoZIhvcNAQcCoIIFgzCCBX8CAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCAuZpEWyfVkvU0s
# Ni58atv8NRNzppKcFvI49wSsAeY3baCCAwgwggMEMIIB7KADAgECAhBdW0cdgisz
# skPlmGd0f0iFMA0GCSqGSIb3DQEBCwUAMBoxGDAWBgNVBAMMD01laW5Db2RlU2ln
# bmluZzAeFw0yNjAyMDMxNDI5MjRaFw0yNzAyMDMxNDQ5MjRaMBoxGDAWBgNVBAMM
# D01laW5Db2RlU2lnbmluZzCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEB
# ANJWh2JTCtag0dRldJNELqXoDX4ijtg+rmoFzXW1j3cKBjZWgw9mvsokTvGml5Qj
# SRC2XF62RlIgjiUR83XAPhoea8Y+QlLgw2c0kuBgcdGBXmN43zLMVwy09SLocnyg
# zClsNaFJ+TzUxsW1HnlfNrn5MP9L1t7KILFool2HNeGGpZ1iDuKx5lCNXSrdKAie
# sAKFFeBvvgOB8GOIT1gM+hLeCnt64dbWP8zvrKgBMRxN/B7MjG4m599I+Ctf3QFX
# XZXM5nOBDFpo9wQYsk22UImvWuJ2Ju1BI8wJGYWdYZnw7qnx42xNdxu2X/d2f53w
# JfVNYpqFPfZrJ2Hr6DELJXkCAwEAAaNGMEQwDgYDVR0PAQH/BAQDAgeAMBMGA1Ud
# JQQMMAoGCCsGAQUFBwMDMB0GA1UdDgQWBBR5hlDyYO2DRKllzzuymvbyeE3bcDAN
# BgkqhkiG9w0BAQsFAAOCAQEAT5jih3tYyz/KXtkxK+rIRwBbeFzNI4Sn6oIZc30l
# xuACnNFfxHrapXYioiE8I3mT8ZBYCEyP/lOQdX9oE3+QXPIr4ASSiA/Do0+noIR7
# tRC60E6grsYE4lFOHBNv2dI6XXiJiFxbD6iradD2ZxJJNqBmQtxlA25syAhjhyJU
# 5lOAeBlLxwG9hDUUKe406/JjAqi2vnkMjBAuN1QEDhJlefDmCkBp7F9iKLv/rkJY
# NSnHLVYbbvd1Q3WqNSh1+JcUPhGiyu7ZvhhAly+uXP1Tafrgltj7jLsWjuqmxagK
# z9hgBI7s6QcoVa8dfxbyFizbIWHQW78D1kQGzj8O4aPsVzGCAeAwggHcAgEBMC4w
# GjEYMBYGA1UEAwwPTWVpbkNvZGVTaWduaW5nAhBdW0cdgiszskPlmGd0f0iFMA0G
# CWCGSAFlAwQCAQUAoIGEMBgGCisGAQQBgjcCAQwxCjAIoAKAAKECgAAwGQYJKoZI
# hvcNAQkDMQwGCisGAQQBgjcCAQQwHAYKKwYBBAGCNwIBCzEOMAwGCisGAQQBgjcC
# ARUwLwYJKoZIhvcNAQkEMSIEIOIT0f294Mhh4RQt+uLpA/hHGIW/BvfFldQhP6Et
# /89EMA0GCSqGSIb3DQEBAQUABIIBAGu56Lz05n27Cs9+K0JeCWgMXTO4/4wLvGBk
# 7V8uvKUZO0k+JqG+4/fqbnJjKMqknzzuW4JtrJrJHruatDn3bfOhXhpnTe3Jc915
# GuRfQg8dPbQwcWRA7kdsmATB8DB2u+25O39OM8SRJDIDs1mZWV6wqStVEOM4upcr
# d/nK5hCL+Ow9EMaI8Y99HVHcSe6jJMT5O0TFGFkA0zJxEx6nn5PxrXBj70M6SnZn
# 66G2l50e7CWlpICdKk59TK5lYVa7bDT1d63EsSiWO7ik7hBSGCwO/CL0XtESOGLG
# EfUIkeYUNPU+JiUV5v7LJYBVP+wRYJmxnDkSirlFKIvoBudbats=
# SIG # End signature block
