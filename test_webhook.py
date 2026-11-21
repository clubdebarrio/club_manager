# test_webhook.py
"""
Test de idempotencia del webhook de ChytaPay.

Crea un payment_request de prueba y simula webhooks (sin llamar
a la API real de ChytaPay).

Uso:
    python test_webhook.py
"""
import os
from datetime import datetime

from app import create_app, db
from app.models import (
    ChytaPayWebhookEvent, ChytaPayPaymentRequest, CuotaSocial,
    Movimiento, Socio, Empresa,
)
from app.services.chytapay_service import ChytaPayService


def main():
    app = create_app()
    with app.app_context():
        # --- Buscar cualquier cuota para usar como sujeto de prueba ---
        cuota = (
            CuotaSocial.query
            .join(Socio)
            .filter(CuotaSocial.estado.in_(['pendiente', 'pagada']))
            .order_by(CuotaSocial.id.desc())
            .first()
        )
        if not cuota:
            print('❌ No hay cuotas en la base.')
            print('   Creá una cuota primero (o generá cuotas).')
            return

        print(f'Usando cuota: #{cuota.id} - {cuota.periodo} '
              f'- ${cuota.monto} - estado: {cuota.estado}')

        # --- Crear payment_request de prueba si no existe ---
        reference_id = f'TEST-{cuota.id}-{int(datetime.utcnow().timestamp())}'
        pr = ChytaPayPaymentRequest.query.filter_by(
            cuota_social_id=cuota.id
        ).first()

        if not pr:
            pr = ChytaPayPaymentRequest(
                reference_id=reference_id,
                cuota_social_id=cuota.id,
                chytapay_status='pending',
                intentos=0,
            )
            db.session.add(pr)
            db.session.commit()
            print(f'✓ Payment request de prueba creado: {reference_id}')
        else:
            reference_id = pr.reference_id
            print(f'✓ Payment request existente: {reference_id}')

        # Resetear estado de la cuota para que el test tenga sentido
        cuota.estado = 'pendiente'
        cuota.fecha_pago = None
        db.session.commit()

        # --- Verificar empresa ---
        empresa = Empresa.query.get(cuota.socio.id_empresa)
        print(f'Empresa: {empresa.nombre} '
              f'(puede_chytapay={empresa.puede_usar_chytapay})')

        # --- Contar movimientos previos ---
        movs_antes = Movimiento.query.filter(
            Movimiento.descripcion.like(f'%{reference_id}%')
        ).count()
        print(f'Movimientos con reference_id antes: {movs_antes}')

        # --- Event ID único ---
        event_id = f'test-idempotencia-{os.urandom(4).hex()}'
        payload = {
            'eventId': event_id,
            'data': {
                'referenceId': reference_id,
                'state': 'total_payment',
                'amount': float(cuota.monto),
            },
        }

        token = os.environ.get('CHYTAPAY_WEBHOOK_SECRET', '').strip()
        if not token:
            print('❌ CHYTAPAY_WEBHOOK_SECRET no está seteado.')
            return

        print()
        print('=' * 60)
        print('PRIMERA LLAMADA (debe procesar)')
        print('=' * 60)
        try:
            ChytaPayService.process_webhook(payload, token)
            print('✅ Procesado OK')
        except Exception as e:
            print(f'❌ Error: {e}')
            return

        db.session.expire_all()
        cuota_after = CuotaSocial.query.get(cuota.id)
        movs_despues = Movimiento.query.filter(
            Movimiento.descripcion.like(f'%{reference_id}%')
        ).count()
        print(f'   Estado cuota: {cuota_after.estado}')
        print(f'   Movimientos creados: {movs_despues}')

        print()
        print('=' * 60)
        print('SEGUNDA LLAMADA (mismo eventId, debe ignorar)')
        print('=' * 60)
        try:
            ChytaPayService.process_webhook(payload, token)
            print('✅ Procesado OK (sin duplicar)')
        except Exception as e:
            print(f'❌ Error: {e}')
            return

        db.session.expire_all()
        movs_final = Movimiento.query.filter(
            Movimiento.descripcion.like(f'%{reference_id}%')
        ).count()

        print()
        print('=' * 60)
        print('RESULTADOS')
        print('=' * 60)
        print(f'Eventos con eventId: '
              f'{ChytaPayWebhookEvent.query.filter_by(event_id=event_id).count()}')
        print(f'Movimientos después de 1ª llamada: {movs_despues}')
        print(f'Movimientos después de 2ª llamada: {movs_final}')

        if movs_despues == movs_final:
            print()
            print('🎉 IDEMPOTENCIA OK - No se duplicó nada.')
        else:
            print()
            print('❌ BUG - Se duplicaron movimientos.')

        # --- Cleanup (opcional) ---
        print()
        respuesta = input('¿Borrar datos de prueba? (s/n): ')
        if respuesta.strip().lower() == 's':
            ChytaPayWebhookEvent.query.filter_by(
                event_id=event_id
            ).delete()
            # Borrar el movimiento creado (y su asiento si lo tiene)
            for m in Movimiento.query.filter(
                Movimiento.descripcion.like(f'%{reference_id}%')
            ).all():
                db.session.delete(m)
            cuota.estado = 'pendiente'
            cuota.fecha_pago = None
            db.session.commit()
            print('✓ Cleanup OK')


if __name__ == '__main__':
    main()