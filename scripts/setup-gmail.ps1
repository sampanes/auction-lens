param(
    [string]$Config = "config\local.toml",
    [string]$EnvFile = ".env",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Get-RepoPath {
    param([string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
}

function Write-Utf8File {
    param([string]$Path, [string[]]$Lines)

    $encoding = New-Object System.Text.UTF8Encoding($false)
    $text = ($Lines -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::WriteAllText($Path, $text, $encoding)
}

function Read-EnvSettings {
    param([string]$Path)

    $settings = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $key, $value = $trimmed.Split("=", 2)
        $settings[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
    }
    return $settings
}

function Set-EnvSettings {
    param([string]$Path, [System.Collections.IDictionary]$Settings)

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lines.Add($line)
    }

    foreach ($key in $Settings.Keys) {
        $replacement = "$key=$($Settings[$key])"
        $found = $false
        for ($index = 0; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match "^\s*$([regex]::Escape($key))\s*=") {
                $lines[$index] = $replacement
                $found = $true
                break
            }
        }
        if (-not $found) {
            $lines.Add($replacement)
        }
    }
    Write-Utf8File -Path $Path -Lines $lines
}

function Set-GmailEmailReport {
    param([string]$Path)

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lines.Add($line)
    }

    $section = -1
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].Trim() -eq "[reports.email]") {
            $section = $index
            break
        }
    }
    if ($section -lt 0) {
        throw "$Path has no [reports.email] section. Recreate it from the current example config."
    }

    $nextSection = $lines.Count
    for ($index = $section + 1; $index -lt $lines.Count; $index++) {
        if ($lines[$index].Trim().StartsWith("[")) {
            $nextSection = $index
            break
        }
    }

    $gmailSettings = [ordered]@{
        enabled = "enabled = true"
        port = "port = 465"
        security = 'security = "ssl"'
    }
    foreach ($key in $gmailSettings.Keys) {
        $found = $false
        for ($index = $section + 1; $index -lt $nextSection; $index++) {
            if ($lines[$index] -match "^\s*$key\s*=") {
                $lines[$index] = $gmailSettings[$key]
                $found = $true
                break
            }
        }
        if (-not $found) {
            $lines.Insert($section + 1, $gmailSettings[$key])
            $nextSection++
        }
    }
    Write-Utf8File -Path $Path -Lines $lines
}

function Test-GmailSetup {
    param([string]$ConfigPath, [string]$EnvPath)

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "Missing $ConfigPath. Run '.venv\Scripts\auction-lens.exe setup' first."
    }
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        throw "Missing $EnvPath. Run '.venv\Scripts\auction-lens.exe setup' first."
    }

    $settings = Read-EnvSettings -Path $EnvPath
    $required = @(
        "AUCTION_LENS_SMTP_HOST",
        "AUCTION_LENS_SMTP_USERNAME",
        "AUCTION_LENS_SMTP_PASSWORD",
        "AUCTION_LENS_EMAIL_FROM",
        "AUCTION_LENS_EMAIL_TO"
    )
    foreach ($key in $required) {
        if (-not $settings.ContainsKey($key) -or -not $settings[$key]) {
            throw "$key is missing or empty in $EnvPath."
        }
    }

    if ($settings["AUCTION_LENS_SMTP_HOST"] -ne "smtp.gmail.com") {
        throw "AUCTION_LENS_SMTP_HOST must be smtp.gmail.com for this Gmail setup."
    }
    $password = $settings["AUCTION_LENS_SMTP_PASSWORD"]
    if ($password -match "\s" -or $password.Length -ne 16) {
        throw "The Gmail app password must contain 16 characters with no spaces."
    }

    $inEmailSection = $false
    $emailSettings = @{}
    foreach ($line in Get-Content -LiteralPath $ConfigPath) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith("[")) {
            $inEmailSection = $trimmed -eq "[reports.email]"
            continue
        }
        if ($inEmailSection -and $trimmed.Contains("=")) {
            $key, $value = $trimmed.Split("=", 2)
            $emailSettings[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
        }
    }
    if ($emailSettings["enabled"] -ne "true") {
        throw "[reports.email] must contain enabled = true in $ConfigPath."
    }
    if ($emailSettings["port"] -ne "465" -or $emailSettings["security"] -ne "ssl") {
        throw "Gmail requires port = 465 and security = 'ssl' in $ConfigPath."
    }

    Write-Host "[OK] Gmail settings are complete; no credential values were displayed."
}

$configPath = Get-RepoPath -Path $Config
$envPath = Get-RepoPath -Path $EnvFile

if ($CheckOnly) {
    Test-GmailSetup -ConfigPath $configPath -EnvPath $envPath
    exit 0
}

if (-not (Test-Path -LiteralPath $configPath) -or -not (Test-Path -LiteralPath $envPath)) {
    $auctionLens = Join-Path $repoRoot ".venv\Scripts\auction-lens.exe"
    if (-not (Test-Path -LiteralPath $auctionLens)) {
        throw "Install Auction Lens first by following 'Start here' in README.md."
    }
    Push-Location $repoRoot
    try {
        & $auctionLens setup --config $configPath --env-file $envPath
        if ($LASTEXITCODE -ne 0) {
            throw "Auction Lens setup failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

Write-Host "Gmail requires 2-Step Verification and a dedicated App Password."
Write-Host "Create one named 'Auction Lens' at:"
Write-Host "https://myaccount.google.com/apppasswords"
Write-Host ""

$gmailAddress = Read-Host "Gmail address used to send reports"
try {
    $parsedAddress = [System.Net.Mail.MailAddress]::new($gmailAddress)
} catch {
    throw "That is not a valid email address."
}
if ($parsedAddress.Address -ne $gmailAddress) {
    throw "Enter one plain email address without a display name."
}

$recipient = Read-Host "Report recipient (press Enter to use the same address)"
if (-not $recipient) {
    $recipient = $gmailAddress
}
try {
    $parsedRecipient = [System.Net.Mail.MailAddress]::new($recipient)
} catch {
    throw "The report recipient is not a valid email address."
}
if ($parsedRecipient.Address -ne $recipient) {
    throw "Enter one plain recipient address without a display name."
}

$securePassword = Read-Host "Paste the 16-character Gmail App Password" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $appPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) -replace "\s", ""
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
if ($appPassword.Length -ne 16) {
    throw "After removing spaces, the Gmail App Password must contain 16 characters."
}

$settings = [ordered]@{
    AUCTION_LENS_SMTP_HOST = "smtp.gmail.com"
    AUCTION_LENS_SMTP_USERNAME = $gmailAddress
    AUCTION_LENS_SMTP_PASSWORD = $appPassword
    AUCTION_LENS_EMAIL_FROM = $gmailAddress
    AUCTION_LENS_EMAIL_TO = $recipient
}
Set-EnvSettings -Path $envPath -Settings $settings
Set-GmailEmailReport -Path $configPath
$appPassword = $null

Test-GmailSetup -ConfigPath $configPath -EnvPath $envPath
Write-Host ""
Write-Host "Next, email the lots explicitly marked hunting:"
Write-Host ".venv\Scripts\auction-lens.exe watchlist --verdict hunting --email"
