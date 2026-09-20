# Loads .env into this PowerShell session. The API can use `uvicorn --env-file .env`,
# but the worker and the provisioning CLI read the process environment, so run this first.
Get-Content .env | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
    $i = $line.IndexOf('=')
    $name = $line.Substring(0, $i).Trim()
    $value = $line.Substring($i + 1).Trim()
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}
Write-Host "loaded .env into this session"
