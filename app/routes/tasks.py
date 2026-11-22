# app/routes/tasks.py
from flask import Blueprint, request, jsonify, current_app
from app import db
from app.models import CuotaSocial, Socio, ChytaPayPaymentRequest
from app.services.chytapay_service import ChytaPayService
from datetime import datetime

tasks_bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

@tasks_bp.route('/chytapay-pendientes', methods=['GET', 'POST'])
def procesar_chytapay_pendientes():
    """
    Endpoint para ser llamado por un CRON.
    Busca cuotas pendientes sin CVU y les genera el pago digital.
    Procesa de a 8 por vez para no dar Timeout en Vercel.
    """
    # 1. Seguridad: Verificar token secreto
    secret = current_app.config.get('CRON_SECRET', '')
    token = request.headers.get('Authorization')
    
    # ESTO ES PARA DEBUGGEAR:
    print(f"DEBUG: Secreto en Vercel: '{secret}'")
    print(f"DEBUG: Token recibido de GitHub: '{token}'")
    
    if not secret or token != f'Bearer {secret}':
        return jsonify({'error': 'No autorizado'}), 401

    # 2. Buscar cuotas candidatas (solo 5 por ejecución para no saturar)
    cuotas = (
        CuotaSocial.query
        .join(Socio, CuotaSocial.id_socio == Socio.id)
        .filter(
            CuotaSocial.estado == 'pendiente',
            CuotaSocial.chytapay_cvu.is_(None),
            Socio.enviar_cuota_whatsapp == True,
            Socio.fecha_baja.is_(None) # Solo socios activos
        )
        .limit(8) 
        .all()
    )

    if not cuotas:
        return jsonify({'message': 'No hay cuotas pendientes para procesar.'}), 200

    procesadas = 0
    errores = 0

    # 3. Procesar cada cuota
    for cuota in cuotas:
        try:
            # Usamos el método que ya tenías en el service
            exito, mensaje = ChytaPayService.reintentar_creacion(cuota)
            if exito:
                procesadas += 1
            else:
                errores += 1
        except Exception as e:
            current_app.logger.error(f"Error procesando cuota {cuota.id}: {e}")
            errores += 1

    return jsonify({
        'message': 'Proceso completado',
        'encontradas': len(cuotas),
        'procesadas': procesadas,
        'errores': errores
    }), 200