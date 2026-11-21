# app/auditoria.py
"""
Helper para registrar acciones en el log de auditoría.

Uso:
    from app.auditoria import auditar

    # Dentro de una ruta, antes del commit:
    auditar('crear', 'Socio', nuevo_socio.id, despues=nuevo_socio)

    # Para ediciones, guardar el estado antes y después:
    auditar('editar', 'Socio', socio.id, antes=socio_viejo, despues=socio)

    # Para login (no hay entidad_id todavía):
    auditar('login', 'Usuario', usuario.id)

No commitea: se suma a la transacción en curso. Si la operación falla,
el log también se revierte.
"""
from decimal import Decimal
from datetime import datetime, date

from flask import session, request, has_request_context

from app import db
from app.models import AuditLog


# Campos que NUNCA se guardan en el log (aunque estén en el modelo).
CAMPOS_SENSIBLES = {
    'password_hash', 'password', 'access_token', 'refresh_token',
    'chytapay_cvu', 'chytapay_pago_id', 'cvv', 'tarjeta',
}


def _serializar(valor):
    """Convierte un valor a algo JSON-safe."""
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, dict):
        return {k: _serializar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializar(v) for v in valor]
    return valor


# Campos que se guardan como string JSON en la DB pero queremos
# exponer como estructura real en el audit log.
CAMPOS_JSON_EN_TEXTO = {'permisos'}


def _snapshot(entidad):
    """..."""
    if entidad is None:
        return None
    if isinstance(entidad, dict):
        return _serializar(entidad)

    resultado = {}
    for columna in entidad.__table__.columns:
        valor = getattr(entidad, columna.name, None)
        if columna.name in CAMPOS_JSON_EN_TEXTO and isinstance(valor, str):
            import json
            try:
                valor = json.loads(valor)
            except Exception:
                pass
        resultado[columna.name] = _serializar(valor)
    return resultado


def _limpiar_sensibles(datos):
    """Elimina campos sensibles de un snapshot."""
    if not datos:
        return datos
    return {k: v for k, v in datos.items() if k not in CAMPOS_SENSIBLES}


def auditar(accion, entidad, entidad_id=None, antes=None, despues=None,
            nota=None, id_empresa_forzada=None, id_usuario_forzado=None):
    """
    Registra una acción en el log de auditoría.

    Parámetros:
        accion: str
        entidad: str
        entidad_id: int o None
        antes: modelo, dict o None
        despues: modelo, dict o None
        nota: str opcional
        id_empresa_forzada: si se especifica, el log se guarda bajo esta
            empresa (útil para acciones del backoffice del sistema).
        id_usuario_forzado: si se especifica, se registra este usuario
            en lugar del de la sesión.
    """
    if not has_request_context():
        return

    id_empresa = id_empresa_forzada or session.get('id_empresa')
    if not id_empresa:
        return

    id_usuario = id_usuario_forzado or session.get('usuario_id')
    id_usuario_efectivo = session.get('usuario_original_id')

    antes_dict = _limpiar_sensibles(_snapshot(antes))
    despues_dict = _limpiar_sensibles(_snapshot(despues))

    registro = AuditLog(
        id_empresa=id_empresa,
        id_usuario=id_usuario,
        id_usuario_efectivo=id_usuario_efectivo,
        accion=accion,
        entidad=entidad,
        entidad_id=entidad_id,
        datos_antes=antes_dict,
        datos_despues=despues_dict,
        ip=request.remote_addr,
        user_agent=(request.headers.get('User-Agent') or '')[:300],
        nota=(nota or '')[:500] if nota else None,
    )
    db.session.add(registro)