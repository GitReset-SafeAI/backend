
$ErrorActionPreference = "Stop"

# Load the local .env without printing its contents.
$envFile = Join-Path (Get-Location) ".env"

if (-not (Test-Path $envFile)) {
    throw ".env file not found in the repository root."
}

$settings = @{}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()

    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line -split "=", 2
        $settings[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
}

# Adjust these names if your .env uses different variable names.
$url = $settings["SUPABASE_URL"]
$apiKey = $settings["SUPABASE_KEY"]

if (-not $url) { $url = $settings["SUPABASE_URL"] }
if (-not $apiKey) { $apiKey = $settings["SUPABASE_ANON_KEY"] }
if (-not $apiKey) { $apiKey = $settings["SUPABASE_PUBLISHABLE_KEY"] }

if (-not $url -or -not $apiKey) {
    throw "Supabase URL or publishable/anon key was not found. Check the variable names in .env."
}

$email = Read-Host "Test user's email"
$securePassword = Read-Host "Test user's password" -AsSecureString
$credential = [System.Net.NetworkCredential]::new("", $securePassword)
$password = $credential.Password

try {
    $body = @{
        email    = $email
        password = $password
    } | ConvertTo-Json

    $response = Invoke-RestMethod `
        -Method Post `
        -Uri "$($url.TrimEnd('/'))/auth/v1/token?grant_type=password" `
        -Headers @{ apikey = $apiKey } `
        -ContentType "application/json" `
        -Body $body

    if (-not $response.access_token) {
        throw "Supabase did not return an access token."
    }

    Write-Host "`nLogin successful." -ForegroundColor Green
    Write-Host "Token type: $($response.token_type)"
    Write-Host "Expires in: $($response.expires_in) seconds"
    Write-Host "User ID: $($response.user.id)"

    # Keep the token in this PowerShell session for the next test.
    $global:SUPABASE_TEST_ACCESS_TOKEN = $response.access_token
    Write-Host "`nAccess token saved in this PowerShell session as `$SUPABASE_TEST_ACCESS_TOKEN."
    Write-Host "Do not share or print the token."
}
catch {
    $status = $null
    if ($_.Exception.Response) {
        $status = [int]$_.Exception.Response.StatusCode
    }

    if ($status) {
        Write-Host "Login failed. HTTP status: $status" -ForegroundColor Red
    } else {
        Write-Host "Login failed. Check the local configuration and network connection." -ForegroundColor Red
    }

    throw "Supabase login did not complete. Sensitive response details were not printed."
}
finally {
    $password = $null
    $credential = $null
    $securePassword = $null
    $body = $null
}
