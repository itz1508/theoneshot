# ─────────────────────────────────────────────────────────────────────────────
# Build all three product container images from their pinned Dockerfiles.
#
# Each product is independent and separately deployable:
#   - A-Flow            -> theoneshot-aflow:<tag>
#   - OneShot Fix       -> theoneshot-fix:<tag>
#   - Audisor Toolkit   -> theoneshot-audisor-agent:<tag>   (built from the audisor submodule)
#
# Usage (from the repository root):
#   pwsh packaging/build-images.ps1                 # default tag 0.9.0
#   pwsh packaging/build-images.ps1 -Tag 1.0.0      # custom tag
#   pwsh packaging/build-images.ps1 -Product aflow  # build a single product
#
# Base images are digest-pinned in each Dockerfile; dependency sets are frozen
# from each package's uv.lock. See docs/submissions/openai-build-week-2026.md for
# the historical submission images (different CLI surface, preserved as-is).
# ─────────────────────────────────────────────────────────────────────────────
[CmdletBinding()]
param(
    [string]$Tag = "0.9.0",
    [ValidateSet('all', 'aflow', 'fix', 'toolkit')]
    [string]$Product = 'all'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Build-Image {
    param(
        [string]$Name,
        [string]$Context,
        [string]$Dockerfile,
        [string]$ImageTag
    )
    Write-Output ""
    Write-Output "==> Building $Name ($ImageTag)"
    Write-Output "    context:    $Context"
    Write-Output "    dockerfile: $Dockerfile"
    docker build -f $Dockerfile -t $ImageTag $Context
    if ($LASTEXITCODE -ne 0) { throw "build failed for $Name" }
}

if ($Product -in 'all', 'aflow') {
    Build-Image -Name 'A-Flow' `
        -Context $Root `
        -Dockerfile (Join-Path $Root 'packaging/aflow/Dockerfile') `
        -ImageTag "theoneshot-aflow:$Tag"
}

if ($Product -in 'all', 'fix') {
    Build-Image -Name 'OneShot Fix' `
        -Context $Root `
        -Dockerfile (Join-Path $Root 'packaging/oneshot-fix/Dockerfile') `
        -ImageTag "theoneshot-fix:$Tag"
}

if ($Product -in 'all', 'toolkit') {
    Build-Image -Name 'Audisor Toolkit' `
        -Context (Join-Path $Root 'audisor') `
        -Dockerfile (Join-Path $Root 'audisor/docker/Dockerfile') `
        -ImageTag "theoneshot-audisor-agent:$Tag"
}

Write-Output ""
Write-Output "==> Smoke checks"
docker run --rm "theoneshot-aflow:$Tag" demo | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'aflow demo failed' }
Write-Output "    aflow demo: OK (exit 0)"

docker run --rm "theoneshot-audisor-agent:$Tag" --version
if ($LASTEXITCODE -ne 0) { throw 'toolkit --version failed' }

docker run --rm --entrypoint python "theoneshot-fix:$Tag" -c "import audisor_backend, audisor; print('    oneshot-fix imports: OK', audisor.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'oneshot-fix import check failed' }

Write-Output ""
Write-Output "All images built and smoke-checked: theoneshot-aflow:$Tag, theoneshot-fix:$Tag, theoneshot-audisor-agent:$Tag"
