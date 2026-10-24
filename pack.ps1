$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Join-Path $root "photara_render"
$dest = Join-Path $root "photara_render-1.0.6.zip"

if (-not (Test-Path $pkg)) { throw "missing $pkg" }
if (Test-Path $dest) { Remove-Item -LiteralPath $dest -Force }

$tmp = Join-Path $env:TEMP ("photara_pack_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
Copy-Item -LiteralPath $pkg -Destination $tmp -Recurse
Get-ChildItem -LiteralPath $tmp -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

Compress-Archive -Path (Join-Path $tmp "photara_render") -DestinationPath $dest
Remove-Item -LiteralPath $tmp -Recurse -Force
Write-Host "打包完成: $dest"
