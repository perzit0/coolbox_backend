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
    ("stock.actualizar", "Actualizar el stock del almacén"),
    ("ventas.crear", "Registrar una nueva venta en tienda"),
    ("ventas.ver", "Consultar el historial de ventas"),
    ("ventas.anular", "Anular una venta ya emitida"),
    ("reportes.ver", "Consultar reportes de operación"),
]

# Roles que existieron en versiones anteriores y deben retirarse de la BD.
ROLES_OBSOLETOS = {
    # codigo_obsoleto: codigo_de_reemplazo (para reasignar usuarios y ventas)
    "cajero": "vendedor",
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
            "ventas.crear", "ventas.ver", "ventas.anular",
            "reportes.ver",
        ],
    },
    {
        "codigo": "vendedor",
        "nombre": "Vendedor",
        "descripcion": "Atiende al cliente en tienda, registra la venta, cobra y entrega el comprobante.",
        "es_admin": False,
        # Sin "ventas.anular": solo el Supervisor (y el Administrador) anulan ventas.
        "permisos": ["productos.ver", "ventas.crear", "ventas.ver"],
    },
    {
        "codigo": "almacenero",
        "nombre": "Almacenero",
        "descripcion": "Recepciona mercadería, actualiza el stock y controla el inventario de tienda.",
        "es_admin": False,
        "permisos": ["productos.ver", "productos.editar", "stock.actualizar"],
    },
    {
        "codigo": "supervisor",
        "nombre": "Supervisor de Ventas",
        "descripcion": "Supervisa las ventas de tienda, revisa reportes y es el único rol de tienda que puede anular ventas.",
        "es_admin": False,
        "permisos": ["productos.ver", "ventas.ver", "ventas.anular", "reportes.ver"],
    },
]
