# app/services/compra_service.py
from datetime import datetime
from decimal import Decimal

from app import db
from app.models import (
    Compra, PagoCompra, Movimiento, Cheque, CuentaBancaria,
)
from app.services.contabilidad_service import ContabilidadService


class CompraService:

    @staticmethod
    def anular_compra(compra_id, motivo=None, commit=True):
        """
        Anula una compra completa. Revierte:
        - Stock de cada producto (bloquea si el stock actual es
          menor al comprado).
        - Movimientos de caja (espejos sin asiento, marcados como
          anulados).
        - Cheques: propios → 'rechazado', terceros → 'en_cartera'.
        - Contabilidad: un contra-asiento unificado.

        Bloquea si la compra tiene pagos posteriores
        (registrados vía `pagar_compra`) o si algún cheque ya fue
        cobrado/descontado.
        """
        from app.services.caja_service import CajaService

        compra = Compra.query.get(compra_id)
        if not compra:
            raise ValueError('Compra no encontrada.')

        if compra.estado == 'anulada':
            raise ValueError('Esta compra ya fue anulada.')

        # --- Bloquear si tiene pagos posteriores ---
        pagos_posteriores = [
            p for p in compra.pagos if p.fecha_pago is not None
        ]
        if pagos_posteriores:
            raise ValueError(
                'No se puede anular: la compra tiene pagos posteriores '
                'registrados. En ese caso, hacé una nota de crédito '
                'en lugar de anular la compra completa.'
            )

        # --- Validar stock ---
        for det in compra.detalles:
            producto = det.producto
            if producto and producto.stock_actual < det.cantidad:
                raise ValueError(
                    f'No se puede anular: el stock actual de '
                    f'"{producto.nombre}" ({producto.stock_actual}) es '
                    f'menor a la cantidad comprada ({det.cantidad}). '
                    f'Se vendió parte de esta compra.'
                )

        # --- Validar cheques ---
        for pago in compra.pagos:
            if pago.id_cheque:
                cheque = Cheque.query.get(pago.id_cheque)
                if not cheque:
                    continue
                if cheque.tipo == 'emitido':
                    if cheque.estado not in ('en_cartera', 'pendiente'):
                        raise ValueError(
                            f'No se puede anular: el cheque propio '
                            f'#{cheque.numero} ya está en estado '
                            f'"{cheque.estado}". No se puede anular '
                            f'una compra cuyo cheque ya fue pagado.'
                        )
                elif cheque.tipo == 'recibido':
                    if cheque.estado != 'entregado':
                        raise ValueError(
                            f'No se puede anular: el cheque de tercero '
                            f'#{cheque.numero} está en estado '
                            f'"{cheque.estado}". Solo se puede anular '
                            f'si sigue entregado (no cobrado).'
                        )

        # --- Revertir stock ---
        for det in compra.detalles:
            if det.producto:
                det.producto.stock_actual -= det.cantidad

        # --- Anular movimientos + revertir cheques ---
        movs_compra = Movimiento.query.filter(
            Movimiento.id_empresa == compra.id_empresa,
            Movimiento.descripcion.like(f'%Compra #{compra.id}%'),
            Movimiento.anulado == False,
            Movimiento.es_anulacion == False,
        ).all()

        ahora = datetime.utcnow()
        for mov in movs_compra:
            # Crear espejo SIN asiento (el contra-asiento unificado
            # cubre todo el evento contable)
            tipo_inverso = 'egreso' if mov.tipo == 'ingreso' else 'ingreso'
            espejo = Movimiento(
                id_cuenta=mov.id_cuenta,
                id_cuenta_contable=mov.id_cuenta_contable,
                fecha=ahora,
                tipo=tipo_inverso,
                monto=mov.monto,
                descripcion=f'Anulación Compra #{compra.id}: {mov.descripcion}',
                metodo_pago=mov.metodo_pago,
                comprobante=mov.comprobante,
                conciliado=False,
                id_empresa=mov.id_empresa,
                es_anulacion=True,
            )
            db.session.add(espejo)
            db.session.flush()

            # Revertir saldo de la cuenta
            cuenta = CuentaBancaria.query.get(mov.id_cuenta)
            if cuenta:
                if espejo.tipo == 'ingreso':
                    cuenta.saldo_actual += espejo.monto
                else:
                    cuenta.saldo_actual -= espejo.monto

            # Marcar original como anulado
            mov.anulado = True
            mov.fecha_anulacion = ahora
            mov.motivo_anulacion = (
                motivo or f'Anulación de compra #{compra.id}'
            )[:200]
            mov.id_movimiento_anulacion = espejo.id

        # --- Revertir cheques ---
        for pago in compra.pagos:
            if not pago.id_cheque:
                continue
            cheque = Cheque.query.get(pago.id_cheque)
            if not cheque:
                continue
            if cheque.tipo == 'emitido':
                cheque.estado = 'rechazado'
                cheque.observaciones = (
                    (cheque.observaciones or '') +
                    f' [Anulado por reversión compra #{compra.id}]'
                )[:200]
            elif cheque.tipo == 'recibido':
                cheque.estado = 'en_cartera'
                cheque.observaciones = (
                    (cheque.observaciones or '') +
                    f' [Devuelto por proveedor al anular compra #{compra.id}]'
                )[:200]

        # --- Contra-asiento unificado ---
        ContabilidadService.generar_contra_asiento_compra(
            compra, compra.pagos
        )

        # --- Marcar la compra como anulada ---
        compra.estado = 'anulada'
        compra.fecha_anulacion = ahora
        compra.motivo_anulacion = (
            motivo or 'Sin motivo especificado'
        )[:200]

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return compra