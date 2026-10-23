# Stops the project's own PostgreSQL.
$root = Split-Path -Parent $PSScriptRoot
& "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" -D "$root\data\pgdata" stop -m fast
