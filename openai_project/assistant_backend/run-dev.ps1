# Reproducible development startup for the assistant backend.
# Prints the effective non-secret configuration, validates it per provider,
# then starts the server on 127.0.0.1:8799 (foreground).
#
# Usage:
#   .\run-dev.ps1                                   # local-openai-compatible (requires -Model or AUDISOR_MODEL_ID)
#   .\run-dev.ps1 -Model qwen2.5-coder:7b
#   .\run-dev.ps1 -Provider fake-deterministic      # offline demos / tests only
param(
    [string]$Provider = $(if ($env:AUDISOR_PROVIDER) { $env:AUDISOR_PROVIDER } else { 'local-openai-compatible' }),
    [string]$Model    = $(if ($env:AUDISOR_MODEL_ID) { $env:AUDISOR_MODEL_ID } else { '' }),
    [string]$BaseUrl  = $(if ($env:AUDISOR_BASE_URL) { $env:AUDISOR_BASE_URL } else { 'http://127.0.0.1:11434' }),
    [string]$FixEngine = $(if ($env:AUDISOR_FIX_ENGINE) { $env:AUDISOR_FIX_ENGINE } else { 'model' }),
    [string]$FixFallback = $(if ($env:AUDISOR_FIX_FALLBACK) { $env:AUDISOR_FIX_FALLBACK } else { 'none' }),
    [switch]$SkipSync
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$knownProviders = @('local-openai-compatible', 'fake-deterministic', 'cloud-openai-compatible')
$knownEngines   = @('model', 'languagetool', 'auto')
$knownFallbacks = @('none', 'model')

$javaCmd = Get-Command java -ErrorAction SilentlyContinue
$javaAvailable = [bool]$javaCmd

Write-Host '=== Audisor assistant backend — effective configuration (non-secret) ==='
Write-Host "  provider        : $Provider"
Write-Host "  model id        : $(if ($Model) { $Model } else { '(not set)' })"
Write-Host "  base url        : $BaseUrl"
Write-Host "  listen          : http://127.0.0.1:8799"
Write-Host "  fix engine      : $FixEngine"
Write-Host "  fix fallback    : $FixFallback"
Write-Host "  java available  : $javaAvailable$(if ($javaAvailable) { ' (' + $javaCmd.Source + ')' })"
Write-Host "  cors origins    : $(if ($env:AUDISOR_CORS_ORIGINS) { $env:AUDISOR_CORS_ORIGINS } else { '(disabled)' })"
Write-Host "  environment     : $(if ($env:AUDISOR_ASSISTANT_ENV) { $env:AUDISOR_ASSISTANT_ENV } else { 'development (default)' })"
Write-Host '========================================================================'

# Conditional validation: fail fast with a specific reason, never start half-configured.
if ($knownProviders -notcontains $Provider) {
    throw "Unknown AUDISOR_PROVIDER '$Provider'. Known: $($knownProviders -join ', ')"
}
if ($knownEngines -notcontains $FixEngine) {
    throw "Unknown AUDISOR_FIX_ENGINE '$FixEngine'. Known: $($knownEngines -join ', ')"
}
if ($knownFallbacks -notcontains $FixFallback) {
    throw "Unknown AUDISOR_FIX_FALLBACK '$FixFallback'. Known: $($knownFallbacks -join ', ')"
}
if ($Provider -eq 'local-openai-compatible') {
    if (-not $Model) {
        throw 'AUDISOR_MODEL_ID is required for local-openai-compatible (e.g. -Model qwen2.5-coder:7b). Refusing to start with an empty model id.'
    }
    if (-not $BaseUrl) { throw 'AUDISOR_BASE_URL must not be empty for local-openai-compatible.' }
}
if ($FixEngine -in @('languagetool', 'auto')) {
    & uv run --no-sync python -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('language_tool_python') else 1)" 2>$null
    $ltInstalled = ($LASTEXITCODE -eq 0)
    if (-not $ltInstalled -and $FixEngine -eq 'languagetool' -and $FixFallback -eq 'none') {
        throw "AUDISOR_FIX_ENGINE=$FixEngine requires the 'grammar' extra (uv sync --extra grammar) when no fallback is configured."
    }
    if (-not $javaAvailable -and $FixEngine -eq 'languagetool' -and $FixFallback -eq 'none') {
        throw "AUDISOR_FIX_ENGINE=languagetool requires Java on PATH when AUDISOR_FIX_FALLBACK=none."
    }
    if (-not $ltInstalled -or -not $javaAvailable) {
        Write-Warning "LanguageTool prerequisites incomplete (installed=$ltInstalled, java=$javaAvailable). Startup continues; fix_wording behavior follows the fallback policy '$FixFallback'."
    }
}

# Export the validated configuration for the server process.
$env:AUDISOR_PROVIDER = $Provider
if ($Model)   { $env:AUDISOR_MODEL_ID = $Model }
$env:AUDISOR_BASE_URL = $BaseUrl
$env:AUDISOR_FIX_ENGINE = $FixEngine
$env:AUDISOR_FIX_FALLBACK = $FixFallback

if (-not $SkipSync) { uv sync --extra dev | Out-Host }
uv run --no-sync audisor-assistant
