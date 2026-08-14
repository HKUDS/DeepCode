param(
    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"

if (-not $env:WINDOWS_CERTIFICATE -or -not $env:WINDOWS_CERTIFICATE_PASSWORD) {
    throw "Windows signing credentials are missing"
}

$outputPath = [System.IO.Path]::GetFullPath($Output)
$outputDirectory = [System.IO.Path]::GetDirectoryName($outputPath)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$pfxPath = [System.IO.Path]::Combine(
    [System.IO.Path]::GetTempPath(),
    "deepcode-signing-$([System.Guid]::NewGuid().ToString('N')).pfx"
)

try {
    $bytes = [System.Convert]::FromBase64String(
        ($env:WINDOWS_CERTIFICATE -replace '\s', '')
    )
    [System.IO.File]::WriteAllBytes($pfxPath, $bytes)
    $password = ConvertTo-SecureString `
        -String $env:WINDOWS_CERTIFICATE_PASSWORD `
        -Force `
        -AsPlainText
    $certificate = Import-PfxCertificate `
        -FilePath $pfxPath `
        -CertStoreLocation Cert:\CurrentUser\My `
        -Password $password
    if (-not $certificate.Thumbprint) {
        throw "The imported Windows certificate has no thumbprint"
    }
    $config = @{
        bundle = @{
            windows = @{
                certificateThumbprint = $certificate.Thumbprint
                digestAlgorithm = "sha256"
                timestampUrl = "http://timestamp.digicert.com"
            }
        }
    }
    $config | ConvertTo-Json -Depth 5 | Set-Content `
        -LiteralPath $outputPath `
        -Encoding utf8
}
finally {
    Remove-Item -LiteralPath $pfxPath -Force -ErrorAction SilentlyContinue
}
