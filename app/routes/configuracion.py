# app/routes/configuracion.py

from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import TipoSocio, CategoriaSocio, Adicional, SocioAdicional
from app.forms import TipoSocioForm, CategoriaSocioForm, AdicionalForm
from app.utils import current_empresa
from app.auditoria import auditar
from app.decorators import requiere_ver, requiere_editar

config_bp = Blueprint('config', __name__, url_prefix='/config')


# ============================================================
# Tipos de socio
# ============================================================

@config_bp.route('/tipos')
@requiere_ver('configuracion')
def listar_tipos():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    tipos = TipoSocio.query.filter_by(
        id_empresa=empresa.id
    ).order_by(TipoSocio.nombre).all()
    return render_template('config/tipos.html', tipos=tipos)


@config_bp.route('/tipos/nuevo', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def nuevo_tipo():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = TipoSocioForm()
    if form.validate_on_submit():
        tipo = TipoSocio(
            nombre=form.nombre.data,
            descripcion=form.descripcion.data,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(tipo)
        db.session.flush()
        auditar('crear', 'TipoSocio', tipo.id, despues=tipo)
        db.session.commit()
        flash('Tipo de socio creado.', 'success')
        return redirect(url_for('config.listar_tipos'))

    return render_template('config/tipo_form.html', form=form)


@config_bp.route('/tipos/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def editar_tipo(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    tipo = TipoSocio.query.get_or_404(id)
    if tipo.id_empresa != empresa.id:
        flash('Tipo no encontrado.', 'danger')
        return redirect(url_for('config.listar_tipos'))

    form = TipoSocioForm(obj=tipo)
    if form.validate_on_submit():
        estado_antes = {
            'nombre': tipo.nombre,
            'descripcion': tipo.descripcion,
            'activo': tipo.activo,
        }
        tipo.nombre = form.nombre.data
        tipo.descripcion = form.descripcion.data
        tipo.activo = form.activo.data
        db.session.flush()
        auditar(
            'editar', 'TipoSocio', tipo.id,
            antes=estado_antes, despues=tipo,
        )
        db.session.commit()
        flash('Tipo actualizado.', 'success')
        return redirect(url_for('config.listar_tipos'))

    return render_template('config/tipo_form.html', form=form, tipo=tipo)


# ============================================================
# Categorías de socio
# ============================================================

@config_bp.route('/categorias')
@requiere_ver('configuracion')
def listar_categorias():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    categorias = CategoriaSocio.query.filter_by(
        id_empresa=empresa.id
    ).order_by(CategoriaSocio.nombre).all()
    return render_template('config/categorias.html', categorias=categorias)


@config_bp.route('/categorias/nuevo', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def nueva_categoria():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    form = CategoriaSocioForm()
    if form.validate_on_submit():
        cat = CategoriaSocio(
            nombre=form.nombre.data,
            descripcion=form.descripcion.data,
            activo=form.activo.data,
            id_empresa=empresa.id,
        )
        db.session.add(cat)
        db.session.flush()
        auditar('crear', 'CategoriaSocio', cat.id, despues=cat)
        db.session.commit()
        flash('Categoría creada.', 'success')
        return redirect(url_for('config.listar_categorias'))

    return render_template('config/categoria_form.html', form=form)


@config_bp.route('/categorias/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def editar_categoria(id):
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    cat = CategoriaSocio.query.get_or_404(id)
    if cat.id_empresa != empresa.id:
        flash('Categoría no encontrada.', 'danger')
        return redirect(url_for('config.listar_categorias'))

    form = CategoriaSocioForm(obj=cat)
    if form.validate_on_submit():
        estado_antes = {
            'nombre': cat.nombre,
            'descripcion': cat.descripcion,
            'activo': cat.activo,
        }
        cat.nombre = form.nombre.data
        cat.descripcion = form.descripcion.data
        cat.activo = form.activo.data
        db.session.flush()
        auditar(
            'editar', 'CategoriaSocio', cat.id,
            antes=estado_antes, despues=cat,
        )
        db.session.commit()
        flash('Categoría actualizada.', 'success')
        return redirect(url_for('config.listar_categorias'))

    return render_template('config/categoria_form.html', form=form, categoria=cat)

# ============================================================
# Panel de ChytaPay
# ============================================================

@config_bp.route('/chytapay')
@requiere_editar('configuracion')
def chytapay_panel():
    """Panel de estado de la conexión de ChytaPay."""
    from app.models import (
        ChytaPayToken, ChytaPayPaymentRequest, CuotaSocial, Socio,
    )

    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    token = ChytaPayToken.query.filter_by(id_empresa=empresa.id).first()

    # --- Estado del token ---
    token_info = {
        'conectado': False,
        'expirado': False,
        'expira_en': None,
        'ultima_conexion': None,
    }
    if token:
        token_info['conectado'] = True
        token_info['expirado'] = token.is_expired()
        token_info['expira_en'] = token.expires_at
        token_info['ultima_conexion'] = token.updated_at

    # --- Stats ---
    cuotas_con_cvu = (
        CuotaSocial.query
        .join(Socio, CuotaSocial.id_socio == Socio.id)
        .filter(
            Socio.id_empresa == empresa.id,
            CuotaSocial.estado == 'pendiente',
            CuotaSocial.chytapay_cvu.isnot(None),
        )
        .count()
    )

    cuotas_pagadas_digital = (
        CuotaSocial.query
        .join(Socio, CuotaSocial.id_socio == Socio.id)
        .filter(
            Socio.id_empresa == empresa.id,
            CuotaSocial.estado == 'pagada',
            CuotaSocial.chytapay_cvu.isnot(None),
        )
        .count()
    )

    # Cuántos socios tienen WhatsApp activo
    socios_con_whatsapp = Socio.query.filter_by(
        id_empresa=empresa.id, enviar_cuota_whatsapp=True,
    ).filter(Socio.fecha_baja.is_(None)).count()

    stats = {
        'cuotas_con_cvu': cuotas_con_cvu,
        'cuotas_pagadas_digital': cuotas_pagadas_digital,
        'socios_con_whatsapp': socios_con_whatsapp,
    }

    return render_template(
        'config/chytapay_panel.html',
        empresa=empresa,
        token_info=token_info,
        stats=stats,
    )

@config_bp.route('/adicionales')
@requiere_ver('configuracion')
def listar_adicionales():
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))
    adicionales = Adicional.query.filter_by(id_empresa=empresa.id).order_by(Adicional.nombre).all()
    return render_template('config/adicionales.html', adicionales=adicionales)

@config_bp.route('/adicionales/nuevo', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def nuevo_adicional():
    empresa = current_empresa()
    form = AdicionalForm()
    if form.validate_on_submit():
        adicional = Adicional(
            nombre=form.nombre.data,
            monto=form.monto.data,
            fecha_desde=form.fecha_desde.data,
            fecha_hasta=form.fecha_hasta.data,
            activo=form.activo.data,
            id_empresa=empresa.id
        )
        db.session.add(adicional)
        db.session.commit()
        flash('Adicional creado.', 'success')
        return redirect(url_for('config.listar_adicionales'))
    return render_template('config/adicional_form.html', form=form)

@config_bp.route('/adicionales/editar/<int:id>', methods=['GET', 'POST'])
@requiere_editar('configuracion')
def editar_adicional(id):
    empresa = current_empresa()
    adicional = Adicional.query.get_or_404(id)
    form = AdicionalForm(obj=adicional)
    if form.validate_on_submit():
        adicional.nombre = form.nombre.data
        adicional.monto = form.monto.data
        adicional.activo = form.activo.data
        adicional.fecha_desde = form.fecha_desde.data
        adicional.fecha_hasta = form.fecha_hasta.data        
        db.session.commit()
        flash('Adicional actualizado.', 'success')
        return redirect(url_for('config.listar_adicionales'))
    return render_template('config/adicional_form.html', form=form, adicional=adicional)