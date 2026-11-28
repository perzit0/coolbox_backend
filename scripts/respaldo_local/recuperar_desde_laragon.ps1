<#
  COOLBOX - Recupera de la API de facturacion LOCAL (Laragon) el PDF, XML y CDR de un
  comprobante que solo se emitio ahi, y los deja en su carpeta de C:\COOLBOX\comprobantes.
  En la siguiente pasada, el agente de respaldo los registra en el sistema y en la nube
  (el servidor revisa que el XML y la CDR sean de ese comprobante antes de aceptarlos).

  Antes: abre Laragon (Start All) y en la carpeta de fact_api ejecuta  php artisan serve
#>
param(
    [string]$Api = 'http://127.0.0.1:8000',
    [string]$Serie = 'B001',
    [int]$Numero = 2,
    [string]$Tipo = 'boletas',
    [string]$Carpeta = 'C:\COOLBOX\comprobantes'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Api = $Api.TrimEnd('/')
$nombre = '{0}-{1:D6}' -f $Serie, $Numero
Write-Host "Recuperando $nombre desde $Api" -ForegroundColor Cyan

$correo = Read-Host 'Correo del usuario de la API local'
$clave = Read-Host 'Contrasena' -AsSecureString
$plano = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($clave))
try {
    $login = Invoke-RestMethod -Uri "$Api/api/auth/login" -Method Post -ContentType 'application/json' `
        -Body (@{ email = $correo; password = $plano } | ConvertTo-Json) -TimeoutSec 30
} catch { Write-Host "No se pudo iniciar sesion en $Api : $($_.Exception.Message)" -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }
finally { $plano = $null }
$h = @{ Authorization = "Bearer $($login.access_token)"; Accept = 'application/json' }

$ruta = @{ boletas = 'boletas'; facturas = 'invoices' }[$Tipo]
$doc = $null; $pagina = 1
do {
    $r = Invoke-RestMethod -Uri "$Api/api/v1/$ruta`?per_page=100&page=$pagina" -Headers $h -TimeoutSec 60
    $doc = $r.data | Where-Object { $_.serie -eq $Serie -and [int]$_.correlativo -eq $Numero } | Select-Object -First 1
    $pagina++
} while (-not $doc -and $r.pagination -and $pagina -le [int]$r.pagination.last_page)
if (-not $doc) { Write-Host "No se encontro $nombre en la API local." -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }

$fecha = [datetime]::Parse(([string]$doc.fecha_emision).Substring(0, 10))
$destino = Join-Path $Carpeta ('{0}\{1:yyyy}\{1:MM}\{1:dd}\{2}' -f $Tipo, $fecha, $nombre)
New-Item -ItemType Directory -Force -Path $destino | Out-Null
$base = "$Api/api/v1/$ruta/$($doc.id)"
try { Invoke-RestMethod -Uri "$base/generate-pdf" -Method Post -Headers $h -TimeoutSec 60 | Out-Null } catch { }
foreach ($par in @(@('xml', "$nombre.xml"), @('pdf', "$nombre.pdf"), @('cdr', "$nombre-CDR.zip"))) {
    $archivo = Join-Path $destino $par[1]
    try {
        Invoke-WebRequest -Uri "$base/download-$($par[0])" -Headers $h -OutFile $archivo -TimeoutSec 60 -UseBasicParsing | Out-Null
        Write-Host "  OK  $($par[1])"
    } catch { Write-Host "  --  $($par[1]) no disponible: $($_.Exception.Message)" -ForegroundColor Yellow }
}
$xml = Join-Path $destino "$nombre.xml"
if ((Test-Path $xml) -and ((Get-Content $xml -Raw) -notmatch "<cbc:ID>\s*$Serie-0*$Numero\s*</cbc:ID>")) {
    Write-Host 'El XML descargado NO es de este comprobante; se borra la carpeta.' -ForegroundColor Red
    Remove-Item -LiteralPath $destino -Recurse -Force; Read-Host 'Enter para salir'; exit 1
}
Write-Host "`nListo: $destino" -ForegroundColor Green
Write-Host 'Ahora ejecuta sincronizar_ahora.bat: el sistema lo registrara y subira a la nube.'
Read-Host 'Enter para salir'
