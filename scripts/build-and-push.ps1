#Requires -Version 5.1
<#
.SYNOPSIS
    Сборка и публикация Docker-образа в приватный registry.

.DESCRIPTION
    Читает переменные из registry.env или .env в корне проекта.
    Пример:
      copy registry.example.env registry.env
      .\scripts\build-and-push.ps1 -MultiArch
      .\scripts\publish.ps1

.PARAMETER EnvFile
    Путь к файлу с REGISTRY, IMAGE_NAME, IMAGE_TAG.

.PARAMETER MultiArch
    Мультиарх-сборка через docker buildx (linux/amd64, linux/arm64/v8).

.PARAMETER BuildOnly
    Только сборка, без push.

.PARAMETER NoCache
    Сборка без кэша Docker.

.NOTES
    Эквивалент ручной multi-arch команды (copy-paste):

    docker buildx build `
      --builder=container `
      --platform=linux/amd64,linux/arm64/v8 `
      --provenance=false `
      --sbom=false `
      --build-arg BUILD_TIME="$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" `
      --build-arg GIT_SHA="$(git rev-parse --short HEAD)" `
      -f docker/Dockerfile `
      -t registry.mageek.su/al-web-video-encoder:latest `
      --push `
      .
#>
param(
    [string]$EnvFile = "",
    [switch]$MultiArch,
    [switch]$BuildOnly,
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

function Import-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        if ($line -match '^\s*([^#=]+?)=(.*)$') {
            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Update-BuildNumber {
    <#
    .SYNOPSIS
        Увеличивает номер сборки в build_number.txt на хосте (+1) перед docker build.
    #>
    param([string]$Root)
    $path = Join-Path $Root "build_number.txt"
    $current = 0
    if (Test-Path $path) {
        $raw = (Get-Content -Path $path -Raw -ErrorAction SilentlyContinue)
        if ($null -ne $raw) {
            $trimmed = $raw.Trim()
            if ($trimmed -match '^\d+$') { $current = [int]$trimmed }
        }
    }
    $next = $current + 1
    Set-Content -Path $path -Value "$next" -NoNewline -Encoding ascii
    Write-Host "Номер сборки: $next (было $current)"
    return $next
}

$candidates = @()
if ($EnvFile) { $candidates += $EnvFile }
$candidates += @(
    (Join-Path $ProjectRoot "registry.env"),
    (Join-Path $ProjectRoot ".env")
)
foreach ($file in $candidates) {
    if ($file -and (Test-Path $file)) {
        Write-Host "Загрузка переменных из: $file"
        Import-EnvFile -Path $file
        break
    }
}

$Registry = if ($env:REGISTRY) { $env:REGISTRY } else { "registry.mageek.su" }
$ImageName = if ($env:IMAGE_NAME) { $env:IMAGE_NAME } else { "al-web-video-encoder" }
$ImageTag = if ($env:IMAGE_TAG) { $env:IMAGE_TAG } else { "latest" }
$FullImage = "${Registry}/${ImageName}:${ImageTag}"

$BuildTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$GitSha = "unknown"
try {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $sha = git rev-parse --short HEAD 2>$null
  if ($LASTEXITCODE -eq 0 -and $sha) { $GitSha = "$sha".Trim() }
} catch { }
finally { if ($null -ne $prev) { $ErrorActionPreference = $prev } }

Write-Host "Образ: $FullImage"
Write-Host "BUILD_TIME: $BuildTime, GIT_SHA: $GitSha"

if ($env:REGISTRY_USER -and $env:REGISTRY_PASSWORD) {
    Write-Host "Вход в registry: $Registry"
    $env:REGISTRY_PASSWORD | docker login $Registry -u $env:REGISTRY_USER --password-stdin
    if ($LASTEXITCODE -ne 0) { throw "docker login failed" }
}

[void](Update-BuildNumber -Root $ProjectRoot)

$Dockerfile = "docker/Dockerfile"
$MetadataArgs = @(
    "--build-arg", "BUILD_TIME=$BuildTime",
    "--build-arg", "GIT_SHA=$GitSha"
)
$BuildArgs = @("-f", $Dockerfile, "-t", $FullImage) + $MetadataArgs + @(".")
if ($NoCache) { $BuildArgs = @("--no-cache") + $BuildArgs }

if ($MultiArch -or $env:MULTIARCH -eq "1") {
    Write-Host "Мультиарх-сборка и push (buildx)..."
    $platforms = "linux/amd64,linux/arm64/v8"
    $buildxArgs = @(
        "buildx", "build",
        "--builder=container",
        "--platform=$platforms",
        "--provenance=false",
        "--sbom=false"
    ) + $MetadataArgs + @(
        "-f", $Dockerfile,
        "-t", $FullImage
    ) + @(".")
    if ($NoCache) { $buildxArgs += "--no-cache" }
    if (-not $BuildOnly) { $buildxArgs += "--push" }
    docker @buildxArgs
} else {
    Write-Host "Локальная сборка..."
    docker build @BuildArgs
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

    if (-not $BuildOnly) {
        Write-Host "Публикация образа..."
        docker push $FullImage
    }
}

if ($LASTEXITCODE -ne 0) { throw "Операция завершилась с ошибкой" }

Write-Host ""
Write-Host "Готово: $FullImage"
if ($BuildOnly) {
    Write-Host "Push не выполнялся (флаг -BuildOnly)."
} else {
    Write-Host "Запуск на сервере (Windows):"
    Write-Host "  docker compose --env-file registry.env -f docker-compose.registry.windows.yml pull"
    Write-Host "  docker compose --env-file registry.env -f docker-compose.registry.windows.yml up -d"
    Write-Host "Запуск на Linux:"
    Write-Host "  docker compose --env-file registry.env -f docker-compose.registry.yml pull"
    Write-Host "  docker compose --env-file registry.env -f docker-compose.registry.yml up -d"
}
