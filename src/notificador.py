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
from src.utils import formatear_presupuesto


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


def construir_email_licitacion(datos: dict) -> tuple[str, str]:
    """
    Construye el asunto y el cuerpo HTML de un email de notificación.

    Recibe uno de los diccionarios que devuelve
    db.obtener_matches_para_notificar(), con los datos ya planos.

    Args:
        datos: dict con titulo, organo, presupuesto, link, score, razon,
               nombre_cliente, nombre_perfil.

    Returns:
        (asunto, cuerpo_html) listos para pasar a enviar_email().
    """
    # — Asunto: corto y con el dato más llamativo (el título recortado) —
    titulo = datos.get("titulo") or "Licitación sin título"
    asunto = f"🐕 Nueva licitación relevante: {titulo[:60]}"

    # — Preparamos los valores, manejando los que pueden faltar —
    organo = datos.get("organo") or "No especificado"
    presupuesto_str = formatear_presupuesto(datos.get("presupuesto"))
    score = datos.get("score")
    score_str = f"{score}/100" if score is not None else "N/D"
    razon = datos.get("razon") or "Sin explicación disponible"
    link = datos.get("link")
    nombre_cliente = datos.get("nombre_cliente") or "cliente"
    nombre_perfil = datos.get("nombre_perfil") or ""

    # — Bloque del enlace: solo lo mostramos si existe —
    if link:
        bloque_link = (
            f'<p style="margin-top:20px;">'
            f'<a href="{link}" '
            f'style="background:#1a73e8;color:#fff;padding:10px 18px;'
            f'text-decoration:none;border-radius:5px;">'
            f'Ver licitación en la plataforma</a></p>'
        )
    else:
        bloque_link = ""

    # — Cuerpo HTML: tabla sencilla, sin CSS complejo (mejor entregabilidad) —
    cuerpo_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;color:#333;">
        <h2 style="color:#1a73e8;">Nueva licitación para ti</h2>
        <p>Hola {nombre_cliente},</p>
        <p>Hemos encontrado una licitación que encaja con tu perfil
           <strong>{nombre_perfil}</strong>:</p>

        <table style="width:100%;border-collapse:collapse;margin-top:15px;">
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;width:140px;">Título</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{titulo}</td>
            </tr>
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">Órgano</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{organo}</td>
            </tr>
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">Presupuesto</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{presupuesto_str}</td>
            </tr>
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">Relevancia IA</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{score_str}</td>
            </tr>
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;">Por qué</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{razon}</td>
            </tr>
        </table>

        {bloque_link}

        <p style="margin-top:25px;font-size:12px;color:#999;">
            Sabueso de Licitaciones · Notificación automática
        </p>
    </div>
    """

    return asunto, cuerpo_html


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