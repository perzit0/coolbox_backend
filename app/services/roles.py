"""Catálogo maestro de roles y permisos.

Un usuario elige uno de sus roles al iniciar sesión (ver blueprint auth).
Ese rol determina qué acciones puede ejecutar en la interfaz.
"""

# Permisos atómicos referenciados en el frontend por su código.
PERMISOS = [
    ("usuarios.ver", "Ver el listado de usuarios"),
    ("usuarios.crear", "Registrar nuevos usuarios"),
    ("usuarios.editar", "Editar datos y roles de usuarios"),
    ("usuarios.desactivar", "Desactivar o reactivar usuarios"),
    ("roles.ver", "Ver el catálogo de roles"),
    ("productos.ver", "Ver el catálogo de productos"),
    ("productos.crear", "Registrar nuevos productos"),
    ("productos.editar", "Editar productos existentes"),
    ("stock.actualizar", "Registrar entradas, salidas y ajustes de stock"),
    ("kardex.ver", "Consultar el kardex (historial de movimientos de stock)"),
    ("ventas.crear", "Registrar una nueva venta en tienda"),
    ("ventas.ver", "Consultar el historial de ventas"),
    ("ventas.anular", "Anular una venta ya emitida"),
    ("reportes.ver", "Consultar reportes de operación"),
    ("caja.operar", "Abrir y cerrar su propia caja (turno de ventas)"),
    ("caja.supervisar", "Controlar las cajas de todos los cajeros y sus arqueos"),
    ("promociones.gestionar", "Crear, programar y desactivar promociones (2x1, % de descuento, precio especial, cupones)"),
    ("pedidos.gestionar", "Ver los pedidos online de la tienda web y entregarlos en la sede"),
    ("caja.autorizar", "Autorizar con PIN, en la caja del cajero, acciones restringidas (anulaciones, descuentos, retiros, reimpresiones)"),
]

# Roles que existieron en versiones anteriores y deben retirarse de la BD.
ROLES_OBSOLETOS = {
    # codigo_obsoleto: codigo_de_reemplazo (para reasignar usuarios y ventas)
    "cajero": "vendedor",
    # El módulo se concentra en ventas y caja: las tareas de almacén
    # (entradas, salidas, ajustes y kardex) pasan al Administrador.
    "almacenero": None,
}

ROLES = [
    {
        "codigo": "administrador",
        "nombre": "Administrador",
        "descripcion": "Gestiona usuarios, catálogo, roles y accede a todas las operaciones del sistema.",
        "es_admin": True,
        "permisos": [
            "usuarios.ver", "usuarios.crear", "usuarios.editar", "usuarios.desactivar",
            "roles.ver",
            "productos.ver", "productos.crear", "productos.editar", "stock.actualizar",
            "kardex.ver",
            "ventas.crear", "ventas.ver", "ventas.anular",
            "reportes.ver",
            "caja.operar", "caja.supervisar", "caja.autorizar",
            "promociones.gestionar", "pedidos.gestionar",
        ],
    },
    {
        # El código interno se mantiene ("vendedor") para no romper usuarios ni ventas existentes.
        "codigo": "vendedor",
        "nombre": "Cajero",
        "descripcion": "Atiende al cliente en caja: registra la venta, cobra con cualquier medio de pago y entrega el comprobante. "
                       "Las acciones restringidas las aprueba un Supervisor de Caja con su PIN, sin salir de la sesión.",
        "es_admin": False,
        # Sin "ventas.anular" directo: el cajero anula solo con el PIN de un supervisor.
        "permisos": ["productos.ver", "ventas.crear", "ventas.ver", "caja.operar", "pedidos.gestionar"],
    },
    {
        "codigo": "supervisor",
        "nombre": "Supervisor de Caja",
        "descripcion": "Supervisa las cajas de la tienda: autoriza con su PIN anulaciones, descuentos, retiros y cierres con diferencia, "
                       "y revisa los arqueos.",
        "es_admin": False,
        "permisos": ["productos.ver", "kardex.ver", "ventas.ver", "ventas.anular", "reportes.ver",
                     "caja.supervisar", "caja.autorizar", "pedidos.gestionar"],
    },
]
