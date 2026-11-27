<#
  COOLBOX - Respaldo local de comprobantes electronicos.

  Copia a esta PC todos los PDF, XML y CDR, en carpetas por tipo, anio, mes y dia:
      C:\COOLBOX\comprobantes\boletas\2026\11\27\B001-000045\B001-000045.pdf
  Asi, aunque falle el servidor o la nube, siempre hay una copia local.

  - Solo descarga lo que falta (o lo que cambio de tamanio).
  - Verifica la huella SHA-256 de cada archivo antes de guardarlo.
  - Avisa al sistema cuantos archivos tiene (pantalla Comprobantes > Respaldo local).
  - Los archivos que ya no figuran en el sistema (estructura anterior o archivos equivocados)
    se mueven a _retirados\ (nunca se borran).
  - -Verificar recalcula la huella de todos los archivos locales y repone los alterados.

  Compatible con Windows PowerShell 5.1. La configuracion (URL, clave y carpeta) esta en
  config.json junto a este script; la crea instalar_respaldo.bat.
#>
param(
    [switch]$Verificar,
    [string]$Config = (Join-Path $PSScriptRoot 'config.json')
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest es mucho mas rapido sin barra
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Log = Join-Path $PSScriptRoot 'respaldo.log'
function Escribir([string]$texto) {
    $linea = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $texto
    Write-Host $linea
    try {
        if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Move-Item $Log "$Log.1" -Force }
        Add-Content -Path $Log -Value $linea -Encoding UTF8
    } catch { }
}

if (-not (Test-Path $Config)) { Escribir "Falta $Config. Ejecuta instalar_respaldo.bat."; exit 2 }
$cfg = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$Api = ([string]$cfg.api_url).TrimEnd('/')
$Carpeta = [string]$cfg.carpeta
$Cabeceras = @{ 'X-API-Key' = [string]$cfg.clave }
if (-not $Api -or -not $Carpeta -or -not $cfg.clave) { Escribir 'config.json incompleto (api_url, clave, carpeta).'; exit 2 }

# Una sola ejecucion a la vez (la tarea programada puede coincidir con una manual)
$Candado = Join-Path $PSScriptRoot 'respaldo.lock'
try { $bloqueo = [IO.File]::Open($Candado, 'OpenOrCreate', 'ReadWrite', 'None') }
catch { Write-Host 'Ya hay un respaldo en curso.'; exit 0 }

function Llamar([string]$url) {
    # El servidor gratuito puede estar dormido: se reintenta con espera creciente.
    for ($i = 1; $i -le 3; $i++) {
        try { return Invoke-RestMethod -Uri $url -Headers $Cabeceras -TimeoutSec 90 }
        catch {
            $codigo = $null
            if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
            if ($codigo -eq 401 -or $codigo -eq 503 -or $i -eq 3) { throw }
            Start-Sleep -Seconds (10 * $i)
        }
    }
}

function Huella([string]$ruta) { (Get-FileHash -Path $ruta -Algorithm SHA256).Hash.ToLowerInvariant() }

$enIndice = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
$total = 0; $tiene = 0; $bytes = [int64]0; $descargados = 0; $pendientes = 0; $errores = 0; $alterados = 0
$inicio = Get-Date
try {
    New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null
    $desde = 0
    do {
        $pagina = Llamar "$Api/respaldo/comprobantes?desde=$desde&limite=500"
        foreach ($a in $pagina.archivos) {
            $total++
            $ruta = [string]$a.ruta
            # Solo rutas relativas simples (tipo/anio/mes/dia/archivo): nada de .. ni unidades
            if ($ruta -notmatch '^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)+$' -or $ruta -match '\.\.') {
                Escribir "Ruta no valida ignorada: $ruta"; $errores++; continue
            }
            $destino = $Carpeta
            foreach ($parte in $ruta.Split('/')) { $destino = Join-Path $destino $parte }
            [void]$enIndice.Add([IO.Path]::GetFullPath($destino))

            $falta = -not (Test-Path -LiteralPath $destino)
            if (-not $falta) {
                $largo = (Get-Item -LiteralPath $destino).Length
                if ($largo -ne [int64]$a.bytes) { $falta = $true }
                elseif ($Verificar -and (Huella $destino) -ne $a.sha256) {
                    Escribir "Alterado, se repone: $ruta"; $alterados++; $falta = $true
                }
            }
            if ($falta) {
                $temporal = "$destino.descarga"
                try {
                    New-Item -ItemType Directory -Force -Path (Split-Path $destino -Parent) | Out-Null
                    Invoke-WebRequest -Uri "$Api/respaldo/comprobantes/$($a.id)" -Headers $Cabeceras `
                        -OutFile $temporal -TimeoutSec 120 -UseBasicParsing | Out-Null
                    if ((Huella $temporal) -ne $a.sha256) {
                        Remove-Item -LiteralPath $temporal -Force
                        Escribir "Huella distinta, no se guarda: $ruta"; $errores++; continue
                    }
                    Move-Item -LiteralPath $temporal -Destination $destino -Force
                    $descargados++
                } catch {
                    if (Test-Path -LiteralPath $temporal) { Remove-Item -LiteralPath $temporal -Force }
                    $codigo = $null
                    if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
                    if ($codigo -eq 404) { $pendientes++ } else { $errores++; Escribir "Error en ${ruta}: $($_.Exception.Message)" }
                    continue
                }
            }
            $tiene++; $bytes += [int64]$a.bytes
        }
        $desde = $pagina.siguiente
    } while ($desde)

    # Lo que ya no figura en el sistema pasa a _retirados (solo si el indice respondio con datos)
    $retirados = 0
    if ($total -gt 0) {
        $papelera = Join-Path (Join-Path $Carpeta '_retirados') (Get-Date -Format 'yyyy-MM-dd')
        $raiz = [IO.Path]::GetFullPath($Carpeta).TrimEnd('\', '/')
        Get-ChildItem -LiteralPath $Carpeta -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
            $completo = $_.FullName
            $relativo = $completo.Substring($raiz.Length).TrimStart('\', '/')
            if ($relativo -like '_retirados*' -or $completo -like '*.descarga' -or $enIndice.Contains($completo)) { return }
            $nuevo = Join-Path $papelera $relativo
            New-Item -ItemType Directory -Force -Path (Split-Path $nuevo -Parent) | Out-Null
            Move-Item -LiteralPath $completo -Destination $nuevo -Force
            $retirados++
        }
        # Carpetas que quedaron vacias
        Get-ChildItem -LiteralPath $Carpeta -Recurse -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike '*_retirados*' } | Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object { if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) { Remove-Item -LiteralPath $_.FullName -Force } }
        if ($retirados) { Escribir "$retirados archivos que ya no figuran en el sistema se movieron a _retirados" }
    }

    $cuerpo = @{
        equipo = [Environment]::MachineName; carpeta = $Carpeta; archivos = $tiene; bytes = $bytes
        descargados = $descargados; pendientes = $pendientes; errores = $errores
    } | ConvertTo-Json
    try {
        Invoke-RestMethod -Uri "$Api/respaldo/latido" -Method Post -Headers $Cabeceras `
            -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo)) -TimeoutSec 60 | Out-Null
    } catch { Escribir "No se pudo informar el estado: $($_.Exception.Message)" }

    $seg = [int]((Get-Date) - $inicio).TotalSeconds
    $resumen = "Respaldo: $tiene de $total archivos en $Carpeta ($descargados nuevos"
    if ($Verificar) { $resumen += ", $alterados repuestos" }
    if ($pendientes) { $resumen += ", $pendientes aun no disponibles" }
    if ($errores) { $resumen += ", $errores con error" }
    Escribir "$resumen) en $seg s."
    if ($errores) { exit 1 }
} catch {
    $codigo = $null
    if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
    if ($codigo -eq 401) { Escribir 'La clave de respaldo no es valida: revisa RESPALDO_API_KEY en Render y config.json.' }
    elseif ($codigo -eq 503) { Escribir 'El respaldo no esta habilitado en el servidor: falta RESPALDO_API_KEY en Render.' }
    else { Escribir "No se pudo conectar con el servidor: $($_.Exception.Message)" }
    exit 1
} finally {
    $bloqueo.Close()
    Remove-Item -LiteralPath $Candado -Force -ErrorAction SilentlyContinue
}
