"""
src/notificador.py — Sabueso de Licitaciones
==============================================
Capa de abstracción para enviar notificaciones por email.

Igual que ia_client.py es la única puerta hacia Gemini, este módulo
es la única puerta hacia el proveedor de email (Resend). Si mañana
cambiamos de Resend a otro proveedor, solo se toca este fichero.

De momento contiene la función de bajo nivel enviar_email().
La lógica de construir el email de una licitación se añadirá después.
"""

from __future__ import annotations

from typing import Optional

import config
from src.logger import get_logger

logger = get_logger(__name__)


def _get_cliente_resend():
    """
    Configura y devuelve el módulo de Resend listo para usar.

    Lo hacemos aquí dentro (no al importar) para que el error de
    "API key no configurada" solo salte cuando de verdad se usa el envío,
    igual que hacemos en ia_client.py con Gemini.
    """
    try:
        import resend
    except ImportError:
        raise ImportError(
            "Falta instalar resend. Ejecuta: pip install resend"
        )

    if not config.RESEND_API_KEY:
        raise ValueError(
            "RESEND_API_KEY no está configurada. "
            "Añádela al fichero .env: RESEND_API_KEY=re_tu_clave"
        )

    # Resend se configura asignando la key a una variable del módulo.
    resend.api_key = config.RESEND_API_KEY
    return resend


def enviar_email(
    destinatario: str,
    asunto: str,
    cuerpo_html: str,
) -> Optional[str]:
    """
    Envía un email a través de Resend.

    Args:
        destinatario: Email de destino.
        asunto:       Asunto del email.
        cuerpo_html:  Contenido del email en HTML.

    Returns:
        El ID del email enviado si tuvo éxito, o None si falló.
    """
    # Si las notificaciones están desactivadas, no enviamos (modo prueba).
    if not config.EMAIL_NOTIFICACIONES_ACTIVO:
        logger.warning(
            "Notificaciones desactivadas (EMAIL_NOTIFICACIONES_ACTIVO=False). "
            "No se envía email a %s.", destinatario,
        )
        return None

    try:
        resend = _get_cliente_resend()

        respuesta = resend.Emails.send({
            "from": config.EMAIL_FROM,
            "to": destinatario,
            "subject": asunto,
            "html": cuerpo_html,
        })

        # La respuesta es un dict con el id del email enviado.
        email_id = respuesta.get("id") if isinstance(respuesta, dict) else None
        logger.info("Email enviado a %s (id=%s)", destinatario, email_id)
        return email_id

    except Exception as exc:
        logger.error("Error enviando email a %s: %s", destinatario, exc)
        return None