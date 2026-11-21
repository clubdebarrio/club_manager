# app/routes/cuotas.py

from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    flash, jsonify, send_file,
)
from app import db
from app.models import (
    CuotaSocial, Socio, ConfiguracionCuota, TipoSocio, CategoriaSocio,
    CuentaBancaria,
)
from app.forms import ConfiguracionCuotaForm, PagoCuotaForm
from app.services.cuota_service import CuotaService
from app.utils import quitar_acentos, fecha_hora_efectiva, current_empresa, local_to_utc, utc_to_local
from sqlalchemy import func, or_, and_
from datetime import datetime, date
from app.services.reporte_service import ReporteService
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

cuotas_bp = Blueprint('cuotas', __name__, url_prefix='/cuotas')


@cuotas_bp.route('/')
@requiere_ver('cuotas')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    periodo = request.args.get('periodo', '')
    query = request.args.get('q', '').strip()
    solo_vencidas = request.args.get('solo_vencidas', '0') == '1'

    base_query = CuotaSocial.query.join(Socio).filter(Socio.id_empresa == empresa.id)

    if periodo:
        base_query = base_query.filter(CuotaSocial.periodo == periodo)

    # Filtro de vencidas
    if solo_vencidas:
        from datetime import date
        hoy = date.today()
        base_query = base_query.filter(
            or_(
                CuotaSocial.estado == 'vencida',
                and_(
                    CuotaSocial.estado == 'pendiente',
                    CuotaSocial.fecha_vencimiento < hoy
                )
            )
        )

    if query:
        query_normalizada = quitar_acentos(query).lower()
        base_query = base_query.filter(
            (func.unaccent(Socio.apellido).ilike(f'%{query_normalizada}%')) |
            (func.unaccent(Socio.nombre).ilike(f'%{query_normalizada}%'))
        )

    pagination = base_query.order_by(
        CuotaSocial.periodo.desc(),
        CuotaSocial.id.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    cuotas = pagination.items

    return render_template('cuotas/listar.html',
                         cuotas=cuotas,
                         pagination=pagination,
                         periodo=periodo,
                         query=query,
                         solo_vencidas=solo_vencidas)


@cuotas_bp.route('/generar', methods=['GET', 'POST'])
@requiere_editar('cuotas')
def generar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        ano = int(request.form.get('ano', date.today().year))
        mes = int(request.form.get('mes', date.today().month))

        resultado = CuotaService.generar_cuotas_para_mes(
            ano, mes, id_empresa=empresa.id
        )

        # Log de auditoría con el resultado de la generación masiva
        auditar(
            'generar', 'CuotaSocial', None,
            nota=(
                f'Generación masiva período {mes:02d}/{ano}: '
                f'{resultado["cantidad"]} cuotas creadas, '
                f'{len(resultado["errores"])} errores'
            ),
        )
        db.session.commit()

        flash(
            f'Se generaron {resultado["cantidad"]} cuotas para '
            f'{mes:02d}/{ano}.',
            'success'
        )
        if resultado['errores']:
            for error in resultado['errores']:
                flash(f'⚠️ {error}', 'warning')
        return redirect(url_for('cuotas.listar'))

    return render_template('cuotas/generar.html')


@cuotas_bp.route('/pagar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('cuotas')
def pagar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuota = CuotaSocial.query.get_or_404(id)
    if cuota.socio.id_empresa != empresa.id:
        flash('Cuota no encontrada.', 'danger')
        return redirect(url_for('cuotas.listar'))

    form = PagoCuotaForm()
    form.fecha_pago.data = date.today()

    cuentas = CuentaBancaria.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).all()
    form.id_cuenta.choices = [(c.id, c.nombre) for c in cuentas]
    cuentas_con_tipo = [(c.id, c.nombre, c.tipo) for c in cuentas]

    if form.validate_on_submit():
        metodo = form.metodo_pago.data
        cuenta = CuentaBancaria.query.get(form.id_cuenta.data)
        if not cuenta or cuenta.id_empresa != empresa.id:
            flash('Cuenta seleccionada no existe.', 'danger')
            return render_template('cuotas/pagar.html', cuota=cuota, form=form,
                                   cuentas_con_tipo=cuentas_con_tipo)
        if metodo == 'efectivo' and cuenta.tipo != 'efectivo':
            flash('Para pagos en efectivo, la cuenta debe ser de tipo "Efectivo (Caja chica)".', 'danger')
            return render_template('cuotas/pagar.html', cuota=cuota, form=form,
                                   cuentas_con_tipo=cuentas_con_tipo)
        elif metodo in ['transferencia', 'deposito_efectivo', 'deposito_cheque', 'debito', 'credito'] \
                and cuenta.tipo != 'banco':
            flash(f'Para pagos por {metodo.replace("_", " ")}, la cuenta debe ser de tipo "Banco / Cuenta bancaria".', 'danger')
            return render_template('cuotas/pagar.html', cuota=cuota, form=form,
                                   cuentas_con_tipo=cuentas_con_tipo)

        from app.utils_db import transaccion
        try:
            id_cheque = request.form.get('id_cheque')
            with transaccion():
                exito = CuotaService.marcar_cuota_pagada(
                    cuota.id,
                    fecha_pago=local_to_utc(fecha_hora_efectiva(form.fecha_pago.data)),
                    metodo=form.metodo_pago.data,
                    id_cuenta=form.id_cuenta.data,
                    id_cheque=id_cheque
                )
            if exito:
                from app.auditoria import auditar
                auditar(
                    'pagar', 'CuotaSocial', cuota.id,
                    despues={
                        'periodo': cuota.periodo,
                        'monto': str(cuota.monto),
                        'metodo_pago': form.metodo_pago.data,
                        'id_cuenta': form.id_cuenta.data,
                        'fecha_pago': cuota.fecha_pago.isoformat() if cuota.fecha_pago else None,
                    },
                )
                db.session.commit()
                flash('Cuota marcada como pagada y movimiento registrado.', 'success')
            else:
                flash('La cuota ya estaba pagada.', 'warning')
            return redirect(url_for('cuotas.listar'))
        except Exception as e:
            flash(f'Error al procesar el pago: {str(e)}', 'danger')

    return render_template('cuotas/pagar.html', cuota=cuota, form=form,
                           cuentas_con_tipo=cuentas_con_tipo)


@cuotas_bp.route('/configuracion', methods=['GET', 'POST'])
@requiere_editar('cuotas')
def configuracion():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ConfiguracionCuotaForm()
    form.id_tipo_socio.choices = [
        (t.id, t.nombre)
        for t in TipoSocio.query.filter_by(
            activo=True, id_empresa=empresa.id
        ).all()
    ]
    form.id_categoria_socio.choices = [
        (c.id, c.nombre)
        for c in CategoriaSocio.query.filter_by(
            activo=True, id_empresa=empresa.id
        ).all()
    ]

    if form.validate_on_submit():
        from datetime import timedelta

        fecha_desde_nueva = form.fecha_desde.data
        fecha_hasta_nueva = form.fecha_hasta.data

        # --- Validación 1: duplicado exacto (mismo tipo, categoría y fecha_desde)
        duplicado_exacto = ConfiguracionCuota.query.filter_by(
            id_empresa=empresa.id,
            id_tipo_socio=form.id_tipo_socio.data,
            id_categoria_socio=form.id_categoria_socio.data,
            fecha_desde=fecha_desde_nueva,
        ).first()
        if duplicado_exacto:
            flash(
                'Ya existe una configuración para ese tipo/categoría '
                'con la misma fecha de vigencia. '
                'Eliminá la existente o cambiá la fecha.',
                'danger',
            )
            return redirect(url_for('cuotas.configuracion'))

        # --- Validación 2: rango inválido
        if fecha_hasta_nueva and fecha_hasta_nueva < fecha_desde_nueva:
            flash(
                'La fecha de fin no puede ser anterior a la de inicio.',
                'danger',
            )
            return redirect(url_for('cuotas.configuracion'))

        # --- Auto-cierre: buscar configs vigentes que se solapen ---
        # Solapamiento: la existente arranca antes o igual, y termina
        # después de que arranca la nueva (o no tiene fin).
        anteriores = (
            ConfiguracionCuota.query
            .filter(
                ConfiguracionCuota.id_empresa == empresa.id,
                ConfiguracionCuota.id_tipo_socio == form.id_tipo_socio.data,
                ConfiguracionCuota.id_categoria_socio == form.id_categoria_socio.data,
                ConfiguracionCuota.fecha_desde < fecha_desde_nueva,
            )
            .all()
        )

        cerradas = []
        for cfg in anteriores:
            # Solo cerrar las que todavía están vigentes
            if cfg.fecha_hasta is None or cfg.fecha_hasta >= fecha_desde_nueva:
                cfg.fecha_hasta = fecha_desde_nueva - timedelta(days=1)
                cerradas.append(cfg)

        # --- Crear la nueva config ---
        config = ConfiguracionCuota(
            id_tipo_socio=form.id_tipo_socio.data,
            id_categoria_socio=form.id_categoria_socio.data,
            monto=form.monto.data,
            fecha_desde=fecha_desde_nueva,
            fecha_hasta=fecha_hasta_nueva,
            id_empresa=empresa.id,
        )
        db.session.add(config)
        db.session.flush()

        auditar('crear', 'ConfiguracionCuota', config.id, despues=config)

        if cerradas:
            nota = ', '.join(
                f'#{c.id} (cerrada al {c.fecha_hasta.strftime("%d/%m/%Y")})'
                for c in cerradas
            )
            auditar(
                'auto_cerrar', 'ConfiguracionCuota', None,
                nota=f'Configs anteriores cerradas: {nota}',
            )

        db.session.commit()

        if cerradas:
            flash(
                f'Configuración agregada. Se cerraron automáticamente '
                f'{len(cerradas)} configuración(es) anterior(es) del mismo '
                f'tipo/categoría.',
                'success',
            )
        else:
            flash('Configuración agregada.', 'success')
        return redirect(url_for('cuotas.configuracion'))

    # --- Ordenar por tipo, categoría y fecha ---
    configs = (
        ConfiguracionCuota.query
        .filter_by(id_empresa=empresa.id)
        .order_by(
            ConfiguracionCuota.id_tipo_socio,
            ConfiguracionCuota.id_categoria_socio,
            ConfiguracionCuota.fecha_desde.desc(),
        )
        .all()
    )
    from datetime import date
    return render_template(
        'cuotas/configuracion.html',
        form=form,
        configs=configs,
        today=date.today(),
    )


@cuotas_bp.route('/configuracion/eliminar/<int:id>', methods=['POST'])
@requiere_editar('cuotas')
def eliminar_configuracion(id):
    """Elimina una configuración de cuota."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    config = ConfiguracionCuota.query.get_or_404(id)
    if config.id_empresa != empresa.id:
        flash('Configuración no encontrada.', 'danger')
        return redirect(url_for('cuotas.configuracion'))

    # Verificar que no tenga cuotas asociadas con ese monto
    # (opcional: podrías bloquear si ya se generaron cuotas)
    auditar(
        'eliminar', 'ConfiguracionCuota', config.id,
        antes=config,
        nota=(
            f'Eliminada: tipo={config.tipo_socio.nombre}, '
            f'cat={config.categoria_socio.nombre}, '
            f'monto=${config.monto}'
        ),
    )
    db.session.delete(config)
    db.session.commit()
    flash('Configuración eliminada.', 'success')
    return redirect(url_for('cuotas.configuracion'))


@cuotas_bp.route('/reporte')
@requiere_ver('cuotas')
def reporte():
    formato = request.args.get('formato', 'pdf')
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # BUGFIX: antes listaba cuotas de todos los clubes.
    cuotas = (
        CuotaSocial.query
        .join(Socio)
        .filter(Socio.id_empresa == empresa.id)
        .order_by(CuotaSocial.periodo.desc(), CuotaSocial.id.desc())
        .all()
    )
    columnas = ['Período', 'Socio', 'Monto', 'Vencimiento', 'Estado']
    datos = [
        {
            'Período': c.periodo,
            'Socio': f"{c.socio.apellido}, {c.socio.nombre}",
            'Monto': f"${c.monto:.2f}",
            'Vencimiento': c.fecha_vencimiento.strftime('%d/%m/%Y'),
            'Estado': c.estado.capitalize()
        } for c in cuotas
    ]
    titulo = "Reporte de Cuotas Sociales"
    if formato.lower() == 'excel':
        buffer = ReporteService.generar_excel(titulo, columnas, datos)
        return send_file(buffer, download_name='cuotas.xlsx', as_attachment=True)
    else:
        buffer = ReporteService.generar_pdf(titulo, columnas, datos)
        return send_file(buffer, download_name='cuotas.pdf', as_attachment=True)


@cuotas_bp.route('/api/generar-pago-digital/<int:id>', methods=['POST'])
@requiere_editar('cuotas')
def api_generar_pago_digital(id):
    """Genera un payment request en ChytaPay para una cuota."""
    from app.models import ChytaPayToken, ChytaPayPaymentRequest

    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'Sin empresa'}), 400

    # --- Validar que el club pueda usar ChytaPay ---
    if not empresa.puede_usar_chytapay:
        return jsonify({
            'error': (
                'ChytaPay no está disponible en el período de prueba. '
                'Activá un plan pago para usar esta función.'
            ),
            'code': 'plan_no_habilitado',
        }), 403

    # --- Validar token conectado ---
    token = ChytaPayToken.query.filter_by(id_empresa=empresa.id).first()
    if not token:
        return jsonify({
            'error': (
                'El club todavía no conectó su cuenta de ChytaPay. '
                'Andá a Configuración → ChytaPay y conectala.'
            ),
            'code': 'sin_conectar',
        }), 400

    # --- Validar cuota ---
    cuota = CuotaSocial.query.get_or_404(id)
    if cuota.socio.id_empresa != empresa.id:
        return jsonify({'error': 'Cuota no encontrada.'}), 404

    if cuota.estado != 'pendiente':
        return jsonify({
            'error': f'La cuota no está pendiente (estado: {cuota.estado}).',
            'code': 'estado_invalido',
        }), 400

    if not cuota.socio.enviar_cuota_whatsapp:
        return jsonify({
            'error': (
                'El socio no tiene activada la notificación por WhatsApp. '
                'Activala desde el detalle del socio.'
            ),
            'code': 'sin_whatsapp',
        }), 400

    if cuota.chytapay_cvu:
        return jsonify({
            'error': 'Esta cuota ya tiene un CVU generado.',
            'code': 'ya_tiene_cvu',
        }), 400

    existing = ChytaPayPaymentRequest.query.filter_by(
        cuota_social_id=cuota.id
    ).first()
    if existing:
        return jsonify({
            'error': (
                'Esta cuota ya tiene un pago digital generado. '
                'Podés reenviar la notificación desde la cuota.'
            ),
            'code': 'ya_existe_request',
        }), 400

    # --- Intentar crear el payment request ---
    try:
        from app.services.chytapay_service import ChytaPayService
        chyta_response = ChytaPayService.create_payment_request(
            cuota, send_whatsapp=True
        )

        if chyta_response and chyta_response.get('CVU'):
            cuota.chytapay_cvu = chyta_response.get('CVU')
            db.session.commit()

            return jsonify({
                'success': True,
                'cvu': chyta_response.get('CVU'),
                'reference_id': chyta_response.get('referenceId'),
                'amount': float(cuota.monto),
                'due_date': cuota.fecha_vencimiento.strftime('%d/%m/%Y'),
                'bank': chyta_response.get(
                    'bankAccountInfo', {}
                ).get('bankName', ''),
                'account_holder': chyta_response.get(
                    'bankAccountInfo', {}
                ).get('accountHolderName', ''),
            })

        return jsonify({
            'error': 'ChytaPay no devolvió un CVU válido.',
            'code': 'respuesta_invalida',
        }), 500

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f'Error generando pago digital para cuota {cuota.id}: {e}'
        )
        return jsonify({
            'error': f'Error al generar el pago: {str(e)[:200]}',
            'code': 'error_chytapay',
        }), 500


@cuotas_bp.route('/imprimir', methods=['GET', 'POST'])
@requiere_ver('cuotas')
def imprimir_seleccionar_mes():
    if request.method == 'POST':
        mes = request.form.get('mes', type=int)
        ano = request.form.get('ano', type=int)
        if mes and ano:
            return redirect(url_for('cuotas.imprimir_preview', mes=mes, ano=ano))
    return render_template('cuotas/imprimir_seleccionar_mes.html')


@cuotas_bp.route('/imprimir/preview')
@requiere_ver('cuotas')
def imprimir_preview():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)
    if not mes or not ano:
        flash('Seleccione un mes y año válidos.', 'warning')
        return redirect(url_for('cuotas.imprimir_seleccionar_mes'))

    periodo = f"{ano}-{mes:02d}"
    # BUGFIX: antes traía cuotas de todos los clubes.
    cuotas = (
        CuotaSocial.query
        .join(Socio)
        .filter(
            CuotaSocial.periodo == periodo,
            Socio.id_empresa == empresa.id,
        )
        .order_by(CuotaSocial.id)
        .all()
    )

    for cuota in cuotas:
        cuota.preseleccionada = not cuota.socio.enviar_cuota_whatsapp

    return render_template('cuotas/imprimir_preview.html', cuotas=cuotas,
                           mes=mes, ano=ano)


@cuotas_bp.route('/imprimir/generate-pdf', methods=['POST'])
@requiere_ver('cuotas')
def imprimir_generate_pdf():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    ids = request.form.getlist('cuotas')
    if not ids:
        flash('Seleccione al menos una cuota para imprimir.', 'warning')
        return redirect(url_for('cuotas.imprimir_seleccionar_mes'))

    mes = request.form.get('mes', type=int)
    ano = request.form.get('ano', type=int)

    ids = [int(id) for id in ids]
    # BUGFIX: filtrar por empresa (join a Socio).
    cuotas = (
        CuotaSocial.query
        .join(Socio)
        .filter(
            CuotaSocial.id.in_(ids),
            Socio.id_empresa == empresa.id,
        )
        .all()
    )

    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.5*inch, leftMargin=0.5*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)

    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle('Titulo', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=12)
    estilo_normal = styles['Normal']

    nombre_club = empresa.nombre if empresa else "Club"

    elementos = []

    for idx, cuota in enumerate(cuotas):
        if idx > 0:
            elementos.append(PageBreak())

        elementos.append(Paragraph(f"{nombre_club}", estilo_titulo))
        elementos.append(Paragraph("Comprobante de Cuota Social", estilo_titulo))
        elementos.append(Spacer(1, 0.2*inch))

        data = [
            ['Período:', cuota.periodo],
            ['Socio:', f"{cuota.socio.apellido}, {cuota.socio.nombre}"],
            ['Nro. Socio:', cuota.socio.numero_socio],
            ['Monto:', f"${cuota.monto:.2f}"],
            ['Vencimiento:', cuota.fecha_vencimiento.strftime('%d/%m/%Y')],
            ['Estado:', cuota.estado.capitalize()]
        ]

        tabla = Table(data, colWidths=[2*inch, 4*inch])
        tabla.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('BACKGROUND', (0,0), (0,-1), colors.lightgrey),
        ]))
        elementos.append(tabla)
        elementos.append(Spacer(1, 0.2*inch))

        elementos.append(Paragraph("Firma del socio:", estilo_normal))
        elementos.append(Spacer(1, 0.5*inch))
        elementos.append(Paragraph("_________________________", estilo_normal))

    doc.build(elementos)
    buffer.seek(0)

    return send_file(buffer, download_name=f"cuotas_{mes:02d}_{ano}.pdf",
                     as_attachment=True, mimetype='application/pdf')