# app/routes/auditoria.py
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    current_app,
)
from sqlalchemy import or_

from app import db
from app.models import AuditLog, Usuario
from app.decorators import requiere_ver
from app.utils import current_empresa


auditoria_bp = Blueprint('auditoria', __name__, url_prefix='/auditoria')


@auditoria_bp.route('/')
@requiere_ver('auditoria')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 30)

    # --- Filtros ---
    filtro_usuario = request.args.get('usuario_id', type=int)
    filtro_entidad = (request.args.get('entidad') or '').strip()
    filtro_accion = (request.args.get('accion') or '').strip()
    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    q = (request.args.get('q') or '').strip()

    query = AuditLog.query.filter_by(id_empresa=empresa.id)

    if filtro_usuario:
        query = query.filter(AuditLog.id_usuario == filtro_usuario)
    if filtro_entidad:
        query = query.filter(AuditLog.entidad == filtro_entidad)
    if filtro_accion:
        query = query.filter(AuditLog.accion == filtro_accion)

    if fecha_desde_str:
        try:
            fd = datetime.strptime(fecha_desde_str, '%Y-%m-%d')
            query = query.filter(AuditLog.timestamp >= fd)
        except ValueError:
            pass

    if fecha_hasta_str:
        try:
            fh = datetime.strptime(fecha_hasta_str, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(AuditLog.timestamp < fh)
        except ValueError:
            pass

    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            AuditLog.nota.ilike(like),
            AuditLog.entidad.ilike(like),
            AuditLog.accion.ilike(like),
        ))

    pagination = query.order_by(
        AuditLog.timestamp.desc(), AuditLog.id.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    # --- Datos para los dropdowns ---
    usuarios = Usuario.query.filter_by(
        id_empresa=empresa.id
    ).order_by(Usuario.username).all()

    entidades_disponibles = [
        r[0] for r in db.session.query(AuditLog.entidad)
        .filter_by(id_empresa=empresa.id)
        .distinct()
        .order_by(AuditLog.entidad)
        .all()
    ]
    acciones_disponibles = [
        r[0] for r in db.session.query(AuditLog.accion)
        .filter_by(id_empresa=empresa.id)
        .distinct()
        .order_by(AuditLog.accion)
        .all()
    ]

    return render_template(
        'auditoria/listar.html',
        registros=pagination.items,
        pagination=pagination,
        usuarios=usuarios,
        entidades_disponibles=entidades_disponibles,
        acciones_disponibles=acciones_disponibles,
        filtro_usuario=filtro_usuario,
        filtro_entidad=filtro_entidad,
        filtro_accion=filtro_accion,
        fecha_desde=fecha_desde_str,
        fecha_hasta=fecha_hasta_str,
        q=q,
    )