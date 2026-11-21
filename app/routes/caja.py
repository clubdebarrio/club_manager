# app/routes/caja.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, send_file, current_app,
)
from app import db
from app.models import (
    CuentaBancaria, Movimiento,
    Cheque, PlanCuenta,
    AsientoContable, LineaAsiento,  
)
from app.services.contabilidad_service import ContabilidadService 
from app.services.cuenta_contable_resolver import CuentaContableResolver
from app.forms import CuentaForm, MovimientoForm, ConciliacionForm
from app.services.caja_service import CajaService
from app.utils import current_empresa, local_to_utc, utc_to_local
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import or_, and_, case, func
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar
import json
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
import io

caja_bp = Blueprint('caja', __name__, url_prefix='/caja')


# ============================================================
# Index y listado de cuentas
# ============================================================

@caja_bp.route('/')
@requiere_ver('caja')
def index():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # Traemos todas las cuentas activas
    cuentas_raw = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, activo=True
    ).all()

    # Definimos el orden de prioridad por liquidez
    prioridad = {'efectivo': 1, 'banco': 2, 'cartera': 3}
    
    # Ordenamos: primero por prioridad de tipo, luego por nombre alfabéticamente
    cuentas = sorted(
        cuentas_raw, 
        key=lambda c: (prioridad.get(c.tipo, 4), c.nombre.lower())
    )

    return render_template('caja/index.html', cuentas=cuentas)

@caja_bp.route('/cuentas')
@requiere_ver('caja')
def listar_cuentas():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuentas_con_estado = CajaService.cuentas_con_estado_saldo(empresa.id)
    hay_desincronizadas = any(i['desincronizada'] for i in cuentas_con_estado)

    return render_template(
        'caja/cuentas.html',
        cuentas_con_estado=cuentas_con_estado,
        hay_desincronizadas=hay_desincronizadas,
    )


def _parse_date(s):
    if not s:
        return None
    try:
        # Convertimos la fecha del filtro (Argentina) a UTC
        return local_to_utc(datetime.strptime(s, '%Y-%m-%d'))
    except ValueError:
        return None


# ============================================================
# Extracto de cuenta
# ============================================================

@caja_bp.route('/cuenta/<int:id>')
@requiere_ver('caja')
def detalle_cuenta(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta = CuentaBancaria.query.get_or_404(id)
    if cuenta.id_empresa != empresa.id:
        flash('Cuenta no pertenece a la empresa actual.', 'danger')
        return redirect(url_for('caja.listar_cuentas'))

    fecha_desde = _parse_date(request.args.get('fecha_desde'))
    fecha_hasta = _parse_date(request.args.get('fecha_hasta'))
    ver_anulados = request.args.get('ver_anulados', '0') == '1'
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)

    extracto = CajaService.obtener_extracto(
        cuenta,
        page=page,
        per_page=per_page,
        fecha_desde=fecha_desde,
        fecha_hasta=(fecha_hasta + timedelta(days=1)) if fecha_hasta else None,
        ver_anulados=ver_anulados,
    )

    return render_template(
        'caja/detalle_cuenta.html',
        cuenta=cuenta,
        movimientos=extracto['movimientos'],
        saldo_inicial=extracto['saldo_inicial'],
        saldo_final=extracto['saldo_final'],
        page=extracto['page'],
        total_pages=extracto['total_pages'],
        hay_filtros=extracto['hay_filtros'],
        ver_anulados=ver_anulados,
        fecha_desde=request.args.get('fecha_desde'),
        fecha_hasta=request.args.get('fecha_hasta'),
    )


@caja_bp.route('/cuenta/<int:id>/exportar')
@requiere_ver('caja')
def exportar_extracto(id):
    """
    Exporta el extracto de una cuenta a Excel con filtros de fecha opcionales.
    Genera un archivo con: Fecha, Descripción, Debe, Haber, Saldo, Conciliado.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta = CuentaBancaria.query.get_or_404(id)
    if cuenta.id_empresa != empresa.id:
        flash('Cuenta no encontrada.', 'danger')
        return redirect(url_for('caja.listar_cuentas'))

    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')

    query = Movimiento.query.filter_by(
        id_cuenta=cuenta.id, id_empresa=empresa.id
    )

    fecha_desde_dt = None
    fecha_hasta_dt = None
    if fecha_desde:
        try:
            fecha_desde_dt = local_to_utc(datetime.strptime(fecha_desde, '%Y-%m-%d'))
            query = query.filter(Movimiento.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            fecha_hasta_dt = local_to_utc(datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
            query = query.filter(Movimiento.fecha < fecha_hasta_dt)
        except ValueError:
            pass

    movimientos = query.order_by(
        Movimiento.fecha.asc(), Movimiento.id.asc()
    ).all()

    saldo_inicial = cuenta.saldo_inicial or 0
    if fecha_desde_dt:
        anteriores = Movimiento.query.filter(
            Movimiento.id_cuenta == cuenta.id,
            Movimiento.id_empresa == empresa.id,
            Movimiento.fecha < fecha_desde_dt,
        ).all()
        for m in anteriores:
            if m.tipo == 'ingreso':
                saldo_inicial += m.monto
            else:
                saldo_inicial -= m.monto

    wb = Workbook()
    ws = wb.active
    ws.title = "Extracto"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    bold_font = Font(bold=True)
    currency_fmt = '#,##0.00'

    ws.cell(row=1, column=1, value=f"Extracto - {cuenta.nombre}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"Empresa: {empresa.nombre}")
    if fecha_desde or fecha_hasta:
        rango = f"Período: {fecha_desde or 'inicio'} a {fecha_hasta or 'hoy'}"
        ws.cell(row=3, column=1, value=rango)

    headers = ['Fecha', 'Descripción', 'Debe', 'Haber', 'Saldo', 'Conciliado']
    start_row = 5
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    row_idx = start_row + 1
    ws.cell(row=row_idx, column=1, value="SALDO INICIAL")
    ws.cell(row=row_idx, column=2, value="")
    ws.cell(row=row_idx, column=3, value="")
    ws.cell(row=row_idx, column=4, value="")
    ws.cell(row=row_idx, column=5, value=float(saldo_inicial))
    ws.cell(row=row_idx, column=5).number_format = currency_fmt
    ws.cell(row=row_idx, column=6, value="")
    for col in range(1, 7):
        ws.cell(row=row_idx, column=col).font = bold_font
        ws.cell(row=row_idx, column=col).fill = PatternFill(
            start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
        )
        ws.cell(row=row_idx, column=col).border = thin_border
    row_idx += 1

    saldo = saldo_inicial
    for m in movimientos:
        if m.tipo == 'ingreso':
            saldo += m.monto
            debe = m.monto
            haber = 0
        else:
            saldo -= m.monto
            debe = 0
            haber = m.monto

        ws.cell(row=row_idx, column=1, value=utc_to_local(m.fecha).strftime('%d/%m/%Y %H:%M'))
        ws.cell(row=row_idx, column=2, value=m.descripcion)
        ws.cell(row=row_idx, column=3, value=float(debe))
        ws.cell(row=row_idx, column=3).number_format = currency_fmt
        ws.cell(row=row_idx, column=4, value=float(haber))
        ws.cell(row=row_idx, column=4).number_format = currency_fmt
        ws.cell(row=row_idx, column=5, value=float(saldo))
        ws.cell(row=row_idx, column=5).number_format = currency_fmt
        ws.cell(row=row_idx, column=6, value='Sí' if m.conciliado else 'No')
        for col in range(1, 7):
            ws.cell(row=row_idx, column=col).border = thin_border
        row_idx += 1

    ws.cell(row=row_idx, column=4, value="SALDO FINAL")
    ws.cell(row=row_idx, column=5, value=float(saldo))
    ws.cell(row=row_idx, column=5).number_format = currency_fmt
    for col in range(4, 7):
        ws.cell(row=row_idx, column=col).font = bold_font
        ws.cell(row=row_idx, column=col).fill = PatternFill(
            start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
        )
        ws.cell(row=row_idx, column=col).border = thin_border

    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 45
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 12

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    nombre_archivo = f"extracto_{cuenta.nombre.replace(' ', '_')}.xlsx"
    return send_file(
        output,
        download_name=nombre_archivo,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ============================================================
# CRUD de cuentas
# ============================================================

@caja_bp.route('/cuentas/nueva', methods=['GET', 'POST'])
@requiere_editar('caja')
def nueva_cuenta():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = CuentaForm()
    if form.validate_on_submit():
        cuenta = CuentaBancaria(
            nombre=form.nombre.data,
            tipo=form.tipo.data,
            saldo_inicial=form.saldo_inicial.data,
            saldo_actual=form.saldo_inicial.data,
            numero_cuenta=form.numero_cuenta.data,
            cbu=form.cbu.data,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(cuenta)
        db.session.flush()                                       # ← NUEVO
        auditar('crear', 'CuentaBancaria', cuenta.id, despues=cuenta)  # ← NUEVO
        db.session.commit()
        flash(
            'Cuenta creada. Ahora asignale una cuenta contable en '
            '"Asignar cuentas contables".',
            'success'
        )
        return redirect(url_for('contabilidad.asignar_cuentas_bancarias'))

    return render_template('caja/cuenta_form.html', form=form)


@caja_bp.route('/cuentas/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('caja')
def editar_cuenta(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta = CuentaBancaria.query.get_or_404(id)
    if cuenta.id_empresa != empresa.id:
        flash('Cuenta no encontrada.', 'danger')
        return redirect(url_for('caja.listar_cuentas'))

    form = CuentaForm(obj=cuenta)
    if form.validate_on_submit():
        # Snapshot del estado ANTES de modificar (para la auditoría)
        estado_antes = {                                          # ← NUEVO
            'nombre': cuenta.nombre,
            'tipo': cuenta.tipo,
            'numero_cuenta': cuenta.numero_cuenta,
            'cbu': cuenta.cbu,
            'activo': cuenta.activo,
        }

        cuenta.nombre = form.nombre.data
        cuenta.tipo = form.tipo.data
        cuenta.numero_cuenta = form.numero_cuenta.data
        cuenta.cbu = form.cbu.data
        cuenta.activo = form.activo.data

        db.session.flush()                                        # ← NUEVO
        auditar(                                                  # ← NUEVO
            'editar', 'CuentaBancaria', cuenta.id,
            antes=estado_antes, despues=cuenta,
        )
        db.session.commit()
        flash('Cuenta actualizada.', 'success')
        return redirect(url_for('caja.listar_cuentas'))

    return render_template('caja/cuenta_form.html', form=form, cuenta=cuenta)


# ============================================================
# Nuevo movimiento (simple / multi)
# ============================================================

@caja_bp.route('/movimientos/nuevo', methods=['GET', 'POST'])
@requiere_editar('caja')
def nuevo_movimiento():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta_id = request.args.get('cuenta', type=int)
    if not cuenta_id:
        flash('Elegí una cuenta para registrar el movimiento.', 'warning')
        return redirect(url_for('caja.index'))

    cuenta = CuentaBancaria.query.get(cuenta_id)
    if (not cuenta or not cuenta.activo
            or cuenta.id_empresa != empresa.id):
        flash('Cuenta no válida.', 'danger')
        return redirect(url_for('caja.index'))

    if cuenta.tipo == 'cartera':
        flash('La Cartera de Cheques no admite movimientos manuales. '
              'Se llena automáticamente al recibir cheques.', 'warning')
        return redirect(url_for('caja.index'))

    if not cuenta.id_cuenta_contable:
        flash(
            f'La cuenta "{cuenta.nombre}" no tiene una cuenta contable '
            f'asignada. Asignala antes de registrar movimientos.',
            'warning'
        )
        return redirect(url_for('contabilidad.asignar_cuentas_bancarias'))

    form = MovimientoForm()

    cuentas_elegibles = PlanCuenta.imputables(
        empresa.id,
        tipos=['Recurso', 'Gasto', 'Activo', 'Pasivo', 'Patrimonio'],
        solo_manuales=True,
    )
    # Excluir la propia cuenta contable del banco/caja
    if cuenta.id_cuenta_contable:
        cuentas_elegibles = [
            c for c in cuentas_elegibles
            if c.id != cuenta.id_cuenta_contable
        ]
    form.id_cuenta_contable.choices = [
        (c.id, f"{c.codigo} - {c.nombre}") for c in cuentas_elegibles
    ]

    form.id_cheque.choices = [(0, 'Ninguno')] + [
        (c.id, f"{c.numero} - ${c.monto} ({c.estado})")
        for c in Cheque.query.filter_by(estado='en_cartera', tipo='recibido',
                                        id_empresa=empresa.id).all()
    ]

    cuentas_splits_orm = CuentaBancaria.query.filter(
        CuentaBancaria.activo == True,
        CuentaBancaria.id_empresa == empresa.id,
        CuentaBancaria.tipo != 'cartera',
    ).order_by(CuentaBancaria.nombre).all()

    metodos_por_tipo = {
        'efectivo': ['efectivo', 'cheque'],
        'banco': ['transferencia', 'deposito_efectivo', 'deposito_cheque',
                  'debito', 'credito'],
    }
    metodos_por_tipo_multi = {
        'efectivo': ['efectivo'],
        'banco': ['transferencia', 'deposito_efectivo', 'deposito_cheque',
                  'debito', 'credito'],
    }
    nombres_metodos = {
        'efectivo': 'Efectivo',
        'cheque': 'Cheque',
        'transferencia': 'Transferencia',
        'deposito_efectivo': 'Depósito en efectivo',
        'deposito_cheque': 'Depósito de cheque',
        'debito': 'Débito',
        'credito': 'Crédito',
    }

    modo = (request.form.get('modo')
            or request.args.get('modo')
            or 'simple')
    if modo not in ('simple', 'multi'):
        modo = 'simple'

    splits_iniciales = []
    if request.method == 'POST':
        try:
            splits_iniciales = json.loads(
                request.form.get('splits_json', '[]')
            ) or []
        except Exception:
            splits_iniciales = []

    cuentas_splits_data = [
        {'id': c.id, 'nombre': c.nombre, 'tipo': c.tipo}
        for c in cuentas_splits_orm
    ]

    def _render():
        return render_template(
            'caja/movimiento_form.html',
            form=form,
            cuenta_fijada=cuenta,
            saldo_disponible=cuenta.saldo_actual,
            cuentas_splits=cuentas_splits_data,
            metodos_por_tipo=metodos_por_tipo,
            metodos_por_tipo_multi=metodos_por_tipo_multi,
            nombres_metodos=nombres_metodos,
            modo=modo,
            splits_iniciales=splits_iniciales,
        )

    if form.validate_on_submit():
        fecha_utc = local_to_utc(form.fecha.data)
        cuenta_contable = PlanCuenta.query.get(form.id_cuenta_contable.data)
        if not cuenta_contable or cuenta_contable.id_empresa != empresa.id:
            flash('Cuenta contable inválida.', 'danger')
            return _render()

        if not cuenta_contable.es_imputable:
            flash(
                f'La cuenta {cuenta_contable.codigo} - {cuenta_contable.nombre} '
                f'no admite movimientos.',
                'danger'
            )
            return _render()

        # Red de seguridad: contrapartida no puede ser la del propio banco
        if (cuenta.id_cuenta_contable
                and cuenta_contable.id == cuenta.id_cuenta_contable):
            flash(
                f'La contrapartida no puede ser la misma cuenta contable del '
                f'banco/caja. Si querés registrar un ingreso, elegí una cuenta '
                f'de Recurso (por ejemplo, "4.5 Donaciones").',
                'danger'
            )
            return _render()

        # ==================== MODO SIMPLE ====================
        if modo == 'simple':
            if not form.monto.data or form.monto.data <= 0:
                flash('El monto debe ser mayor a 0.', 'danger')
                return _render()
            if not form.metodo_pago.data:
                flash('Elegí un método de pago.', 'danger')
                return _render()

            es_cheque = (form.metodo_pago.data == 'cheque')

            try:
                if es_cheque:
                    # ---------- CASO CHEQUE ----------
                    movimiento = _registrar_movimiento_con_cheque(
                        form=form,
                        cuenta_contable=cuenta_contable,
                        empresa=empresa,
                        fecha_utc=fecha_utc
                    )
                    # _registrar_movimiento_con_cheque ya hizo audit y
                    # generó los asientos correspondientes.
                    return redirect(url_for(
                        'caja.detalle_cuenta',
                        id=movimiento.id_cuenta if movimiento else cuenta.id
                    ))
                else:
                    # ---------- CASO NORMAL (sin cheque) ----------
                    data = {
                        'id_cuenta': cuenta.id,
                        'id_cuenta_contable': cuenta_contable.id,
                        'fecha': fecha_utc,
                        'tipo': form.tipo.data,
                        'monto': form.monto.data,
                        'descripcion': form.descripcion.data,
                        'metodo_pago': form.metodo_pago.data,
                        'comprobante': form.comprobante.data,
                        'id_empresa': empresa.id,
                    }
                    if form.metodo_pago.data in ['efectivo', 'deposito_efectivo',
                                                  'debito', 'credito']:
                        data['conciliado'] = True
                    else:
                        data['conciliado'] = form.conciliado.data
                    if form.id_cheque.data and form.id_cheque.data != 0:
                        data['id_cheque'] = form.id_cheque.data

                    movimiento = CajaService.registrar_movimiento(
                        data, commit=False
                    )
                    ContabilidadService.generar_asiento(movimiento)
                    auditar(
                        'crear', 'Movimiento', movimiento.id,
                        despues=movimiento,
                    )
                    db.session.commit()
                    flash('Movimiento registrado exitosamente.', 'success')
                    return redirect(url_for(
                        'caja.detalle_cuenta', id=cuenta.id
                    ))

            except ValueError as e:
                db.session.rollback()
                flash(str(e), 'danger')
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception('Error al registrar movimiento')
                flash(f'Error al registrar movimiento: {str(e)}', 'danger')

            return _render()

        # ==================== MODO MULTI ====================
        try:
            splits = json.loads(request.form.get('splits_json', '[]'))
        except Exception:
            splits = []

        if not splits:
            flash('Cargá al menos un medio de pago.', 'warning')
            return _render()

        splits_norm = []
        for idx, s in enumerate(splits):
            if not s.get('id_cuenta'):
                flash(f'Medio #{idx + 1}: falta la cuenta.', 'danger')
                return _render()
            if not s.get('metodo_pago'):
                flash(f'Medio #{idx + 1}: falta el método de pago.', 'danger')
                return _render()
            if s.get('metodo_pago') == 'cheque':
                flash(
                    f'Medio #{idx + 1}: en modo multi-método no se admiten '
                    f'cheques. Registrá el cheque como un movimiento aparte '
                    f'(modo "Un solo método").',
                    'danger'
                )
                return _render()

            cuenta_split = CuentaBancaria.query.get(s['id_cuenta'])
            if not cuenta_split or cuenta_split.id_empresa != empresa.id:
                flash(f'Medio #{idx + 1}: cuenta inválida.', 'danger')
                return _render()

            monto_raw = s.get('monto')
            monto = None
            try:
                monto = Decimal(str(monto_raw))
            except Exception:
                monto = None
            if monto is None:
                try:
                    limpio = str(monto_raw).replace('.', '').replace(',', '.')
                    monto = Decimal(limpio)
                except Exception:
                    monto = None
            if monto is None:
                try:
                    monto = Decimal(str(float(monto_raw)))
                except Exception:
                    monto = None

            if monto is None:
                flash(
                    f'Medio #{idx + 1}: no se pudo interpretar el monto.',
                    'danger'
                )
                return _render()
            if monto <= 0:
                flash(
                    f'Medio #{idx + 1}: el monto debe ser mayor a 0.',
                    'danger'
                )
                return _render()

            splits_norm.append({
                'id_cuenta': int(s['id_cuenta']),
                'metodo_pago': str(s['metodo_pago']),
                'monto': str(monto),
                'conciliado': bool(s.get('conciliado', False)),
            })

        operacion = {
            'fecha': fecha_utc,
            'tipo': form.tipo.data,
            'id_cuenta_contable': cuenta_contable.id,
            'descripcion': form.descripcion.data,
            'id_empresa': empresa.id,
            'splits': splits_norm,
        }

        try:
            # commit=False para poder loguear ANTES del commit final
            movimientos = CajaService.registrar_operacion(
                operacion, commit=False
            )
            for m in movimientos:                                 # ← NUEVO
                auditar(                                          # ← NUEVO
                    'crear', 'Movimiento', m.id, despues=m,
                )
            db.session.commit()                                   # ← NUEVO
            flash(
                f'Operación registrada con {len(movimientos)} movimientos.',
                'success'
            )
            return redirect(url_for('caja.detalle_cuenta',
                                    id=splits_norm[0]['id_cuenta']))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Error al registrar operación')
            flash(f'Error al registrar operación: {str(e)}', 'danger')
        return _render()

    for field, errors in form.errors.items():
        for error in errors:
            flash(f'{field}: {error}', 'danger')

    return _render()


# ============================================================
# Conciliación
# ============================================================

@caja_bp.route('/movimientos/conciliar', methods=['GET', 'POST'])
@requiere_editar('caja')
def conciliar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = ConciliacionForm()
    if request.method == 'POST':
        ids = request.form.getlist('movimientos')
        if ids:
            ids = [int(i) for i in ids]
            cantidad = CajaService.conciliar_movimientos(ids, empresa.id)
            auditar(                                              # ← NUEVO
                'conciliar', 'Movimiento', None,
                nota=f'{cantidad} movimientos conciliados (IDs: {ids})',
            )
            db.session.commit()                                   # ← NUEVO
            flash(f'{cantidad} movimientos conciliados.', 'success')
            return redirect(url_for('caja.index'))
        else:
            flash('Selecciona al menos un movimiento.', 'warning')

    movimientos = Movimiento.query.filter_by(
        id_empresa=empresa.id, conciliado=False
    ).order_by(Movimiento.fecha.desc(), Movimiento.id.desc()).all()

    return render_template('caja/conciliar.html',
                           form=form, movimientos=movimientos)


# ============================================================
# APIs
# ============================================================

@caja_bp.route('/api/metodos_pago/<int:cuenta_id>')
@requiere_ver('caja')
def api_metodos_pago(cuenta_id):
    empresa = current_empresa()
    if not empresa:
        return jsonify([]), 400

    cuenta = CuentaBancaria.query.get_or_404(cuenta_id)
    if cuenta.id_empresa != empresa.id:
        return jsonify([]), 404

    if cuenta.tipo == 'efectivo':
        metodos = [
            {'value': 'efectivo', 'label': 'Efectivo'},
            {'value': 'cheque', 'label': 'Cheque'}
        ]
    else:
        metodos = [
            {'value': 'transferencia', 'label': 'Transferencia'},
            {'value': 'cheque', 'label': 'Cheque'},
            {'value': 'debito', 'label': 'Débito'},
            {'value': 'credito', 'label': 'Crédito'}
        ]
    return jsonify(metodos)


@caja_bp.route('/api/tipo_cuenta/<int:cuenta_id>')
@requiere_ver('caja')
def api_tipo_cuenta(cuenta_id):
    """Retorna el tipo (ingreso/egreso) para una cuenta contable."""
    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'Sin empresa'}), 400

    cuenta = PlanCuenta.query.get_or_404(cuenta_id)
    if cuenta.id_empresa != empresa.id:
        return jsonify({'error': 'Cuenta no encontrada'}), 404

    tipo = 'ingreso' if cuenta.tipo == 'Recurso' else 'egreso'
    return jsonify({'tipo': tipo})


@caja_bp.route('/api/preview_asiento', methods=['POST'])
@requiere_ver('caja')
def api_preview_asiento():
    """
    Devuelve el asiento que se generaría para un movimiento.

    La dirección (ingreso/egreso) se deduce:
      - Recurso  -> siempre ingreso
      - Gasto    -> siempre egreso
      - Activo / Pasivo / Patrimonio -> se usa el `tipo` recibido
    """
    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'No hay empresa configurada.'}), 400

    payload = request.get_json(silent=True) or request.form

    id_cuenta = payload.get('id_cuenta')
    id_cuenta_contable = payload.get('id_cuenta_contable')
    tipo = payload.get('tipo')

    if not id_cuenta or not id_cuenta_contable:
        return jsonify({'error': 'Faltan datos.'}), 400

    cuenta = CuentaBancaria.query.get(id_cuenta)
    if not cuenta or cuenta.id_empresa != empresa.id:
        return jsonify({'error': 'Cuenta de caja/banco inválida.'}), 400

    cuenta_contable = PlanCuenta.query.get(id_cuenta_contable)
    if not cuenta_contable or cuenta_contable.id_empresa != empresa.id:
        return jsonify({'error': 'Cuenta contable inválida.'}), 400

    activo_id = cuenta.id_cuenta_contable
    activo = PlanCuenta.query.get(activo_id) if activo_id else None
    if not activo:
        return jsonify({
            'error': f'La cuenta de caja/banco "{cuenta.nombre}" no tiene '
                     f'una cuenta contable de Activo asociada.'
        }), 400

    if activo.id == cuenta_contable.id:
        return jsonify({
            'error': 'La contrapartida no puede ser la misma cuenta de activo.'
        }), 400

    if cuenta_contable.tipo == 'Recurso':
        tipo_efectivo = 'ingreso'
    elif cuenta_contable.tipo == 'Gasto':
        tipo_efectivo = 'egreso'
    else:
        tipo_efectivo = tipo if tipo in ('ingreso', 'egreso') else 'ingreso'

    if tipo_efectivo == 'ingreso':
        debe, haber = activo, cuenta_contable
    else:
        debe, haber = cuenta_contable, activo

    return jsonify({
        'tipo': tipo_efectivo,
        'debe': {'codigo': debe.codigo, 'nombre': debe.nombre},
        'haber': {'codigo': haber.codigo, 'nombre': haber.nombre},
        'contrapartida_tipo': cuenta_contable.tipo,
    })

def _registrar_movimiento_con_cheque(form, cuenta_contable, empresa, fecha_utc):
    """
    Registra un movimiento con método 'cheque'.
    """
    tipo_operacion = form.tipo.data
    tipo_cheque = 'recibido' if tipo_operacion == 'ingreso' else 'emitido'
    estado_inicial = 'en_cartera' if tipo_cheque == 'recibido' else 'pendiente'

    # Si no se especificó fecha de cobro, es "al día": usar la de emisión.
    fecha_cobro = form.cheque_fecha_cobro.data or form.cheque_fecha_emision.data

    # Para cheques emitidos, el titular somos siempre nosotros (el club).
    titular = form.cheque_titular.data.strip() if form.cheque_titular.data else ''
    if tipo_cheque == 'emitido':
        titular = empresa.nombre

    cheque = Cheque(
        numero=form.cheque_numero.data.strip(),
        banco_emisor=form.cheque_banco_emisor.data.strip(),
        titular=titular,
        monto=form.monto.data,
        # Usamos las fechas del formulario, NO fecha_utc (porque son DateField)
        fecha_emision=form.cheque_fecha_emision.data,
        fecha_cobro=fecha_cobro,
        tipo=tipo_cheque,
        estado=estado_inicial,
        observaciones=form.descripcion.data,
        id_empresa=empresa.id,
    )
    db.session.add(cheque)
    db.session.flush()

    if tipo_cheque == 'recibido':
        # ---------- CASO A: cheque recibido ----------
        cartera = CuentaBancaria.query.filter_by(
            id_empresa=empresa.id, tipo='cartera', activo=True
        ).first()
        if not cartera:
            raise ValueError(
                'No existe la Cartera de Cheques. '
                'Corré: flask caja:seed-cartera-cheques'
            )

        data = {
            'id_cuenta': cartera.id,
            'id_cuenta_contable': cuenta_contable.id,
            'fecha': fecha_utc, # Acá sí va fecha_utc (es un DateTime)
            'tipo': 'ingreso',
            'monto': form.monto.data,
            'descripcion': form.descripcion.data,
            'metodo_pago': 'cheque',
            'comprobante': form.comprobante.data or cheque.numero,
            'id_cheque': cheque.id,
            'id_empresa': empresa.id,
            'conciliado': False,
            '_forzar_cuenta': True,   # no redirigir a otra cartera
        }
        movimiento = CajaService.registrar_movimiento(data, commit=False)
        ContabilidadService.generar_asiento(movimiento)

        auditar('crear', 'Cheque', cheque.id, despues=cheque)
        auditar('crear', 'Movimiento', movimiento.id, despues=movimiento)
        db.session.commit()
        flash(
            f'Cheque #{cheque.numero} recibido. Guardado en la '
            f'Cartera de Cheques. Vence el '
            f'{cheque.fecha_vencimiento.strftime("%d/%m/%Y")}.',
            'success'
        )
        return movimiento

    else:
        # ---------- CASO B: cheque emitido ----------
        cuenta_cheques_pagar = CuentaContableResolver.resolver_o_fallar(
            empresa.id, 'cheques_a_pagar'
        )

        asiento = AsientoContable(
            fecha=fecha_utc, # Acá sí va fecha_utc (es un DateTime)
            descripcion=(
                f'Emisión cheque {cheque.numero} - '
                f'{form.descripcion.data}'
            ),
            id_movimiento=None,
            id_empresa=empresa.id,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id,
            id_cuenta=cuenta_contable.id,
            debe=form.monto.data,
            haber=0,
        ))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id,
            id_cuenta=cuenta_cheques_pagar.id,
            debe=0,
            haber=form.monto.data,
        ))

        auditar('crear', 'Cheque', cheque.id, despues=cheque)
        auditar('crear', 'AsientoContable', asiento.id, despues=asiento)
        db.session.commit()
        flash(
            f'Cheque #{cheque.numero} emitido. Se pagará desde el banco '
            f'cuando llegue su vencimiento ({cheque.fecha_vencimiento.strftime("%d/%m/%Y")}).',
            'success'
        )
        return None   # no hay movimiento asociado

@caja_bp.route('/movimiento/<int:id>/anular', methods=['POST'])
@requiere_editar('caja')
def anular_movimiento(id):
    """Anula un movimiento creando su espejo inverso."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    mov = Movimiento.query.get_or_404(id)
    if mov.id_empresa != empresa.id:
        flash('Movimiento no encontrado.', 'danger')
        return redirect(url_for('caja.index'))

    motivo = (request.form.get('motivo') or '').strip() or None

    try:
        from app.utils_db import transaccion
        with transaccion():
            original, espejo = CajaService.anular_movimiento(
                mov.id, motivo=motivo
            )
            auditar(
                'anular', 'Movimiento', original.id,
                despues=original,
                nota=(
                    f'Movimiento #{original.id} anulado. '
                    f'Motivo: {motivo or "no especificado"}. '
                    f'Espejo: movimiento #{espejo.id}.'
                ),
            )
            auditar(
                'crear', 'Movimiento', espejo.id,
                despues=espejo,
                nota=f'Es el espejo de anulación del movimiento #{original.id}',
            )

        flash(
            f'Movimiento #{original.id} anulado correctamente. '
            f'Se generó el contra-asiento.',
            'success',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al anular movimiento')
        flash(f'Error al anular: {str(e)}', 'danger')

    # Volver al detalle de la cuenta del movimiento
    return redirect(url_for('caja.detalle_cuenta', id=mov.id_cuenta))    

@caja_bp.route('/operacion/<id_operacion>/anular', methods=['POST'])
@requiere_editar('caja')
def anular_operacion_route(id_operacion):
    """Anula una operación multi-método completa."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    movs = Movimiento.query.filter_by(
        id_operacion=id_operacion, id_empresa=empresa.id
    ).all()
    if not movs:
        flash('Operación no encontrada.', 'danger')
        return redirect(url_for('caja.index'))

    motivo = (request.form.get('motivo') or '').strip() or None
    id_cuenta_redirect = movs[0].id_cuenta

    try:
        from app.utils_db import transaccion
        with transaccion():
            originales, espejos = CajaService.anular_operacion(
                id_operacion, motivo=motivo
            )
            for orig, espejo in zip(originales, espejos):
                auditar(
                    'anular', 'Movimiento', orig.id,
                    despues=orig,
                    nota=(
                        f'Operación anulada completa. '
                        f'Motivo: {motivo or "no especificado"}. '
                        f'Espejo: #{espejo.id}.'
                    ),
                )
                auditar(
                    'crear', 'Movimiento', espejo.id,
                    despues=espejo,
                    nota=f'Espejo de anulación del movimiento #{orig.id}',
                )

        flash(
            f'Operación anulada ({len(originales)} movimientos revertidos).',
            'success',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error al anular operación')
        flash(f'Error al anular: {str(e)}', 'danger')

    return redirect(url_for('caja.detalle_cuenta', id=id_cuenta_redirect))

@caja_bp.route('/movimiento/<int:id>/recibo')
@requiere_ver('caja')
def imprimir_recibo(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    mov = Movimiento.query.get_or_404(id)
    if mov.id_empresa != empresa.id:
        flash('Movimiento no encontrado.', 'danger')
        return redirect(url_for('caja.index'))

    # Crear el PDF en memoria
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, 
        rightMargin=2*cm, leftMargin=2*cm, 
        topMargin=2*cm, bottomMargin=2*cm
    )
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Estilos personalizados
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], alignment=1, fontSize=18, spaceAfter=20)
    normal_style = styles['Normal']
    bold_style = ParagraphStyle('Bold', parent=normal_style, fontName='Helvetica-Bold')
    center_style = ParagraphStyle('Center', parent=normal_style, alignment=1)

    # Encabezado (Datos del Club)
    elements.append(Paragraph(f"<b>{empresa.nombre}</b>", ParagraphStyle('Empresa', parent=styles['Heading2'], alignment=1)))
    if empresa.cuit:
        elements.append(Paragraph(f"CUIT: {empresa.cuit}", center_style))
    if empresa.direccion:
        elements.append(Paragraph(f"Dirección: {empresa.direccion}", center_style))
    elements.append(Spacer(1, 20))

    # Título del comprobante
    tipo_texto = "RECIBO DE INGRESO" if mov.tipo == 'ingreso' else "COMPROBANTE DE EGRESO"
    elements.append(Paragraph(tipo_texto, title_style))
    elements.append(Spacer(1, 20))

    # Tabla de datos
    contrapartida = mov.cuenta_contable.nombre if mov.cuenta_contable else '—'
    
    data = [
        ["Nº de Operación:", f"{mov.id:06d}"],
        ["Fecha y Hora:", utc_to_local(mov.fecha).strftime('%d/%m/%Y %H:%M')],
        ["Cuenta:", mov.cuenta.nombre if mov.cuenta else '—'],
        ["Contrapartida:", contrapartida],
        ["Método de Pago:", mov.metodo_pago.replace('_', ' ').capitalize()],
        ["Concepto:", mov.descripcion],
        ["Nº Comprobante:", mov.comprobante or "—"],
    ]

    t = Table(data, colWidths=[4*cm, 12*cm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 30))

    # Monto Total
    amount_data = [
        ["MONTO TOTAL:", f"$ {mov.monto:,.2f}"]
    ]
    t_amount = Table(amount_data, colWidths=[4*cm, 12*cm])
    t_amount.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 14),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(t_amount)
    elements.append(Spacer(1, 50))

    # Firma
    elements.append(Paragraph("_________________________________________", normal_style))
    elements.append(Paragraph("Firma y Aclaración", normal_style))

    # Construir el PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=f"recibo_{mov.id}.pdf",
        as_attachment=False, # Se abre en el navegador para imprimir directo
        mimetype='application/pdf'
    )