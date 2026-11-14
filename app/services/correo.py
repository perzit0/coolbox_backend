"""Envío de correos a los clientes (código de acceso y confirmación de pedido).

Usa SMTP. Con Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=tu cuenta
y SMTP_PASSWORD = una "contraseña de aplicación" (no la contraseña normal).
Si SMTP no está configurado, el envío no está disponible y la tienda oculta
la opción "Código por correo".
"""
import smtplib
from email.message import EmailMessage

from flask import current_app


def disponible():
    c = current_app.config
    return bool(c.get("SMTP_HOST") and c.get("SMTP_USER") and c.get("SMTP_PASSWORD"))


def enviar(destino, asunto, texto, html=None):
    c = current_app.config
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = c.get("SMTP_FROM") or c["SMTP_USER"]
    msg["To"] = destino
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(c["SMTP_HOST"], c["SMTP_PORT"], timeout=15) as s:
        s.starttls()
        s.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
        s.send_message(msg)
