-- =====================================================================================
-- COOLBOX - Procedimientos almacenados (PostgreSQL / Supabase)
--
-- Requerimiento del profesor: el código de la aplicación no lleva consultas SQL.
-- Las operaciones de base de datos de los módulos nuevos se hacen aquí y el backend
-- solo invoca el procedimiento con sus parámetros (sin armar texto SQL, sin riesgo
-- de SQL injection).
--
-- Módulos:
--   1. Utilidades de documento de identidad (catálogo 06 de SUNAT)
--   2. Maestro de clientes (registro, mantenimiento, búsqueda, exportación)
--   3. Integración de clientes con el grupo pareado (lotes, tabla stage, traza del API)
--   4. Devolución de pedidos online entregados
--   5. Triggers (normalización y alimentación automática del maestro)
--   6. Gestor documental de comprobantes (índice de PDF, XML y CDR con huella SHA-256)
--   7. Seguridad de Supabase (RLS)
--
-- En PostgreSQL un procedimiento que devuelve datos se escribe como FUNCTION; los
-- nombres empiezan con sp_ (stored procedure) y los de triggers con tg_.
-- El backend ejecuta este archivo al arrancar: se borran las versiones anteriores y se
-- vuelven a crear, todo en una sola transacción (si algo falla no queda a medias).
-- =====================================================================================

-- Se eliminan las versiones anteriores (también sus triggers, que se recrean abajo).
DO $$
DECLARE
    f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS firma
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND (p.proname LIKE 'sp\_%' OR p.proname LIKE 'tg\_%')
    LOOP
        EXECUTE 'DROP FUNCTION IF EXISTS ' || f.firma || ' CASCADE';
    END LOOP;
END
$$;


-- =====================================================================================
-- 1. Documento de identidad
-- =====================================================================================

-- Convierte cualquier forma de escribir el tipo de documento (texto o código SUNAT)
-- al valor interno: DNI, CE, RUC o PASAPORTE. NULL si no se reconoce.
CREATE FUNCTION sp_tipo_documento(p_tipo text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE regexp_replace(upper(coalesce(p_tipo, '')), '[^A-Z0-9]', '', 'g')
        WHEN 'DNI' THEN 'DNI' WHEN '1' THEN 'DNI' WHEN '01' THEN 'DNI' WHEN 'LE' THEN 'DNI'
        WHEN 'CE' THEN 'CE' WHEN '4' THEN 'CE' WHEN '04' THEN 'CE'
        WHEN 'CARNETEXTRANJERIA' THEN 'CE' WHEN 'CARNEDEEXTRANJERIA' THEN 'CE'
        WHEN 'CARNETDEEXTRANJERIA' THEN 'CE' WHEN 'CARNEEXTRANJERIA' THEN 'CE'
        WHEN 'RUC' THEN 'RUC' WHEN '6' THEN 'RUC' WHEN '06' THEN 'RUC'
        WHEN 'PASAPORTE' THEN 'PASAPORTE' WHEN 'PAS' THEN 'PASAPORTE' WHEN 'PASSPORT' THEN 'PASAPORTE'
        WHEN '7' THEN 'PASAPORTE' WHEN '07' THEN 'PASAPORTE'
        ELSE NULL
    END
$$;

-- Código del catálogo 06 de SUNAT para el tipo interno.
CREATE FUNCTION sp_codigo_sunat(p_tipo text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE p_tipo WHEN 'DNI' THEN '1' WHEN 'CE' THEN '4' WHEN 'RUC' THEN '6' WHEN 'PASAPORTE' THEN '7' END
$$;

-- Tipo de documento según la cantidad de dígitos (ventas en caja: 8 DNI, 11 RUC, otro CE).
CREATE FUNCTION sp_tipo_por_longitud(p_nro text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE length(coalesce(p_nro, '')) WHEN 8 THEN 'DNI' WHEN 11 THEN 'RUC' ELSE 'CE' END
$$;

-- Texto del error si el número no corresponde al tipo; NULL si es válido.
CREATE FUNCTION sp_documento_error(p_tipo text, p_nro text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE
        WHEN p_tipo IS NULL THEN 'Tipo de documento no reconocido'
        WHEN coalesce(p_nro, '') = '' THEN 'Falta el número de documento'
        WHEN p_tipo = 'DNI' AND p_nro !~ '^[0-9]{8}$' THEN 'El DNI debe tener 8 dígitos'
        WHEN p_tipo = 'RUC' AND p_nro !~ '^(10|15|17|20)[0-9]{9}$' THEN 'El RUC debe tener 11 dígitos y empezar con 10, 15, 17 o 20'
        WHEN p_tipo = 'CE' AND p_nro !~ '^[A-Z0-9]{9,12}$' THEN 'El carné de extranjería debe tener de 9 a 12 caracteres'
        WHEN p_tipo = 'PASAPORTE' AND p_nro !~ '^[A-Z0-9]{6,12}$' THEN 'El pasaporte debe tener de 6 a 12 caracteres'
        ELSE NULL
    END
$$;

-- Número de documento sin espacios ni guiones, en mayúsculas.
CREATE FUNCTION sp_limpiar_documento(p_nro text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT nullif(upper(regexp_replace(coalesce(p_nro, ''), '[^A-Za-z0-9]', '', 'g')), '')
$$;

-- Texto recortado; NULL si queda vacío.
CREATE FUNCTION sp_texto(p_valor text, p_largo integer)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT nullif(left(btrim(regexp_replace(coalesce(p_valor, ''), '\s+', ' ', 'g')), p_largo), '')
$$;

-- Lanza un error de negocio que el backend convierte en respuesta HTTP:
-- MESSAGE = código del error, DETAIL = texto para el usuario, HINT = estado HTTP.
CREATE FUNCTION sp_error(p_codigo text, p_texto text, p_http integer DEFAULT 409)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = p_codigo, DETAIL = p_texto, HINT = p_http::text;
END
$$;


-- =====================================================================================
-- 2. Maestro de clientes
-- =====================================================================================

-- Registra un cliente o completa el que ya existe (clave única: tipo + número de documento).
--   p_sobrescribir = true : los datos recibidos reemplazan a los guardados (fuente propia).
--   p_sobrescribir = false: solo se llenan los datos vacíos (integración: no se pisan
--                           los datos que el cliente nos dio a nosotros).
-- Devuelve el id y la acción: insertado | actualizado | sin_cambios.
CREATE FUNCTION sp_cliente_registrar(
    p_tipo text, p_nro text, p_nombres text, p_apellidos text, p_razon_social text,
    p_email text, p_telefono text, p_direccion text,
    p_origen text, p_origen_detalle text, p_sobrescribir boolean
)
RETURNS TABLE (cliente_id integer, accion text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_tipo text := sp_tipo_documento(p_tipo);
    v_nro text := sp_limpiar_documento(p_nro);
    v_nombres text := sp_texto(p_nombres, 120);
    v_apellidos text := sp_texto(p_apellidos, 160);
    v_razon text := sp_texto(p_razon_social, 200);
    v_email text := lower(sp_texto(p_email, 160));
    v_telefono text := sp_texto(regexp_replace(coalesce(p_telefono, ''), '[^0-9+]', '', 'g'), 20);
    v_direccion text := sp_texto(p_direccion, 240);
    v_error text;
    v_antes maestro_cliente;
    v_despues maestro_cliente;
BEGIN
    v_error := sp_documento_error(v_tipo, v_nro);
    IF v_error IS NOT NULL THEN
        PERFORM sp_error('documento_invalido', v_error, 400);
    END IF;
    IF coalesce(v_nombres, v_apellidos, v_razon) IS NULL THEN
        PERFORM sp_error('nombre_requerido', 'Indique los nombres o la razón social del cliente.', 400);
    END IF;
    IF v_email IS NOT NULL AND v_email !~ '^[^@\s]+@[^@\s]+\.[a-z]{2,}$' THEN
        v_email := NULL;  -- un correo mal escrito no impide registrar al cliente
    END IF;
    IF p_origen NOT IN ('caja', 'tienda', 'manual', 'integracion') THEN
        PERFORM sp_error('origen_invalido', 'Origen del cliente no válido.', 400);
    END IF;

    INSERT INTO maestro_cliente (
        tipo_documento, nro_documento, nombres, apellidos, razon_social, email, telefono, direccion,
        origen, origen_detalle, activo, fecha_registro, fecha_actualizacion
    )
    VALUES (
        v_tipo, v_nro, v_nombres, v_apellidos, v_razon, v_email, v_telefono, v_direccion,
        p_origen, sp_texto(p_origen_detalle, 80), true, now(), now()
    )
    ON CONFLICT (tipo_documento, nro_documento) DO NOTHING
    RETURNING id INTO cliente_id;

    IF cliente_id IS NOT NULL THEN
        accion := 'insertado';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT * INTO v_antes FROM maestro_cliente
    WHERE tipo_documento = v_tipo AND nro_documento = v_nro
    FOR UPDATE;

    UPDATE maestro_cliente m SET
        nombres = CASE WHEN p_sobrescribir THEN coalesce(v_nombres, m.nombres) ELSE coalesce(m.nombres, v_nombres) END,
        apellidos = CASE WHEN p_sobrescribir THEN coalesce(v_apellidos, m.apellidos) ELSE coalesce(m.apellidos, v_apellidos) END,
        razon_social = CASE WHEN p_sobrescribir THEN coalesce(v_razon, m.razon_social) ELSE coalesce(m.razon_social, v_razon) END,
        email = CASE WHEN p_sobrescribir THEN coalesce(v_email, m.email) ELSE coalesce(m.email, v_email) END,
        telefono = CASE WHEN p_sobrescribir THEN coalesce(v_telefono, m.telefono) ELSE coalesce(m.telefono, v_telefono) END,
        direccion = CASE WHEN p_sobrescribir THEN coalesce(v_direccion, m.direccion) ELSE coalesce(m.direccion, v_direccion) END
    WHERE m.id = v_antes.id
    RETURNING * INTO v_despues;

    cliente_id := v_antes.id;
    accion := CASE
        WHEN (v_despues.nombres, v_despues.apellidos, v_despues.razon_social, v_despues.email,
              v_despues.telefono, v_despues.direccion)
             IS DISTINCT FROM
             (v_antes.nombres, v_antes.apellidos, v_antes.razon_social, v_antes.email,
              v_antes.telefono, v_antes.direccion)
        THEN 'actualizado' ELSE 'sin_cambios' END;
    RETURN NEXT;
END
$$;

-- Mantenimiento manual: crea (p_id NULL) o edita un cliente con los datos tal cual.
CREATE FUNCTION sp_cliente_guardar(
    p_id integer, p_tipo text, p_nro text, p_nombres text, p_apellidos text, p_razon_social text,
    p_email text, p_telefono text, p_direccion text
)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_tipo text := sp_tipo_documento(p_tipo);
    v_nro text := sp_limpiar_documento(p_nro);
    v_email text := lower(sp_texto(p_email, 160));
    v_error text;
    v_id integer;
BEGIN
    v_error := sp_documento_error(v_tipo, v_nro);
    IF v_error IS NOT NULL THEN
        PERFORM sp_error('documento_invalido', v_error, 400);
    END IF;
    IF coalesce(sp_texto(p_nombres, 120), sp_texto(p_apellidos, 160), sp_texto(p_razon_social, 200)) IS NULL THEN
        PERFORM sp_error('nombre_requerido', 'Indique los nombres o la razón social del cliente.', 400);
    END IF;
    IF v_tipo = 'RUC' AND sp_texto(p_razon_social, 200) IS NULL THEN
        PERFORM sp_error('razon_social_requerida', 'Un cliente con RUC necesita su razón social.', 400);
    END IF;
    IF v_email IS NOT NULL AND v_email !~ '^[^@\s]+@[^@\s]+\.[a-z]{2,}$' THEN
        PERFORM sp_error('email_invalido', 'El correo electrónico no es válido.', 400);
    END IF;

    SELECT id INTO v_id FROM maestro_cliente WHERE tipo_documento = v_tipo AND nro_documento = v_nro;
    IF v_id IS NOT NULL AND v_id IS DISTINCT FROM p_id THEN
        PERFORM sp_error('cliente_duplicado', 'Ya existe un cliente con ' || v_tipo || ' ' || v_nro || '.', 409);
    END IF;

    IF p_id IS NULL THEN
        INSERT INTO maestro_cliente (
            tipo_documento, nro_documento, nombres, apellidos, razon_social, email, telefono, direccion,
            origen, origen_detalle, activo, fecha_registro, fecha_actualizacion
        )
        VALUES (
            v_tipo, v_nro, sp_texto(p_nombres, 120), sp_texto(p_apellidos, 160), sp_texto(p_razon_social, 200),
            v_email, sp_texto(p_telefono, 20), sp_texto(p_direccion, 240),
            'manual', 'Registro en el sistema', true, now(), now()
        )
        RETURNING id INTO v_id;
        RETURN v_id;
    END IF;

    UPDATE maestro_cliente SET
        tipo_documento = v_tipo,
        nro_documento = v_nro,
        nombres = sp_texto(p_nombres, 120),
        apellidos = sp_texto(p_apellidos, 160),
        razon_social = sp_texto(p_razon_social, 200),
        email = v_email,
        telefono = sp_texto(p_telefono, 20),
        direccion = sp_texto(p_direccion, 240)
    WHERE id = p_id;
    IF NOT FOUND THEN
        PERFORM sp_error('cliente_no_encontrado', 'El cliente no existe.', 404);
    END IF;
    RETURN p_id;
END
$$;

-- Activa o desactiva un cliente (no se borra: puede tener ventas y comprobantes).
CREATE FUNCTION sp_cliente_estado(p_id integer, p_activo boolean)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE maestro_cliente SET activo = p_activo WHERE id = p_id;
    IF NOT FOUND THEN
        PERFORM sp_error('cliente_no_encontrado', 'El cliente no existe.', 404);
    END IF;
END
$$;

-- Datos de un cliente con los campos calculados que usa el sistema.
CREATE FUNCTION sp_cliente_json(m maestro_cliente)
RETURNS jsonb
LANGUAGE sql STABLE
AS $$
    SELECT to_jsonb(m) || jsonb_build_object(
        'codigo_sunat', sp_codigo_sunat(m.tipo_documento),
        'clave', m.tipo_documento || '-' || m.nro_documento,
        'nombre_completo', coalesce(m.razon_social, btrim(concat_ws(' ', m.nombres, m.apellidos)))
    )
$$;

CREATE FUNCTION sp_cliente_obtener(p_id integer)
RETURNS TABLE (cliente jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT sp_cliente_json(m) FROM maestro_cliente m WHERE m.id = p_id
$$;

-- Búsqueda por documento (la caja la usa antes de consultar RENIEC o SUNAT).
-- Si no se indica el tipo, se busca solo por número.
CREATE FUNCTION sp_cliente_buscar(p_tipo text, p_nro text)
RETURNS TABLE (cliente jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT sp_cliente_json(m)
    FROM maestro_cliente m
    WHERE m.activo
      AND m.nro_documento = sp_limpiar_documento(p_nro)
      AND (p_tipo IS NULL OR m.tipo_documento = sp_tipo_documento(p_tipo))
    ORDER BY m.fecha_actualizacion DESC
    LIMIT 1
$$;

-- Listado paginado con búsqueda por documento, nombre, razón social o correo.
CREATE FUNCTION sp_clientes_listar(
    p_buscar text, p_origen text, p_activo boolean, p_limite integer, p_offset integer
)
RETURNS TABLE (cliente jsonb, total bigint)
LANGUAGE sql STABLE
AS $$
    SELECT sp_cliente_json(m), count(*) OVER ()
    FROM maestro_cliente m
    WHERE (p_origen IS NULL OR m.origen = p_origen)
      AND (p_activo IS NULL OR m.activo = p_activo)
      AND (
          coalesce(btrim(p_buscar), '') = ''
          OR m.nro_documento ILIKE '%' || btrim(p_buscar) || '%'
          OR concat_ws(' ', m.nombres, m.apellidos, m.razon_social, m.email) ILIKE '%' || btrim(p_buscar) || '%'
      )
    ORDER BY m.fecha_actualizacion DESC, m.id DESC
    LIMIT least(greatest(coalesce(p_limite, 50), 1), 200)
    OFFSET greatest(coalesce(p_offset, 0), 0)
$$;

-- Cantidad de clientes por origen (tarjetas del módulo).
CREATE FUNCTION sp_clientes_resumen()
RETURNS TABLE (origen text, total bigint, activos bigint)
LANGUAGE sql STABLE
AS $$
    SELECT m.origen::text, count(*), count(*) FILTER (WHERE m.activo)
    FROM maestro_cliente m
    GROUP BY m.origen
    ORDER BY m.origen
$$;

-- Clientes que se exponen al grupo pareado (solo activos). p_desde permite pedir solo
-- los creados o modificados desde una fecha (sincronización incremental).
CREATE FUNCTION sp_clientes_exportar(p_desde timestamptz, p_limite integer, p_offset integer)
RETURNS TABLE (cliente jsonb, total bigint)
LANGUAGE sql STABLE
AS $$
    SELECT jsonb_build_object(
               'clave', m.tipo_documento || '-' || m.nro_documento,
               'tipo_documento', m.tipo_documento,
               'codigo_tipo_documento', sp_codigo_sunat(m.tipo_documento),
               'numero_documento', m.nro_documento,
               'nombres', m.nombres,
               'apellidos', m.apellidos,
               'razon_social', m.razon_social,
               'nombre_completo', coalesce(m.razon_social, btrim(concat_ws(' ', m.nombres, m.apellidos))),
               'email', m.email,
               'telefono', m.telefono,
               'direccion', m.direccion,
               'fecha_registro', m.fecha_registro,
               'fecha_actualizacion', m.fecha_actualizacion
           ),
           count(*) OVER ()
    FROM maestro_cliente m
    WHERE m.activo AND (p_desde IS NULL OR m.fecha_actualizacion >= p_desde)
    ORDER BY m.id
    LIMIT least(greatest(coalesce(p_limite, 100), 1), 500)
    OFFSET greatest(coalesce(p_offset, 0), 0)
$$;

-- Carga inicial del maestro con los clientes que ya había: cuentas de la tienda online
-- con documento y compradores de la caja. Idempotente: no duplica ni modifica.
CREATE FUNCTION sp_maestro_sincronizar()
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_tienda integer;
    v_caja integer;
BEGIN
    INSERT INTO maestro_cliente (
        tipo_documento, nro_documento, nombres, apellidos, email, telefono,
        origen, origen_detalle, activo, fecha_registro, fecha_actualizacion
    )
    SELECT DISTINCT ON (sp_tipo_documento(c.tipo_documento), sp_limpiar_documento(c.nro_documento))
           sp_tipo_documento(c.tipo_documento), sp_limpiar_documento(c.nro_documento),
           sp_texto(c.nombres, 120), sp_texto(c.apellidos, 160), lower(c.email), sp_texto(c.celular, 20),
           'tienda', 'Cuenta de la tienda online', true, c.fecha_registro, now()
    FROM cliente c
    WHERE sp_documento_error(sp_tipo_documento(c.tipo_documento), sp_limpiar_documento(c.nro_documento)) IS NULL
      AND coalesce(sp_texto(c.nombres, 120), sp_texto(c.apellidos, 160)) IS NOT NULL
    ORDER BY sp_tipo_documento(c.tipo_documento), sp_limpiar_documento(c.nro_documento), c.id DESC
    ON CONFLICT (tipo_documento, nro_documento) DO NOTHING;
    GET DIAGNOSTICS v_tienda = ROW_COUNT;

    INSERT INTO maestro_cliente (
        tipo_documento, nro_documento, nombres, razon_social,
        origen, origen_detalle, activo, fecha_registro, fecha_actualizacion
    )
    SELECT DISTINCT ON (sp_limpiar_documento(v.cliente_documento))
           sp_tipo_por_longitud(sp_limpiar_documento(v.cliente_documento)),
           sp_limpiar_documento(v.cliente_documento),
           CASE WHEN length(sp_limpiar_documento(v.cliente_documento)) <> 11 THEN sp_texto(v.cliente_nombre, 120) END,
           CASE WHEN length(sp_limpiar_documento(v.cliente_documento)) = 11 THEN sp_texto(v.cliente_nombre, 200) END,
           'caja', 'Venta en caja ' || v.codigo, true, v.fecha, now()
    FROM venta v
    WHERE v.estado = 'completada'
      AND sp_texto(v.cliente_nombre, 200) IS NOT NULL
      AND sp_documento_error(
              sp_tipo_por_longitud(sp_limpiar_documento(v.cliente_documento)),
              sp_limpiar_documento(v.cliente_documento)
          ) IS NULL
    ORDER BY sp_limpiar_documento(v.cliente_documento), v.fecha DESC
    ON CONFLICT (tipo_documento, nro_documento) DO NOTHING;
    GET DIAGNOSTICS v_caja = ROW_COUNT;

    RETURN v_tienda + v_caja;
END
$$;


-- =====================================================================================
-- 3. Integración de clientes con el grupo pareado
-- =====================================================================================

-- Abre un lote de carga. p_modo: consulta (leímos su API), recepcion (nos enviaron los
-- clientes a nuestra API) o archivo (JSON cargado por el administrador).
CREATE FUNCTION sp_lote_crear(p_origen text, p_modo text, p_url text, p_usuario_id integer)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_id integer;
BEGIN
    IF p_modo NOT IN ('consulta', 'recepcion', 'archivo') THEN
        PERFORM sp_error('modo_invalido', 'Modo de carga no válido.', 400);
    END IF;
    INSERT INTO integracion_lote (
        origen, modo, url, estado, intentos, total, insertados, actualizados, sin_cambios,
        duplicados, rechazados, usuario_id, fecha_inicio
    )
    VALUES (
        coalesce(sp_texto(p_origen, 80), 'Sin nombre'), p_modo, sp_texto(p_url, 300), 'en_proceso', 0,
        0, 0, 0, 0, 0, 0, p_usuario_id, now()
    )
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Desembarco: guarda en la tabla stage los registros recibidos, tal cual llegaron
-- (payload) y con los campos ya identificados. p_filas es un arreglo JSON de objetos
-- {payload, tipo_documento, nro_documento, nombres, apellidos, razon_social, email,
-- telefono, direccion}. Devuelve cuántos se guardaron.
CREATE FUNCTION sp_stage_cargar(p_lote integer, p_filas jsonb)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_base integer;
    v_cuantos integer;
BEGIN
    IF jsonb_typeof(p_filas) IS DISTINCT FROM 'array' THEN
        PERFORM sp_error('datos_invalidos', 'Los clientes deben enviarse como una lista.', 400);
    END IF;
    PERFORM 1 FROM integracion_lote WHERE id = p_lote AND estado = 'en_proceso' FOR UPDATE;
    IF NOT FOUND THEN
        PERFORM sp_error('lote_cerrado', 'El lote no existe o ya fue procesado.', 409);
    END IF;
    SELECT coalesce(max(fila), 0) INTO v_base FROM cliente_stage WHERE lote_id = p_lote;

    INSERT INTO cliente_stage (
        lote_id, fila, payload, tipo_documento, nro_documento, nombres, apellidos, razon_social,
        email, telefono, direccion, estado, fecha_carga
    )
    SELECT p_lote, v_base + t.orden::integer, t.f -> 'payload',
           left(t.f ->> 'tipo_documento', 20), left(t.f ->> 'nro_documento', 20),
           left(t.f ->> 'nombres', 200), left(t.f ->> 'apellidos', 200), left(t.f ->> 'razon_social', 200),
           left(t.f ->> 'email', 200), left(t.f ->> 'telefono', 40), left(t.f ->> 'direccion', 240),
           'pendiente', now()
    FROM jsonb_array_elements(p_filas) WITH ORDINALITY AS t(f, orden);
    GET DIAGNOSTICS v_cuantos = ROW_COUNT;

    UPDATE integracion_lote SET total = total + v_cuantos WHERE id = p_lote;
    RETURN v_cuantos;
END
$$;

-- Pasa los registros pendientes de la tabla stage al maestro de clientes.
-- Criterio de unicidad: tipo + número de documento.
--   rechazado  : documento inválido o sin nombre
--   duplicado  : el mismo cliente vino repetido en el envío
--   insertado  : cliente nuevo
--   actualizado: ya existía y se completaron datos vacíos (no se pisan los nuestros)
--   sin_cambios: ya existía con los mismos datos
CREATE FUNCTION sp_stage_procesar(p_lote integer)
RETURNS TABLE (insertados integer, actualizados integer, sin_cambios integer, duplicados integer, rechazados integer)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_origen text;
    r cliente_stage;
    v_tipo text;
    v_nro text;
    v_error text;
    v_res record;
BEGIN
    SELECT origen INTO v_origen FROM integracion_lote WHERE id = p_lote FOR UPDATE;
    IF NOT FOUND THEN
        PERFORM sp_error('lote_no_encontrado', 'El lote no existe.', 404);
    END IF;

    FOR r IN SELECT * FROM cliente_stage WHERE lote_id = p_lote AND estado = 'pendiente' ORDER BY fila, id LOOP
        v_tipo := sp_tipo_documento(r.tipo_documento);
        v_nro := sp_limpiar_documento(r.nro_documento);
        -- Sin tipo pero con número: se deduce por la cantidad de dígitos (8 DNI, 11 RUC).
        IF v_tipo IS NULL AND coalesce(r.tipo_documento, '') = '' AND v_nro ~ '^[0-9]{8}$' THEN
            v_tipo := 'DNI';
        ELSIF v_tipo IS NULL AND coalesce(r.tipo_documento, '') = '' AND v_nro ~ '^[0-9]{11}$' THEN
            v_tipo := 'RUC';
        END IF;
        v_error := sp_documento_error(v_tipo, v_nro);
        IF v_error IS NULL
           AND coalesce(sp_texto(r.nombres, 120), sp_texto(r.apellidos, 160), sp_texto(r.razon_social, 200)) IS NULL THEN
            v_error := 'El registro no tiene nombres ni razón social';
        END IF;

        IF v_error IS NOT NULL THEN
            UPDATE cliente_stage SET estado = 'rechazado', mensaje = v_error WHERE id = r.id;
            CONTINUE;
        END IF;

        UPDATE cliente_stage SET tipo_documento = v_tipo, nro_documento = v_nro WHERE id = r.id;

        IF EXISTS (
            SELECT 1 FROM cliente_stage s
            WHERE s.lote_id = p_lote AND s.id <> r.id
              AND s.estado IN ('insertado', 'actualizado', 'sin_cambios')
              AND s.tipo_documento = v_tipo AND s.nro_documento = v_nro
        ) THEN
            UPDATE cliente_stage SET estado = 'duplicado', mensaje = 'Vino repetido en el mismo envío'
            WHERE id = r.id;
            CONTINUE;
        END IF;

        BEGIN
            SELECT * INTO v_res FROM sp_cliente_registrar(
                v_tipo, v_nro, r.nombres, r.apellidos, r.razon_social, r.email, r.telefono, r.direccion,
                'integracion', v_origen, false
            );
            UPDATE cliente_stage SET
                estado = v_res.accion,
                cliente_id = v_res.cliente_id,
                mensaje = CASE v_res.accion
                    WHEN 'insertado' THEN 'Cliente nuevo registrado'
                    WHEN 'actualizado' THEN 'Ya existía: se completaron datos vacíos'
                    ELSE 'Ya existía con los mismos datos'
                END
            WHERE id = r.id;
        EXCEPTION WHEN OTHERS THEN
            UPDATE cliente_stage SET estado = 'rechazado', mensaje = left(coalesce(nullif(PG_EXCEPTION_DETAIL, ''), SQLERRM), 200)
            WHERE id = r.id;
        END;
    END LOOP;

    SELECT count(*) FILTER (WHERE s.estado = 'insertado'),
           count(*) FILTER (WHERE s.estado = 'actualizado'),
           count(*) FILTER (WHERE s.estado = 'sin_cambios'),
           count(*) FILTER (WHERE s.estado = 'duplicado'),
           count(*) FILTER (WHERE s.estado = 'rechazado')
    INTO insertados, actualizados, sin_cambios, duplicados, rechazados
    FROM cliente_stage s WHERE s.lote_id = p_lote;

    UPDATE integracion_lote l SET
        estado = 'completado',
        insertados = sp_stage_procesar.insertados,
        actualizados = sp_stage_procesar.actualizados,
        sin_cambios = sp_stage_procesar.sin_cambios,
        duplicados = sp_stage_procesar.duplicados,
        rechazados = sp_stage_procesar.rechazados,
        mensaje = 'Procesado: ' || sp_stage_procesar.insertados || ' nuevos, '
                  || sp_stage_procesar.actualizados || ' completados, '
                  || sp_stage_procesar.rechazados || ' rechazados',
        fecha_fin = now()
    WHERE l.id = p_lote;
    RETURN NEXT;
END
$$;

-- Cierra un lote que no se pudo completar (por ejemplo, la API del otro grupo no respondió).
CREATE FUNCTION sp_lote_cerrar(p_lote integer, p_estado text, p_mensaje text, p_intentos integer)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_estado NOT IN ('completado', 'error') THEN
        PERFORM sp_error('estado_invalido', 'Estado de lote no válido.', 400);
    END IF;
    UPDATE integracion_lote SET
        estado = CASE WHEN estado = 'en_proceso' THEN p_estado ELSE estado END,
        mensaje = coalesce(sp_texto(p_mensaje, 300), mensaje),
        intentos = coalesce(p_intentos, intentos),
        fecha_fin = coalesce(fecha_fin, now())
    WHERE id = p_lote;
END
$$;

-- Registra la cantidad de intentos que tomó leer la API del otro grupo.
CREATE FUNCTION sp_lote_intentos(p_lote integer, p_intentos integer)
RETURNS void
LANGUAGE sql
AS $$
    UPDATE integracion_lote SET intentos = p_intentos WHERE id = p_lote
$$;

CREATE FUNCTION sp_lote_json(l integracion_lote)
RETURNS jsonb
LANGUAGE sql STABLE
AS $$
    SELECT to_jsonb(l) || jsonb_build_object(
        'usuario_nombre', (SELECT btrim(concat_ws(' ', u.nombres, u.apellido_paterno)) FROM usuario u WHERE u.id = l.usuario_id)
    )
$$;

CREATE FUNCTION sp_lotes_listar(p_limite integer)
RETURNS TABLE (lote jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT sp_lote_json(l) FROM integracion_lote l
    ORDER BY l.id DESC
    LIMIT least(greatest(coalesce(p_limite, 30), 1), 200)
$$;

CREATE FUNCTION sp_lote_obtener(p_lote integer)
RETURNS TABLE (lote jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT sp_lote_json(l) FROM integracion_lote l WHERE l.id = p_lote
$$;

-- Registros de la tabla stage de un lote (con lo que llegó y lo que se hizo con cada uno).
CREATE FUNCTION sp_stage_listar(p_lote integer, p_estado text)
RETURNS TABLE (registro jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT to_jsonb(s) FROM cliente_stage s
    WHERE s.lote_id = p_lote AND (p_estado IS NULL OR s.estado = p_estado)
    ORDER BY s.fila, s.id
    LIMIT 2000
$$;

-- Traza del API: cada llamada entrante o saliente queda registrada.
CREATE FUNCTION sp_traza_registrar(
    p_direccion text, p_sistema text, p_metodo text, p_endpoint text, p_estado_http integer,
    p_exito boolean, p_intento integer, p_duracion_ms integer, p_registros integer,
    p_mensaje text, p_ip text, p_lote integer
)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_id integer;
BEGIN
    INSERT INTO api_traza (
        direccion, sistema, metodo, endpoint, estado_http, exito, intento, duracion_ms,
        registros, mensaje, ip, lote_id, fecha
    )
    VALUES (
        CASE WHEN p_direccion = 'salida' THEN 'salida' ELSE 'entrada' END,
        sp_texto(p_sistema, 80), left(coalesce(p_metodo, 'GET'), 8), left(coalesce(p_endpoint, ''), 300),
        p_estado_http, coalesce(p_exito, false), coalesce(p_intento, 1), p_duracion_ms, p_registros,
        sp_texto(p_mensaje, 300), sp_texto(p_ip, 64), p_lote, now()
    )
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE FUNCTION sp_traza_listar(p_direccion text, p_limite integer)
RETURNS TABLE (traza jsonb)
LANGUAGE sql STABLE
AS $$
    SELECT to_jsonb(t) FROM api_traza t
    WHERE p_direccion IS NULL OR t.direccion = p_direccion
    ORDER BY t.id DESC
    LIMIT least(greatest(coalesce(p_limite, 100), 1), 500)
$$;


-- =====================================================================================
-- 4. Devolución de pedidos online entregados
-- =====================================================================================

-- Registra la devolución de productos de un pedido ya entregado y reingresa el stock.
--   p_items: [{"pedido_detalle_id": 1, "cantidad": 1, "reingresa_stock": true}, ...]
-- Reglas: pedido entregado, boleta aceptada por SUNAT, dentro del plazo desde la entrega,
-- sin devolver más de lo comprado, reembolso por el mismo medio de pago (PagoEfectivo se
-- devuelve por transferencia), importe proporcional a lo cobrado (incluye promociones y
-- cupón). Devuelve el id de la devolución; la nota de crédito la emite el backend.
CREATE FUNCTION sp_pedido_devolver(
    p_pedido_id integer, p_usuario_id integer, p_autorizada_por integer, p_motivo text,
    p_metodo text, p_nro_operacion text, p_items jsonb, p_dias_max integer, p_zona_horas integer
)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_pedido pedido;
    v_metodo text;
    v_nro text := upper(regexp_replace(coalesce(p_nro_operacion, ''), '\s', '', 'g'));
    v_factor numeric;
    v_suma numeric;
    v_ya_devuelto numeric;
    v_item jsonb;
    v_det pedido_detalle;
    v_cantidad integer;
    v_disponible integer;
    v_ids integer[] := '{}';
    v_cantidades integer[] := '{}';
    v_montos numeric[] := '{}';
    v_reingresa boolean[] := '{}';
    v_completa boolean;
    v_total numeric;
    v_dev_id integer;
    v_codigo text;
    v_stock integer;
    i integer;
BEGIN
    SELECT * INTO v_pedido FROM pedido WHERE id = p_pedido_id FOR UPDATE;
    IF NOT FOUND THEN
        PERFORM sp_error('pedido_no_encontrado', 'El pedido no existe.', 404);
    END IF;
    IF v_pedido.estado <> 'entregado' THEN
        PERFORM sp_error('estado_invalido',
            'Solo se devuelven pedidos ya entregados. Si aún no se entregó, cancélelo.', 409);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pedido_comprobante
        WHERE pedido_id = p_pedido_id AND tipo = 'boleta' AND estado_sunat = 'aceptado'
    ) THEN
        PERFORM sp_error('comprobante_requerido',
            'El pedido no tiene boleta aceptada por SUNAT. Emita la boleta antes de la devolución.', 409);
    END IF;
    IF ((now() AT TIME ZONE 'UTC') + make_interval(hours => p_zona_horas))::date
       - ((v_pedido.fecha_entrega AT TIME ZONE 'UTC') + make_interval(hours => p_zona_horas))::date > p_dias_max THEN
        PERFORM sp_error('plazo_vencido',
            'El plazo de devolución es de ' || p_dias_max || ' días desde la entrega.', 409);
    END IF;
    IF length(btrim(coalesce(p_motivo, ''))) < 10 THEN
        PERFORM sp_error('motivo_requerido', 'Describa el motivo de la devolución (mínimo 10 caracteres).', 400);
    END IF;

    v_metodo := CASE v_pedido.metodo_pago WHEN 'pagoefectivo' THEN 'transferencia' ELSE v_pedido.metodo_pago END;
    IF lower(coalesce(p_metodo, '')) <> v_metodo THEN
        PERFORM sp_error('metodo_invalido',
            'El reembolso se hace por el mismo medio de pago del pedido (' || v_metodo || ').', 400);
    END IF;
    IF v_nro !~ '^[A-Z0-9-]{4,30}$' THEN
        PERFORM sp_error('operacion_requerida', 'Ingrese el N° de operación del extorno (4 a 30 caracteres).', 400);
    END IF;
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array' OR jsonb_array_length(p_items) = 0 THEN
        PERFORM sp_error('datos_invalidos', 'Seleccione al menos un producto a devolver.', 400);
    END IF;

    SELECT coalesce(sum(subtotal), 0) INTO v_suma FROM pedido_detalle WHERE pedido_id = p_pedido_id;
    v_factor := CASE WHEN v_suma > 0 THEN v_pedido.total / v_suma ELSE 1 END;

    FOR v_item IN SELECT * FROM jsonb_array_elements(p_items) LOOP
        BEGIN
            v_cantidad := coalesce((v_item ->> 'cantidad')::integer, 0);
        EXCEPTION WHEN OTHERS THEN
            PERFORM sp_error('datos_invalidos', 'La cantidad a devolver no es válida.', 400);
        END;
        CONTINUE WHEN v_cantidad <= 0;

        SELECT * INTO v_det FROM pedido_detalle
        WHERE id = (v_item ->> 'pedido_detalle_id')::integer AND pedido_id = p_pedido_id;
        IF NOT FOUND OR v_det.id = ANY (v_ids) THEN
            PERFORM sp_error('datos_invalidos', 'Un producto no pertenece al pedido o está repetido.', 400);
        END IF;
        SELECT v_det.cantidad - coalesce(sum(dd.cantidad), 0) INTO v_disponible
        FROM pedido_devolucion_detalle dd WHERE dd.pedido_detalle_id = v_det.id;
        IF v_cantidad > v_disponible THEN
            PERFORM sp_error('cantidad_invalida',
                'De ' || coalesce((SELECT nombre FROM producto WHERE id = v_det.producto_id), 'el producto')
                || ' solo quedan ' || v_disponible || ' unidad(es) por devolver.', 409);
        END IF;

        v_ids := v_ids || v_det.id;
        v_cantidades := v_cantidades || v_cantidad;
        v_montos := v_montos || round(v_det.subtotal / v_det.cantidad * v_cantidad * v_factor, 2);
        v_reingresa := v_reingresa || coalesce((v_item ->> 'reingresa_stock')::boolean, true);
    END LOOP;

    IF cardinality(v_ids) = 0 THEN
        PERFORM sp_error('datos_invalidos', 'Indique la cantidad a devolver.', 400);
    END IF;

    -- ¿Con esta devolución vuelve todo el pedido? Entonces la última línea absorbe los
    -- céntimos para que lo devuelto sume exactamente lo cobrado.
    SELECT bool_and(
               d.cantidad <= coalesce((SELECT sum(dd.cantidad) FROM pedido_devolucion_detalle dd
                                       WHERE dd.pedido_detalle_id = d.id), 0)
                             + coalesce(v_cantidades[array_position(v_ids, d.id)], 0)
           )
    INTO v_completa
    FROM pedido_detalle d WHERE d.pedido_id = p_pedido_id;

    SELECT coalesce(sum(total), 0) INTO v_ya_devuelto FROM pedido_devolucion WHERE pedido_id = p_pedido_id;
    IF v_completa THEN
        v_montos[cardinality(v_montos)] := v_montos[cardinality(v_montos)]
            + (v_pedido.total - v_ya_devuelto) - (SELECT sum(x) FROM unnest(v_montos) AS x);
    END IF;
    SELECT sum(x) INTO v_total FROM unnest(v_montos) AS x;
    IF v_total <= 0 THEN
        PERFORM sp_error('monto_invalido', 'El importe a devolver debe ser mayor a cero.', 400);
    END IF;

    INSERT INTO pedido_devolucion (
        codigo, pedido_id, usuario_id, autorizada_por_id, fecha, motivo, cod_motivo, total,
        metodo_reembolso, nro_operacion, estado_sunat, sunat_intentos
    )
    VALUES (
        'TMP-' || left(md5(random()::text || clock_timestamp()::text), 16), p_pedido_id, p_usuario_id, p_autorizada_por,
        now(), left(btrim(p_motivo), 240),
        CASE WHEN v_completa AND v_ya_devuelto = 0 THEN '06' ELSE '07' END,
        v_total, v_metodo, v_nro, 'pendiente', 0
    )
    RETURNING id INTO v_dev_id;
    v_codigo := 'DW-' || lpad(v_dev_id::text, 6, '0');
    UPDATE pedido_devolucion SET codigo = v_codigo WHERE id = v_dev_id;

    FOR i IN 1 .. cardinality(v_ids) LOOP
        INSERT INTO pedido_devolucion_detalle (devolucion_id, pedido_detalle_id, cantidad, monto, reingresa_stock)
        VALUES (v_dev_id, v_ids[i], v_cantidades[i], v_montos[i], v_reingresa[i]);

        IF v_reingresa[i] THEN
            SELECT p.stock INTO v_stock FROM producto p
            WHERE p.id = (SELECT producto_id FROM pedido_detalle WHERE id = v_ids[i])
            FOR UPDATE;
            UPDATE producto SET stock = coalesce(v_stock, 0) + v_cantidades[i]
            WHERE id = (SELECT producto_id FROM pedido_detalle WHERE id = v_ids[i]);
            INSERT INTO movimiento_stock (
                producto_id, tipo, cantidad, stock_anterior, stock_nuevo, motivo, usuario_id, fecha
            )
            VALUES (
                (SELECT producto_id FROM pedido_detalle WHERE id = v_ids[i]), 'devolucion', v_cantidades[i],
                coalesce(v_stock, 0), coalesce(v_stock, 0) + v_cantidades[i],
                left('Devolución ' || v_codigo || ' del pedido online ' || v_pedido.codigo, 240),
                p_usuario_id, now()
            );
        END IF;
    END LOOP;

    RETURN v_dev_id;
END
$$;


-- =====================================================================================
-- 5. Triggers
-- =====================================================================================

-- Normaliza los datos del maestro y actualiza la fecha de modificación solo si algo cambió
-- (la fecha sirve para que el otro grupo pida solo lo nuevo).
CREATE FUNCTION tg_maestro_cliente_normalizar()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.nro_documento := sp_limpiar_documento(NEW.nro_documento);
    NEW.email := lower(sp_texto(NEW.email, 160));
    NEW.nombres := sp_texto(NEW.nombres, 120);
    NEW.apellidos := sp_texto(NEW.apellidos, 160);
    NEW.razon_social := sp_texto(NEW.razon_social, 200);
    NEW.telefono := sp_texto(NEW.telefono, 20);
    NEW.direccion := sp_texto(NEW.direccion, 240);
    IF TG_OP = 'INSERT' THEN
        NEW.fecha_registro := coalesce(NEW.fecha_registro, now());
        NEW.fecha_actualizacion := coalesce(NEW.fecha_actualizacion, now());
    ELSIF (NEW.tipo_documento, NEW.nro_documento, NEW.nombres, NEW.apellidos, NEW.razon_social,
           NEW.email, NEW.telefono, NEW.direccion, NEW.activo)
          IS DISTINCT FROM
          (OLD.tipo_documento, OLD.nro_documento, OLD.nombres, OLD.apellidos, OLD.razon_social,
           OLD.email, OLD.telefono, OLD.direccion, OLD.activo) THEN
        NEW.fecha_actualizacion := now();
    ELSE
        NEW.fecha_actualizacion := OLD.fecha_actualizacion;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_maestro_cliente_normalizar
BEFORE INSERT OR UPDATE ON maestro_cliente
FOR EACH ROW EXECUTE FUNCTION tg_maestro_cliente_normalizar();

-- Cada cuenta de la tienda online con documento pasa al maestro (sus datos son propios).
-- Si algo falla, la cuenta se guarda igual: el error solo queda como aviso en el log.
CREATE FUNCTION tg_cliente_tienda_maestro()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.tipo_documento IS NOT NULL AND NEW.nro_documento IS NOT NULL
       AND coalesce(NEW.nombres, NEW.apellidos) IS NOT NULL THEN
        BEGIN
            PERFORM sp_cliente_registrar(
                NEW.tipo_documento, NEW.nro_documento, NEW.nombres, NEW.apellidos, NULL,
                NEW.email, NEW.celular, NULL, 'tienda', 'Cuenta de la tienda online', true
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Maestro de clientes: no se registró la cuenta %: %', NEW.id, SQLERRM;
        END;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_cliente_tienda_maestro
AFTER INSERT OR UPDATE OF tipo_documento, nro_documento, nombres, apellidos, celular, email ON cliente
FOR EACH ROW EXECUTE FUNCTION tg_cliente_tienda_maestro();

-- Cada comprador de la caja (DNI, CE o RUC con nombre de RENIEC/SUNAT) pasa al maestro.
-- Solo completa datos vacíos: no reemplaza nombres y apellidos ya separados.
CREATE FUNCTION tg_venta_maestro()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_nro text := sp_limpiar_documento(NEW.cliente_documento);
BEGIN
    IF v_nro IS NOT NULL AND sp_texto(NEW.cliente_nombre, 200) IS NOT NULL THEN
        BEGIN
            PERFORM sp_cliente_registrar(
                sp_tipo_por_longitud(v_nro), v_nro,
                CASE WHEN length(v_nro) <> 11 THEN NEW.cliente_nombre END, NULL,
                CASE WHEN length(v_nro) = 11 THEN NEW.cliente_nombre END,
                NULL, NULL, NULL, 'caja', 'Venta en caja', false
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Maestro de clientes: no se registró el comprador de la venta %: %', NEW.id, SQLERRM;
        END;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_venta_maestro
AFTER INSERT OR UPDATE OF cliente_documento, cliente_nombre ON venta
FOR EACH ROW EXECUTE FUNCTION tg_venta_maestro();


-- =====================================================================================
-- 6. Gestor documental de comprobantes (PDF, XML y CDR de la SUNAT)
-- =====================================================================================

-- Registra (o actualiza) un archivo guardado en comprobantes/<tipo>/<AAAA>/<MM>/<DD>/.
-- La ruta es única: volver a guardar el mismo comprobante actualiza su huella y su copia en la nube.
CREATE FUNCTION sp_archivo_registrar(p_datos jsonb)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_id integer;
    v_ruta text := sp_texto(p_datos->>'ruta', 300);
BEGIN
    IF v_ruta IS NULL OR v_ruta LIKE '%..%' OR left(v_ruta, 1) = '/' THEN
        PERFORM sp_error('ruta_invalida', 'La ruta del archivo no es válida.', 400);
    END IF;
    IF coalesce(p_datos->>'sha256', '') !~ '^[0-9a-f]{64}$' THEN
        PERFORM sp_error('huella_invalida', 'La huella SHA-256 del archivo no es válida.', 400);
    END IF;
    INSERT INTO comprobante_archivo (
        ruta, tipo, formato, serie, numero, fecha_emision, cliente_documento, cliente_nombre,
        origen, origen_codigo, bytes, sha256, en_nube, fecha_guardado
    )
    VALUES (
        v_ruta, p_datos->>'tipo', p_datos->>'formato', sp_texto(p_datos->>'serie', 10),
        sp_texto(p_datos->>'numero', 20), (p_datos->>'fecha_emision')::date,
        sp_texto(p_datos->>'cliente_documento', 15), sp_texto(p_datos->>'cliente_nombre', 200),
        sp_texto(p_datos->>'origen', 20), sp_texto(p_datos->>'origen_codigo', 20),
        coalesce((p_datos->>'bytes')::integer, 0), p_datos->>'sha256',
        coalesce((p_datos->>'en_nube')::boolean, false), now()
    )
    ON CONFLICT (ruta) DO UPDATE SET
        bytes = EXCLUDED.bytes,
        sha256 = EXCLUDED.sha256,
        en_nube = EXCLUDED.en_nube OR comprobante_archivo.en_nube AND comprobante_archivo.sha256 = EXCLUDED.sha256,
        cliente_documento = coalesce(EXCLUDED.cliente_documento, comprobante_archivo.cliente_documento),
        cliente_nombre = coalesce(EXCLUDED.cliente_nombre, comprobante_archivo.cliente_nombre),
        fecha_guardado = now()
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE FUNCTION sp_archivo_obtener(p_ruta text)
RETURNS SETOF comprobante_archivo
LANGUAGE sql STABLE
AS $$
    SELECT * FROM comprobante_archivo WHERE ruta = p_ruta
$$;

CREATE FUNCTION sp_archivo_por_id(p_id integer)
RETURNS SETOF comprobante_archivo
LANGUAGE sql STABLE
AS $$
    SELECT * FROM comprobante_archivo WHERE id = p_id
$$;

-- Árbol de carpetas: cuántos comprobantes y archivos hay por año, mes, día y tipo.
CREATE FUNCTION sp_comprobantes_arbol()
RETURNS TABLE (anio integer, mes integer, dia integer, tipo text, comprobantes integer, archivos integer,
               bytes bigint, en_nube integer)
LANGUAGE sql STABLE
AS $$
    SELECT extract(year FROM fecha_emision)::integer, extract(month FROM fecha_emision)::integer,
           extract(day FROM fecha_emision)::integer, tipo::text,
           count(DISTINCT coalesce(serie || '-' || numero, ruta))::integer, count(*)::integer,
           coalesce(sum(a.bytes), 0)::bigint, count(*) FILTER (WHERE a.en_nube)::integer
    FROM comprobante_archivo a
    GROUP BY 1, 2, 3, 4
    ORDER BY 1 DESC, 2 DESC, 3 DESC, 4
$$;

-- Archivos de un rango de fechas, con filtro por tipo y por cliente (documento o nombre) o número.
CREATE FUNCTION sp_comprobantes_listar(p_desde date, p_hasta date, p_tipo text, p_texto text)
RETURNS SETOF comprobante_archivo
LANGUAGE sql STABLE
AS $$
    SELECT * FROM comprobante_archivo
    WHERE (p_desde IS NULL OR fecha_emision >= p_desde)
      AND (p_hasta IS NULL OR fecha_emision <= p_hasta)
      AND (sp_texto(p_tipo, 15) IS NULL OR tipo = p_tipo)
      AND (sp_texto(p_texto, 80) IS NULL
           OR cliente_documento ILIKE '%' || p_texto || '%'
           OR cliente_nombre ILIKE '%' || p_texto || '%'
           OR (serie || '-' || numero) ILIKE '%' || p_texto || '%'
           OR origen_codigo ILIKE '%' || p_texto || '%')
    ORDER BY fecha_emision DESC, serie, numero, formato
    LIMIT 600
$$;

-- Marca un archivo como copiado a la nube (sincronización posterior).
CREATE FUNCTION sp_archivo_nube(p_ruta text)
RETURNS void
LANGUAGE sql
AS $$
    UPDATE comprobante_archivo SET en_nube = true WHERE ruta = p_ruta
$$;


-- =====================================================================================
-- 7. Seguridad de Supabase
-- =====================================================================================

-- Supabase publica las tablas de "public" en su API REST. Con RLS activo y sin políticas
-- esa API no puede leer ni escribir nada; el backend se conecta como dueño y no le afecta.
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;
END
$$;

-- Clientes que ya existían antes del maestro (idempotente).
SELECT sp_maestro_sincronizar();
