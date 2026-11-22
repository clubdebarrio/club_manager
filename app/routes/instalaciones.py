# app/routes/instalaciones.py

from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import Instalacion
from app.forms import InstalacionForm
from app.utils import current_empresa
from app.decorators import requiere_ver, requiere_editar
from app.auditoria import auditar

instalaciones_bp = Blueprint('instalaciones', __name__, url_prefix='/instalaciones')


@instalaciones_bp.route('/')
@requiere_ver('instalaciones')
def listar():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    instalaciones = Instalacion.query.filter_by(
        id_empresa=empresa.id
    ).order_by(Instalacion.nombre).all()
    return render_template('instalaciones/listar.html', instalaciones=instalaciones)


@instalaciones_bp.route('/nuevo', methods=['GET', 'POST'])
@requiere_editar('instalaciones')
def nuevo():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = InstalacionForm()
    if form.validate_on_submit():
        instalacion = Instalacion(
            nombre=form.nombre.data.strip().title(),
            descripcion=form.descripcion.data,
            capacidad=form.capacidad.data,
            tipo_cobro=form.tipo_cobro.data,
            precio_por_hora=form.precio_por_hora.data,
            precio_por_uso=form.precio_por_uso.data,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(instalacion)
        db.session.flush()          
        auditar('crear', 'Instalacion', instalacion.id, despues=instalacion)
        db.session.commit()
        flash('Instalación creada exitosamente.', 'success')
        return redirect(url_for('instalaciones.listar'))

    return render_template('instalaciones/form.html', form=form)


@instalaciones_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('instalaciones')
def editar(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    instalacion = Instalacion.query.get_or_404(id)
    if instalacion.id_empresa != empresa.id:
        flash('Instalación no encontrada.', 'danger')
        return redirect(url_for('instalaciones.listar'))

    form = InstalacionForm(obj=instalacion)
    if form.validate_on_submit():
        estado_antes = {
            'nombre': instalacion.nombre,
            'descripcion': instalacion.descripcion,
            'capacidad': instalacion.capacidad,
            'tipo_cobro': instalacion.tipo_cobro,
            'precio_por_hora': str(instalacion.precio_por_hora) if instalacion.precio_por_hora else None,
            'precio_por_uso': str(instalacion.precio_por_uso) if instalacion.precio_por_uso else None,
            'activo': instalacion.activo,
        }
        # -------------------------------
    
        instalacion.nombre = form.nombre.data
        instalacion.descripcion = form.descripcion.data
        instalacion.capacidad = form.capacidad.data
        instalacion.tipo_cobro = form.tipo_cobro.data
        instalacion.precio_por_hora = form.precio_por_hora.data
        instalacion.precio_por_uso = form.precio_por_uso.data
        instalacion.activo = form.activo.data
        db.session.flush()
        auditar('editar', 'Instalacion', instalacion.id, antes=estado_antes, despues=instalacion)         
        db.session.commit()
        flash('Instalación actualizada.', 'success')
        return redirect(url_for('instalaciones.listar'))

    return render_template('instalaciones/form.html', form=form, instalacion=instalacion)