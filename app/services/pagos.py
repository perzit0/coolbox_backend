"""Catálogos de medios de pago (los mismos para la caja y la tienda online)."""

# efectivo | tarjeta (POS) | qr (QR del POS) | yape | plin (directos)
METODOS_PAGO = ("efectivo", "tarjeta", "qr", "yape", "plin")
METODOS_ELECTRONICOS = ("tarjeta", "qr", "yape", "plin")
PROVEEDORES_POS = ("OpenPay", "Niubiz", "Izipay", "Culqi", "Otro")
MARCAS_TARJETA = ("Visa", "Mastercard", "American Express", "Diners Club")
TIPOS_TARJETA = ("debito", "credito")
