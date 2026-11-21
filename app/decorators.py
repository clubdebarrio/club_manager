# app/decorators.py
"""
Decoradores y helpers de autorización por permisos.

Los permisos son strings con formato `<modulo>.<accion>`, donde
`accion` es `ver` o `editar`.

Ejemplo:
    @requiere_ver('socios')      -> exige permiso 'socios.ver'
    @requiere_editar('socios')   -> exige permiso 'socios.editar'

El super admin (`es_super_admin=True`) tiene todos los permisos por
definición, sin importar el rol.
"""

from functools import wraps
from flask import session, redirect, url_for, flash, g, jsonify, request


def cargar_usuario_actual():
    """
    Devuelve el Usuario logueado. Se cachea en `g` durante el request.
    Devuelve None si no hay sesión activa o el usuario no existe.
    """
    if 'usuario_actual' in g:
        return g.usuario_actual

    usuario_id = session.get('usuario_id')
    if not usuario_id:
        g.usuario_actual = None
        return None

    from app.models import Usuario
    g.usuario_actual = Usuario.query.get(usuario_id)
    return g.usuario_actual


def _denegar():
    """
    Maneja el rechazo según el tipo de request:
      - Si es una request AJAX/JSON: devuelve 403 con JSON.
      - Si es HTML: flash + redirect al home.
    """
    # Detectar AJAX por header o por Accept
    acepta_json = request.accept_mimetypes.best == 'application/json'
    es_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    es_json_path = '/api/' in request.path

    if es_ajax or acepta_json or es_json_path:
        return jsonify({'error': 'No tenés permiso para esta acción.'}), 403

    flash('No tenés permiso para acceder a esta sección.', 'danger')
    return redirect(url_for('main.index'))


def requiere_permiso(clave):
    """Decorador que exige un permiso específico."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            usuario = cargar_usuario_actual()
            if not usuario:
                flash('Debes iniciar sesión.', 'warning')
                return redirect(url_for('auth.login'))
            if not usuario.tiene_permiso(clave):
                return _denegar()
            return view(*args, **kwargs)
        return wrapped
    return decorator


def requiere_ver(modulo):
    """Exige permiso `<modulo>.ver`."""
    return requiere_permiso(f'{modulo}.ver')


def requiere_editar(modulo):
    """Exige permiso `<modulo>.editar`."""
    return requiere_permiso(f'{modulo}.editar')


def requiere_super_admin(view):
    """Exige que el usuario sea super admin (Presidente)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        usuario = cargar_usuario_actual()
        if not usuario:
            flash('Debes iniciar sesión.', 'warning')
            return redirect(url_for('auth.login'))
        if not usuario.es_super_admin:
            return _denegar()
        return view(*args, **kwargs)
    return wrapped

def requiere_super_admin_sistema(view):
    """
    Exige que el usuario sea super admin del SISTEMA (dueño de la app).
    Es un nivel por encima de es_super_admin (presidente del club).
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        usuario = cargar_usuario_actual()
        if not usuario:
            flash('Debes iniciar sesión.', 'warning')
            return redirect(url_for('auth.login'))
        if not usuario.es_super_admin_sistema:
            flash('Acceso restringido.', 'danger')
            return redirect(url_for('main.index'))
        return view(*args, **kwargs)
    return wrapped