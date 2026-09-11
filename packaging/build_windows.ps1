#Requires -Version 5.1
<#
.SYNOPSIS
    Builds the SAT-SA Windows distribution with PyInstaller.

.DESCRIPTION
    Run this ON WINDOWS, from the project root. PyInstaller does not
    cross-compile: a Windows SAT-SA.exe can only be produced on Windows.

    Produces dist\SAT-SA\ — a folder, not an installer and not a single
    file. onedir is deliberate:

      * a onefile executable unpacks itself to a temporary directory on
        every launch, costing seconds each time, and Qt plugin loading
        from a temp path is a recurring source of "works on my machine"
        failures;
      * an accreditation review can read a directory, but not a
        self-extracting blob.

    Nothing here reaches the network. The Qwen model is NOT bundled —
    the executable stays a few hundred megabytes and the optional AI
    layer is provisioned separately by SAT-SA-Setup-AI.ps1.

.PARAMETER Clean
    Remove build\ and dist\ first. Use after changing the spec, or when
    a previous build behaved oddly — PyInstaller caches aggressively.

.PARAMETER SkipTests
    Skip the test suite. Not recommended: the suite is what catches a
    packaging change that broke an import.

.EXAMPLE
    .\packaging\build_windows.ps1

.EXAMPLE
    .\packaging\build_windows.ps1 -Clean
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "    [FAIL] $m" -ForegroundColor Red }

# --------------------------------------------------------------------
# Locate the project root — this script lives in packaging\
# --------------------------------------------------------------------
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Spec = Join-Path $PSScriptRoot "SAT-SA.spec"
if (-not (Test-Path -LiteralPath $Spec)) {
    Write-Fail "Spec not found: $Spec"
    exit 1
}

Write-Host ""
Write-Host "SAT-SA — Windows build" -ForegroundColor White
Write-Host "----------------------" -ForegroundColor White
Write-Host "project root: $ProjectRoot"

# --------------------------------------------------------------------
Write-Step "1/6  Platform check"
# --------------------------------------------------------------------
# PyInstaller emits a binary for the platform it runs on. Building here
# on anything but Windows silently produces a non-Windows binary, which
# is worse than failing.
if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    Write-Fail "This is not Windows. PyInstaller cannot cross-compile."
    Write-Host "    A Windows SAT-SA.exe must be built on a Windows machine."
    exit 1
}
Write-Ok "Windows"

# --------------------------------------------------------------------
Write-Step "2/6  Python and dependencies"
# --------------------------------------------------------------------
$python = "python"
if (Test-Path -LiteralPath ".\venv\Scripts\python.exe") {
    $python = ".\venv\Scripts\python.exe"
    Write-Ok "using the project virtual environment"
} else {
    Write-Warn "no .\venv found — using whatever 'python' resolves to"
}

& $python --version
if ($LASTEXITCODE -ne 0) { Write-Fail "Python not runnable"; exit 1 }

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "PyInstaller not installed; installing the dev requirements"
    & $python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Write-Fail "dependency install failed"; exit 1 }
}
Write-Ok "PyInstaller available"

# --------------------------------------------------------------------
Write-Step "3/6  Test suite"
# --------------------------------------------------------------------
if ($SkipTests) {
    Write-Warn "skipped by request"
} else {
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "tests failed — not building a package from a failing tree"
        exit 1
    }
    Write-Ok "tests passed"
}

# --------------------------------------------------------------------
Write-Step "4/6  Build"
# --------------------------------------------------------------------
if ($Clean) {
    foreach ($dir in @("build", "dist")) {
        if (Test-Path -LiteralPath $dir) {
            Remove-Item -LiteralPath $dir -Recurse -Force
            Write-Ok "removed $dir\"
        }
    }
}

& $python -m PyInstaller $Spec --noconfirm
if ($LASTEXITCODE -ne 0) { Write-Fail "PyInstaller failed"; exit 1 }

$Exe = Join-Path $ProjectRoot "dist\SAT-SA\SAT-SA.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Fail "build reported success but SAT-SA.exe is not there"
    exit 1
}
$sizeMb = [math]::Round(((Get-ChildItem -LiteralPath (Split-Path $Exe) -Recurse |
    Measure-Object -Property Length -Sum).Sum / 1MB), 0)
Write-Ok "built dist\SAT-SA\  ($sizeMb MB)"

if ($sizeMb -gt 1500) {
    Write-Warn "that is unexpectedly large — check the spec's excludes, and"
    Write-Warn "confirm no model file was pulled into the bundle"
}

# --------------------------------------------------------------------
Write-Step "5/6  Self-check the packaged build"
# --------------------------------------------------------------------
# The point of building is a working application, not a folder. This
# runs the frozen executable and fails the build if it cannot find its
# own configuration, engine, backends or writable storage.
& $Exe --self-check
$selfCheck = $LASTEXITCODE
if ($selfCheck -ne 0) {
    Write-Fail "the packaged application failed its own self-check"
    Write-Host "    The build exists but is not usable. Do not ship it."
    exit 1
}
Write-Ok "packaged application passed --self-check"

# --------------------------------------------------------------------
Write-Step "6/6  Stage the distributable"
# --------------------------------------------------------------------
foreach ($file in @("SAT-SA-Setup-AI.ps1", "INSTALL.txt", "README.txt")) {
    $source = Join-Path $PSScriptRoot $file
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Split-Path $Exe) -Force
        Write-Ok "staged $file"
    } else {
        Write-Warn "missing, not staged: $file"
    }
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host ""
Write-Host "    Package : dist\SAT-SA\"
Write-Host "    Launch  : dist\SAT-SA\SAT-SA.exe"
Write-Host "    Verify  : dist\SAT-SA\SAT-SA.exe --self-check"
Write-Host ""
Write-Host "    The Qwen model is NOT bundled. For optional local AI, run"
Write-Host "    SAT-SA-Setup-AI.ps1 once on the target machine, or install"
Write-Host "    Ollama and pull the model yourself."
Write-Host ""
Write-Host "    Still to do by hand: zip dist\SAT-SA\ for distribution, and"
Write-Host "    test on a clean Windows machine that has no Python."
Write-Host ""
