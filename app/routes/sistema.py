# app/routes/sistema.py
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify,
)
from sqlalchemy import func, or_

from app import db
from app.models import Empresa, Socio, Tarifa, Usuario, AuditLog
from app.decorators import requiere_super_admin_sistema
from app.auditoria import auditar


sistema_bp = Blueprint('sistema', __name__, url_prefix='/sistema')


# ============================================================
# Helpers
# ============================================================

def _contar_socios_activos(id_empresa):
    return Socio.query.filter(
        Socio.id_empresa == id_empresa,
        Socio.fecha_baja.is_(None),
    ).count()


def _ultimo_login(id_empresa):
    """Último login registrado en audit_log para la empresa."""
    ultimo = (
        AuditLog.query
        .filter_by(id_empresa=id_empresa, accion='login')
        .order_by(AuditLog.timestamp.desc())
        .first()
    )
    return ultimo.timestamp if ultimo else None


def _data_clubes():
    """Devuelve la lista de clubes con métricas para el listado."""
    clubes = (
        Empresa.query
        .filter_by(es_club_fantasma=False)
        .order_by(Empresa.nombre)
        .all()
    )
    filas = []
    for c in clubes:
        filas.append({
            'club': c,
            'socios_activos': _contar_socios_activos(c.id),
            'ultimo_login': _ultimo_login(c.id),
            'tarifa': c.tarifa,
        })
    return filas


def _recalcular_tarifa_sugerida(id_empresa):
    """Sugiere la tarifa según la cantidad de socios activos."""
    cantidad = _contar_socios_activos(id_empresa)
    tarifas = (
        Tarifa.query
        .filter_by(activo=True)
        .order_by(Tarifa.orden)
        .all()
    )
    for t in tarifas:
        if cantidad < t.socios_desde:
            continue
        if t.socios_hasta is None or cantidad <= t.socios_hasta:
            return t, cantidad
    return None, cantidad


# ============================================================
# Listado
# ============================================================

@sistema_bp.route('/')
@requiere_super_admin_sistema
def listar():
    q = (request.args.get('q') or '').strip()

    query = Empresa.query.filter_by(es_club_fantasma=False)
    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            Empresa.nombre.ilike(like),
            Empresa.cuit.ilike(like),
            Empresa.email.ilike(like),
        ))

    clubes = query.order_by(Empresa.nombre).all()

    filas = []
    for c in clubes:
        filas.append({
            'club': c,
            'socios_activos': _contar_socios_activos(c.id),
            'ultimo_login': _ultimo_login(c.id),
            'tarifa': c.tarifa,
        })

    # Estadísticas agregadas
    total_clubes = len(filas)
    al_dia = sum(1 for f in filas if f['club'].estado_pago == 'al_dia')
    trial = sum(1 for f in filas if f['club'].plan == 'trial')
    suspendidos = sum(1 for f in filas if f['club'].estado_pago == 'suspendido')
    trial_vencido = sum(1 for f in filas if f['club'].trial_vencido)

    stats = {
        'total': total_clubes,
        'al_dia': al_dia,
        'trial': trial,
        'suspendidos': suspendidos,
        'trial_vencido': trial_vencido,
    }

    return render_template(
        'sistema/listar.html',
        filas=filas,
        stats=stats,
        q=q,
    )


# ============================================================
# Detalle
# ============================================================

@sistema_bp.route('/club/<int:id>')
@requiere_super_admin_sistema
def detalle(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede acceder al club fantasma desde acá.', 'warning')
        return redirect(url_for('sistema.listar'))

    usuarios = (
        Usuario.query
        .filter_by(id_empresa=club.id)
        .order_by(Usuario.username)
        .all()
    )

    socios_activos = _contar_socios_activos(club.id)
    ultimo_login = _ultimo_login(club.id)

    # Tarifa sugerida según socios actuales
    tarifa_sugerida, cantidad = _recalcular_tarifa_sugerida(club.id)

    tarifas = Tarifa.query.filter_by(activo=True).order_by(Tarifa.orden).all()

    return render_template(
        'sistema/detalle.html',
        club=club,
        usuarios=usuarios,
        socios_activos=socios_activos,
        ultimo_login=ultimo_login,
        tarifa_sugerida=tarifa_sugerida,
        tarifas=tarifas,
    )


# ============================================================
# Acciones
# ============================================================

@sistema_bp.route('/club/<int:id>/extender-trial', methods=['POST'])
@requiere_super_admin_sistema
def extender_trial(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede modificar el club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    try:
        dias = int(request.form.get('dias', 0))
    except (TypeError, ValueError):
        dias = 0

    if dias <= 0 or dias > 365:
        flash('Días inválidos (debe ser entre 1 y 365).', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    base = club.fecha_vencimiento_prueba or datetime.utcnow()
    if base < datetime.utcnow():
        base = datetime.utcnow()

    anterior = club.fecha_vencimiento_prueba
    club.fecha_vencimiento_prueba = base + timedelta(days=dias)
    # Si extiende trial, vuelve a estado trial
    if club.plan == 'trial':
        club.estado_pago = 'trial'

    db.session.flush()
    auditar(
        'sistema:extender_trial', 'Empresa', club.id,
        id_empresa_forzada=club.id,
        nota=(
            f'Extendido {dias} días por sos_messi. '
            f'Nuevo vencimiento: {club.fecha_vencimiento_prueba.strftime("%d/%m/%Y")}'
        ),
    )
    db.session.commit()

    flash(f'Trial extendido {dias} días para "{club.nombre}".', 'success')
    return redirect(url_for('sistema.detalle', id=club.id))


@sistema_bp.route('/club/<int:id>/marcar-pago', methods=['POST'])
@requiere_super_admin_sistema
def marcar_pago(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede modificar el club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    plan = (request.form.get('plan') or '').strip()
    id_tarifa = request.form.get('id_tarifa', type=int)
    estado_pago = (request.form.get('estado_pago') or 'al_dia').strip()

    if plan not in ('trial', 'basico', 'pro'):
        flash('Plan inválido.', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    if plan != 'trial' and not id_tarifa:
        flash('Si el plan no es trial, hay que asignar una tarifa.', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    # --- Validación: trial único ---
    # Si intentan volver a trial, y el club ya lo usó, rechazar.
    if (plan == 'trial'
            and club.trial_usado
            and club.plan != 'trial'):
        flash(
            f'"{club.nombre}" ya usó su período de prueba. '
            f'No se puede volver a trial.',
            'danger',
        )
        return redirect(url_for('sistema.detalle', id=club.id))

    anterior = {
        'plan': club.plan,
        'estado_pago': club.estado_pago,
        'id_tarifa': club.id_tarifa,
    }

    # Si pasa de trial a un plan pago, marcar el trial como consumido
    if club.plan == 'trial' and plan != 'trial':
        club.trial_usado = True

    club.plan = plan
    club.estado_pago = estado_pago
    club.id_tarifa = id_tarifa if id_tarifa else None

    # Si pasa a plan pago, sacar el vencimiento de prueba
    if plan != 'trial':
        club.fecha_vencimiento_prueba = None

    db.session.flush()
    auditar(
        'sistema:marcar_pago', 'Empresa', club.id,
        antes=anterior,
        despues={
            'plan': club.plan,
            'estado_pago': club.estado_pago,
            'id_tarifa': club.id_tarifa,
        },
        id_empresa_forzada=club.id,
        nota=f'Plan actualizado por sos_messi: {plan} / {estado_pago}',
    )
    db.session.commit()

    flash(f'Plan de "{club.nombre}" actualizado.', 'success')
    return redirect(url_for('sistema.detalle', id=club.id))


@sistema_bp.route('/club/<int:id>/asignar-tarifa', methods=['POST'])
@requiere_super_admin_sistema
def asignar_tarifa(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede modificar el club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    id_tarifa = request.form.get('id_tarifa', type=int)
    if not id_tarifa:
        flash('Elegí una tarifa.', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    tarifa = Tarifa.query.get(id_tarifa)
    if not tarifa:
        flash('Tarifa no encontrada.', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    anterior_id = club.id_tarifa
    club.id_tarifa = tarifa.id

    # Actualizar el snapshot de socios al cierre
    club.socios_al_cierre = _contar_socios_activos(club.id)

    db.session.flush()
    auditar(
        'sistema:asignar_tarifa', 'Empresa', club.id,
        antes={'id_tarifa': anterior_id},
        despues={'id_tarifa': tarifa.id},
        id_empresa_forzada=club.id,
        nota=(
            f'Tarifa asignada: {tarifa.nombre} '
            f'(${tarifa.precio_mensual}/mes) con '
            f'{club.socios_al_cierre} socios activos'
        ),
    )
    db.session.commit()

    flash(f'Tarifa "{tarifa.nombre}" asignada a "{club.nombre}".', 'success')
    return redirect(url_for('sistema.detalle', id=club.id))


@sistema_bp.route('/club/<int:id>/suspender', methods=['POST'])
@requiere_super_admin_sistema
def suspender(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede modificar el club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    anterior = club.estado_pago
    club.estado_pago = 'suspendido'
    db.session.flush()
    auditar(
        'sistema:suspender', 'Empresa', club.id,
        antes={'estado_pago': anterior},
        despues={'estado_pago': 'suspendido'},
        id_empresa_forzada=club.id,
        nota='Suspendido por sos_messi',
    )
    db.session.commit()
    flash(f'Club "{club.nombre}" suspendido.', 'warning')
    return redirect(url_for('sistema.detalle', id=club.id))


@sistema_bp.route('/club/<int:id>/reactivar', methods=['POST'])
@requiere_super_admin_sistema
def reactivar(id):
    club = Empresa.query.get_or_404(id)
    if club.es_club_fantasma:
        flash('No se puede modificar el club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    anterior = club.estado_pago
    club.estado_pago = 'al_dia'
    # Si estaba en plan trial, mantenerlo; si no, mantener el plan
    db.session.flush()
    auditar(
        'sistema:reactivar', 'Empresa', club.id,
        antes={'estado_pago': anterior},
        despues={'estado_pago': 'al_dia'},
        id_empresa_forzada=club.id,
        nota='Reactivado por sos_messi',
    )
    db.session.commit()
    flash(f'Club "{club.nombre}" reactivado.', 'success')
    return redirect(url_for('sistema.detalle', id=club.id))

# ============================================================
# Impersonation
# ============================================================

@sistema_bp.route('/club/<int:id_club>/impersonar/<int:id_usuario>',
                  methods=['POST'])
@requiere_super_admin_sistema
def iniciar_impersonation(id_club, id_usuario):
    """
    Cambia la sesión para ver el sistema como el usuario indicado.
    Guarda en sesión el id del sos_messi original para poder salir.
    """
    club = Empresa.query.get_or_404(id_club)
    if club.es_club_fantasma:
        flash('No se puede impersonar al club fantasma.', 'danger')
        return redirect(url_for('sistema.listar'))

    usuario = Usuario.query.get_or_404(id_usuario)
    if usuario.id_empresa != club.id:
        flash('El usuario no pertenece a ese club.', 'danger')
        return redirect(url_for('sistema.detalle', id=club.id))

    # Guardar datos del usuario original
    original = Usuario.query.get(session['usuario_id'])
    if not original or not original.es_super_admin_sistema:
        flash('Error de sesión. Volvé a loguearte.', 'danger')
        return redirect(url_for('auth.logout'))

    session['usuario_original_id'] = original.id
    session['usuario_original_nombre'] = original.nombre
    session['usuario_original_username'] = original.username
    session['usuario_original_id_empresa'] = original.id_empresa

    # Cambiar la sesión al usuario impersonado
    session['usuario_id'] = usuario.id
    session['usuario_nombre'] = usuario.nombre
    session['usuario_username'] = usuario.username
    session['id_empresa'] = usuario.id_empresa

    auditar(
        'sistema:iniciar_impersonation', 'Usuario', usuario.id,
        id_empresa_forzada=usuario.id_empresa,
        nota=(
            f'Soporte inició suplantación del usuario '
            f'"{usuario.username}" ({usuario.nombre})'
        ),
    )
    db.session.commit()

    flash(
        f'Ahora estás viendo el sistema como {usuario.nombre} '
        f'({usuario.username}).',
        'info',
    )
    return redirect(url_for('main.index'))


@sistema_bp.route('/salir-impersonation', methods=['POST', 'GET'])
def salir_impersonation():
    """
    Restaura la sesión al super admin del sistema.
    No usa @requiere_super_admin_sistema porque durante la
    impersonation el usuario activo es el impersonado.
    """
    original_id = session.get('usuario_original_id')
    if not original_id:
        flash('No estás impersonando a nadie.', 'info')
        return redirect(url_for('main.index'))

    original = Usuario.query.get(original_id)
    if not original:
        session.clear()
        flash('Error al restaurar la sesión. Volvé a loguearte.', 'danger')
        return redirect(url_for('auth.login'))

    # Restaurar la sesión
    session['usuario_id'] = original.id
    session['usuario_nombre'] = original.nombre
    session['usuario_username'] = original.username
    session['id_empresa'] = original.id_empresa

    # Limpiar flags de impersonation
    session.pop('usuario_original_id', None)
    session.pop('usuario_original_nombre', None)
    session.pop('usuario_original_username', None)
    session.pop('usuario_original_id_empresa', None)

    flash('Saliste de la suplantación.', 'success')
    return redirect(url_for('sistema.listar'))