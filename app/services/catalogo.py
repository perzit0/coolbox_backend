"""Catálogo maestro de familias, subfamilias y marcas + generador de SKU.

ESTRUCTURA DEL SKU (8 dígitos)
==============================

    F F   S S   M M   C C
    └─┬─┘ └─┬─┘ └─┬─┘ └─┬─┘
      │     │     │     └── Correlativo (01-99) dentro de la misma
      │     │     │         familia + subfamilia + marca
      │     │     └──────── Marca (01-99), código global de la marca
      │     └────────────── Subfamilia (01-99) dentro de la familia
      └──────────────────── Familia (10, 20, ... 90)

Ejemplo: 10 02 04 01
    10 -> Familia Laptops
    02 -> Subfamilia Laptops gamer
    04 -> Marca HP
    01 -> Primer producto registrado con esa combinación
    => "10020401" = Laptop gamer HP Victus 15

Reglas:
- El SKU lo genera el sistema al registrar el producto; no se escribe a mano.
- Es inmutable: si se registró mal la familia o la marca, se desactiva el
  producto y se registra uno nuevo (así no se rompe el historial de ventas
  ni el kardex, que referencian el SKU).
- Las familias avanzan de 10 en 10 para dejar espacio a nuevas familias
  intermedias sin reordenar códigos.
"""
import re

FAMILIAS = [
    # codigo, nombre, descripcion, subfamilias [(codigo, nombre)]
    ("10", "Laptops", "Computadoras portátiles para hogar, oficina y gaming", [
        ("01", "Uso personal y oficina"),
        ("02", "Laptops gamer"),
        ("03", "Ultradelgadas y premium"),
    ]),
    ("20", "Celulares", "Smartphones de todas las gamas", [
        ("01", "Gama de entrada"),
        ("02", "Gama media"),
        ("03", "Gama alta"),
    ]),
    ("30", "Tablets", "Tablets, tablets para niños y tabletas gráficas", [
        ("01", "Tablets"),
        ("02", "Tablets para niños"),
        ("03", "Tabletas gráficas"),
    ]),
    ("40", "Televisores", "Smart TVs por tecnología de panel", [
        ("01", "LED HD / Full HD"),
        ("02", "LED 4K UHD"),
        ("03", "QLED / NanoCell / QNED"),
        ("04", "Mini LED / OLED"),
    ]),
    ("50", "Audio", "Audífonos, parlantes y barras de sonido", [
        ("01", "Audífonos on ear"),
        ("02", "Audífonos true wireless"),
        ("03", "Parlantes portátiles"),
        ("04", "Barras de sonido"),
    ]),
    ("60", "Smartwatch y Wearables", "Relojes inteligentes y bandas", [
        ("01", "Smartwatch"),
        ("02", "Smartband"),
    ]),
    ("70", "Gamer", "Consolas, consolas portátiles y accesorios gamer", [
        ("01", "Consolas"),
        ("02", "Consolas portátiles"),
        ("03", "Mandos y accesorios"),
        ("04", "Sillas gamer"),
    ]),
    ("80", "Accesorios de Cómputo", "Periféricos y monitores", [
        ("01", "Mouse"),
        ("02", "Teclados"),
        ("03", "Monitores"),
        ("04", "Headsets"),
    ]),
]

MARCAS = [
    ("01", "Apple"),
    ("02", "Samsung"),
    ("03", "Xiaomi"),
    ("04", "HP"),
    ("05", "Lenovo"),
    ("06", "Asus"),
    ("07", "LG"),
    ("08", "TCL"),
    ("09", "JBL"),
    ("10", "Sony"),
    ("11", "Logitech"),
    ("12", "Motorola"),
    ("13", "Honor"),
    ("14", "Huawei"),
    ("15", "Roadtrip"),
    ("16", "Teros"),
    ("17", "Coolbox Teraware"),
    ("18", "Microsoft"),
]

# Nombres de categorías de versiones anteriores -> código de familia nuevo
FAMILIAS_ANTERIORES = {
    "Laptops": "10",
    "Celulares": "20",
    "Televisores": "40",
    "Audio": "50",
    "Gaming": "70",
    "Accesorios": "80",
}

SKU_REGEX = re.compile(r"^\d{8}$")


def descomponer_sku(sku: str) -> dict | None:
    """Devuelve las 4 partes del SKU (o None si no es válido)."""
    if not sku or not SKU_REGEX.match(sku):
        return None
    return {
        "familia": sku[0:2],
        "subfamilia": sku[2:4],
        "marca": sku[4:6],
        "correlativo": sku[6:8],
    }


def prefijo_sku(familia, subfamilia, marca) -> str:
    return f"{familia.codigo}{subfamilia.codigo}{marca.codigo}"


def siguiente_sku(familia, subfamilia, marca, skus_existentes) -> str:
    """Calcula el siguiente SKU libre para la combinación indicada.

    `skus_existentes` es un iterable de SKUs que ya empiezan con el prefijo.
    Se toma el correlativo máximo + 1 (no se reutilizan huecos, para que un
    SKU dado de baja no vuelva a apuntar a otro producto).
    """
    prefijo = prefijo_sku(familia, subfamilia, marca)
    maximo = 0
    for sku in skus_existentes:
        if sku and sku.startswith(prefijo) and SKU_REGEX.match(sku):
            maximo = max(maximo, int(sku[6:8]))
    siguiente = maximo + 1
    if siguiente > 99:
        raise ValueError(
            f"Se alcanzó el máximo de 99 productos para el prefijo {prefijo}."
        )
    return f"{prefijo}{siguiente:02d}"
