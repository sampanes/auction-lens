$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$inputPath = Join-Path $repoRoot "data\inbox\listings.json"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Follow the README quick start first."
}
if (-not (Test-Path -LiteralPath $inputPath)) {
    throw "No canonical input found at $inputPath"
}

Push-Location $repoRoot
try {
    & $python -m auction_lens.cli run `
        --input $inputPath `
        --config "config\local.toml" `
        --database "data\auction-lens.sqlite3" `
        --env-file ".env" `
        --email
    if ($LASTEXITCODE -ne 0) {
        throw "Auction Lens exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
