# app/routes/cheques.py

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, current_app,
)
from app import db
from app.models import Cheque, CuentaBancaria
from app.forms import ChequeForm
from app.services.caja_service import CajaService
from app.utils import current_empresa
from datetime import datetime, date
from decimal import Decimal
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

cheques_bp = Blueprint('cheques', __name__, url_prefix='/cheques')


@cheques_bp.route('/')
@requiere_ver('cheques')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    tipo = request.args.get('tipo', 'recibido')
    if tipo not in ('recibido', 'emitido'):
        tipo = 'recibido'

    ver_todos = request.args.get('ver_todos', '0') == '1'
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)

    query = Cheque.query.filter_by(id_empresa=empresa.id, tipo=tipo)
    if not ver_todos:
        if tipo == 'recibido':
            # Cheques que tenemos en nuestro poder y todavía no cobramos
            query = query.filter(Cheque.estado.in_(['en_cartera', 'depositado']))
        else:
            # Cheques emitidos pendientes de pago.
            # Aceptamos 'en_cartera' por retrocompatibilidad con datos viejos.
            query = query.filter(Cheque.estado.in_(['pendiente', 'en_cartera']))

    pagination = query.order_by(Cheque.fecha_cobro).paginate(
        page=page, per_page=per_page, error_out=False
    )
    cheques = pagination.items

    return render_template(
        'cheques/listar.html',
        cheques=cheques,
        pagination=pagination,
        ver_todos=ver_todos,
        tipo=tipo,
    )


@cheques_bp.route('/nuevo', methods=['GET', 'POST'])
@requiere_editar('cheques')
def nuevo():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ChequeForm()
    form.tipo.choices = [('recibido', 'Recibido (nos pagan)')]
    form.tipo.data = 'recibido'    
    if form.validate_on_submit():
        cheque = Cheque(
            numero=form.numero.data,
            banco_emisor=form.banco_emisor.data,
            titular=form.titular.data,
            monto=form.monto.data,
            fecha_emision=form.fecha_emision.data,
            fecha_cobro=form.fecha_cobro.data,
            tipo=form.tipo.data,
            observaciones=form.observaciones.data,
            id_empresa=empresa.id,
        )
        db.session.add(cheque)
        db.session.flush()
        auditar('crear', 'Cheque', cheque.id, despues=cheque)
        db.session.commit()
        flash('Cheque registrado.', 'success')
        return redirect(url_for('cheques.listar'))

    return render_template('cheques/form.html', form=form)


@cheques_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('cheques')
def editar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    form = ChequeForm(obj=cheque)
    if form.validate_on_submit():
        estado_antes = {
            'numero': cheque.numero,
            'banco_emisor': cheque.banco_emisor,
            'titular': cheque.titular,
            'monto': str(cheque.monto),
            'fecha_emision': cheque.fecha_emision.isoformat() if cheque.fecha_emision else None,
            'fecha_cobro': cheque.fecha_cobro.isoformat() if cheque.fecha_cobro else None,
            'tipo': cheque.tipo,
            'observaciones': cheque.observaciones,
        }

        cheque.numero = form.numero.data
        cheque.banco_emisor = form.banco_emisor.data
        cheque.titular = form.titular.data
        cheque.monto = form.monto.data
        cheque.fecha_emision = form.fecha_emision.data
        cheque.fecha_cobro = form.fecha_cobro.data
        cheque.tipo = form.tipo.data
        cheque.observaciones = form.observaciones.data

        db.session.flush()
        auditar(
            'editar', 'Cheque', cheque.id,
            antes=estado_antes, despues=cheque,
        )
        db.session.commit()
        flash('Cheque actualizado.', 'success')
        return redirect(url_for('cheques.listar'))

    return render_template('cheques/form.html', form=form, cheque=cheque)


@cheques_bp.route('/<int:id>/cambiar-estado/<estado>', methods=['POST'])
@requiere_editar('cheques')
def cambiar_estado(id, estado):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    estado_anterior = cheque.estado

    try:
        CajaService.actualizar_estado_cheque(
            id, estado,
            fecha_cobro=date.today() if estado == 'cobrado' else None,
            commit=False,
        )
        db.session.flush()
        auditar(
            'cambiar_estado', 'Cheque', cheque.id,
            nota=f'{estado_anterior} → {estado}',
        )
        db.session.commit()
        flash(f'Cheque actualizado a {estado}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('cheques.listar'))


@cheques_bp.route('/vencidos')
@requiere_ver('cheques')
def vencidos():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    hoy = date.today()
    cheques_vencidos = Cheque.query.filter(
        Cheque.id_empresa == empresa.id,
        Cheque.estado.in_(['en_cartera', 'depositado', 'pendiente']),
        Cheque.fecha_cobro < hoy,
    ).all()
    return render_template('cheques/vencidos.html', cheques=cheques_vencidos)


@cheques_bp.route('/api/nuevo', methods=['POST'])
@requiere_editar('cheques')
def api_nuevo():
    """Endpoint AJAX para crear un cheque rápidamente y devolver sus datos."""
    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'No hay empresa configurada'}), 400

    numero = request.form.get('numero')
    banco_emisor = request.form.get('banco_emisor')
    titular = request.form.get('titular')
    monto = request.form.get('monto')
    fecha_emision = request.form.get('fecha_emision')
    fecha_cobro = request.form.get('fecha_cobro')
    tipo = request.form.get('tipo')
    observaciones = request.form.get('observaciones', '')

    if not all([numero, banco_emisor, titular, monto,
                fecha_emision, fecha_cobro, tipo]):
        return jsonify({'error': 'Faltan datos obligatorios'}), 400

    try:
        monto = Decimal(monto)
    except Exception:
        return jsonify({'error': 'Monto inválido'}), 400

    try:
        fecha_emision_date = datetime.strptime(fecha_emision, '%Y-%m-%d').date()
        fecha_cobro_date = datetime.strptime(fecha_cobro, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    cheque = Cheque(
        numero=numero,
        banco_emisor=banco_emisor,
        titular=titular,
        monto=monto,
        fecha_emision=fecha_emision_date,
        fecha_cobro=fecha_cobro_date,
        tipo=tipo,
        observaciones=observaciones,
        id_empresa=empresa.id,
        estado='en_cartera',
    )
    db.session.add(cheque)
    db.session.flush()
    auditar('crear', 'Cheque', cheque.id, despues=cheque)
    db.session.commit()

    return jsonify({
        'id': cheque.id,
        'descripcion': f"{cheque.numero} - ${cheque.monto} ({cheque.estado})"
    })


@cheques_bp.route('/<int:id>/cobrar', methods=['GET', 'POST'])
@requiere_editar('cheques')
def cobrar(id):
    """Formulario para cobrar un cheque: elegir banco destino."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    if cheque.tipo != 'recibido':
        flash('Solo se pueden cobrar cheques de tipo "recibido".', 'warning')
        return redirect(url_for('cheques.listar'))

    cuentas = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, tipo='banco', activo=True
    ).all()

    if request.method == 'POST':
        id_cuenta = request.form.get('id_cuenta', type=int)
        fecha_cobro_str = request.form.get('fecha_cobro')
        try:
            fecha_cobro = (
                datetime.strptime(fecha_cobro_str, '%Y-%m-%d').date()
                if fecha_cobro_str else date.today()
            )
            CajaService.actualizar_estado_cheque(
                cheque.id, 'cobrado',
                fecha_cobro=fecha_cobro,
                id_cuenta=id_cuenta,
                commit=False,
            )
            cuenta_destino = CuentaBancaria.query.get(id_cuenta)
            auditar(
                'cobrar', 'Cheque', cheque.id,
                nota=(
                    f'Cobrado en {cuenta_destino.nombre if cuenta_destino else "cuenta desconocida"}'
                    f' el {fecha_cobro.strftime("%d/%m/%Y")}'
                ),
            )
            db.session.commit()
            flash(f'Cheque #{cheque.numero} cobrado exitosamente.', 'success')
            return redirect(url_for('cheques.listar'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al cobrar: {str(e)}', 'danger')

    return render_template('cheques/cobrar.html',
                           cheque=cheque, cuentas=cuentas)

# ============================================================
# Reemplazo de cheques
# ============================================================

@cheques_bp.route('/<int:id>/reemplazar', methods=['GET'])
@requiere_editar('cheques')
def reemplazar(id):
    """Página con las dos opciones: reemplazar o cancelar en efectivo."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    if cheque.estado not in ('pendiente', 'en_cartera'):
        flash(
            f'El cheque #{cheque.numero} no se puede reemplazar '
            f'(estado actual: {cheque.estado}).',
            'warning'
        )
        return redirect(url_for('cheques.listar', tipo=cheque.tipo))

    cuentas = CuentaBancaria.query.filter(
        CuentaBancaria.id_empresa == empresa.id,
        CuentaBancaria.activo == True,
        CuentaBancaria.tipo != 'cartera',
    ).order_by(CuentaBancaria.nombre).all()

    return render_template(
        'cheques/reemplazar.html',
        cheque=cheque,
        cuentas=cuentas,
    )


@cheques_bp.route('/<int:id>/reemplazar/con-cheque', methods=['POST'])
@requiere_editar('cheques')
def reemplazar_con_cheque(id):
    """Procesa el reemplazo por un cheque nuevo."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    numero = (request.form.get('numero') or '').strip()
    banco = (request.form.get('banco_emisor') or '').strip()
    titular = (request.form.get('titular') or '').strip()
    fecha_emision_str = (request.form.get('fecha_emision') or '').strip()
    fecha_cobro_str = (request.form.get('fecha_cobro') or '').strip()
    motivo = (request.form.get('motivo') or '').strip() or None

    errores = []
    if not numero:
        errores.append('Falta el número del cheque nuevo.')
    if not banco:
        errores.append('Falta el banco emisor.')
    if not fecha_emision_str:
        errores.append('Falta la fecha de emisión.')

    fecha_emision = None
    if fecha_emision_str:
        try:
            fecha_emision = datetime.strptime(
                fecha_emision_str, '%Y-%m-%d'
            ).date()
        except ValueError:
            errores.append('Fecha de emisión inválida.')

    fecha_cobro = None
    if fecha_cobro_str:
        try:
            fecha_cobro = datetime.strptime(
                fecha_cobro_str, '%Y-%m-%d'
            ).date()
        except ValueError:
            errores.append('Fecha de cobro inválida.')

    if errores:
        for e in errores:
            flash(e, 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))

    try:
        from app.utils_db import transaccion
        with transaccion():
            viejo, nuevo = CajaService.reemplazar_cheque(
                cheque.id,
                {
                    'numero': numero,
                    'banco_emisor': banco,
                    'titular': titular,
                    'fecha_emision': fecha_emision,
                    'fecha_cobro': fecha_cobro or fecha_emision,
                },
                motivo=motivo,
            )
            auditar(
                'reemplazar', 'Cheque', viejo.id,
                despues=viejo,
                nota=(
                    f'Reemplazado por #{nuevo.numero}'
                    + (f'. Motivo: {motivo}' if motivo else '')
                ),
            )
            auditar(
                'crear', 'Cheque', nuevo.id,
                despues=nuevo,
                nota=f'Reemplaza al cheque #{viejo.numero}',
            )

        flash(
            f'Cheque #{viejo.numero} reemplazado por #{nuevo.numero}.',
            'success',
        )
        return redirect(url_for('cheques.listar', tipo=cheque.tipo))

    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al reemplazar cheque')
        flash(f'Error al reemplazar el cheque: {str(e)}', 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))


@cheques_bp.route('/<int:id>/reemplazar/en-efectivo', methods=['POST'])
@requiere_editar('cheques')
def cancelar_en_efectivo(id):
    """Procesa la cancelación en efectivo/transferencia."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar'))

    id_cuenta = request.form.get('id_cuenta', type=int)
    fecha_pago_str = (request.form.get('fecha_pago') or '').strip()
    motivo = (request.form.get('motivo') or '').strip() or None

    if not id_cuenta:
        flash('Elegí una cuenta bancaria.', 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))

    fecha_pago = datetime.utcnow()
    if fecha_pago_str:
        try:
            fecha_base = datetime.strptime(
                fecha_pago_str, '%Y-%m-%d'
            ).date()
            fecha_pago = datetime.combine(fecha_base, datetime.min.time())
        except ValueError:
            pass

    try:
        from app.utils_db import transaccion
        with transaccion():
            cheque_mov, movimiento = CajaService.cancelar_cheque_en_efectivo(
                cheque.id,
                id_cuenta=id_cuenta,
                fecha_pago=fecha_pago,
                motivo=motivo,
            )
            accion = (
                'cancelar_efectivo'
                if cheque_mov.tipo == 'emitido'
                else 'cobrar_efectivo'
            )
            auditar(
                accion, 'Cheque', cheque_mov.id,
                despues=cheque_mov,
                nota=motivo or 'Cancelado en efectivo/transferencia',
            )
            if movimiento:
                auditar(
                    'crear', 'Movimiento', movimiento.id,
                    despues=movimiento,
                )

        flash(
            f'Cheque #{cheque.numero} cancelado en efectivo/transferencia.',
            'success',
        )
        return redirect(url_for('cheques.listar', tipo=cheque.tipo))

    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(
            'Error al cancelar cheque en efectivo'
        )
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('cheques.reemplazar', id=id))

@cheques_bp.route('/<int:id>/pagar', methods=['GET', 'POST'])
@requiere_editar('cheques')
def pagar(id):
    """Formulario para pagar un cheque emitido: elegir banco origen."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cheque = Cheque.query.get_or_404(id)
    if cheque.id_empresa != empresa.id:
        flash('Cheque no encontrado.', 'danger')
        return redirect(url_for('cheques.listar', tipo='emitido'))

    if cheque.tipo != 'emitido':
        flash('Solo se pueden pagar cheques de tipo "emitido".', 'warning')
        return redirect(url_for('cheques.listar', tipo='recibido'))

    if cheque.estado == 'pagado':
        flash('El cheque ya está pagado.', 'warning')
        return redirect(url_for('cheques.listar', tipo='emitido'))

    cuentas = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, tipo='banco', activo=True
    ).all()

    if not cuentas:
        flash('No hay cuentas bancarias activas.', 'warning')
        return redirect(url_for('caja.listar_cuentas'))

    if request.method == 'POST':
        id_cuenta = request.form.get('id_cuenta', type=int)
        fecha_pago_str = request.form.get('fecha_pago')
        try:
            fecha_pago = (
                datetime.strptime(fecha_pago_str, '%Y-%m-%d')
                if fecha_pago_str else date.today()
            )
            CajaService.pagar_cheque_emitido(
                cheque.id, id_cuenta,
                fecha_pago=fecha_pago,
                commit=False,
            )
            cuenta_origen = CuentaBancaria.query.get(id_cuenta)
            auditar(
                'pagar', 'Cheque', cheque.id,
                nota=(
                    f'Pagado desde {cuenta_origen.nombre if cuenta_origen else "cuenta desconocida"}'
                    f' el {fecha_pago.strftime("%d/%m/%Y")}'
                ),
            )
            db.session.commit()
            flash(
                f'Cheque #{cheque.numero} descontado de la cuenta bancaria.',
                'success',
            )
            return redirect(url_for('cheques.listar', tipo='emitido'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al pagar: {str(e)}', 'danger')

    return render_template('cheques/pagar.html', cheque=cheque, cuentas=cuentas)