#Requires -Version 5.1
<#
.SYNOPSIS
    Prepares the optional local AI runtime for SAT-SA. Run once.

.DESCRIPTION
    SAT-SA's assessment is deterministic and needs no AI. This script
    sets up the OPTIONAL explanation layer so that SAT-SA can find it
    with no configuration: no paths to type, no endpoint, no model file
    to locate.

    OFFLINE BY DEFAULT. Everything is installed from files shipped
    beside this script in the .\ai folder. Nothing is downloaded unless
    you pass -AllowDownload, and even then only from Ollama's official
    site, named in full before the request is made.

    It is idempotent: run it twice and the second run reports what is
    already in place and changes nothing.

    WHAT IT DOES NOT DO, deliberately:
      * does not expose the runtime to the network — it binds to
        127.0.0.1 and this script sets OLLAMA_HOST to make that explicit
      * does not add, remove or modify any firewall rule
      * does not disable, weaken or reconfigure any security control
      * does not require, request or use Administrator rights beyond
        what the vendor installer itself needs
      * does not fetch or execute a remote script
      * does not send any SOC data anywhere: assessment data never
        leaves the machine, with or without AI

.PARAMETER Model
    The model tag SAT-SA should be able to resolve. Default qwen2.5:7b,
    which matches SAT-SA's "Balanced" setting.

.PARAMETER AllowDownload
    Permit fetching the runtime and model from Ollama's official
    endpoints when they are not bundled. Off by default. Do not use
    this on an air-gapped assessment machine.

.EXAMPLE
    .\SAT-SA-Setup-AI.ps1
    Offline setup from the bundled .\ai folder.

.EXAMPLE
    .\SAT-SA-Setup-AI.ps1 -AllowDownload
    Permit downloading from ollama.com on a machine with internet.
#>

[CmdletBinding()]
param(
    [string]$Model = "qwen2.5:7b",
    [switch]$AllowDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Endpoint  = "http://127.0.0.1:11434"
$AssetDir  = Join-Path $PSScriptRoot "ai"
$Installer = Join-Path $AssetDir "OllamaSetup.exe"

# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------
function Write-Step   { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok     { param($m) Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Info   { param($m) Write-Host "    [info] $m" -ForegroundColor Gray }
function Write-Warn   { param($m) Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Write-Fail   { param($m) Write-Host "    [FAIL] $m" -ForegroundColor Red }

function Exit-With {
    param([int]$Code, [string]$Message)
    if ($Code -eq 0) { Write-Host "`n$Message" -ForegroundColor Green }
    else             { Write-Host "`n$Message" -ForegroundColor Yellow }
    Write-Host ""
    exit $Code
}

# ---------------------------------------------------------------------
# Discovery — the same places SAT-SA itself looks
# ---------------------------------------------------------------------
function Find-Ollama {
    $onPath = Get-Command ollama -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    # Each base is checked first: Join-Path throws on a null base, and
    # ProgramFiles variables are not all defined on every edition.
    $candidates = @()
    foreach ($pair in @(
        @($env:LOCALAPPDATA, "Programs\Ollama\ollama.exe"),
        @($env:ProgramFiles, "Ollama\ollama.exe"),
        @($env:ProgramW6432, "Ollama\ollama.exe")
    )) {
        if ($pair[0]) { $candidates += (Join-Path $pair[0] $pair[1]) }
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { return $c }
    }
    return $null
}

function Get-InstalledModels {
    # Loopback only. A failure here means "not running", which is an
    # expected state, not an error.
    #
    # Returns $null when the service is not responding and an ARRAY of
    # names when it is — including an empty one, which means "running,
    # nothing installed". Both leading commas below are load-bearing:
    # PowerShell unrolls a returned empty array to $null, which would
    # collapse those two very different states into one.
    try {
        $r = Invoke-RestMethod -Uri "$Endpoint/api/tags" -TimeoutSec 5 `
                               -Method Get -ErrorAction Stop
    } catch {
        return $null
    }

    # StrictMode raises on a property that is not there, so ask first.
    if (-not $r.PSObject.Properties.Match('models').Count) { return ,@() }
    if ($null -eq $r.models)                               { return ,@() }

    return ,@($r.models | ForEach-Object { [string]$_.name })
}

function Test-ModelPresent {
    param([string[]]$Installed, [string]$Wanted)
    if ($null -eq $Installed) { return $false }
    foreach ($name in $Installed) {
        if ($name -eq $Wanted -or $name.StartsWith("${Wanted}:")) { return $true }
    }
    return $false
}

function Wait-ForService {
    param([int]$Seconds = 30)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if ($null -ne (Get-InstalledModels)) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ---------------------------------------------------------------------
Write-Host ""
Write-Host "SAT-SA — local AI setup" -ForegroundColor White
Write-Host "-----------------------" -ForegroundColor White
Write-Host "Optional. SAT-SA assesses submissions without AI; this only"
Write-Host "enables plain-language explanations of findings it has"
Write-Host "already determined."

# ---------------------------------------------------------------------
Write-Step "1/4  Local AI runtime"
# ---------------------------------------------------------------------
$exe = Find-Ollama

if ($exe) {
    Write-Ok "already installed: $exe"
    Write-Info "nothing to install; leaving it untouched"
}
elseif (Test-Path -LiteralPath $Installer -PathType Leaf) {
    # Verify before executing. A checksum file is shipped alongside the
    # installer; if it is present the hash must match, and a mismatch
    # stops the script rather than running an unverified binary.
    $checksumFile = "$Installer.sha256"
    if (Test-Path -LiteralPath $checksumFile -PathType Leaf) {
        $expected = (Get-Content -LiteralPath $checksumFile -Raw).Trim().Split()[0]
        $actual   = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash
        if ($actual -ne $expected.ToUpper() -and $actual -ne $expected) {
            Write-Fail "installer checksum does not match"
            Write-Info "expected $expected"
            Write-Info "actual   $actual"
            Exit-With 3 "Setup stopped. The bundled installer is not the one that was published with this package."
        }
        Write-Ok "installer checksum verified (SHA-256)"
    } else {
        Write-Warn "no .sha256 file beside the installer; cannot verify it"
    }

    Write-Info "installing from $Installer"
    Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT" -Wait
    $exe = Find-Ollama
    if (-not $exe) {
        Exit-With 4 "The installer ran but Ollama was not found afterwards. Install it manually, then run this script again."
    }
    Write-Ok "installed: $exe"
}
elseif ($AllowDownload) {
    Write-Warn "no bundled installer; -AllowDownload was given"
    Write-Info "this will contact https://ollama.com/download/OllamaSetup.exe"
    Write-Info "do not do this on an air-gapped assessment machine"
    $temp = Join-Path $env:TEMP "OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" `
                      -OutFile $temp -UseBasicParsing
    Start-Process -FilePath $temp -ArgumentList "/VERYSILENT" -Wait
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
    $exe = Find-Ollama
    if (-not $exe) {
        Exit-With 4 "Download completed but Ollama was not found afterwards. Install it manually, then run this script again."
    }
    Write-Ok "installed: $exe"
}
else {
    Write-Fail "no AI runtime found, and none bundled in $AssetDir"
    Write-Host ""
    Write-Host "    To enable AI on an air-gapped machine, place these beside"
    Write-Host "    this script before running it again:"
    Write-Host ""
    Write-Host "        ai\OllamaSetup.exe          (the runtime installer)"
    Write-Host "        ai\OllamaSetup.exe.sha256   (its published checksum)"
    Write-Host "        ai\$Model.gguf              (the model weights)"
    Write-Host ""
    Write-Host "    On a machine that is permitted internet access, you may"
    Write-Host "    instead run:  .\SAT-SA-Setup-AI.ps1 -AllowDownload"
    Exit-With 2 "SAT-SA still works. Without AI it produces the same findings, scores and reports; only the plain-language explanations are unavailable."
}

# Make local-only binding explicit rather than relying on the default.
# User scope, not machine scope: this changes nothing for other users
# and needs no elevation.
if ($env:OLLAMA_HOST -ne "127.0.0.1:11434") {
    [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "127.0.0.1:11434", "User")
    $env:OLLAMA_HOST = "127.0.0.1:11434"
    Write-Ok "runtime pinned to 127.0.0.1 (not reachable from the network)"
} else {
    Write-Ok "runtime already pinned to 127.0.0.1"
}

# ---------------------------------------------------------------------
Write-Step "2/4  Local service"
# ---------------------------------------------------------------------
$installed = Get-InstalledModels

if ($null -ne $installed) {
    Write-Ok "already responding at $Endpoint"
} else {
    Write-Info "starting it (loopback only)"
    Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
    if (Wait-ForService -Seconds 30) {
        Write-Ok "responding at $Endpoint"
        $installed = Get-InstalledModels
    } else {
        Exit-With 5 "The AI service did not start within 30 seconds. SAT-SA will report 'AI NOT RUNNING' and continue to work without explanations."
    }
}

# ---------------------------------------------------------------------
Write-Step "3/4  Model: $Model"
# ---------------------------------------------------------------------
if (Test-ModelPresent -Installed $installed -Wanted $Model) {
    Write-Ok "already installed; leaving it untouched"
}
else {
    $gguf = Get-ChildItem -LiteralPath $AssetDir -Filter "*.gguf" `
                          -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($gguf) {
        Write-Info "importing from $($gguf.Name) (no download)"
        $modelfile = Join-Path $env:TEMP "SAT-SA-$($PID).Modelfile"
        "FROM `"$($gguf.FullName)`"" | Set-Content -LiteralPath $modelfile -Encoding ASCII
        try {
            & $exe create $Model -f $modelfile
            if ($LASTEXITCODE -ne 0) { throw "ollama create exited with $LASTEXITCODE" }
            Write-Ok "imported as '$Model'"
        } catch {
            Write-Fail "import failed: $_"
            Exit-With 6 "SAT-SA will report 'AI MODEL MISSING' and continue to work without explanations."
        } finally {
            Remove-Item -LiteralPath $modelfile -ErrorAction SilentlyContinue
        }
    }
    elseif ($AllowDownload) {
        Write-Warn "no bundled .gguf; -AllowDownload was given"
        Write-Info "this will contact Ollama's model registry for '$Model'"
        & $exe pull $Model
        if ($LASTEXITCODE -ne 0) {
            Exit-With 6 "The model could not be pulled. SAT-SA will report 'AI MODEL MISSING' and continue to work without explanations."
        }
        Write-Ok "pulled '$Model'"
    }
    else {
        Write-Fail "'$Model' is not installed and no .gguf was found in $AssetDir"
        Write-Host ""
        Write-Host "    Place the model weights at:  ai\$Model.gguf"
        Write-Host "    or, where internet is permitted, re-run with -AllowDownload"
        Exit-With 6 "SAT-SA still works. It will report 'AI MODEL MISSING' and keep every rule-generated rationale."
    }
}

# ---------------------------------------------------------------------
Write-Step "4/4  Verification"
# ---------------------------------------------------------------------
# Confirm the end state SAT-SA itself will discover, rather than
# assuming the steps above worked.
$installed = Get-InstalledModels
if ($null -eq $installed) {
    Exit-With 5 "The service stopped responding after setup. Start it and run this script again."
}
if (-not (Test-ModelPresent -Installed $installed -Wanted $Model)) {
    Exit-With 6 "'$Model' is still not resolvable. SAT-SA will report 'AI MODEL MISSING'."
}

Write-Ok "runtime installed"
Write-Ok "service responding on 127.0.0.1 only"
Write-Ok "model '$Model' resolvable"
Write-Host ""
Write-Host "    Models present: $($installed -join ', ')" -ForegroundColor Gray

$done = @"
Setup complete. Start SAT-SA.exe — it will detect this automatically
and show AI READY. There is nothing to configure.

Explanations are never generated automatically. Run an assessment,
open the Review Queue, and select [ Explain Top Findings ] when you
want them.
"@

Exit-With 0 $done
