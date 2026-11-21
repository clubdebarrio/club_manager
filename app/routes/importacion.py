# app/routes/importacion.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, send_file,
)
from app import db
from app.models import Socio, TipoSocio, CategoriaSocio
from app.forms import ImportacionForm
from app.services.socio_service import SocioService
from app.utils import current_empresa
import pandas as pd
import re
from datetime import datetime
import io
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from io import BytesIO
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

def _normalizar_int(valor):
    if valor is None or pd.isna(valor):
        return ''
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()

importacion_bp = Blueprint('importacion', __name__, url_prefix='/importacion')

CAMPOS_ESPERADOS = [
    'numero_socio', 'apellido', 'nombre', 'tipo_documento', 'nro_documento',
    'email', 'telefono', 'tipo_socio', 'categoria_socio', 'id_familia',
    'fecha_alta', 'fecha_baja'
]

TIPOS_DOCUMENTO_VALIDOS = ['DNI', 'CI', 'Pasaporte', 'Otro']


@importacion_bp.route('/descargar-plantilla')
@requiere_ver('importacion')
def descargar_plantilla():
    """Genera y descarga una plantilla Excel con validaciones de datos."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero debes crear un club.', 'warning')
        return redirect(url_for('main.index'))

    tipos_socio = [t.nombre for t in TipoSocio.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).all()]
    categorias = [c.nombre for c in CategoriaSocio.query.filter_by(
        activo=True, id_empresa=empresa.id
    ).all()]

    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"

    headers = [
        'numero_socio', 'apellido', 'nombre', 'tipo_documento', 'nro_documento',
        'email', 'telefono', 'tipo_socio', 'categoria_socio', 'id_familia',
        'fecha_alta', 'fecha_baja'
    ]

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F75B5", end_color="2F75B5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    dv_documento = DataValidation(
        type="list",
        formula1='"DNI,CI,Pasaporte,Otro"',
        allow_blank=False,
        showErrorMessage=True,
        errorTitle="Tipo de documento inválido",
        error="Selecciona un tipo de documento válido: DNI, CI, Pasaporte u Otro."
    )
    ws.add_data_validation(dv_documento)
    dv_documento.add('D2:D1048576')

    if tipos_socio:
        formula_tipos = '"' + ','.join(tipos_socio) + '"'
        dv_tipo = DataValidation(
            type="list",
            formula1=formula_tipos,
            allow_blank=False,
            showErrorMessage=True,
            errorTitle="Tipo de socio inválido",
            error=f"Selecciona un tipo de socio válido: {', '.join(tipos_socio)}"
        )
        ws.add_data_validation(dv_tipo)
        dv_tipo.add('H2:H1048576')

    if categorias:
        formula_categorias = '"' + ','.join(categorias) + '"'
        dv_categoria = DataValidation(
            type="list",
            formula1=formula_categorias,
            allow_blank=False,
            showErrorMessage=True,
            errorTitle="Categoría inválida",
            error=f"Selecciona una categoría válida: {', '.join(categorias)}"
        )
        ws.add_data_validation(dv_categoria)
        dv_categoria.add('I2:I1048576')

    dv_fecha = DataValidation(
        type="date",
        operator="greaterThanOrEqual",
        formula1="1900-01-01",
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Formato de fecha inválido",
        error="Usa formato YYYY-MM-DD (ej. 2026-07-10)"
    )
    ws.add_data_validation(dv_fecha)
    dv_fecha.add('K2:K1048576')

    dv_fecha_baja = DataValidation(
        type="date",
        operator="greaterThanOrEqual",
        formula1="1900-01-01",
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Formato de fecha inválido",
        error="Usa formato YYYY-MM-DD (ej. 2026-07-10)"
    )
    ws.add_data_validation(dv_fecha_baja)
    dv_fecha_baja.add('L2:L1048576')

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[chr(64 + col)].width = 20
    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['D'].width = 18
    ws.column_dimensions['E'].width = 18
    ws.column_dimensions['H'].width = 18
    ws.column_dimensions['I'].width = 18
    ws.column_dimensions['K'].width = 15
    ws.column_dimensions['L'].width = 15

    ws2 = wb.create_sheet("Instrucciones")
    ws2.column_dimensions['A'].width = 30
    ws2.column_dimensions['B'].width = 50

    instrucciones = [
        ("INSTRUCCIONES PARA LA CARGA DE SOCIOS", ""),
        ("", ""),
        ("Campos obligatorios (*)", ""),
        ("apellido", "Apellido del socio (requerido)"),
        ("nombre", "Nombre del socio (requerido)"),
        ("tipo_documento", "DNI, CI, Pasaporte u Otro (requerido)"),
        ("nro_documento", "Número de documento (requerido, solo números)"),
        ("tipo_socio", f"Debe coincidir con los tipos existentes: {', '.join(tipos_socio) if tipos_socio else 'Ninguno creado'}"),
        ("categoria_socio", f"Debe coincidir con las categorías existentes: {', '.join(categorias) if categorias else 'Ninguna creada'}"),
        ("", ""),
        ("Campos opcionales", ""),
        ("numero_socio", "Si se deja vacío, el sistema lo asignará automáticamente. Si se ingresa, se formateará a 4 dígitos (ej. 1 → 0001)."),
        ("email", "Correo electrónico del socio."),
        ("telefono", "Teléfono de contacto."),
        ("id_familia", "Número de socio principal (solo para integrantes de una familia)."),
        ("fecha_alta", "Fecha de alta (formato YYYY-MM-DD). Si se deja vacío, se usa la fecha actual."),
        ("fecha_baja", "Fecha de baja (formato YYYY-MM-DD). Solo para socios inactivos."),
        ("", ""),
        ("⚠️ RECOMENDACIONES", ""),
        ("", "1. No modifiques los nombres de las columnas."),
        ("", "2. Usa las listas desplegables para tipo_documento, tipo_socio y categoria_socio."),
        ("", "3. Guarda el archivo como .xlsx antes de subirlo."),
        ("", "4. Si usas fechas, asegúrate de que estén en formato YYYY-MM-DD."),
    ]

    for row, (col_a, col_b) in enumerate(instrucciones, 1):
        if "INSTRUCCIONES" in col_a or "⚠️" in col_a:
            ws2.cell(row=row, column=1, value=col_a).font = Font(bold=True, size=12)
        else:
            ws2.cell(row=row, column=1, value=col_a)
            ws2.cell(row=row, column=2, value=col_b)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name='plantilla_importacion_socios.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@importacion_bp.route('/', methods=['GET', 'POST'])
@requiere_editar('importacion')
def index():
    empresa = current_empresa()
    if not empresa:
        flash('Primero debes crear un club (empresa).', 'warning')
        return redirect(url_for('main.index'))

    form = ImportacionForm()
    if form.validate_on_submit():
        file = form.archivo.data
        if not file:
            flash('No se seleccionó ningún archivo.', 'danger')
            return redirect(url_for('importacion.index'))

        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            flash(f'Error al leer el archivo: {str(e)}', 'danger')
            return redirect(url_for('importacion.index'))

        columnas_faltantes = [col for col in CAMPOS_ESPERADOS if col not in df.columns]
        if columnas_faltantes:
            flash(f'Columnas faltantes: {", ".join(columnas_faltantes)}', 'danger')
            return redirect(url_for('importacion.index'))

        registros_validos = []
        registros_invalidos = []

        tipos_socio_map = {
            t.nombre: t.id
            for t in TipoSocio.query.filter_by(id_empresa=empresa.id).all()
        }
        categorias_map = {
            c.nombre: c.id
            for c in CategoriaSocio.query.filter_by(id_empresa=empresa.id).all()
        }
        socios_existentes_nums = {
            s.numero_socio: s.id
            for s in Socio.query.filter_by(id_empresa=empresa.id).all()
        }
        socios_existentes_docs = {
            s.nro_documento: s.id
            for s in Socio.query.filter_by(id_empresa=empresa.id).all()
        }

        for idx, row in df.iterrows():
            errores = []
            datos_fila = {}

            for campo in ['apellido', 'nombre', 'tipo_documento',
                          'nro_documento', 'tipo_socio', 'categoria_socio']:
                valor = row.get(campo)
                if pd.isna(valor) or str(valor).strip() == '':
                    errores.append(f'{campo} es obligatorio')
                else:
                    datos_fila[campo] = str(valor).strip()

            tipo_doc = str(row.get('tipo_documento', '')).strip()
            if tipo_doc and tipo_doc not in TIPOS_DOCUMENTO_VALIDOS:
                errores.append(f'tipo_documento inválido: {tipo_doc}')
            else:
                datos_fila['tipo_documento'] = tipo_doc

            nro_doc = str(row.get('nro_documento', '')).strip()
            if nro_doc:
                if not re.match(r'^\d+$', nro_doc):
                    errores.append('nro_documento debe contener solo números')
                elif nro_doc in socios_existentes_docs:
                    errores.append('nro_documento ya existe en la base de datos')
                else:
                    datos_fila['nro_documento'] = nro_doc
            else:
                datos_fila['nro_documento'] = ''

            tipo_socio_nombre = str(row.get('tipo_socio', '')).strip()
            if tipo_socio_nombre:
                if tipo_socio_nombre in tipos_socio_map:
                    datos_fila['tipo_socio'] = tipo_socio_nombre
                else:
                    errores.append(f'tipo_socio "{tipo_socio_nombre}" no existe')
            else:
                datos_fila['tipo_socio'] = ''

            categoria_nombre = str(row.get('categoria_socio', '')).strip()
            if categoria_nombre:
                if categoria_nombre in categorias_map:
                    datos_fila['categoria_socio'] = categoria_nombre
                else:
                    errores.append(f'categoria_socio "{categoria_nombre}" no existe')
            else:
                datos_fila['categoria_socio'] = ''

            email = str(row.get('email', '')).strip() if not pd.isna(row.get('email')) else ''
            if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
                errores.append('email inválido')
            datos_fila['email'] = email if email else None

            telefono = str(row.get('telefono', '')).strip() if not pd.isna(row.get('telefono')) else ''
            datos_fila['telefono'] = telefono if telefono else None

            numero_socio_raw = _normalizar_int(row.get('numero_socio'))
            if numero_socio_raw:
                try:
                    num = int(numero_socio_raw)
                    if num <= 0:
                        errores.append('número de socio debe ser mayor a 0')
                    else:
                        numero_socio = f"{num:04d}"
                        if numero_socio in socios_existentes_nums:
                            errores.append(f'número de socio "{numero_socio}" ya existe (ingresaste "{numero_socio_raw}")')
                        else:
                            datos_fila['numero_socio'] = numero_socio
                except ValueError:
                    errores.append('número de socio debe contener solo dígitos')
                    datos_fila['numero_socio'] = None
            else:
                datos_fila['numero_socio'] = None

            id_familia_raw = _normalizar_int(row.get('id_familia'))
            if id_familia_raw:
                try:
                    num_fam = int(id_familia_raw)
                    if num_fam <= 0:
                        errores.append('id_familia debe ser mayor a 0')
                    else:
                        id_familia_norm = f"{num_fam:04d}"
                        socio_familia = Socio.query.filter_by(
                            numero_socio=id_familia_norm,
                            id_empresa=empresa.id
                        ).first()
                        if not socio_familia:
                            errores.append(f'id_familia "{id_familia_norm}" no corresponde a un socio existente')
                        else:
                            # Guardamos el ID del socio padre (integer), no su
                            # numero_socio (string), porque en la DB id_familia es FK.
                            datos_fila['id_familia'] = socio_familia.id
                except ValueError:
                    errores.append('id_familia debe contener solo dígitos')
                    datos_fila['id_familia'] = None
            else:
                datos_fila['id_familia'] = None

            fecha_alta = row.get('fecha_alta')
            if not pd.isna(fecha_alta) and fecha_alta is not None:
                if hasattr(fecha_alta, 'strftime'):
                    datos_fila['fecha_alta'] = fecha_alta.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    datos_fila['fecha_alta'] = str(fecha_alta)
            else:
                datos_fila['fecha_alta'] = None

            fecha_baja = row.get('fecha_baja')
            if not pd.isna(fecha_baja) and fecha_baja is not None:
                if hasattr(fecha_baja, 'strftime'):
                    datos_fila['fecha_baja'] = fecha_baja.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    datos_fila['fecha_baja'] = str(fecha_baja)
            else:
                datos_fila['fecha_baja'] = None

            if errores:
                registros_invalidos.append({
                    'fila': idx + 2,
                    'errores': errores,
                    'datos': datos_fila
                })
            else:
                registro = {
                    'apellido': datos_fila['apellido'],
                    'nombre': datos_fila['nombre'],
                    'tipo_documento': datos_fila['tipo_documento'],
                    'nro_documento': datos_fila['nro_documento'],
                    'email': datos_fila['email'],
                    'telefono': datos_fila['telefono'],
                    'id_tipo_socio': tipos_socio_map[datos_fila['tipo_socio']],
                    'id_categoria_socio': categorias_map[datos_fila['categoria_socio']],
                    'id_familia': datos_fila['id_familia'],
                    'fecha_alta': datos_fila['fecha_alta'],
                    'fecha_baja': datos_fila['fecha_baja'],
                    'numero_socio': datos_fila['numero_socio'],
                }
                registros_validos.append(registro)

        session['registros_validos'] = registros_validos
        session['registros_invalidos'] = registros_invalidos
        session['total_filas'] = len(df)

        return render_template('importacion/previsualizacion.html',
                             validos=registros_validos,
                             invalidos=registros_invalidos,
                             total=len(df))

    return render_template('importacion/index.html', form=form)


@importacion_bp.route('/confirmar', methods=['POST'])
@requiere_editar('importacion')
def confirmar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero debes crear un club.', 'warning')
        return redirect(url_for('main.index'))

    registros_validos = session.get('registros_validos', [])
    if not registros_validos:
        flash('No hay registros válidos para importar.', 'warning')
        return redirect(url_for('importacion.index'))

    importados = 0
    errores_importacion = []

    for registro in registros_validos:
        try:
            # Savepoint: si esta inserción falla, se revierte solo hasta acá
            with db.session.begin_nested():
                if not registro.get('numero_socio'):
                    numero_socio = SocioService.generar_numero_socio(
                        empresa.id, registro.get('id_familia')
                    )
                else:
                    numero_socio = registro['numero_socio']
                    if Socio.query.filter_by(
                        numero_socio=numero_socio, id_empresa=empresa.id
                    ).first():
                        raise ValueError(
                            f"Número de socio {numero_socio} ya existe."
                        )

                fecha_alta = registro.get('fecha_alta')
                if fecha_alta and isinstance(fecha_alta, str):
                    try:
                        fecha_alta = datetime.strptime(fecha_alta, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        try:
                            fecha_alta = datetime.strptime(fecha_alta, '%Y-%m-%d')
                        except ValueError:
                            fecha_alta = datetime.utcnow()
                else:
                    fecha_alta = fecha_alta or datetime.utcnow()

                fecha_baja = registro.get('fecha_baja')
                if fecha_baja and isinstance(fecha_baja, str):
                    try:
                        fecha_baja = datetime.strptime(fecha_baja, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        try:
                            fecha_baja = datetime.strptime(fecha_baja, '%Y-%m-%d')
                        except ValueError:
                            fecha_baja = None
                else:
                    fecha_baja = fecha_baja or None

                socio = Socio(
                    numero_socio=numero_socio,
                    nombre=registro['nombre'],
                    apellido=registro['apellido'],
                    tipo_documento=registro['tipo_documento'],
                    nro_documento=registro['nro_documento'],
                    email=registro.get('email'),
                    telefono=registro.get('telefono'),
                    id_tipo_socio=registro['id_tipo_socio'],
                    id_categoria_socio=registro['id_categoria_socio'],
                    id_familia=registro.get('id_familia'),
                    id_empresa=empresa.id,
                    fecha_alta=fecha_alta,
                    fecha_baja=fecha_baja,
                )
                db.session.add(socio)
                importados += 1
        except Exception as e:
            errores_importacion.append(
                f"Error al importar socio {registro.get('apellido', '')}, "
                f"{registro.get('nombre', '')}: {str(e)}"
            )

    try:
        db.session.flush()
        auditar(
            'importar', 'Socio', None,
            nota=(
                f'Importación masiva: {importados} socios nuevos, '
                f'{len(errores_importacion)} errores'
            ),
        )
        db.session.commit()
        flash(f'Se importaron {importados} socios correctamente.', 'success')
        if errores_importacion:
            for error in errores_importacion:
                flash(error, 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al guardar los datos: {str(e)}', 'danger')

    session.pop('registros_validos', None)
    session.pop('registros_invalidos', None)
    session.pop('total_filas', None)

    return redirect(url_for('socios.listar'))


@importacion_bp.route('/descargar-reporte')
@requiere_ver('importacion')
def descargar_reporte():
    """Genera un archivo Excel con los registros inválidos y sus errores."""
    invalidos = session.get('registros_invalidos', [])
    if not invalidos:
        flash('No hay registros inválidos para descargar.', 'warning')
        return redirect(url_for('importacion.index'))

    data = []
    for inv in invalidos:
        row = inv['datos'].copy()
        if 'fecha_alta' in row and row['fecha_alta'] is not None:
            row['fecha_alta'] = str(row['fecha_alta'])
        if 'fecha_baja' in row and row['fecha_baja'] is not None:
            row['fecha_baja'] = str(row['fecha_baja'])
        row['errores'] = ', '.join(inv['errores'])
        row['fila'] = inv['fila']
        data.append(row)

    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Errores')
    output.seek(0)

    return send_file(
        output,
        download_name='errores_importacion.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )