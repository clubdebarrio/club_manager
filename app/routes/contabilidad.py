# app/routes/contabilidad.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    send_file, current_app,
)
from decimal import Decimal
from app import db
from app.models import (
    AsientoContable, LineaAsiento, PlanCuenta, CuentaBancaria,
)
from app.forms import CuentaContableForm, PlanCuentaForm
from app.utils import current_empresa, local_to_utc, utc_to_local
from datetime import datetime, timedelta
from app.services.reporte_service import ReporteService
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

# Formato universal de moneda. Excel lo muestra con el separador
# del sistema del usuario: en español se ve "1.234,56".
CURRENCY_FMT = '#,##0.00'


def _write_money(ws, row, col, valor):
    """
    Escribe un valor monetario en la celda.
    - Si es None o 0, deja la celda vacía.
    - Si tiene valor, escribe el número con formato de moneda.
    """
    if valor is None or valor == 0:
        ws.cell(row=row, column=col, value=None)
        return
    c = ws.cell(row=row, column=col, value=float(valor))
    c.number_format = CURRENCY_FMT


contabilidad_bp = Blueprint('contabilidad', __name__, url_prefix='/contabilidad')


@contabilidad_bp.route('/')
@requiere_ver('contabilidad')
def index():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')

    query = AsientoContable.query.filter_by(id_empresa=empresa.id)
    if fecha_desde:
        try:
            fecha_desde_dt = local_to_utc(datetime.strptime(fecha_desde, '%Y-%m-%d'))
            query = query.filter(AsientoContable.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            # Sumamos 1 día y lo pasamos a UTC
            fecha_hasta_dt = local_to_utc(datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
            query = query.filter(AsientoContable.fecha < fecha_hasta_dt)
        except ValueError:
            pass

    pagination = query.order_by(
        AsientoContable.fecha.desc(),
        AsientoContable.id.desc()
    ).paginate(
        page=page, per_page=per_page, error_out=False
    )
    asientos = pagination.items

    return render_template('contabilidad/index.html',
                         asientos=asientos,
                         pagination=pagination,
                         fecha_desde=fecha_desde,
                         fecha_hasta=fecha_hasta)


@contabilidad_bp.route('/asiento/<int:id>')
@requiere_ver('contabilidad')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    asiento = AsientoContable.query.get_or_404(id)
    if asiento.id_empresa != empresa.id:
        flash('Asiento no encontrado.', 'danger')
        return redirect(url_for('contabilidad.index'))

    lineas = LineaAsiento.query.filter_by(id_asiento=asiento.id).all()
    return render_template('contabilidad/detalle.html', asiento=asiento, lineas=lineas)


@contabilidad_bp.route('/plan-cuentas')
@requiere_ver('contabilidad')
def plan_cuentas():
    """Lista todas las cuentas del plan contable."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuentas = PlanCuenta.query.filter_by(
        id_empresa=empresa.id
    ).order_by(PlanCuenta.codigo).all()
    return render_template('contabilidad/plan_cuentas.html', cuentas=cuentas)


def _choices_padre(empresa_id, excluir_id=None):
    """Choices para el select de cuenta padre, con indentación jerárquica."""
    cuentas = (
        PlanCuenta.query
        .filter_by(id_empresa=empresa_id)
        .order_by(PlanCuenta.codigo)
        .all()
    )
    choices = [(0, '— Ninguna (cuenta raíz) —')]
    for c in cuentas:
        if excluir_id and c.id == excluir_id:
            continue
        indent = '    ' * (c.nivel - 1)
        choices.append((c.id, f"{c.codigo} {indent}{c.nombre}"))
    return choices


def _render_cuenta_form(form, cuenta=None, padre=None):
    """Renderiza el form de cuenta contable."""
    return render_template(
        'contabilidad/cuenta_form.html',
        form=form,
        cuenta=cuenta,
        padre=padre,
    )


@contabilidad_bp.route('/plan-cuentas/nueva', methods=['GET', 'POST'])
@requiere_editar('contabilidad')
def nueva_cuenta():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    padre_id = request.args.get('padre', type=int)
    if not padre_id:
        flash(
            'Las cuentas raíz no se crean desde aquí. '
            'Para agregar una subcuenta, usá el botón "+" en la fila '
            'de la cuenta padre.',
            'warning'
        )
        return redirect(url_for('contabilidad.plan_cuentas'))

    padre = PlanCuenta.query.get(padre_id)
    if not padre or padre.id_empresa != empresa.id:
        flash('Cuenta padre no encontrada.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    if not padre.puede_tener_subcuentas:
        fijos = PlanCuenta.SEGUNDO_NIVEL_FIJO.get(padre.tipo)
        if padre.nivel == 1 and fijos:
            flash(
                f'La cuenta "{padre.codigo} {padre.nombre}" ya tiene sus '
                f'dos divisiones ("Corriente" y "No Corriente"). '
                f'No se pueden agregar más subcuentas en este nivel.',
                'warning'
            )
        else:
            flash(
                f'La cuenta "{padre.codigo} {padre.nombre}" no admite '
                f'subcuentas (está inactiva o alcanzó el nivel máximo '
                f'{PlanCuenta.MAX_NIVEL}).',
                'danger'
            )
        return redirect(url_for('contabilidad.plan_cuentas'))

    form = PlanCuentaForm()

    codigo_sugerido = padre.siguiente_codigo_hijo()
    if not codigo_sugerido:
        flash('No se pudo calcular el siguiente código.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    form.id_padre.choices = [(padre.id, f"{padre.codigo} - {padre.nombre}")]

    if request.method == 'GET':
        form.codigo.data = codigo_sugerido
        form.id_padre.data = padre.id
        form.tipo.data = padre.tipo

    if form.validate_on_submit():
        codigo = (form.codigo.data or '').strip()
        if PlanCuenta.query.filter_by(
            codigo=codigo, id_empresa=empresa.id
        ).first():
            flash(f'Ya existe una cuenta con el código "{codigo}".', 'danger')
            return _render_cuenta_form(form, padre=padre)

        cuenta = PlanCuenta(
            codigo=codigo,
            nombre=(form.nombre.data or '').strip(),
            tipo=form.tipo.data,
            id_padre=padre.id,
            id_empresa=empresa.id,
            activo=form.activo.data,
            es_automatica=False,
        )
        db.session.add(cuenta)
        db.session.flush()
        auditar('crear', 'PlanCuenta', cuenta.id, despues=cuenta)
        db.session.commit()
        flash(f'Cuenta "{codigo} {cuenta.nombre}" creada.', 'success')
        return redirect(url_for('contabilidad.plan_cuentas'))

    return _render_cuenta_form(form, padre=padre)


@contabilidad_bp.route('/plan-cuentas/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('contabilidad')
def editar_cuenta(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta = PlanCuenta.query.get_or_404(id)
    if cuenta.id_empresa != empresa.id:
        flash('Cuenta no encontrada.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    form = PlanCuentaForm(obj=cuenta, cuenta_editando=cuenta)
    form.id_padre.choices = _choices_padre(empresa.id, excluir_id=cuenta.id)

    if form.validate_on_submit():
        codigo = form.codigo.data.strip()
        otra = PlanCuenta.query.filter_by(
            codigo=codigo, id_empresa=empresa.id
        ).first()
        if otra and otra.id != cuenta.id:
            flash(f'Ya existe otra cuenta con el código "{codigo}".', 'danger')
            return _render_cuenta_form(form, cuenta=cuenta)

        estado_antes = {
            'codigo': cuenta.codigo,
            'nombre': cuenta.nombre,
            'tipo': cuenta.tipo,
            'id_padre': cuenta.id_padre,
            'activo': cuenta.activo,
        }

        cuenta.codigo = codigo
        cuenta.nombre = form.nombre.data.strip()
        cuenta.tipo = form.tipo.data
        cuenta.id_padre = form.id_padre.data if form.id_padre.data else None
        cuenta.activo = form.activo.data

        db.session.flush()
        auditar(
            'editar', 'PlanCuenta', cuenta.id,
            antes=estado_antes, despues=cuenta,
        )
        db.session.commit()
        flash('Cuenta actualizada correctamente.', 'success')
        return redirect(url_for('contabilidad.plan_cuentas'))

    return _render_cuenta_form(form, cuenta=cuenta)


@contabilidad_bp.route('/plan-cuentas/eliminar/<int:id>', methods=['POST'])
@requiere_editar('contabilidad')
def eliminar_cuenta(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuenta = PlanCuenta.query.get_or_404(id)
    if cuenta.id_empresa != empresa.id:
        flash('Cuenta no encontrada.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    subcuentas = PlanCuenta.query.filter_by(id_padre=cuenta.id).count()
    if subcuentas > 0:
        flash('No se puede eliminar esta cuenta porque tiene subcuentas.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    usada = LineaAsiento.query.filter_by(id_cuenta=cuenta.id).first()
    if usada:
        flash('No se puede eliminar esta cuenta porque está siendo usada en asientos contables.', 'danger')
        return redirect(url_for('contabilidad.plan_cuentas'))

    datos_cuenta = {
        'codigo': cuenta.codigo,
        'nombre': cuenta.nombre,
        'tipo': cuenta.tipo,
        'id_padre': cuenta.id_padre,
        'id_empresa': cuenta.id_empresa,
    }
    auditar(
        'eliminar', 'PlanCuenta', cuenta.id,
        antes=datos_cuenta,
        nota=f'Cuenta "{cuenta.codigo} {cuenta.nombre}" eliminada',
    )
    db.session.delete(cuenta)
    db.session.commit()
    flash('Cuenta eliminada.', 'success')
    return redirect(url_for('contabilidad.plan_cuentas'))


@contabilidad_bp.route('/cuentas-bancarias/asignar', methods=['GET', 'POST'])
@requiere_editar('contabilidad')
def asignar_cuentas_bancarias():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cuentas = CuentaBancaria.query.filter_by(id_empresa=empresa.id).order_by(
        CuentaBancaria.nombre
    ).all()

    if request.method == 'POST':
        cambios = []
        for cb in cuentas:
            campo = request.form.get(f'cuenta_contable_{cb.id}')
            if campo is not None:
                valor_nuevo = int(campo) if campo != '0' else None
                if cb.id_cuenta_contable != valor_nuevo:
                    cambios.append({
                        'cuenta': cb.nombre,
                        'antes': cb.id_cuenta_contable,
                        'despues': valor_nuevo,
                    })
                cb.id_cuenta_contable = valor_nuevo

        db.session.flush()
        if cambios:
            auditar(
                'asignar_cuentas', 'CuentaBancaria', None,
                nota=f'{len(cambios)} cuenta(s) actualizada(s): '
                     f'{", ".join(c["cuenta"] for c in cambios)}',
            )
        db.session.commit()
        flash('Asignaciones actualizadas.', 'success')
        return redirect(url_for('contabilidad.asignar_cuentas_bancarias'))

    opciones = PlanCuenta.query.filter(
        PlanCuenta.tipo == 'Activo',
        PlanCuenta.id_empresa == empresa.id,
        PlanCuenta.activo == True,
    ).order_by(PlanCuenta.codigo).all()
    opciones = [c for c in opciones if c.es_hoja]

    cuentas_data = [{
        'cuenta': cb,
        'opciones': opciones,
        'seleccionada': cb.id_cuenta_contable,
    } for cb in cuentas]

    return render_template('contabilidad/asignar_cuentas.html',
                           cuentas_data=cuentas_data)


@contabilidad_bp.route('/exportar-excel')
@requiere_ver('contabilidad')
def exportar_excel():
    """
    Exporta los asientos contables a Excel con filtro de fechas.
    Incluye dos hojas:
      - 'Asientos': detalle línea por línea.
      - 'Sumas y Saldos': total debe/haber por cuenta contable.
    """
    from sqlalchemy import func
    from openpyxl.styles import numbers as xl_numbers

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde = request.args.get('fecha_desde')
    fecha_hasta = request.args.get('fecha_hasta')

    fecha_desde_dt = None
    fecha_hasta_dt = None
    if fecha_desde:
        try:
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d')
        except ValueError:
            pass
    if fecha_hasta:
        try:
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            pass

    # ---------------- Hoja 1: Asientos (detalle) ----------------
    query = AsientoContable.query.filter_by(id_empresa=empresa.id)
    if fecha_desde_dt:
        query = query.filter(AsientoContable.fecha >= fecha_desde_dt)
    if fecha_hasta_dt:
        query = query.filter(AsientoContable.fecha <= fecha_hasta_dt)
    asientos = query.order_by(AsientoContable.fecha.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Asientos"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    bold_font = Font(bold=True)
    currency_fmt = xl_numbers.FORMAT_CURRENCY_USD_SIMPLE

    headers = ['Fecha', 'Descripción', 'Cuenta', 'Código', 'Debe', 'Haber']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    row_idx = 2
    total_debe = 0
    total_haber = 0
    for asiento in asientos:
        lineas = LineaAsiento.query.filter_by(id_asiento=asiento.id).all()
        for linea in lineas:
            cuenta = linea.cuenta
            ws.cell(row=row_idx, column=1, value=utc_to_local(asiento.fecha).strftime('%d/%m/%Y %H:%M'))
            ws.cell(row=row_idx, column=2, value=asiento.descripcion)
            ws.cell(row=row_idx, column=3, value=cuenta.nombre)
            ws.cell(row=row_idx, column=4, value=cuenta.codigo)
            ws.cell(row=row_idx, column=5, value=float(linea.debe))
            ws.cell(row=row_idx, column=5).number_format = currency_fmt
            ws.cell(row=row_idx, column=6, value=float(linea.haber))
            ws.cell(row=row_idx, column=6).number_format = currency_fmt
            total_debe += linea.debe
            total_haber += linea.haber
            for col in range(1, 7):
                ws.cell(row=row_idx, column=col).border = thin_border
            row_idx += 1

    ws.cell(row=row_idx, column=4, value="TOTALES")
    ws.cell(row=row_idx, column=5, value=float(total_debe))
    ws.cell(row=row_idx, column=5).number_format = currency_fmt
    ws.cell(row=row_idx, column=6, value=float(total_haber))
    ws.cell(row=row_idx, column=6).number_format = currency_fmt
    for col in range(4, 7):
        ws.cell(row=row_idx, column=col).font = bold_font
        ws.cell(row=row_idx, column=col).fill = PatternFill(
            start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
        )
        ws.cell(row=row_idx, column=col).border = thin_border

    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 40
    ws.column_dimensions['C'].width = 30
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 15

    # ---------------- Hoja 2: Sumas y Saldos ----------------
    ws2 = wb.create_sheet("Sumas y Saldos")

    headers_ss = ['Código', 'Cuenta', 'Debe', 'Haber']
    for col, header in enumerate(headers_ss, 1):
        cell = ws2.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    query_ss = (
        db.session.query(
            PlanCuenta.codigo,
            PlanCuenta.nombre,
            func.coalesce(func.sum(LineaAsiento.debe), 0).label('total_debe'),
            func.coalesce(func.sum(LineaAsiento.haber), 0).label('total_haber'),
        )
        .select_from(LineaAsiento)
        .join(AsientoContable, AsientoContable.id == LineaAsiento.id_asiento)
        .join(PlanCuenta, PlanCuenta.id == LineaAsiento.id_cuenta)
        .filter(
            AsientoContable.id_empresa == empresa.id,
            PlanCuenta.id_empresa == empresa.id,
        )
    )
    if fecha_desde_dt:
        query_ss = query_ss.filter(AsientoContable.fecha >= fecha_desde_dt)
    if fecha_hasta_dt:
        query_ss = query_ss.filter(AsientoContable.fecha <= fecha_hasta_dt)

    query_ss = (
        query_ss
        .group_by(PlanCuenta.id, PlanCuenta.codigo, PlanCuenta.nombre)
        .order_by(PlanCuenta.codigo)
    )

    row_idx = 2
    total_debe_ss = 0
    total_haber_ss = 0

    for codigo, nombre, t_debe, t_haber in query_ss.all():
        ws2.cell(row=row_idx, column=1, value=codigo)
        ws2.cell(row=row_idx, column=2, value=nombre)
        ws2.cell(row=row_idx, column=3, value=float(t_debe))
        ws2.cell(row=row_idx, column=3).number_format = currency_fmt
        ws2.cell(row=row_idx, column=4, value=float(t_haber))
        ws2.cell(row=row_idx, column=4).number_format = currency_fmt
        for col in range(1, 5):
            ws2.cell(row=row_idx, column=col).border = thin_border
        total_debe_ss += t_debe
        total_haber_ss += t_haber
        row_idx += 1

    ws2.cell(row=row_idx, column=2, value="TOTALES")
    ws2.cell(row=row_idx, column=3, value=float(total_debe_ss))
    ws2.cell(row=row_idx, column=3).number_format = currency_fmt
    ws2.cell(row=row_idx, column=4, value=float(total_haber_ss))
    ws2.cell(row=row_idx, column=4).number_format = currency_fmt
    for col in range(2, 5):
        ws2.cell(row=row_idx, column=col).font = bold_font
        ws2.cell(row=row_idx, column=col).fill = PatternFill(
            start_color="E9ECEF", end_color="E9ECEF", fill_type="solid"
        )
        ws2.cell(row=row_idx, column=col).border = thin_border

    ws2.column_dimensions['A'].width = 15
    ws2.column_dimensions['B'].width = 40
    ws2.column_dimensions['C'].width = 15
    ws2.column_dimensions['D'].width = 15

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    nombre_archivo = "asientos_contables.xlsx"
    return send_file(
        output,
        download_name=nombre_archivo,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ============================================================
# Reportes contables
# ============================================================

@contabilidad_bp.route('/reportes')
@requiere_ver('contabilidad')
def reportes_index():
    """Página principal con links a los reportes disponibles."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    return render_template('contabilidad/reportes/index.html')


@contabilidad_bp.route('/reportes/libro-diario')
@requiere_ver('contabilidad')
def libro_diario():
    """Libro Diario: todos los asientos con sus líneas, ordenados por fecha."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    id_cuenta_filtro = request.args.get('id_cuenta', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 30)

    # Defaults: mes actual
    from datetime import date
    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        # Último día del mes
        if hoy.month == 12:
            fin_mes = hoy.replace(year=hoy.year + 1, month=1, day=1)
        else:
            fin_mes = hoy.replace(month=hoy.month + 1, day=1)
        from datetime import timedelta
        fecha_hasta_str = (fin_mes - timedelta(days=1)).strftime('%Y-%m-%d')

    # Parsear
    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        fecha_desde = None
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        fecha_hasta = None

    # Query base
    query = (
        AsientoContable.query
        .filter(AsientoContable.id_empresa == empresa.id)
    )
    if fecha_desde:
        query = query.filter(AsientoContable.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.filter(AsientoContable.fecha < fecha_hasta)

    if id_cuenta_filtro:
        # Solo asientos que tengan al menos una línea en esa cuenta
        query = query.join(
            LineaAsiento, LineaAsiento.id_asiento == AsientoContable.id
        ).filter(LineaAsiento.id_cuenta == id_cuenta_filtro).distinct()

    asientos_paginados = (
        query
        .order_by(AsientoContable.fecha.asc(), AsientoContable.id.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    # Armar la lista con las líneas y totales
    asientos_data = []
    total_debe = Decimal('0')
    total_haber = Decimal('0')
    for asiento in asientos_paginados.items:
        lineas = (
            LineaAsiento.query
            .filter_by(id_asiento=asiento.id)
            .all()
        )
        debe_asiento = sum((l.debe or Decimal('0')) for l in lineas)
        haber_asiento = sum((l.haber or Decimal('0')) for l in lineas)
        total_debe += debe_asiento
        total_haber += haber_asiento

        asientos_data.append({
            'asiento': asiento,
            'lineas': lineas,
            'total_debe': debe_asiento,
            'total_haber': haber_asiento,
        })

    # Cuentas imputables para el filtro
    cuentas_filtro = (
        PlanCuenta.query
        .filter_by(id_empresa=empresa.id, activo=True)
        .order_by(PlanCuenta.codigo)
        .all()
    )

    return render_template(
        'contabilidad/reportes/libro_diario.html',
        asientos_data=asientos_data,
        pagination=asientos_paginados,
        fecha_desde=fecha_desde_str,
        fecha_hasta=fecha_hasta_str,
        id_cuenta_filtro=id_cuenta_filtro,
        cuentas_filtro=cuentas_filtro,
        total_debe=total_debe,
        total_haber=total_haber,
    )

@contabilidad_bp.route('/reportes/libro-diario/excel')
@requiere_ver('contabilidad')
def libro_diario_excel():
    """Exporta el Libro Diario a Excel."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # Parsear filtros (misma lógica que la vista)
    from datetime import date, timedelta
    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    id_cuenta_filtro = request.args.get('id_cuenta', type=int)

    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        flash('Fecha desde inválida.', 'danger')
        return redirect(url_for('contabilidad.libro_diario'))
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        flash('Fecha hasta inválida.', 'danger')
        return redirect(url_for('contabilidad.libro_diario'))

    query = (
        AsientoContable.query
        .filter(
            AsientoContable.id_empresa == empresa.id,
            AsientoContable.fecha >= fecha_desde,
            AsientoContable.fecha < fecha_hasta,
        )
    )
    if id_cuenta_filtro:
        query = query.join(
            LineaAsiento, LineaAsiento.id_asiento == AsientoContable.id
        ).filter(LineaAsiento.id_cuenta == id_cuenta_filtro).distinct()

    asientos = query.order_by(
        AsientoContable.fecha.asc(), AsientoContable.id.asc()
    ).all()

    # Armar filas del Excel
    filas = []
    for a in asientos:
        lineas = LineaAsiento.query.filter_by(id_asiento=a.id).all()
        for l in lineas:
            filas.append({
                'asiento_id': a.id,
                'fecha': utc_to_local(a.fecha).strftime('%d/%m/%Y %H:%M'),
                'descripcion': a.descripcion,
                'codigo': l.cuenta.codigo,
                'cuenta': l.cuenta.nombre,
                'debe': float(l.debe or 0),
                'haber': float(l.haber or 0),
            })

    return _libro_diario_a_excel(
        empresa, filas, fecha_desde_str, fecha_hasta_str
    )


def _libro_diario_a_excel(empresa, filas, fecha_desde, fecha_hasta):
    """Genera el Excel del Libro Diario."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = "Libro Diario"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5",
                              fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # Encabezado
    ws.cell(row=1, column=1, value=f"Libro Diario — {empresa.nombre}").font = \
        Font(bold=True, size=14)
    ws.cell(row=2, column=1,
            value=f"Período: {fecha_desde} al {fecha_hasta}")
    ws.cell(row=3, column=1,
            value=f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    # Cabecera de tabla
    start_row = 5
    headers = ['Asiento #', 'Fecha', 'Descripción', 'Código',
               'Cuenta', 'Debe', 'Haber']
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = header_alignment
        c.border = thin_border

    # Filas
    row = start_row + 1
    total_debe = 0
    total_haber = 0
    for f in filas:
        ws.cell(row=row, column=1, value=f['asiento_id']).border = thin_border
        ws.cell(row=row, column=2, value=f['fecha']).border = thin_border
        ws.cell(row=row, column=3, value=f['descripcion']).border = thin_border
        ws.cell(row=row, column=4, value=f['codigo']).border = thin_border
        ws.cell(row=row, column=5, value=f['cuenta']).border = thin_border
        _write_money(ws, row, 6, f['debe'])
        ws.cell(row=row, column=6).border = thin_border
        _write_money(ws, row, 7, f['haber'])
        ws.cell(row=row, column=7).border = thin_border
        total_debe += f['debe']
        total_haber += f['haber']
        row += 1

    # Totales
    ws.cell(row=row, column=5, value="TOTALES").font = Font(bold=True)
    _write_money(ws, row, 6, total_debe)
    ws.cell(row=row, column=6).font = Font(bold=True)
    _write_money(ws, row, 7, total_haber)
    ws.cell(row=row, column=7).font = Font(bold=True)

    # Anchos
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 40
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 35
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 15

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    nombre = (
        f'libro_diario_{fecha_desde}_{fecha_hasta}.xlsx'
    )
    return send_file(
        output,
        download_name=nombre,
        as_attachment=True,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )

@contabilidad_bp.route('/reportes/libro-diario/pdf')
@requiere_ver('contabilidad')
def libro_diario_pdf():
    """Exporta el Libro Diario a PDF."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # Mismo parseo que el Excel
    from datetime import date, timedelta
    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    id_cuenta_filtro = request.args.get('id_cuenta', type=int)

    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        flash('Fecha desde inválida.', 'danger')
        return redirect(url_for('contabilidad.libro_diario'))
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        flash('Fecha hasta inválida.', 'danger')
        return redirect(url_for('contabilidad.libro_diario'))

    query = (
        AsientoContable.query
        .filter(
            AsientoContable.id_empresa == empresa.id,
            AsientoContable.fecha >= fecha_desde,
            AsientoContable.fecha < fecha_hasta,
        )
    )
    if id_cuenta_filtro:
        query = query.join(
            LineaAsiento, LineaAsiento.id_asiento == AsientoContable.id
        ).filter(LineaAsiento.id_cuenta == id_cuenta_filtro).distinct()

    asientos = query.order_by(
        AsientoContable.fecha.asc(), AsientoContable.id.asc()
    ).all()

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        rightMargin=1*cm, leftMargin=1*cm,
        topMargin=1*cm, bottomMargin=1*cm,
    )

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('T', parent=styles['Heading1'],
                               fontSize=14, alignment=1)
    st_sub = ParagraphStyle('S', parent=styles['Normal'],
                            fontSize=9, alignment=1, textColor=colors.grey)

    elementos = []
    elementos.append(Paragraph(f"Libro Diario — {empresa.nombre}", st_titulo))
    elementos.append(Paragraph(
        f"Período: {fecha_desde_str} al {fecha_hasta_str} "
        f"· Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        st_sub,
    ))
    elementos.append(Spacer(1, 0.4*cm))

    # Filas
    data = [['Asiento', 'Fecha', 'Descripción', 'Código', 'Cuenta',
             'Debe', 'Haber']]
    total_debe = 0
    total_haber = 0
    for a in asientos:
        lineas = LineaAsiento.query.filter_by(id_asiento=a.id).all()
        for i, l in enumerate(lineas):
            data.append([
                str(a.id) if i == 0 else '',
                a.fecha.strftime('%d/%m/%Y') if i == 0 else '',
                a.descripcion[:40] if i == 0 else '',
                l.cuenta.codigo,
                l.cuenta.nombre[:35],
                f"${l.debe:,.2f}" if l.debe else '',
                f"${l.haber:,.2f}" if l.haber else '',
            ])
            total_debe += float(l.debe or 0)
            total_haber += float(l.haber or 0)

    data.append(['', '', '', '', 'TOTALES',
                 f"${total_debe:,.2f}", f"${total_haber:,.2f}"])

    tabla = Table(data, repeatRows=1, colWidths=[
        1.4*cm, 2.2*cm, 6*cm, 2*cm, 6*cm, 2.8*cm, 2.8*cm,
    ])
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2F75B5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (5, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2),
         [colors.white, colors.HexColor('#F8F9FA')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E9ECEF')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    elementos.append(tabla)

    doc.build(elementos)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=f'libro_diario_{fecha_desde_str}_{fecha_hasta_str}.pdf',
        as_attachment=True,
        mimetype='application/pdf',
    )

@contabilidad_bp.route('/reportes/balance')
@requiere_ver('contabilidad')
def balance_sumas_saldos():
    """Balance de Sumas y Saldos."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    solo_con_movimiento = request.args.get('con_movimiento', '0') == '1'

    # Defaults: año actual
    from datetime import date, timedelta
    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    # Parsear
    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        flash('Fecha desde inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        flash('Fecha hasta inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))

    # --- Query agregada: sumas por cuenta ---
    from sqlalchemy import func

    sumas = (
        db.session.query(
            LineaAsiento.id_cuenta,
            func.coalesce(func.sum(LineaAsiento.debe), 0).label('debe'),
            func.coalesce(func.sum(LineaAsiento.haber), 0).label('haber'),
        )
        .join(AsientoContable, AsientoContable.id == LineaAsiento.id_asiento)
        .filter(
            AsientoContable.id_empresa == empresa.id,
            AsientoContable.fecha >= fecha_desde,
            AsientoContable.fecha < fecha_hasta,
        )
        .group_by(LineaAsiento.id_cuenta)
        .all()
    )

    # Indexar por cuenta
    movimientos_por_cuenta = {
        row.id_cuenta: {
            'debe': Decimal(str(row.debe or 0)),
            'haber': Decimal(str(row.haber or 0)),
        }
        for row in sumas
    }

    # --- Traer todas las cuentas imputables (hojas, activas) ---
    cuentas = (
        PlanCuenta.query
        .filter(
            PlanCuenta.id_empresa == empresa.id,
            PlanCuenta.activo == True,
        )
        .order_by(PlanCuenta.codigo)
        .all()
    )

    # Armar filas
    filas = []
    total_debe = Decimal('0')
    total_haber = Decimal('0')
    total_saldo_deudor = Decimal('0')
    total_saldo_acreedor = Decimal('0')

    for c in cuentas:
        # Solo cuentas imputables (hojas). Las cabeceras no tienen asientos.
        if not c.es_hoja:
            continue

        mov = movimientos_por_cuenta.get(c.id)
        debe = mov['debe'] if mov else Decimal('0')
        haber = mov['haber'] if mov else Decimal('0')

        # Filtro: solo con movimiento
        if solo_con_movimiento and debe == 0 and haber == 0:
            continue

        # Calcular saldo según naturaleza
        if c.naturaleza == 'deudora':
            # Activo / Gasto: Debe - Haber
            saldo = debe - haber
            if saldo >= 0:
                saldo_deudor = saldo
                saldo_acreedor = Decimal('0')
            else:
                saldo_deudor = Decimal('0')
                saldo_acreedor = -saldo
        else:
            # Pasivo / Patrimonio / Recurso: Haber - Debe
            saldo = haber - debe
            if saldo >= 0:
                saldo_deudor = Decimal('0')
                saldo_acreedor = saldo
            else:
                saldo_deudor = -saldo
                saldo_acreedor = Decimal('0')

        filas.append({
            'cuenta': c,
            'debe': debe,
            'haber': haber,
            'saldo_deudor': saldo_deudor,
            'saldo_acreedor': saldo_acreedor,
        })

        total_debe += debe
        total_haber += haber
        total_saldo_deudor += saldo_deudor
        total_saldo_acreedor += saldo_acreedor

    # Verificación de cuadre
    diferencia = total_debe - total_haber
    cuadra = (diferencia == 0)
    diferencia_saldos = total_saldo_deudor - total_saldo_acreedor

    return render_template(
        'contabilidad/reportes/balance.html',
        filas=filas,
        fecha_desde=fecha_desde_str,
        fecha_hasta=fecha_hasta_str,
        solo_con_movimiento=solo_con_movimiento,
        total_debe=total_debe,
        total_haber=total_haber,
        total_saldo_deudor=total_saldo_deudor,
        total_saldo_acreedor=total_saldo_acreedor,
        diferencia=diferencia,
        diferencia_saldos=diferencia_saldos,
        cuadra=cuadra,
    )

@contabilidad_bp.route('/reportes/balance/excel')
@requiere_ver('contabilidad')
def balance_excel():
    """Exporta el Balance de Sumas y Saldos a Excel."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    filas, totales = _calcular_balance(empresa)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = "Balance"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5",
                              fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    ws.cell(row=1, column=1,
            value=f"Balance de Sumas y Saldos — {empresa.nombre}").font = \
        Font(bold=True, size=14)
    ws.cell(row=2, column=1,
            value=f"Período: {totales['fecha_desde']} al {totales['fecha_hasta']}")
    ws.cell(row=3, column=1,
            value=f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    start_row = 5
    headers = ['Código', 'Cuenta', 'Debe', 'Haber',
               'Saldo Deudor', 'Saldo Acreedor']
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=col, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = header_alignment
        c.border = thin_border

    row = start_row + 1
    for f in filas:
        ws.cell(row=row, column=1, value=f['cuenta'].codigo).border = thin_border
        ws.cell(row=row, column=2, value=f['cuenta'].nombre).border = thin_border
        _write_money(ws, row, 3, f['debe'])
        ws.cell(row=row, column=3).border = thin_border
        _write_money(ws, row, 4, f['haber'])
        ws.cell(row=row, column=4).border = thin_border
        _write_money(ws, row, 5, f['saldo_deudor'])
        ws.cell(row=row, column=5).border = thin_border
        _write_money(ws, row, 6, f['saldo_acreedor'])
        ws.cell(row=row, column=6).border = thin_border
        row += 1

    # Totales
    ws.cell(row=row, column=2, value="TOTALES").font = Font(bold=True)
    for col, val in zip(
        (3, 4, 5, 6),
        (totales['total_debe'], totales['total_haber'],
         totales['total_saldo_deudor'], totales['total_saldo_acreedor'])
    ):
        _write_money(ws, row, col, val)
        ws.cell(row=row, column=col).font = Font(bold=True)

    # Anchos
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 40
    for col in ('C', 'D', 'E', 'F'):
        ws.column_dimensions[col].width = 15

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name=f'balance_{totales["fecha_desde"]}_{totales["fecha_hasta"]}.xlsx',
        as_attachment=True,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )


def _calcular_balance(empresa):
    """
    Calcula el balance de sumas y saldos (reusado por vista y Excel).
    Devuelve (filas, totales).
    """
    from datetime import date, timedelta
    from sqlalchemy import func

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    solo_con_movimiento = request.args.get('con_movimiento', '0') == '1'

    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    fecha_hasta = (
        datetime.strptime(fecha_hasta_str, '%Y-%m-%d') + timedelta(days=1)
    )

    sumas = (
        db.session.query(
            LineaAsiento.id_cuenta,
            func.coalesce(func.sum(LineaAsiento.debe), 0).label('debe'),
            func.coalesce(func.sum(LineaAsiento.haber), 0).label('haber'),
        )
        .join(AsientoContable, AsientoContable.id == LineaAsiento.id_asiento)
        .filter(
            AsientoContable.id_empresa == empresa.id,
            AsientoContable.fecha >= fecha_desde,
            AsientoContable.fecha < fecha_hasta,
        )
        .group_by(LineaAsiento.id_cuenta)
        .all()
    )

    movimientos_por_cuenta = {
        row.id_cuenta: {
            'debe': Decimal(str(row.debe or 0)),
            'haber': Decimal(str(row.haber or 0)),
        }
        for row in sumas
    }

    cuentas = (
        PlanCuenta.query
        .filter_by(id_empresa=empresa.id, activo=True)
        .order_by(PlanCuenta.codigo)
        .all()
    )

    filas = []
    total_debe = Decimal('0')
    total_haber = Decimal('0')
    total_saldo_deudor = Decimal('0')
    total_saldo_acreedor = Decimal('0')

    for c in cuentas:
        if not c.es_hoja:
            continue

        mov = movimientos_por_cuenta.get(c.id)
        debe = mov['debe'] if mov else Decimal('0')
        haber = mov['haber'] if mov else Decimal('0')

        if solo_con_movimiento and debe == 0 and haber == 0:
            continue

        if c.naturaleza == 'deudora':
            saldo = debe - haber
            saldo_deudor = max(saldo, Decimal('0'))
            saldo_acreedor = max(-saldo, Decimal('0'))
        else:
            saldo = haber - debe
            saldo_deudor = max(-saldo, Decimal('0'))
            saldo_acreedor = max(saldo, Decimal('0'))

        filas.append({
            'cuenta': c,
            'debe': debe,
            'haber': haber,
            'saldo_deudor': saldo_deudor,
            'saldo_acreedor': saldo_acreedor,
        })

        total_debe += debe
        total_haber += haber
        total_saldo_deudor += saldo_deudor
        total_saldo_acreedor += saldo_acreedor

    totales = {
        'fecha_desde': fecha_desde_str,
        'fecha_hasta': fecha_hasta_str,
        'total_debe': total_debe,
        'total_haber': total_haber,
        'total_saldo_deudor': total_saldo_deudor,
        'total_saldo_acreedor': total_saldo_acreedor,
    }
    return filas, totales

@contabilidad_bp.route('/reportes/balance/pdf')
@requiere_ver('contabilidad')
def balance_pdf():
    """Exporta el Balance de Sumas y Saldos a PDF."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    filas, totales = _calcular_balance(empresa)

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        rightMargin=1*cm, leftMargin=1*cm,
        topMargin=1*cm, bottomMargin=1*cm,
    )

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('T', parent=styles['Heading1'],
                               fontSize=14, alignment=1)
    st_sub = ParagraphStyle('S', parent=styles['Normal'],
                            fontSize=9, alignment=1, textColor=colors.grey)

    elementos = []
    elementos.append(Paragraph(
        f"Balance de Sumas y Saldos — {empresa.nombre}", st_titulo
    ))
    elementos.append(Paragraph(
        f"Período: {totales['fecha_desde']} al {totales['fecha_hasta']} "
        f"· Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        st_sub,
    ))
    elementos.append(Spacer(1, 0.4*cm))

    data = [['Código', 'Cuenta', 'Debe', 'Haber',
             'Saldo Deudor', 'Saldo Acreedor']]
    for f in filas:
        data.append([
            f['cuenta'].codigo,
            f['cuenta'].nombre[:40],
            f"${f['debe']:,.2f}" if f['debe'] > 0 else '',
            f"${f['haber']:,.2f}" if f['haber'] > 0 else '',
            f"${f['saldo_deudor']:,.2f}" if f['saldo_deudor'] > 0 else '',
            f"${f['saldo_acreedor']:,.2f}" if f['saldo_acreedor'] > 0 else '',
        ])
    data.append([
        '', 'TOTALES',
        f"${totales['total_debe']:,.2f}",
        f"${totales['total_haber']:,.2f}",
        f"${totales['total_saldo_deudor']:,.2f}",
        f"${totales['total_saldo_acreedor']:,.2f}",
    ])

    tabla = Table(data, repeatRows=1, colWidths=[
        2*cm, 7*cm, 3*cm, 3*cm, 3*cm, 3*cm,
    ])
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2F75B5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2),
         [colors.white, colors.HexColor('#F8F9FA')]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E9ECEF')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    elementos.append(tabla)

    doc.build(elementos)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=(
            f'balance_{totales["fecha_desde"]}_{totales["fecha_hasta"]}.pdf'
        ),
        as_attachment=True,
        mimetype='application/pdf',
    )

@contabilidad_bp.route('/reportes/libro-mayor')
@requiere_ver('contabilidad')
def libro_mayor():
    """Libro Mayor: movimientos por cuenta con saldo acumulado."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    id_cuenta_filtro = request.args.get('id_cuenta', type=int)

    # Defaults: año actual
    from datetime import date, timedelta
    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        flash('Fecha desde inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        flash('Fecha hasta inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))

    # --- Determinar cuentas a mostrar ---
    if id_cuenta_filtro:
        cuentas = PlanCuenta.query.filter_by(
            id=id_cuenta_filtro, id_empresa=empresa.id
        ).all()
    else:
        # Todas las cuentas imputables que tengan movimiento en el período
        from sqlalchemy import func, distinct
        ids_con_movimiento = (
            db.session.query(distinct(LineaAsiento.id_cuenta))
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                AsientoContable.id_empresa == empresa.id,
                AsientoContable.fecha >= fecha_desde,
                AsientoContable.fecha < fecha_hasta,
            )
            .all()
        )
        ids = [row[0] for row in ids_con_movimiento]
        if ids:
            cuentas = (
                PlanCuenta.query
                .filter(PlanCuenta.id.in_(ids))
                .order_by(PlanCuenta.codigo)
                .all()
            )
        else:
            cuentas = []

    # --- Construir el libro por cuenta ---
    cuentas_data = []
    for cuenta in cuentas:
        # Saldo inicial: todo lo anterior a fecha_desde
        sumas_previas = (
            db.session.query(
                func.coalesce(func.sum(LineaAsiento.debe), 0).label('debe'),
                func.coalesce(func.sum(LineaAsiento.haber), 0).label('haber'),
            )
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                AsientoContable.id_empresa == empresa.id,
                LineaAsiento.id_cuenta == cuenta.id,
                AsientoContable.fecha < fecha_desde,
            )
            .first()
        )
        debe_prev = Decimal(str(sumas_previas.debe or 0))
        haber_prev = Decimal(str(sumas_previas.haber or 0))

        # Saldo inicial según naturaleza
        if cuenta.naturaleza == 'deudora':
            saldo_inicial = debe_prev - haber_prev
        else:
            saldo_inicial = haber_prev - debe_prev

        # Movimientos del período
        lineas = (
            LineaAsiento.query
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                LineaAsiento.id_cuenta == cuenta.id,
                AsientoContable.id_empresa == empresa.id,
                AsientoContable.fecha >= fecha_desde,
                AsientoContable.fecha < fecha_hasta,
            )
            .order_by(AsientoContable.fecha.asc(),
                      AsientoContable.id.asc())
            .all()
        )

        # Armar movimientos con saldo acumulado
        movimientos = []
        saldo = saldo_inicial
        total_debe = Decimal('0')
        total_haber = Decimal('0')
        for l in lineas:
            debe = l.debe or Decimal('0')
            haber = l.haber or Decimal('0')
            if cuenta.naturaleza == 'deudora':
                saldo = saldo + debe - haber
            else:
                saldo = saldo + haber - debe

            movimientos.append({
                'linea': l,
                'asiento': l.asiento,
                'debe': debe,
                'haber': haber,
                'saldo': saldo,
            })
            total_debe += debe
            total_haber += haber

        cuentas_data.append({
            'cuenta': cuenta,
            'saldo_inicial': saldo_inicial,
            'movimientos': movimientos,
            'total_debe': total_debe,
            'total_haber': total_haber,
            'saldo_final': saldo,
        })

    # Cuentas imputables para el dropdown
    cuentas_filtro = (
        PlanCuenta.query
        .filter_by(id_empresa=empresa.id, activo=True)
        .order_by(PlanCuenta.codigo)
        .all()
    )
    cuentas_filtro = [c for c in cuentas_filtro if c.es_hoja]

    return render_template(
        'contabilidad/reportes/libro_mayor.html',
        cuentas_data=cuentas_data,
        fecha_desde=fecha_desde_str,
        fecha_hasta=fecha_hasta_str,
        id_cuenta_filtro=id_cuenta_filtro,
        cuentas_filtro=cuentas_filtro,
    )

@contabilidad_bp.route('/reportes/libro-mayor/excel')
@requiere_ver('contabilidad')
def libro_mayor_excel():
    """Exporta el Libro Mayor a Excel."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    datos = _calcular_libro_mayor(empresa)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = "Libro Mayor"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5",
                              fill_type="solid")
    cuenta_fill = PatternFill(start_color="D9E7F5", end_color="D9E7F5",
                              fill_type="solid")
    subtotal_fill = PatternFill(start_color="E9ECEF", end_color="E9ECEF",
                                fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # Encabezado
    ws.cell(row=1, column=1,
            value=f"Libro Mayor — {empresa.nombre}").font = \
        Font(bold=True, size=14)
    ws.cell(row=2, column=1,
            value=f"Período: {datos['fecha_desde']} al {datos['fecha_hasta']}")
    ws.cell(row=3, column=1,
            value=f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    row = 5
    for item in datos['cuentas']:
        # Header de cuenta
        c = ws.cell(row=row, column=1,
                    value=f"{item['cuenta'].codigo} - {item['cuenta'].nombre}")
        c.font = Font(bold=True, size=12)
        c.fill = cuenta_fill
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=6)
        for col in range(1, 7):
            ws.cell(row=row, column=col).border = thin_border
        row += 1

        # Sub-header
        sub_headers = ['Fecha', 'Asiento', 'Descripción',
                       'Debe', 'Haber', 'Saldo']
        for col, h in enumerate(sub_headers, 1):
            c = ws.cell(row=row, column=col, value=h)
            c.font = header_font
            c.fill = header_fill
            c.alignment = header_alignment
            c.border = thin_border
        row += 1

        # Saldo inicial
        ws.cell(row=row, column=1, value="SALDO INICIAL")
        ws.merge_cells(start_row=row, start_column=1,
                       end_row=row, end_column=5)
        ws.cell(row=row, column=1).font = Font(bold=True, italic=True)
        _write_money(ws, row, 6, item['saldo_inicial'])
        ws.cell(row=row, column=6).font = Font(bold=True, italic=True)
        for col in range(1, 7):
            ws.cell(row=row, column=col).fill = subtotal_fill
            ws.cell(row=row, column=col).border = thin_border
        row += 1

        # Movimientos
        for m in item['movimientos']:
            ws.cell(row=row, column=1,
                    value=m['asiento'].fecha.strftime('%d/%m/%Y')).border = thin_border
            ws.cell(row=row, column=2, value=m['asiento'].id).border = thin_border
            ws.cell(row=row, column=3,
                    value=m['asiento'].descripcion[:80]).border = thin_border
            _write_money(ws, row, 4, m['debe'])
            ws.cell(row=row, column=4).border = thin_border
            _write_money(ws, row, 5, m['haber'])
            ws.cell(row=row, column=5).border = thin_border
            _write_money(ws, row, 6, m['saldo'])
            ws.cell(row=row, column=6).border = thin_border
            row += 1

        # Totales
        ws.cell(row=row, column=3, value="TOTALES").font = Font(bold=True)
        _write_money(ws, row, 4, item['total_debe'])
        ws.cell(row=row, column=4).font = Font(bold=True)
        _write_money(ws, row, 5, item['total_haber'])
        ws.cell(row=row, column=5).font = Font(bold=True)
        _write_money(ws, row, 6, item['saldo_final'])
        ws.cell(row=row, column=6).font = Font(bold=True)
        for col in range(1, 7):
            ws.cell(row=row, column=col).fill = subtotal_fill
            ws.cell(row=row, column=col).border = thin_border
        row += 2  # Espacio entre cuentas

    # Anchos
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 10
    ws.column_dimensions['C'].width = 50
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 14
    ws.column_dimensions['F'].width = 16

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name=(
            f'libro_mayor_{datos["fecha_desde"]}_{datos["fecha_hasta"]}.xlsx'
        ),
        as_attachment=True,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )


def _calcular_libro_mayor(empresa):
    """
    Calcula el libro mayor (reusado por vista, Excel y PDF).
    Devuelve dict con fecha_desde, fecha_hasta, cuentas_data.
    """
    from datetime import date, timedelta
    from sqlalchemy import func, distinct

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    id_cuenta_filtro = request.args.get('id_cuenta', type=int)

    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    fecha_hasta = (
        datetime.strptime(fecha_hasta_str, '%Y-%m-%d') + timedelta(days=1)
    )

    if id_cuenta_filtro:
        cuentas = PlanCuenta.query.filter_by(
            id=id_cuenta_filtro, id_empresa=empresa.id
        ).all()
    else:
        ids_con_movimiento = (
            db.session.query(distinct(LineaAsiento.id_cuenta))
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                AsientoContable.id_empresa == empresa.id,
                AsientoContable.fecha >= fecha_desde,
                AsientoContable.fecha < fecha_hasta,
            )
            .all()
        )
        ids = [row[0] for row in ids_con_movimiento]
        if ids:
            cuentas = (
                PlanCuenta.query
                .filter(PlanCuenta.id.in_(ids))
                .order_by(PlanCuenta.codigo)
                .all()
            )
        else:
            cuentas = []

    cuentas_data = []
    for cuenta in cuentas:
        sumas_previas = (
            db.session.query(
                func.coalesce(func.sum(LineaAsiento.debe), 0).label('debe'),
                func.coalesce(func.sum(LineaAsiento.haber), 0).label('haber'),
            )
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                AsientoContable.id_empresa == empresa.id,
                LineaAsiento.id_cuenta == cuenta.id,
                AsientoContable.fecha < fecha_desde,
            )
            .first()
        )
        debe_prev = Decimal(str(sumas_previas.debe or 0))
        haber_prev = Decimal(str(sumas_previas.haber or 0))

        if cuenta.naturaleza == 'deudora':
            saldo_inicial = debe_prev - haber_prev
        else:
            saldo_inicial = haber_prev - debe_prev

        lineas = (
            LineaAsiento.query
            .join(AsientoContable,
                  AsientoContable.id == LineaAsiento.id_asiento)
            .filter(
                LineaAsiento.id_cuenta == cuenta.id,
                AsientoContable.id_empresa == empresa.id,
                AsientoContable.fecha >= fecha_desde,
                AsientoContable.fecha < fecha_hasta,
            )
            .order_by(AsientoContable.fecha.asc(),
                      AsientoContable.id.asc())
            .all()
        )

        movimientos = []
        saldo = saldo_inicial
        total_debe = Decimal('0')
        total_haber = Decimal('0')
        for l in lineas:
            debe = l.debe or Decimal('0')
            haber = l.haber or Decimal('0')
            if cuenta.naturaleza == 'deudora':
                saldo = saldo + debe - haber
            else:
                saldo = saldo + haber - debe

            movimientos.append({
                'linea': l,
                'asiento': l.asiento,
                'debe': debe,
                'haber': haber,
                'saldo': saldo,
            })
            total_debe += debe
            total_haber += haber

        cuentas_data.append({
            'cuenta': cuenta,
            'saldo_inicial': saldo_inicial,
            'movimientos': movimientos,
            'total_debe': total_debe,
            'total_haber': total_haber,
            'saldo_final': saldo,
        })

    return {
        'fecha_desde': fecha_desde_str,
        'fecha_hasta': fecha_hasta_str,
        'cuentas': cuentas_data,
    }

@contabilidad_bp.route('/reportes/libro-mayor/pdf')
@requiere_ver('contabilidad')
def libro_mayor_pdf():
    """Exporta el Libro Mayor a PDF."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    datos = _calcular_libro_mayor(empresa)

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        rightMargin=1*cm, leftMargin=1*cm,
        topMargin=1*cm, bottomMargin=1*cm,
    )

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('T', parent=styles['Heading1'],
                               fontSize=14, alignment=1)
    st_sub = ParagraphStyle('S', parent=styles['Normal'],
                            fontSize=9, alignment=1, textColor=colors.grey)
    st_cuenta = ParagraphStyle('C', parent=styles['Heading2'],
                               fontSize=11, textColor=colors.HexColor('#1F4E79'),
                               spaceBefore=8, spaceAfter=4)

    elementos = []
    elementos.append(Paragraph(
        f"Libro Mayor — {empresa.nombre}", st_titulo
    ))
    elementos.append(Paragraph(
        f"Período: {datos['fecha_desde']} al {datos['fecha_hasta']} "
        f"· Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        st_sub,
    ))
    elementos.append(Spacer(1, 0.3*cm))

    if not datos['cuentas']:
        elementos.append(Paragraph(
            "No hay movimientos en el período seleccionado.",
            st_sub,
        ))
    else:
        for idx, item in enumerate(datos['cuentas']):
            if idx > 0:
                elementos.append(Spacer(1, 0.4*cm))

            elementos.append(Paragraph(
                f"{item['cuenta'].codigo} — {item['cuenta'].nombre}",
                st_cuenta,
            ))

            data = [['Fecha', 'Asiento', 'Descripción',
                     'Debe', 'Haber', 'Saldo']]
            data.append([
                '', '', 'SALDO INICIAL', '', '',
                f"${item['saldo_inicial']:,.2f}",
            ])
            for m in item['movimientos']:
                data.append([
                    m['asiento'].fecha.strftime('%d/%m/%Y'),
                    str(m['asiento'].id),
                    m['asiento'].descripcion[:40],
                    f"${m['debe']:,.2f}" if m['debe'] > 0 else '',
                    f"${m['haber']:,.2f}" if m['haber'] > 0 else '',
                    f"${m['saldo']:,.2f}",
                ])
            data.append([
                '', '', 'TOTALES',
                f"${item['total_debe']:,.2f}",
                f"${item['total_haber']:,.2f}",
                f"${item['saldo_final']:,.2f}",
            ])

            tabla = Table(data, repeatRows=1, colWidths=[
                2.5*cm, 1.5*cm, 9*cm, 2.8*cm, 2.8*cm, 3*cm,
            ])
            tabla.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2F75B5')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                # Saldo inicial
                ('BACKGROUND', (0, 1), (-1, 1),
                 colors.HexColor('#E9ECEF')),
                ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
                # Totales (última fila)
                ('BACKGROUND', (0, -1), (-1, -1),
                 colors.HexColor('#E9ECEF')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            elementos.append(tabla)

    doc.build(elementos)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=(
            f'libro_mayor_{datos["fecha_desde"]}_{datos["fecha_hasta"]}.pdf'
        ),
        as_attachment=True,
        mimetype='application/pdf',
    )

@contabilidad_bp.route('/reportes/estado-resultados')
@requiere_ver('contabilidad')
def estado_resultados():
    """Estado de Resultados (Recursos - Gastos)."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()
    comparar = request.args.get('comparar', '0') == '1'

    # Defaults: año actual
    from datetime import date, timedelta
    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    try:
        fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    except ValueError:
        flash('Fecha desde inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))
    try:
        fecha_hasta = (
            datetime.strptime(fecha_hasta_str, '%Y-%m-%d')
            + timedelta(days=1)
        )
    except ValueError:
        flash('Fecha hasta inválida.', 'danger')
        return redirect(url_for('contabilidad.reportes_index'))

    # --- Cálculo principal ---
    periodo_actual = _calcular_estado_resultados(
        empresa, fecha_desde, fecha_hasta
    )

    # --- Comparación con período anterior ---
    comparacion = None
    if comparar:
        try:
            # Mismo rango pero un año antes
            fd_prev = fecha_desde.replace(year=fecha_desde.year - 1)
            fh_prev = (fecha_hasta - timedelta(days=1)).replace(
                year=fecha_hasta.year - 1
            ) + timedelta(days=1)
            comparacion = _calcular_estado_resultados(
                empresa, fd_prev, fh_prev
            )
        except (ValueError, OverflowError):
            comparacion = None

    return render_template(
        'contabilidad/reportes/estado_resultados.html',
        datos=periodo_actual,
        comparacion=comparacion,
        comparar=comparar,
        fecha_desde=fecha_desde_str,
        fecha_hasta=fecha_hasta_str,
    )


def _calcular_estado_resultados(empresa, fecha_desde, fecha_hasta):
    """
    Calcula el Estado de Resultados para un rango de fechas.
    Devuelve dict con:
      - recursos: [{cuenta, monto, hijos: [...]}]
      - gastos: [{cuenta, monto, hijos: [...]}]
      - total_recursos
      - total_gastos
      - resultado
    """
    from sqlalchemy import func

    # Sumas por cuenta en el período
    sumas = (
        db.session.query(
            LineaAsiento.id_cuenta,
            func.coalesce(func.sum(LineaAsiento.debe), 0).label('debe'),
            func.coalesce(func.sum(LineaAsiento.haber), 0).label('haber'),
        )
        .join(AsientoContable,
              AsientoContable.id == LineaAsiento.id_asiento)
        .filter(
            AsientoContable.id_empresa == empresa.id,
            AsientoContable.fecha >= fecha_desde,
            AsientoContable.fecha < fecha_hasta,
        )
        .group_by(LineaAsiento.id_cuenta)
        .all()
    )
    saldos_por_cuenta = {}
    for row in sumas:
        debe = Decimal(str(row.debe or 0))
        haber = Decimal(str(row.haber or 0))
        saldos_por_cuenta[row.id_cuenta] = {'debe': debe, 'haber': haber}

    # Traer cuentas de tipo Recurso y Gasto
    cuentas = (
        PlanCuenta.query
        .filter(
            PlanCuenta.id_empresa == empresa.id,
            PlanCuenta.tipo.in_(['Recurso', 'Gasto']),
        )
        .order_by(PlanCuenta.codigo)
        .all()
    )

    def _monto(cuenta):
        """Calcula el monto del período según naturaleza."""
        mov = saldos_por_cuenta.get(cuenta.id)
        if not mov:
            return Decimal('0')
        if cuenta.tipo == 'Recurso':
            # Recurso: acreedora. Haber - Debe
            return mov['haber'] - mov['debe']
        else:
            # Gasto: deudora. Debe - Haber
            return mov['debe'] - mov['haber']

    # Armar estructura jerárquica por padre
    # Cada cuenta raíz de Recurso (4.1, 4.2, ...) es un grupo
    # con sus subcuentas indentadas.
    recursos = []
    gastos = []

    def _construir_arbol(cuentas_lista, tipo):
        """Construye la lista jerárquica para Recurso o Gasto."""
        # Agrupar por código de primer nivel (4.1, 4.2, ..., 5.1, 5.2, ...)
        prefijo = '4.' if tipo == 'Recurso' else '5.'
        raices = {}
        for c in cuentas_lista:
            if not c.codigo.startswith(prefijo):
                continue
            partes = c.codigo.split('.')
            if len(partes) < 2:
                continue
            # El "rubro" es el código de dos niveles: 4.1, 4.2, 5.1, etc.
            rubro_codigo = '.'.join(partes[:2])
            if rubro_codigo not in raices:
                raices[rubro_codigo] = []
            raices[rubro_codigo].append(c)

        resultado = []
        for rubro_codigo in sorted(raices.keys()):
            cuentas_rubro = raices[rubro_codigo]
            # La cuenta raíz del rubro (la de código 4.1, 4.2, etc.)
            cuenta_raiz = next(
                (c for c in cuentas_rubro if c.codigo == rubro_codigo),
                None
            )
            # Subcuentas (todas las que no son la raíz)
            subcuentas = [c for c in cuentas_rubro if c.codigo != rubro_codigo]

            # Monto total del rubro: sumar todos
            monto_total = sum(
                (_monto(c) for c in cuentas_rubro), Decimal('0')
            )

            subcuentas_data = []
            for c in subcuentas:
                m = _monto(c)
                subcuentas_data.append({
                    'cuenta': c,
                    'monto': m,
                })

            resultado.append({
                'cuenta_raiz': cuenta_raiz,
                'codigo': rubro_codigo,
                'nombre': cuenta_raiz.nombre if cuenta_raiz else rubro_codigo,
                'monto': monto_total,
                'hijos': subcuentas_data,
            })

        return resultado

    recursos = _construir_arbol(cuentas, 'Recurso')
    gastos = _construir_arbol(cuentas, 'Gasto')

    total_recursos = sum(
        (r['monto'] for r in recursos), Decimal('0')
    )
    total_gastos = sum(
        (g['monto'] for g in gastos), Decimal('0')
    )
    resultado = total_recursos - total_gastos

    return {
        'recursos': recursos,
        'gastos': gastos,
        'total_recursos': total_recursos,
        'total_gastos': total_gastos,
        'resultado': resultado,
    }

@contabilidad_bp.route('/reportes/estado-resultados/excel')
@requiere_ver('contabilidad')
def estado_resultados_excel():
    """Exporta el Estado de Resultados a Excel."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    from datetime import date, timedelta
    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()

    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    fecha_hasta = (
        datetime.strptime(fecha_hasta_str, '%Y-%m-%d') + timedelta(days=1)
    )

    datos = _calcular_estado_resultados(empresa, fecha_desde, fecha_hasta)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = "Estado de Resultados"

    bold = Font(bold=True)
    bold_white = Font(bold=True, color="FFFFFF")
    green_fill = PatternFill(start_color="28A745", end_color="28A745",
                             fill_type="solid")
    red_fill = PatternFill(start_color="DC3545", end_color="DC3545",
                           fill_type="solid")
    light_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA",
                             fill_type="solid")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    # Encabezado
    ws.cell(row=1, column=1,
            value=f"Estado de Resultados — {empresa.nombre}").font = \
        Font(bold=True, size=14)
    ws.cell(row=2, column=1,
            value=f"Período: {fecha_desde_str} al {fecha_hasta_str}")
    ws.cell(row=3, column=1,
            value=f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    row = 5

    # --- RECURSOS ---
    ws.cell(row=row, column=1, value="RECURSOS").font = bold_white
    ws.cell(row=row, column=1).fill = green_fill
    ws.cell(row=row, column=2).fill = green_fill
    ws.cell(row=row, column=3).fill = green_fill
    row += 1

    for r in datos['recursos']:
        ws.cell(row=row, column=1, value=r['codigo']).font = bold
        ws.cell(row=row, column=2, value=r['nombre']).font = bold
        _write_money(ws, row, 3, r['monto'])
        ws.cell(row=row, column=3).font = bold
        row += 1
        for h in r['hijos']:
            if h['monto'] > 0:
                ws.cell(row=row, column=1, value=h['cuenta'].codigo)
                ws.cell(row=row, column=2, value=h['cuenta'].nombre)
                _write_money(ws, row, 3, h['monto'])
                row += 1

    ws.cell(row=row, column=2, value="TOTAL RECURSOS").font = bold
    _write_money(ws, row, 3, datos['total_recursos'])
    ws.cell(row=row, column=3).font = bold
    ws.cell(row=row, column=2).fill = light_fill
    ws.cell(row=row, column=3).fill = light_fill
    row += 2

    # --- GASTOS ---
    ws.cell(row=row, column=1, value="GASTOS").font = bold_white
    ws.cell(row=row, column=1).fill = red_fill
    ws.cell(row=row, column=2).fill = red_fill
    ws.cell(row=row, column=3).fill = red_fill
    row += 1

    for g in datos['gastos']:
        ws.cell(row=row, column=1, value=g['codigo']).font = bold
        ws.cell(row=row, column=2, value=g['nombre']).font = bold
        _write_money(ws, row, 3, g['monto'])
        ws.cell(row=row, column=3).font = bold
        row += 1
        for h in g['hijos']:
            if h['monto'] > 0:
                ws.cell(row=row, column=1, value=h['cuenta'].codigo)
                ws.cell(row=row, column=2, value=h['cuenta'].nombre)
                _write_money(ws, row, 3, h['monto'])
                row += 1

    ws.cell(row=row, column=2, value="TOTAL GASTOS").font = bold
    _write_money(ws, row, 3, datos['total_gastos'])
    ws.cell(row=row, column=3).font = bold
    ws.cell(row=row, column=2).fill = light_fill
    ws.cell(row=row, column=3).fill = light_fill
    row += 2

    # --- RESULTADO ---
    ws.cell(row=row, column=2,
            value="RESULTADO DEL PERÍODO").font = Font(bold=True, size=12)
    _write_money(ws, row, 3, datos['resultado'])
    ws.cell(row=row, column=3).font = Font(bold=True, size=12)
    resultado_fill = (
        PatternFill(start_color="D4EDDA", end_color="D4EDDA",
                    fill_type="solid")
        if datos['resultado'] >= 0
        else PatternFill(start_color="F8D7DA", end_color="F8D7DA",
                         fill_type="solid")
    )
    ws.cell(row=row, column=2).fill = resultado_fill
    ws.cell(row=row, column=3).fill = resultado_fill

    # Anchos
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 50
    ws.column_dimensions['C'].width = 18

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name=(
            f'estado_resultados_{fecha_desde_str}_{fecha_hasta_str}.xlsx'
        ),
        as_attachment=True,
        mimetype=(
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        ),
    )

@contabilidad_bp.route('/reportes/estado-resultados/pdf')
@requiere_ver('contabilidad')
def estado_resultados_pdf():
    """Exporta el Estado de Resultados a PDF."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    from datetime import date, timedelta
    fecha_desde_str = (request.args.get('fecha_desde') or '').strip()
    fecha_hasta_str = (request.args.get('fecha_hasta') or '').strip()

    hoy = date.today()
    if not fecha_desde_str:
        fecha_desde_str = hoy.replace(month=1, day=1).strftime('%Y-%m-%d')
    if not fecha_hasta_str:
        fecha_hasta_str = hoy.strftime('%Y-%m-%d')

    fecha_desde = local_to_utc(datetime.strptime(fecha_desde_str, '%Y-%m-%d'))
    fecha_hasta = (
        datetime.strptime(fecha_hasta_str, '%Y-%m-%d') + timedelta(days=1)
    )

    datos = _calcular_estado_resultados(empresa, fecha_desde, fecha_hasta)

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from io import BytesIO

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )

    styles = getSampleStyleSheet()
    st_titulo = ParagraphStyle('T', parent=styles['Heading1'],
                               fontSize=14, alignment=1)
    st_sub = ParagraphStyle('S', parent=styles['Normal'],
                            fontSize=9, alignment=1, textColor=colors.grey)
    st_seccion = ParagraphStyle('Sec', parent=styles['Heading2'],
                                fontSize=11, textColor=colors.white,
                                backColor=colors.HexColor('#28A745'),
                                borderPadding=4, spaceAfter=2)
    st_seccion_gasto = ParagraphStyle('SecG', parent=styles['Heading2'],
                                      fontSize=11, textColor=colors.white,
                                      backColor=colors.HexColor('#DC3545'),
                                      borderPadding=4, spaceAfter=2)

    elementos = []
    elementos.append(Paragraph(
        f"Estado de Resultados — {empresa.nombre}", st_titulo
    ))
    elementos.append(Paragraph(
        f"Período: {fecha_desde_str} al {fecha_hasta_str} "
        f"· Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        st_sub,
    ))
    elementos.append(Spacer(1, 0.4*cm))

    # --- Recursos ---
    elementos.append(Paragraph("RECURSOS", st_seccion))
    data_rec = []
    for r in datos['recursos']:
        data_rec.append([
            r['codigo'], r['nombre'],
            f"${r['monto']:,.2f}" if r['monto'] > 0 else '',
            'total',
        ])
        for h in r['hijos']:
            if h['monto'] > 0:
                data_rec.append([
                    h['cuenta'].codigo, '    ' + h['cuenta'].nombre,
                    f"${h['monto']:,.2f}", 'hijo',
                ])
    data_rec.append(['', 'TOTAL RECURSOS',
                     f"${datos['total_recursos']:,.2f}", 'total'])

    tabla_rec = Table(data_rec, colWidths=[2.5*cm, 10*cm, 4*cm])
    tabla_rec.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # Filas "total" (encabezados de rubro)
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#D4EDDA')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    # Aplicar bold a las filas de total de rubro
    for i, fila in enumerate(data_rec):
        if fila[3] == 'total' and i != len(data_rec) - 1:
            tabla_rec.setStyle(TableStyle([
                ('FONTNAME', (0, i), (-1, i), 'Helvetica-Bold'),
            ]))
    elementos.append(tabla_rec)
    elementos.append(Spacer(1, 0.5*cm))

    # --- Gastos ---
    elementos.append(Paragraph("GASTOS", st_seccion_gasto))
    data_gas = []
    for g in datos['gastos']:
        data_gas.append([
            g['codigo'], g['nombre'],
            f"${g['monto']:,.2f}" if g['monto'] > 0 else '',
            'total',
        ])
        for h in g['hijos']:
            if h['monto'] > 0:
                data_gas.append([
                    h['cuenta'].codigo, '    ' + h['cuenta'].nombre,
                    f"${h['monto']:,.2f}", 'hijo',
                ])
    data_gas.append(['', 'TOTAL GASTOS',
                     f"${datos['total_gastos']:,.2f}", 'total'])

    tabla_gas = Table(data_gas, colWidths=[2.5*cm, 10*cm, 4*cm])
    tabla_gas.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F8D7DA')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    for i, fila in enumerate(data_gas):
        if fila[3] == 'total' and i != len(data_gas) - 1:
            tabla_gas.setStyle(TableStyle([
                ('FONTNAME', (0, i), (-1, i), 'Helvetica-Bold'),
            ]))
    elementos.append(tabla_gas)
    elementos.append(Spacer(1, 0.8*cm))

    # --- Resultado ---
    color_resultado = (
        colors.HexColor('#28A745') if datos['resultado'] >= 0
        else colors.HexColor('#DC3545')
    )
    st_resultado = ParagraphStyle(
        'R', parent=styles['Heading1'],
        fontSize=16, alignment=1, textColor=color_resultado,
    )
    elementos.append(Paragraph("RESULTADO DEL PERÍODO", st_resultado))
    elementos.append(Paragraph(
        f"${datos['resultado']:,.2f}",
        ParagraphStyle('RV', parent=st_resultado, fontSize=24),
    ))
    etiqueta = 'Superávit' if datos['resultado'] >= 0 else 'Déficit'
    elementos.append(Paragraph(
        etiqueta, ParagraphStyle('RE', parent=st_sub, fontSize=10),
    ))

    doc.build(elementos)
    buffer.seek(0)

    return send_file(
        buffer,
        download_name=(
            f'estado_resultados_{fecha_desde_str}_{fecha_hasta_str}.pdf'
        ),
        as_attachment=True,
        mimetype='application/pdf',
    )