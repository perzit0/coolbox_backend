# API de clientes de COOLBOX (integración entre grupos)

Documento para el grupo pareado. Describe cómo leer nuestros clientes, cómo enviarnos los suyos
y cómo leemos nosotros los de ustedes.

## 1. Datos de conexión

| Dato | Valor |
| --- | --- |
| URL base | `https://coolbox-backend-diny.onrender.com/api/integracion` |
| Autenticación | Cabecera `X-API-Key: <clave>` (también se acepta `Authorization: Bearer <clave>`) |
| Formato | JSON (UTF-8) |
| Límite | 60 peticiones por minuto por IP |

La clave se la entrega nuestro responsable por un medio privado (no por el chat del curso).
El servidor está en el plan gratuito de Render: la primera llamada después de un rato sin uso
puede tardar hasta 1 minuto. Si reciben un error 502 o 503, reintenten.

## 2. Clave única del cliente

Un cliente se identifica por **tipo de documento + número de documento** (campo `clave`,
por ejemplo `DNI-45678912` o `RUC-20512345678`). No usamos solo el número: un DNI y un
carné de extranjería pueden coincidir.

Tipos de documento: `DNI`, `CE`, `RUC`, `PASAPORTE`. También enviamos el código del
catálogo 06 de SUNAT en `codigo_tipo_documento` (1 DNI, 4 CE, 6 RUC, 7 pasaporte).

## 3. Leer nuestros clientes

```
GET /api/integracion/clientes?pagina=1&por_pagina=100
GET /api/integracion/clientes?desde=2026-11-20T00:00:00Z     (solo creados o modificados desde esa fecha)
GET /api/integracion/clientes/DNI/45678912                  (un cliente)
GET /api/integracion/estructura                             (esta misma descripción en JSON)
```

Respuesta del listado:

```json
{
  "sistema": "COOLBOX - Grupo 8",
  "clave_unica": ["tipo_documento", "numero_documento"],
  "total": 125,
  "pagina": 1,
  "por_pagina": 100,
  "total_paginas": 2,
  "siguiente": "https://.../api/integracion/clientes?pagina=2&por_pagina=100",
  "clientes": [
    {
      "clave": "DNI-45678912",
      "tipo_documento": "DNI",
      "codigo_tipo_documento": "1",
      "numero_documento": "45678912",
      "nombres": "María",
      "apellidos": "Salas Ruiz",
      "razon_social": null,
      "nombre_completo": "María Salas Ruiz",
      "email": "maria@gmail.com",
      "telefono": "987654321",
      "direccion": null,
      "fecha_registro": "2026-11-20T21:00:00+00:00",
      "fecha_actualizacion": "2026-11-20T21:00:00+00:00"
    }
  ]
}
```

Para empresas (RUC) viene `razon_social` y `nombres`/`apellidos` pueden venir vacíos.
Mientras `siguiente` no sea `null`, hay más páginas.

## 4. Enviarnos sus clientes (opcional)

Si prefieren enviarnos sus clientes en lugar de que los leamos:

```
POST /api/integracion/clientes
Content-Type: application/json
X-API-Key: <clave>

{ "clientes": [ { "tipo_documento": "DNI", "numero_documento": "71234567", "nombres": "Pedro", "apellidos": "Lezama" } ] }
```

Respuesta:

```json
{ "lote": 12, "recibidos": 5, "insertados": 4, "actualizados": 0, "sin_cambios": 1,
  "duplicados": 0, "rechazados": 0, "detalle_rechazados": [] }
```

Se puede enviar el mismo cliente varias veces: no se duplica.

## 5. Cómo leemos los clientes de ustedes

Necesitamos de su lado:

1. La URL de su listado de clientes (por ejemplo `https://su-api/api/clientes`).
2. Cómo autenticarnos: una clave en una cabecera (indiquen el nombre de la cabecera) o un
   usuario y contraseña para pedir un token.
3. Si paginan, que la respuesta indique la página siguiente (`siguiente`, `next` o
   `pagina`/`total_paginas`).

Reconocemos solos los nombres de campo más comunes (`tipo_documento`, `tipoDoc`,
`numero_documento`, `nro_documento`, `dni`, `ruc`, `nombres`, `apellidos`,
`apellido_paterno`, `razon_social`, `nombre_completo`, `email`, `correo`, `telefono`,
`celular`, `direccion`...). Si usan otros nombres, nos los indican y los configuramos.

Lo que recibimos se guarda tal cual en una **tabla de desembarco (stage)**. Luego cada registro
se valida y pasa a nuestro maestro de clientes:

| Resultado | Cuándo |
| --- | --- |
| insertado | Cliente nuevo |
| actualizado | Ya existía: se completaron datos que teníamos vacíos (no reemplazamos los nuestros) |
| sin_cambios | Ya existía con los mismos datos |
| duplicado | Vino repetido en el mismo envío |
| rechazado | Documento inválido (DNI de 8 dígitos, RUC de 11 que empieza con 10, 15, 17 o 20, CE de 9 a 12, pasaporte de 6 a 12) o sin nombre |

Si su API no responde, reintentamos 3 veces (a los 2, 4 y 8 segundos). Cada llamada, de
entrada o de salida, queda registrada en nuestra traza del API.

## 6. Prueba rápida

```bash
curl -H "X-API-Key: <clave>" "https://coolbox-backend-diny.onrender.com/api/integracion/clientes?por_pagina=5"
```
