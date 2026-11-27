<#
  COOLBOX - Instala el respaldo local de comprobantes en esta PC.
  1. Copia el agente a C:\COOLBOX\respaldo (fuera de los repositorios).
  2. Genera la clave del respaldo (se pega UNA vez en Render como RESPALDO_API_KEY).
  3. Programa la copia automatica cada 5 minutos y al iniciar sesion.
  4. Hace la primera copia.
#>
param(
    [string]$Destino = 'C:\COOLBOX\respaldo',
    [string]$ApiPorDefecto = 'https://coolbox-backend-diny.onrender.com/api',
    [string]$CarpetaPorDefecto = 'C:\COOLBOX\comprobantes'
)
$ErrorActionPreference = 'Stop'
$Tarea = 'COOLBOX - Respaldo de comprobantes'

Write-Host ''
Write-Host '=== COOLBOX: respaldo local de comprobantes ===' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'respaldo_comprobantes.ps1') $Destino -Force
$Agente = Join-Path $Destino 'respaldo_comprobantes.ps1'
$Config = Join-Path $Destino 'config.json'

$actual = $null
if (Test-Path $Config) { $actual = Get-Content $Config -Raw | ConvertFrom-Json }

$api = Read-Host "URL del backend [$ApiPorDefecto]"
if (-not $api) { $api = $ApiPorDefecto }
$carpeta = Read-Host "Carpeta local de comprobantes [$CarpetaPorDefecto]"
if (-not $carpeta) { $carpeta = $CarpetaPorDefecto }

$clave = $null
if ($actual -and $actual.clave) {
    $r = Read-Host 'Ya hay una clave configurada. Conservarla? (S/n)'
    if ($r -notmatch '^[nN]') { $clave = [string]$actual.clave }
}
if (-not $clave) {
    $bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $clave = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
    try { Set-Clipboard -Value $clave } catch { }
    Write-Host ''
    Write-Host 'Clave del respaldo (ya esta copiada al portapapeles):' -ForegroundColor Yellow
    Write-Host "    $clave" -ForegroundColor Green
    Write-Host ''
    Write-Host 'En Render > coolbox-backend > Environment agrega:'
    Write-Host '    RESPALDO_API_KEY = (pega la clave)'
    Write-Host 'Guarda y espera a que el despliegue diga Live. No compartas esta clave.'
    Read-Host 'Cuando Render diga Live, presiona Enter para continuar'
}

@{ api_url = $api.TrimEnd('/'); clave = $clave; carpeta = $carpeta } | ConvertTo-Json |
    Set-Content -Path $Config -Encoding UTF8
# Solo el usuario actual puede leer la clave
try {
    $acl = Get-Acl $Config
    $acl.SetAccessRuleProtection($true, $false)
    $regla = New-Object Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'FullControl', 'Allow')
    $acl.SetAccessRule($regla)
    Set-Acl $Config $acl
} catch { }

# Tarea programada: cada 5 minutos y al iniciar sesion, oculta
$accion = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Agente`""
$disparadores = @(
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)),
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
)
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$descripcion = 'Copia PDF, XML y CDR de COOLBOX a la carpeta local cada 5 minutos.'
try {
    Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $disparadores -Settings $ajustes `
        -Description $descripcion -Force | Out-Null
} catch {
    # Sin permisos de administrador el disparador "al iniciar sesion" puede no permitirse
    try {
        Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $disparadores[0] -Settings $ajustes `
            -Description $descripcion -Force | Out-Null
    } catch {
        & schtasks.exe /Create /F /TN $Tarea /SC MINUTE /MO 5 `
            /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \`"$Agente\`"" | Out-Null
    }
}
Write-Host "Tarea programada creada: '$Tarea' (cada 5 minutos y al iniciar sesion)." -ForegroundColor Cyan

Write-Host ''
Write-Host 'Primera copia (puede tardar si el servidor estaba dormido)...'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Agente
Write-Host ''
Write-Host "Listo. Tus comprobantes estan en $carpeta" -ForegroundColor Green
Write-Host "Historial: $(Join-Path $Destino 'respaldo.log')"
