# app/routes/socios.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, send_file, current_app, Response,
)
from app import db
from app.models import Socio, TipoSocio, CategoriaSocio, Adicional, SocioAdicional
from app.forms import SocioForm
from datetime import datetime
from app.services.socio_service import SocioService
from app.utils import quitar_acentos, current_empresa, formato_pesos
from sqlalchemy import func
from app.services.reporte_service import ReporteService
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar
from app.models import Rol, Usuario
import io
from app.services.foto_socio_service import FotoSocioService

socios_bp = Blueprint('socios', __name__)


@socios_bp.route('/')
@requiere_ver('socios')
def listar():
    """Vista principal que muestra el listado de socios con filtro y paginación."""
    filtro = request.args.get('filtro', 'activos')
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('ITEMS_PER_PAGE', 20)
    query = request.args.get('q', '').strip()

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    base_query = Socio.query.filter(Socio.id_empresa == empresa.id)

    if filtro == 'activos':
        base_query = base_query.filter(Socio.fecha_baja.is_(None))
    elif filtro == 'inactivos':
        base_query = base_query.filter(Socio.fecha_baja.isnot(None))

    if query:
        query_normalizada = quitar_acentos(query).lower()
        base_query = base_query.filter(
            (func.unaccent(Socio.apellido).ilike(f'%{query_normalizada}%')) |
            (func.unaccent(Socio.nombre).ilike(f'%{query_normalizada}%')) |
            Socio.numero_socio.ilike(f'%{query_normalizada}%')
        )

    pagination = base_query.order_by(Socio.numero_socio.asc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    socios = pagination.items

    return render_template('socios/listar.html',
                         socios=socios,
                         pagination=pagination,
                         filtro=filtro,
                         query=query)


@socios_bp.route('/crear', methods=['GET', 'POST'])
@requiere_editar('socios')
def crear():
    empresa = current_empresa()
    if not empresa:
        flash('Primero debes crear un club (empresa).', 'warning')
        return redirect(url_for('main.index'))

    form = SocioForm()
    form.id_empresa.data = empresa.id
    form.id_tipo_socio.choices = [
        (t.id, t.nombre)
        for t in TipoSocio.query.filter_by(activo=True, id_empresa=empresa.id).all()
    ]
    form.id_categoria_socio.choices = [
        (c.id, c.nombre)
        for c in CategoriaSocio.query.filter_by(activo=True, id_empresa=empresa.id).all()
    ]
    
    # NUEVO: Cargar opciones de adicionales
    form.adicionales.choices = [
        (a.id, f"{a.nombre} ({formato_pesos(a.monto)})") 
        for a in Adicional.query.filter_by(id_empresa=empresa.id, activo=True).order_by(Adicional.nombre).all()
    ]

    familias = (
        Socio.query
        .filter(
            Socio.id_empresa == empresa.id,
            Socio.fecha_baja.is_(None),
            Socio.id_familia.is_(None),
        )
        .order_by(Socio.numero_socio)
        .all()
    )
    form.id_familia.choices = [(0, 'Ninguno')] + [
        (f.id, f"{f.numero_socio} - {f.apellido}, {f.nombre}") for f in familias
    ]

    # Buscamos el rol Cobrador
    rol_cobrador = Rol.query.filter_by(nombre='Cobrador', id_empresa=empresa.id).first()
    
    # Cargamos los usuarios que tienen ese rol
    cobradores = []
    if rol_cobrador:
        cobradores = Usuario.query.filter_by(id_rol=rol_cobrador.id, id_empresa=empresa.id, activo=True).all()
        
    form.id_cobrador.choices = [(0, 'Sin cobrador (Paga por su cuenta)')] + [
        (c.id, f"{c.nombre}") for c in cobradores
    ]
    
    if form.validate_on_submit():
        nuevo_socio = Socio(
            nombre=form.nombre.data.strip().title(),
            apellido=form.apellido.data.strip().title(),
            tipo_documento=form.tipo_documento.data,
            nro_documento=form.nro_documento.data,
            email=form.email.data,
            telefono=form.telefono.data,
            numero_socio=SocioService.generar_numero_socio(
                empresa.id,
                form.id_familia.data if form.id_familia.data else None,
            ),
            id_tipo_socio=form.id_tipo_socio.data,
            id_categoria_socio=form.id_categoria_socio.data,
            id_familia=form.id_familia.data if form.id_familia.data else None,
            id_empresa=empresa.id,
            enviar_cuota_whatsapp=form.enviar_cuota_whatsapp.data,
            id_cobrador=form.id_cobrador.data if form.id_cobrador.data and form.id_cobrador.data != 0 else None,
            fecha_nacimiento=form.fecha_nacimiento.data,
        )
        if form.id_cobrador.data and form.id_cobrador.data != 0:
            if form.enviar_cuota_whatsapp.data:
                flash('Advertencia: Asignaste un cobrador pero también activaste el envío por WhatsApp. Se recomienda elegir solo uno para evitar cobros duplicados.', 'warning')
    
        db.session.add(nuevo_socio)
        db.session.flush() 

        # Guardar adicionales seleccionados
        if form.adicionales.data:
            for id_ad in form.adicionales.data:
                db.session.add(SocioAdicional(
                    id_socio=nuevo_socio.id, 
                    id_adicional=id_ad, 
                    activo=True
                ))

        try:
            auditar('crear', 'Socio', nuevo_socio.id, despues=nuevo_socio)
            db.session.commit()
            flash('Socio creado exitosamente.', 'success')
            return redirect(url_for('socios.listar', filtro='activos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar: {str(e)}', 'danger')

    return render_template('socios/crear.html', form=form, empresa=empresa)


@socios_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('socios')
def editar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))

    form = SocioForm(obj=socio)
    form._socio_id = socio.id
    form.id_tipo_socio.choices = [
        (t.id, t.nombre)
        for t in TipoSocio.query.filter_by(activo=True, id_empresa=empresa.id).all()
    ]
    form.id_categoria_socio.choices = [
        (c.id, c.nombre)
        for c in CategoriaSocio.query.filter_by(activo=True, id_empresa=empresa.id).all()
    ]
    
    # NUEVO: Cargar opciones de adicionales
    form.adicionales.choices = [
        (a.id, f"{a.nombre} ({formato_pesos(a.monto)})") 
        for a in Adicional.query.filter_by(id_empresa=empresa.id, activo=True).order_by(Adicional.nombre).all()
    ]

    familias = (
        Socio.query
        .filter(
            Socio.id_empresa == empresa.id,
            Socio.fecha_baja.is_(None),
            Socio.id_familia.is_(None),
            Socio.id != socio.id,
        )
        .order_by(Socio.numero_socio)
        .all()
    )
    form.id_familia.choices = [(0, 'Ninguno')] + [
        (f.id, f"{f.numero_socio} - {f.apellido}, {f.nombre}") for f in familias
    ]

    # Si es un GET, precargar los adicionales que ya tiene el socio
    if request.method == 'GET':
        form.adicionales.data = [
            sa.id_adicional for sa in socio.adicionales_asignados if sa.activo
        ]

    # Buscamos el rol Cobrador
    rol_cobrador = Rol.query.filter_by(nombre='Cobrador', id_empresa=empresa.id).first()
    
    # Cargamos los usuarios que tienen ese rol
    cobradores = []
    if rol_cobrador:
        cobradores = Usuario.query.filter_by(id_rol=rol_cobrador.id, id_empresa=empresa.id, activo=True).all()
        
    form.id_cobrador.choices = [(0, 'Sin cobrador (Paga por su cuenta)')] + [
        (c.id, f"{c.nombre}") for c in cobradores
    ]
    
    if form.validate_on_submit():
        # Snapshot antes
        estado_antes = {
            'nombre': socio.nombre,
            'apellido': socio.apellido,
            'tipo_documento': socio.tipo_documento,
            'nro_documento': socio.nro_documento,
            'email': socio.email,
            'telefono': socio.telefono,
            'id_tipo_socio': socio.id_tipo_socio,
            'id_categoria_socio': socio.id_categoria_socio,
            'id_familia': socio.id_familia,
            'enviar_cuota_whatsapp': socio.enviar_cuota_whatsapp,
            'id_cobrador': socio.id_cobrador,
            'fecha_nacimiento': socio.fecha_nacimiento.isoformat() if socio.fecha_nacimiento else None,
        }
        if form.id_cobrador.data and form.id_cobrador.data != 0:
            if form.enviar_cuota_whatsapp.data:
                flash('Advertencia: Asignaste un cobrador pero también activaste el envío por WhatsApp. Se recomienda elegir solo uno para evitar cobros duplicados.', 'warning')

        socio.nombre = form.nombre.data
        socio.apellido = form.apellido.data
        socio.tipo_documento = form.tipo_documento.data
        socio.nro_documento = form.nro_documento.data
        socio.email = form.email.data
        socio.telefono = form.telefono.data
        socio.id_tipo_socio = form.id_tipo_socio.data
        socio.id_categoria_socio = form.id_categoria_socio.data
        socio.id_familia = form.id_familia.data if form.id_familia.data else None
        socio.enviar_cuota_whatsapp = form.enviar_cuota_whatsapp.data
        socio.id_cobrador = form.id_cobrador.data if form.id_cobrador.data and form.id_cobrador.data != 0 else None 
        socio.fecha_nacimiento = form.fecha_nacimiento.data

        # NUEVO: Actualizar Adicionales (Opción de baja con fecha)
        seleccionados = set(form.adicionales.data or [])
        
        # 1. Dar de baja los que ya no están tildados
        for sa in socio.adicionales_asignados:
            if sa.activo and sa.id_adicional not in seleccionados:
                sa.activo = False
                sa.fecha_baja = datetime.utcnow()
                
        # 2. Agregar los nuevos o reactivar los que habían sido dados de baja
        actuales_activos = {sa.id_adicional for sa in socio.adicionales_asignados if sa.activo}
        for id_ad in seleccionados:
            if id_ad not in actuales_activos:
                existente = SocioAdicional.query.filter_by(
                    id_socio=socio.id, id_adicional=id_ad
                ).first()
                
                if existente:
                    existente.activo = True
                    existente.fecha_baja = None
                else:
                    db.session.add(SocioAdicional(
                        id_socio=socio.id, id_adicional=id_ad, activo=True
                    ))

        try:
            db.session.flush()
            auditar('editar', 'Socio', socio.id, antes=estado_antes, despues=socio)
            db.session.commit()
            flash('Socio actualizado correctamente.', 'success')
            return redirect(url_for('socios.listar', filtro='activos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar: {str(e)}', 'danger')

    return render_template('socios/editar.html', form=form, socio=socio)


@socios_bp.route('/api/socios/<int:id>/datos')
@requiere_ver('socios')
def api_datos_socio(id):
    empresa = current_empresa()
    if not empresa:
        return jsonify({'error': 'Sin empresa'}), 400
    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        return jsonify({'error': 'No encontrado'}), 404
    return jsonify({
        'id_tipo_socio': socio.id_tipo_socio,
        'id_categoria_socio': socio.id_categoria_socio
    })


@socios_bp.route('/<int:id>')
@requiere_ver('socios')
def detalle(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))
    return render_template('socios/detalle.html', socio=socio)


@socios_bp.route('/baja/<int:id>', methods=['POST'])
@requiere_editar('socios')
def baja(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))

    socio.fecha_baja = datetime.utcnow()
    db.session.flush()                                            # ← NUEVO
    auditar(                                                      # ← NUEVO
        'baja', 'Socio', socio.id, nota='Baja del socio',
    )
    db.session.commit()
    flash('Socio dado de baja.', 'info')
    return redirect(url_for('socios.listar',
                            filtro=request.args.get('filtro', 'activos')))


@socios_bp.route('/reactivar/<int:id>', methods=['POST'])
@requiere_editar('socios')
def reactivar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))

    socio.fecha_baja = None
    db.session.flush()                                            # ← NUEVO
    auditar(                                                      # ← NUEVO
        'reactivar', 'Socio', socio.id, nota='Reactivación del socio',
    )
    db.session.commit()
    flash('Socio reactivado.', 'success')
    return redirect(url_for('socios.listar',
                            filtro=request.args.get('filtro', 'activos')))


@socios_bp.route('/api/buscar')
@requiere_ver('socios')
def api_buscar():
    query = request.args.get('q', '').strip()
    filtro = request.args.get('filtro', 'activos')
    empresa = current_empresa()
    if not empresa:
        return jsonify([])

    base_query = Socio.query.filter(Socio.id_empresa == empresa.id)

    if filtro == 'activos':
        base_query = base_query.filter(Socio.fecha_baja.is_(None))
    elif filtro == 'inactivos':
        base_query = base_query.filter(Socio.fecha_baja.isnot(None))

    if query:
        query_normalizada = quitar_acentos(query).lower()
        base_query = base_query.filter(
            (func.unaccent(Socio.apellido).ilike(f'%{query_normalizada}%')) |
            (func.unaccent(Socio.nombre).ilike(f'%{query_normalizada}%')) |
            Socio.numero_socio.ilike(f'%{query_normalizada}%')
        )

    socios = base_query.order_by(Socio.numero_socio.asc()).all()

    data = []
    for s in socios:
        data.append({
            'id': s.id,
            'apellido': s.apellido,
            'nombre': s.nombre,
            'numero_socio': s.numero_socio,
            'tipo_documento': s.tipo_documento,
            'nro_documento': s.nro_documento,
            'tipo_socio': s.tipo_socio_rel.nombre if s.tipo_socio_rel else '',
            'categoria_socio': s.categoria_socio_rel.nombre if s.categoria_socio_rel else '',
            'telefono': s.telefono or '',
            'fecha_baja': s.fecha_baja.isoformat() if s.fecha_baja else None
        })
    return jsonify(data)


@socios_bp.route('/reporte')
@requiere_ver('socios')
def reporte():
    formato = request.args.get('formato', 'pdf')
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # BUGFIX: antes listaba TODOS los socios de todas las empresas.
    socios = Socio.query.filter_by(
        id_empresa=empresa.id, fecha_baja=None
    ).order_by(Socio.numero_socio).all()

    columnas = ['Nro. Socio', 'Apellido', 'Nombre', 'Documento', 'Tipo', 'Categoría', 'Teléfono']
    datos = [
        {
            'Nro. Socio': s.numero_socio,
            'Apellido': s.apellido,
            'Nombre': s.nombre,
            'Documento': f"{s.tipo_documento}: {s.nro_documento}",
            'Tipo': s.tipo_socio_rel.nombre if s.tipo_socio_rel else '',
            'Categoría': s.categoria_socio_rel.nombre if s.categoria_socio_rel else '',
            'Teléfono': s.telefono or ''
        } for s in socios
    ]
    titulo = "Reporte de Socios Activos"

    if formato.lower() == 'excel':
        buffer = ReporteService.generar_excel(titulo, columnas, datos)
        return send_file(buffer, download_name='socios.xlsx', as_attachment=True,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    else:
        buffer = ReporteService.generar_pdf(titulo, columnas, datos)
        return send_file(buffer, download_name='socios.pdf', as_attachment=True,
                         mimetype='application/pdf')

@socios_bp.route('/<int:id>/foto')
@requiere_ver('socios')
def servir_foto(id):
    """Sirve la foto del socio desde Supabase Storage."""
    empresa = current_empresa()
    if not empresa:
        return '', 404

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        return '', 404

    if not socio.tiene_foto:
        return '', 404

    try:
        data = FotoSocioService.obtener_foto(socio)
    except Exception:
        current_app.logger.exception(
            f'Error obteniendo foto de socio {socio.id}'
        )
        return '', 500

    if not data:
        return '', 404

    response = send_file(
        io.BytesIO(data),
        mimetype='image/jpeg',
        as_attachment=False,
    )
    # Cache moderada: el navegador revalida en cada carga,
    # pero sirve del caché si el ETag coincide (agregamos ETag abajo)
    response.headers['Cache-Control'] = 'private, max-age=0, must-revalidate'
    # ETag basado en el timestamp para que la revalidación sea real
    if socio.foto_actualizada_at:
        response.headers['ETag'] = (
            f'W/"{socio.foto_actualizada_at.timestamp():.0f}"'
        )
    return response

@socios_bp.route('/<int:id>/foto', methods=['POST'])
@requiere_editar('socios')
def subir_foto(id):
    """Sube o reemplaza la foto del socio."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))

    archivo = request.files.get('foto')
    if not archivo or not archivo.filename:
        flash('No se seleccionó ninguna foto.', 'warning')
        return redirect(url_for('socios.editar', id=socio.id))

    try:
        from app.utils_db import transaccion
        with transaccion():
            bytes_subidos = FotoSocioService.guardar_foto(socio, archivo)
            auditar(
                'subir_foto', 'Socio', socio.id,
                nota=f'Foto actualizada ({bytes_subidos} bytes)',
            )
        flash('Foto actualizada correctamente.', 'success')
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error subiendo foto de socio')
        flash(f'Error al subir la foto: {str(e)}', 'danger')

    return redirect(url_for('socios.editar', id=socio.id))

@socios_bp.route('/<int:id>/foto/eliminar', methods=['POST'])
@requiere_editar('socios')
def borrar_foto(id):
    """Borra la foto del socio."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    socio = Socio.query.get_or_404(id)
    if socio.id_empresa != empresa.id:
        flash('Socio no encontrado.', 'danger')
        return redirect(url_for('socios.listar'))

    try:
        from app.utils_db import transaccion
        with transaccion():
            FotoSocioService.borrar_foto(socio)
            auditar(
                'borrar_foto', 'Socio', socio.id,
                nota='Foto eliminada',
            )
        flash('Foto eliminada.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error borrando foto de socio')
        flash(f'Error al borrar la foto: {str(e)}', 'danger')

    return redirect(url_for('socios.editar', id=socio.id))