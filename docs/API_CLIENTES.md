# API de clientes de COOLBOX — integración con J2F Monitoreo

Intercambiamos solo los datos básicos del cliente. Lo demás (IDs internos, fechas,
origen, persona de contacto) se queda en cada sistema.

## 1. Conexión

| Dato | Valor |
| --- | --- |
| URL | `https://coolbox-backend-diny.onrender.com/api/integracion/clientes` |
| Autenticación | Cabecera `X-API-Key: <clave>` (también vale `Authorization: Bearer <clave>`) |
| Formato | JSON (UTF-8) |
| Límite | 60 peticiones por minuto |

- La clave se entrega por privado.
- El servidor está en el plan gratuito de Render: la primera llamada tras un rato sin uso
  puede tardar hasta 1 minuto. Si reciben 502 o 503, reintenten.

## 2. Datos que intercambiamos

| Dato | Campo nuestro | Su campo | Regla |
| --- | --- | --- | --- |
| Tipo de documento | `tipo_documento` | `document_type` | `DNI`, `RUC`, `CE` o `PASAPORTE` |
| Número de documento | `numero_documento` | `document_number` | DNI 8 dígitos; RUC 11 dígitos (inicia con 10, 15, 17 o 20); CE 9 a 12 letras o números; pasaporte 6 a 12 letras o números |
| Nombre o razón social | `nombre_completo` | `business_name` | Obligatorio |
| Teléfono | `telefono` | `phone` | Opcional, número peruano sin prefijo |
| Correo | `email` | `email` | Opcional |
| Dirección | `direccion` | `address` | Opcional |

El cliente se identifica por **tipo + número de documento**: si llega dos veces, no se duplica.

## 3. Leer nuestros clientes

```
GET /api/integracion/clientes?pagina=1&por_pagina=100
GET /api/integracion/clientes?desde=2026-11-20T00:00:00Z   (solo nuevos o modificados desde esa fecha)
GET /api/integracion/clientes/DNI/45678912                (un cliente)
```

Respuesta (resumida a los campos que deben usar):

```json
{
  "total": 2,
  "pagina": 1,
  "total_paginas": 1,
  "siguiente": null,
  "clientes": [
    {
      "tipo_documento": "DNI",
      "numero_documento": "45678912",
      "nombre_completo": "María Salas Ruiz",
      "telefono": "987654321",
      "email": "maria@gmail.com",
      "direccion": null
    }
  ]
}
```

Mientras `siguiente` no sea `null`, hay más páginas. La respuesta trae otros campos
(`nombres`, `apellidos`, `razon_social`, fechas); pueden ignorarlos.

**Para guardar en su sistema:** usen `nombre_completo` como `business_name`. Si un CE
nuestro trae letras, su validación actual (solo números) lo rechazará; sugerimos aceptar
letras como hacemos nosotros.

## 4. Cómo leemos los clientes de ustedes

Leemos `GET /api/clients/` iniciando sesión antes con `POST /api/auth/login`
(`identifier` y `password`) y usando el `access_token` como `Authorization: Bearer`.

Necesitamos de ustedes:

1. **URL de su backend en producción.**
2. **Un usuario solo para la integración**, con **un único perfil** que tenga permiso
   `view_all` o `admin`. Debe ser exclusivo: cada inicio de sesión en su sistema cierra las
   demás sesiones de ese usuario.

Lo que recibimos pasa por una tabla de desembarco y se valida antes de entrar a nuestro
maestro. Se rechaza si el documento no cumple las reglas del punto 2 o si no tiene nombre.
Si su API no responde, reintentamos 3 veces (a los 2, 4 y 8 segundos).

## 5. Prueba rápida

```powershell
curl.exe -H "X-API-Key: <clave>" "https://coolbox-backend-diny.onrender.com/api/integracion/clientes?por_pagina=5"
```
