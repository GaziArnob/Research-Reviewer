# Starts the project's own PostgreSQL (port 5434, data in data\pgdata). Independent of any other Postgres on this PC.
$root = Split-Path -Parent $PSScriptRoot
$bin = "C:\Program Files\PostgreSQL\18\bin"
$data = "$root\data\pgdata"
& "$bin\pg_ctl.exe" -D $data status *> $null
if ($LASTEXITCODE -eq 0) { Write-Output "Already running on port 5434."; exit 0 }
Start-Process -FilePath "$bin\pg_ctl.exe" -ArgumentList @("-D", "`"$data`"", "-l", "`"$root\data\pg.log`"", "-o", "`"-p 5434 -c listen_addresses=localhost`"", "start") -WindowStyle Hidden
Write-Output "Starting PostgreSQL on port 5434..."
