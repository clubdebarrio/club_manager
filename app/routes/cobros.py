from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from app.models import Socio, CuotaSocial, Usuario, LiquidacionCobrador, DetalleLiquidacion, CuentaBancaria
from app.decorators import requiere_ver, requiere_editar, cargar_usuario_actual
from app.utils import current_empresa, formato_pesos
from app.services.cuota_service import CuotaService
from app.auditoria import auditar
from decimal import Decimal
from datetime import datetime
import json

cobros_bp = Blueprint('cobros', __name__, url_prefix='/cobros')

@cobros_bp.route('/')
@requiere_ver('cobros')
def mi_cartera():
    empresa = current_empresa()
    usuario = cargar_usuario_actual()
    
    if not empresa or not usuario:
        return redirect(url_for('main.index'))

    socios = Socio.query.filter_by(
        id_empresa=empresa.id, 
        id_cobrador=usuario.id,
        fecha_baja=None
    ).order_by(Socio.apellido).all()

    socios_data = []
    total_a_cobrar = Decimal('0')
    
    for socio in socios:
        cuotas_pendientes = CuotaSocial.query.filter_by(
            id_socio=socio.id, 
            estado='pendiente'
        ).order_by(CuotaSocial.periodo).all()
        
        deuda = sum(c.monto for c in cuotas_pendientes)
        
        if deuda > 0:
            socios_data.append({
                'socio': socio,
                'cuotas': cuotas_pendientes,
                'deuda': deuda
            })
            total_a_cobrar += deuda

    # HISTORIAL DE LIQUIDACIONES DEL COBRADOR
    mis_liquidaciones = LiquidacionCobrador.query.filter_by(
        id_cobrador=usuario.id
    ).order_by(LiquidacionCobrador.fecha_creacion.desc()).all()

    return render_template(
        'cobros/cartera.html', 
        socios_data=socios_data, 
        total_a_cobrar=total_a_cobrar,
        mis_liquidaciones=mis_liquidaciones
    )

@cobros_bp.route('/presentar', methods=['POST'])
@requiere_editar('cobros')
def presentar_liquidacion():
    usuario = cargar_usuario_actual()
    empresa = current_empresa()
    
    cuotas_ids = request.form.getlist('cuotas')
    if not cuotas_ids:
        flash('No seleccionaste ninguna cuota para cobrar.', 'warning')
        return redirect(url_for('cobros.mi_cartera'))

    # Creamos la nueva liquidación
    liquidacion = LiquidacionCobrador(
        id_empresa=empresa.id,
        id_cobrador=usuario.id,
        monto_total=0,
        estado='presentada'
    )
    db.session.add(liquidacion)
    db.session.flush()

    total = Decimal('0')
    
    for c_id in cuotas_ids:
        cuota = CuotaSocial.query.get(c_id)
        if cuota and cuota.estado == 'pendiente':
            detalle = DetalleLiquidacion(
                id_liquidacion=liquidacion.id,
                id_cuota=cuota.id,
                monto_cobrado=cuota.monto
            )
            db.session.add(detalle)
            total += cuota.monto
    
    liquidacion.monto_total = total
    db.session.commit()

    flash(f'Rendición presentada por {formato_pesos(total)}. Esperá la aprobación.', 'success')
    return redirect(url_for('cobros.mi_cartera'))

@cobros_bp.route('/eliminar/<int:id>', methods=['POST'])
@requiere_editar('cobros')
def eliminar_liquidacion(id):
    usuario = cargar_usuario_actual()
    liquidacion = LiquidacionCobrador.query.get_or_404(id)
    
    # Validaciones de seguridad
    if liquidacion.id_cobrador != usuario.id:
        flash('No tenés permiso para eliminar esta rendición.', 'danger')
        return redirect(url_for('cobros.mi_cartera'))
        
    if liquidacion.estado not in ('borrador', 'presentada'):
        flash('Solo podés eliminar rendiciones que no hayan sido aprobadas.', 'warning')
        return redirect(url_for('cobros.mi_cartera'))

    # Las cuotas NO se borran, solo se borra la rendición. 
    # Las cuotas vuelven a aparecer en la cartera automáticamente.
    db.session.delete(liquidacion)
    db.session.commit()
    
    flash('Rendición eliminada correctamente.', 'success')
    return redirect(url_for('cobros.mi_cartera'))

@cobros_bp.route('/admin/liquidaciones')
@requiere_editar('cuotas')
def admin_liquidaciones():
    empresa = current_empresa()
    # Traemos presentadas y rechazadas para que la secretaría vea el historial
    liquidaciones = LiquidacionCobrador.query.filter(
        LiquidacionCobrador.id_empresa == empresa.id,
        LiquidacionCobrador.estado.in_(['presentada', 'rechazada'])
    ).order_by(LiquidacionCobrador.fecha_creacion.desc()).all()
    
    return render_template('cobros/admin_liquidaciones.html', liquidaciones=liquidaciones)

@cobros_bp.route('/admin/liquidacion/<int:id>/aprobar', methods=['POST'])
@requiere_editar('cuotas')
def aprobar_liquidacion(id):
    empresa = current_empresa()
    liquidacion = LiquidacionCobrador.query.get_or_404(id)
    
    if liquidacion.id_empresa != empresa.id or liquidacion.estado != 'presentada':
        flash('Rendición inválida.', 'danger')
        return redirect(url_for('cobros.admin_liquidaciones'))

    caja_efectivo = CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, tipo='efectivo', activo=True
    ).first()

    if not caja_efectivo:
        flash('No hay una Caja Chica activa para recibir el efectivo.', 'danger')
        return redirect(url_for('cobros.admin_liquidaciones'))

    try:
        from app.utils_db import transaccion
        with transaccion():
            for detalle in liquidacion.detalles:
                CuotaService.marcar_cuota_pagada(
                    detalle.id_cuota,
                    fecha_pago=datetime.utcnow(),
                    metodo='efectivo',
                    id_cuenta=caja_efectivo.id
                )
            
            liquidacion.estado = 'aprobada'
            liquidacion.fecha_aprobacion = datetime.utcnow()
            
            auditar(
                'aprobar', 'LiquidacionCobrador', liquidacion.id,
                nota=f'Aprobada rendición de {liquidacion.cobrador.nombre} por ${liquidacion.monto_total}'
            )
            
        flash('Rendición aprobada. Se generaron los ingresos en Caja Chica.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al aprobar: {str(e)}', 'danger')

    return redirect(url_for('cobros.admin_liquidaciones'))

@cobros_bp.route('/admin/liquidacion/<int:id>/rechazar', methods=['POST'])
@requiere_editar('cuotas')
def rechazar_liquidacion(id):
    empresa = current_empresa()
    liquidacion = LiquidacionCobrador.query.get_or_404(id)
    
    if liquidacion.id_empresa != empresa.id or liquidacion.estado != 'presentada':
        flash('Rendición inválida.', 'danger')
        return redirect(url_for('cobros.admin_liquidaciones'))

    motivo = request.form.get('motivo', 'Sin motivo especificado')
    
    liquidacion.estado = 'rechazada'
    liquidacion.observaciones = motivo
    
    auditar(
        'rechazar', 'LiquidacionCobrador', liquidacion.id,
        nota=f'Rechazada rendición de {liquidacion.cobrador.nombre}. Motivo: {motivo}'
    )
    
    db.session.commit()
    flash('Rendición rechazada. El cobrador deberá revisarla.', 'warning')
    return redirect(url_for('cobros.admin_liquidaciones'))