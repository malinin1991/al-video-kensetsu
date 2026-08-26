#Requires -Version 5.1
<#
.SYNOPSIS
    Быстрая мультиарх-публикация образа в registry.

.DESCRIPTION
    Тонкая обёртка над build-and-push.ps1 -MultiArch.
    Читает REGISTRY / IMAGE_NAME / IMAGE_TAG из registry.env (или .env).

    Использование:
      copy registry.example.env registry.env   # один раз
      docker login registry.mageek.su          # при необходимости
      .\scripts\publish.ps1

.NOTES
    Эквивалент ручной команды (copy-paste):

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

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Публикация multi-arch (amd64 + arm64/v8) через buildx..."
& (Join-Path $ScriptDir "build-and-push.ps1") -MultiArch @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
