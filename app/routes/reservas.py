# app/routes/reservas.py

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, send_file, current_app,
)
from app import db
from app.models import (
    Reserva, Instalacion, Socio, PagoAlquiler, CuentaBancaria, Cheque,
)
from app.utils import quitar_acentos, current_empresa, formato_pesos, local_to_utc, utc_to_local
from sqlalchemy import func, or_
from app.forms import ReservaForm, PagoAlquilerForm
from app.services.reserva_service import ReservaService
from app.services.reporte_service import ReporteService
from datetime import datetime
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

HORA_INICIO_DIA_MIN = 0
HORA_FIN_DIA = 24

reservas_bp = Blueprint('reservas', __name__, url_prefix='/reservas')


@reservas_bp.route('/')
@requiere_ver('reservas')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    query = request.args.get('q', '').strip()

    base_query = Reserva.query.join(Instalacion).filter(
        Instalacion.id_empresa == empresa.id
    )

    if query:
        query_normalizada = quitar_acentos(query).lower()
        base_query = base_query.join(Socio, Reserva.id_socio == Socio.id)
        condiciones = [
            func.unaccent(Socio.apellido).ilike(f'%{query_normalizada}%'),
            func.unaccent(Socio.nombre).ilike(f'%{query_normalizada}%'),
            func.unaccent(Instalacion.nombre).ilike(f'%{query_normalizada}%')
        ]
        if query.isdigit():
            condiciones.append(Reserva.id == int(query))
        base_query = base_query.filter(or_(*condiciones))

    pagination = base_query.order_by(
        Reserva.fecha_inicio.desc(),
        Reserva.id.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    reservas = pagination.items

    return render_template('reservas/listar.html', reservas=reservas,
                           pagination=pagination, query=query)


@reservas_bp.route('/calendario')
@requiere_ver('reservas')
def calendario():
    return render_template('reservas/calendario.html')


@reservas_bp.route('/api/eventos')
@requiere_ver('reservas')
def api_eventos():
    """API para FullCalendar - devuelve eventos en formato JSON."""
    empresa = current_empresa()
    if not empresa:
        return jsonify([])

    # BUGFIX: antes devolvía reservas de todas las empresas.
    reservas = (
        Reserva.query
        .join(Instalacion)
        .filter(
            Reserva.estado != 'cancelada',
            Instalacion.id_empresa == empresa.id,
        )
        .all()
    )
    eventos = []
    for r in reservas:
        color = '#28a745' if r.estado == 'confirmada' else '#ffc107' if r.estado == 'pendiente' else '#dc3545'
        # 'No Socio' (reserva sin socio asociado) no debe tumbar toda la API.
        socio = r.socio.apellido if r.socio else 'No Socio'
        eventos.append({
            'id': r.id,
            'title': f"{r.instalacion.nombre} - {socio}",
            'start': utc_to_local(r.fecha_inicio).isoformat(),
            'end': utc_to_local(r.fecha_fin).isoformat(),
            'color': color,
            'textColor': 'white',
            'extendedProps': {
                'estado': r.estado,
                'motivo': r.motivo,
                'monto': float(r.monto_total)
            }
        })
    return jsonify(eventos)


@reservas_bp.route('/nueva', methods=['GET', 'POST'])
@requiere_editar('reservas')
def nueva():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ReservaForm()
    form.id_instalacion.choices = [
        (i.id, f"{i.nombre} ({'por hora' if i.tipo_cobro == 'por_hora' else 'por uso'})")
        for i in Instalacion.query.filter_by(
            activo=True, id_empresa=empresa.id
        ).all()
    ]
    form.id_socio.choices = [(0, 'No Socio')] + [
        (s.id, f"{s.apellido}, {s.nombre}")
        for s in Socio.query.filter_by(
            fecha_baja=None, id_empresa=empresa.id
        ).all()
    ]

    if form.validate_on_submit():
        fi = form.get_datetime_inicio()
        ff = form.get_datetime_fin()
        fi_utc = local_to_utc(fi)
        ff_utc = local_to_utc(ff)

        if not ReservaService.verificar_disponibilidad(
            form.id_instalacion.data, fi_utc, ff_utc
        ):
            flash('La instalación no está disponible en ese horario.', 'danger')
            return render_template('reservas/form.html', form=form)

        # Monto sugerido al momento de guardar (para auditar modificaciones)
        monto_sugerido = ReservaService.calcular_monto_total(
            form.id_instalacion.data, fi_utc, ff_utc
        )
        monto_final = form.monto_total.data

        reserva = Reserva(
            id_instalacion=form.id_instalacion.data,
            id_socio=form.id_socio.data if form.id_socio.data != 0 else None,
            fecha_inicio=fi_utc,
            fecha_fin=ff_utc,
            motivo=form.motivo.data,
            estado='confirmada',
            monto_total=monto_final,
            observaciones=form.observaciones.data,
        )
        db.session.add(reserva)
        db.session.flush()

        nota = None
        if monto_final != monto_sugerido:
            nota = (
                f'Precio modificado manualmente. '
                f'Sugerido: ${monto_sugerido}, cobrado: ${monto_final}.'
            )

        auditar('crear', 'Reserva', reserva.id, despues=reserva, nota=nota)
        db.session.commit()
        flash('Reserva creada exitosamente.', 'success')
        return redirect(url_for('reservas.listar'))

    # GET: precargar fechas sugeridas (hoy, próxima hora en punto)
    if request.method == 'GET':
        from datetime import date, time
        hoy = date.today()
        hora_actual = datetime.utcnow().hour + 1
        hora_sugerida = max(HORA_INICIO_DIA_MIN, min(hora_actual, HORA_FIN_DIA - 1))
        form.fecha_inicio_fecha.data = hoy
        form.fecha_fin_fecha.data = hoy
        form.fecha_inicio_hora.data = f'{hora_sugerida:02d}:00'
        form.fecha_fin_hora.data = f'{(hora_sugerida + 1):02d}:00'

    return render_template('reservas/form.html', form=form)


@reservas_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('reservas')
def editar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    reserva = Reserva.query.get_or_404(id)
    if reserva.instalacion.id_empresa != empresa.id:
        flash('Reserva no encontrada.', 'danger')
        return redirect(url_for('reservas.listar'))

    form = ReservaForm(obj=reserva)
    form.id_instalacion.choices = [
        (i.id, f"{i.nombre} ({'por hora' if i.tipo_cobro == 'por_hora' else 'por uso'})")
        for i in Instalacion.query.filter_by(
            activo=True, id_empresa=empresa.id
        ).all()
    ]
    form.id_socio.choices = [(0, 'No Socio')] + [
        (s.id, f"{s.apellido}, {s.nombre}")
        for s in Socio.query.filter_by(
            fecha_baja=None, id_empresa=empresa.id
        ).all()
    ]

    if request.method == 'GET':
        # Convertimos de UTC a local para que el usuario vea su hora real
        form.fecha_inicio_fecha.data = utc_to_local(reserva.fecha_inicio).date()
        form.fecha_inicio_hora.data = utc_to_local(reserva.fecha_inicio).strftime('%H:%M')
        form.fecha_fin_fecha.data = utc_to_local(reserva.fecha_fin).date()
        form.fecha_fin_hora.data = utc_to_local(reserva.fecha_fin).strftime('%H:%M')
        form.monto_total.data = reserva.monto_total
        # El sugerido se calcula al vuelo en el template (comparar)
        form.precio_sugerido.data = str(reserva.monto_total)

        fi = form.get_datetime_inicio()
        ff = form.get_datetime_fin()
        
        fi_utc = local_to_utc(fi)
        ff_utc = local_to_utc(ff)

        if not ReservaService.verificar_disponibilidad(
            form.id_instalacion.data, fi_utc, ff_utc, reserva_id=reserva.id
        ):
            flash('La instalación no está disponible en ese horario.', 'danger')
            return render_template('reservas/form.html', form=form, reserva=reserva)

        monto_sugerido = ReservaService.calcular_monto_total(
            form.id_instalacion.data, fi_utc, ff_utc
        )
        monto_final = form.monto_total.data

        # Snapshot ANTES
        estado_antes = {
            'id_instalacion': reserva.id_instalacion,
            'id_socio': reserva.id_socio,
            'fecha_inicio': reserva.fecha_inicio.isoformat(),
            'fecha_fin': reserva.fecha_fin.isoformat(),
            'motivo': reserva.motivo,
            'monto_total': str(reserva.monto_total),
            'estado': reserva.estado,
        }

        reserva.id_instalacion = form.id_instalacion.data
        reserva.id_socio = form.id_socio.data if form.id_socio.data != 0 else None
        reserva.fecha_inicio = fi_utc
        reserva.fecha_fin = ff_utc
        reserva.motivo = form.motivo.data
        reserva.monto_total = monto_final
        reserva.observaciones = form.observaciones.data

        db.session.flush()

        nota = None
        if monto_final != monto_sugerido:
            nota = (
                f'Precio modificado manualmente. '
                f'Sugerido: ${monto_sugerido}, cobrado: ${monto_final}.'
            )

        auditar(
            'editar', 'Reserva', reserva.id,
            antes=estado_antes, despues=reserva, nota=nota,
        )
        db.session.commit()
        flash('Reserva actualizada.', 'success')
        return redirect(url_for('reservas.listar'))

    return render_template('reservas/form.html', form=form, reserva=reserva)


@reservas_bp.route('/<int:id>')
@requiere_ver('reservas')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    reserva = Reserva.query.get_or_404(id)
    if reserva.instalacion.id_empresa != empresa.id:
        flash('Reserva no encontrada.', 'danger')
        return redirect(url_for('reservas.listar'))

    pagos = PagoAlquiler.query.filter_by(id_reserva=id).all()
    return render_template('reservas/detalle.html', reserva=reserva, pagos=pagos)


@reservas_bp.route('/<int:id>/pagar', methods=['GET', 'POST'])
@requiere_editar('reservas')
def pagar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    reserva = Reserva.query.get_or_404(id)
    if reserva.instalacion.id_empresa != empresa.id:
        flash('Reserva no encontrada.', 'danger')
        return redirect(url_for('reservas.listar'))

    if reserva.esta_pagada:
        flash('Esta reserva ya está totalmente pagada.', 'info')
        return redirect(url_for('reservas.detalle', id=reserva.id))

    form = PagoAlquilerForm()

    cuentas = CuentaBancaria.query.filter(
        CuentaBancaria.id_empresa == empresa.id,
        CuentaBancaria.activo == True,
        CuentaBancaria.tipo != 'cartera',
    ).order_by(CuentaBancaria.nombre).all()
    form.id_cuenta.choices = [(c.id, c.nombre) for c in cuentas]

    if not cuentas:
        flash('No hay cuentas activas. Crea una cuenta primero.', 'warning')
        return redirect(url_for('caja.listar_cuentas'))

    def _render():
        return render_template(
            'reservas/pagar.html',
            form=form,
            reserva=reserva,
            saldo_pendiente=reserva.saldo_pendiente,
            total_pagado=reserva.total_pagado,
            cuentas_con_tipo=[
                {'id': c.id, 'nombre': c.nombre, 'tipo': c.tipo}
                for c in cuentas
            ],
        )

    if form.validate_on_submit():
        comprobante = form.comprobante.data
        id_cheque = request.form.get('id_cheque')
        if id_cheque and id_cheque.isdigit() and int(id_cheque) > 0:
            cheque = Cheque.query.get(int(id_cheque))
            if cheque and cheque.id_empresa == empresa.id:
                comprobante = f'Cheque #{cheque.numero}'
            else:
                flash('El cheque seleccionado no existe.', 'danger')
                return _render()

        # Validar que la cuenta sea compatible con el método de pago
        cuenta_pago = CuentaBancaria.query.get(form.id_cuenta.data)
        if not cuenta_pago or cuenta_pago.id_empresa != empresa.id:
            flash('Cuenta inválida.', 'danger')
            return _render()

        metodo = form.metodo_pago.data
        if metodo == 'efectivo' and cuenta_pago.tipo != 'efectivo':
            flash(
                'Para pagos en efectivo, elegí una cuenta de tipo '
                '"Efectivo (Caja chica)".',
                'danger'
            )
            return _render()
        if metodo in ('transferencia', 'debito', 'credito') \
                and cuenta_pago.tipo != 'banco':
            flash(
                f'Para pagos por {metodo.replace("_", " ")}, elegí una '
                f'cuenta de tipo "Banco".',
                'danger'
            )
            return _render()
        if metodo == 'cheque' and cuenta_pago.tipo not in ('efectivo', 'banco'):
            flash(
                'Para pagos con cheque, elegí una cuenta de caja o banco.',
                'danger'
            )
            return _render()


        from app.utils_db import transaccion
        try:
            with transaccion():
                pago = ReservaService.registrar_pago(
                    reserva_id=reserva.id,
                    monto=form.monto.data,
                    metodo_pago=form.metodo_pago.data,
                    fecha_pago=local_to_utc(form.fecha_pago.data),
                    comprobante=comprobante,
                    observaciones=form.observaciones.data,
                    id_cuenta=form.id_cuenta.data,
                    id_cheque=id_cheque,
                )
                auditar('crear', 'PagoAlquiler', pago.id, despues=pago)
            flash('Pago registrado exitosamente.', 'success')
            return redirect(url_for('reservas.detalle', id=reserva.id))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
            return _render()
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al registrar pago de reserva')
            flash(f'Error al registrar el pago: {str(e)}', 'danger')
            return _render()

    form.monto.data = reserva.saldo_pendiente
    return _render()


@reservas_bp.route('/<int:id>/cancelar', methods=['GET', 'POST'])
@requiere_editar('reservas')
def cancelar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    reserva = Reserva.query.get_or_404(id)
    if reserva.instalacion.id_empresa != empresa.id:
        flash('Reserva no encontrada.', 'danger')
        return redirect(url_for('reservas.listar'))

    if reserva.esta_pagada:
        flash('Esta reserva ya está totalmente pagada.', 'info')
        return redirect(url_for('reservas.detalle', id=reserva.id))

    # NUEVO: Bloquear solo si está cancelada. 
    # Las finalizadas SÍ se pueden cobrar (pago posterior al uso).
    if reserva.estado_efectivo == 'cancelada':
        flash('No se puede cobrar una reserva cancelada.', 'danger')
        return redirect(url_for('reservas.detalle', id=reserva.id))

    if reserva.estado_efectivo == 'finalizada':
        flash('No se puede cancelar una reserva ya finalizada.', 'warning')
        return redirect(url_for('reservas.detalle', id=reserva.id))

    cuentas = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, activo=True
    ).order_by(CuentaBancaria.nombre).all()

    if request.method == 'POST':
        motivo = (request.form.get('motivo') or '').strip() or None
        accion = request.form.get('accion_devolucion')  # 'no_devolver' | 'devolver_total'
        id_cuenta = request.form.get('id_cuenta', type=int)

        try:
            from app.utils_db import transaccion
            with transaccion():
                if accion == 'devolver_total' and reserva.total_pagado > 0:
                    if not id_cuenta:
                        raise ValueError('Elegí la cuenta bancaria para la devolución.')
                    devolucion, _ = ReservaService.registrar_devolucion(
                        reserva_id=reserva.id,
                        monto=reserva.total_pagado,
                        id_cuenta=id_cuenta,
                        motivo=motivo,
                    )
                    auditar(
                        'devolucion', 'PagoAlquiler', devolucion.id,
                        despues=devolucion,
                        nota=f'Devolución por cancelación reserva #{reserva.id}',
                    )

                reserva.estado = 'cancelada'
                db.session.flush()
                auditar(
                    'cancelar', 'Reserva', reserva.id,
                    nota=(
                        f'{motivo or "Sin motivo"} — '
                        f'{accion or "no_devolver"}'
                    ),
                )

            if accion == 'devolver_total' and reserva.total_pagado > 0:
                flash(
                    f'Reserva cancelada. Se devolvió '
                    f'{formato_pesos(reserva.total_pagado)} al socio.',
                    'success',
                )
            else:
                flash('Reserva cancelada sin devolución.', 'info')

            return redirect(url_for('reservas.detalle', id=reserva.id))

        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al cancelar reserva')
            flash(f'Error al cancelar la reserva: {str(e)}', 'danger')

    return render_template(
        'reservas/cancelar.html',
        reserva=reserva,
        cuentas=cuentas,
    )


@reservas_bp.route('/<int:id>/finalizar', methods=['POST'])
@requiere_editar('reservas')
def finalizar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    reserva = Reserva.query.get_or_404(id)
    if reserva.instalacion.id_empresa != empresa.id:
        flash('Reserva no encontrada.', 'danger')
        return redirect(url_for('reservas.listar'))
    reserva.estado = 'finalizada'
    db.session.flush()
    auditar('finalizar', 'Reserva', reserva.id, nota='Reserva finalizada')
    db.session.commit()
    flash('Reserva finalizada.', 'success')
    return redirect(url_for('reservas.listar'))


@reservas_bp.route('/reporte')
@requiere_ver('reservas')
def reporte():
    formato = request.args.get('formato', 'pdf')
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # BUGFIX: antes listaba reservas de todos los clubes.
    reservas = (
        Reserva.query
        .join(Instalacion)
        .filter(Instalacion.id_empresa == empresa.id)
        .order_by(Reserva.fecha_inicio.desc())
        .all()
    )
    columnas = ['ID', 'Instalación', 'Socio', 'Fecha Inicio', 'Fecha Fin', 'Monto', 'Estado']
    datos = [
        {
            'ID': r.id,
            'Instalación': r.instalacion.nombre,
            'Socio': f"{r.socio.apellido}, {r.socio.nombre}" if r.socio else 'No Socio',
            'Fecha Inicio': utc_to_local(r.fecha_inicio).strftime('%d/%m/%Y %H:%M'),
            'Fecha Fin': utc_to_local(r.fecha_fin).strftime('%d/%m/%Y %H:%M'),
            'Monto': f"${r.monto_total:.2f}",
            'Estado': r.estado.capitalize()
        } for r in reservas
    ]
    titulo = "Reporte de Reservas"
    if formato.lower() == 'excel':
        buffer = ReporteService.generar_excel(titulo, columnas, datos)
        return send_file(buffer, download_name='reservas.xlsx', as_attachment=True)
    else:
        buffer = ReporteService.generar_pdf(titulo, columnas, datos, orientacion='landscape')
        return send_file(buffer, download_name='reservas.pdf', as_attachment=True)

@reservas_bp.route('/api/calcular-monto', methods=['POST'])
@requiere_ver('reservas')
def api_calcular_monto():
    """
    Calcula el monto sugerido de una reserva sin guardarla.
    Payload JSON: { id_instalacion, fecha_inicio (ISO), fecha_fin (ISO) }
    """
    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'Sin empresa'}), 400

    data = request.get_json(silent=True) or {}
    id_instalacion = data.get('id_instalacion')
    fi_str = data.get('fecha_inicio')
    ff_str = data.get('fecha_fin')

    if not all([id_instalacion, fi_str, ff_str]):
        return jsonify({'error': 'Faltan datos'}), 400

    instalacion = Instalacion.query.get(id_instalacion)
    if not instalacion or instalacion.id_empresa != empresa.id:
        return jsonify({'error': 'Instalación inválida'}), 404

    try:
        fi = local_to_utc(datetime.fromisoformat(fi_str.replace('Z', '')))
        ff = local_to_utc(datetime.fromisoformat(ff_str.replace('Z', '')))
    except ValueError:
        return jsonify({'error': 'Formato de fecha inválido'}), 400

    if ff <= fi:
        return jsonify({
            'monto': None,
            'tipo_cobro': instalacion.tipo_cobro,
            'error': 'La fecha de fin debe ser posterior a la de inicio.',
        })

    monto = ReservaService.calcular_monto_total(
        instalacion.id, fi, ff
    )

    return jsonify({
        'monto': str(monto),
        'tipo_cobro': instalacion.tipo_cobro,
    })