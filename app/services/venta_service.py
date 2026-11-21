# app/services/venta_service.py
from datetime import datetime
from decimal import Decimal

from app import db
from app.models import (
    Venta, DetalleVenta, Producto, Movimiento, Cheque, AsientoContable,
    LineaAsiento,
)


class VentaService:

    @staticmethod
    def _buscar_movimiento_venta(venta):
        """
        Busca el movimiento de caja asociado a la venta.
        El movimiento se identifica por la descripción que genera el
        flujo de `ventas.nueva`: 'Venta indumentaria #N - <método>'.
        """
        descripcion = (
            f'Venta indumentaria #{venta.id} - {venta.metodo_pago}'
        )
        return Movimiento.query.filter(
            Movimiento.id_empresa == venta.id_empresa,
            Movimiento.descripcion == descripcion,
            Movimiento.es_anulacion == False,
        ).first()

    @staticmethod
    def anular_venta(venta_id, motivo=None, commit=True):
        """
        Anula una venta:
        - Revierte el stock de cada producto.
        - Anula el movimiento de caja (crea espejo + contra-asiento).
        - Si había cheque, lo marca como 'rechazado' (devuelto al cliente).
        - Genera contra-asiento del CMV.
        - Marca la venta como 'anulada'.

        No commitea si commit=False.

        Retorna (venta, movimiento_original, movimiento_espejo).
        """
        from app.services.caja_service import CajaService
        from app.services.contabilidad_service import ContabilidadService

        venta = Venta.query.get(venta_id)
        if not venta:
            raise ValueError('Venta no encontrada.')

        if venta.estado == 'anulada':
            raise ValueError('Esta venta ya fue anulada.')

        # --- Buscar el movimiento asociado ---
        movimiento = VentaService._buscar_movimiento_venta(venta)

        # --- Validar estado del cheque (si hay) ---
        cheque = None
        if movimiento and movimiento.id_cheque:
            cheque = Cheque.query.get(movimiento.id_cheque)
            if cheque:
                if cheque.estado in ('cobrado', 'depositado', 'rechazado',
                                     'vencido', 'reemplazado'):
                    raise ValueError(
                        f'No se puede anular la venta: el cheque '
                        f'#{cheque.numero} está en estado '
                        f'"{cheque.estado}". Solo se puede anular si el '
                        f'cheque sigue en cartera o entregado.'
                    )

        # --- Revertir stock ---
        for detalle in venta.detalles:
            producto = detalle.producto
            if producto:
                producto.stock_actual += detalle.cantidad

        # --- Anular el movimiento de caja ---
        mov_original = None
        mov_espejo = None
        if movimiento:
            if movimiento.anulado:
                raise ValueError(
                    'El movimiento de caja asociado ya fue anulado. '
                    'Verificá el estado antes de continuar.'
                )
            mov_original, mov_espejo = CajaService.anular_movimiento(
                movimiento.id,
                motivo=motivo or f'Anulación de venta #{venta.id}',
                forzar=True,   # permite anular aunque tenga cheque
                commit=False,
            )

        # --- Rechazar el cheque (devolverlo al cliente) ---
        if cheque:
            cheque.estado = 'rechazado'
            nota = f' [Devuelto al cliente por anulación venta #{venta.id}]'
            cheque.observaciones = (
                (cheque.observaciones or '') + nota
            )[:200]

        # --- Contra-asiento del CMV ---
        costo_total = sum(
            ((d.costo_unitario or Decimal('0')) * d.cantidad
             for d in venta.detalles),
            Decimal('0'),
        )
        if costo_total > 0:
            VentaService._contra_asiento_cmv(venta, costo_total)

        # --- Marcar la venta como anulada ---
        venta.estado = 'anulada'
        venta.fecha_anulacion = datetime.utcnow()
        venta.motivo_anulacion = (motivo or 'Sin motivo especificado')[:200]

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return venta, mov_original, mov_espejo

    @staticmethod
    def _contra_asiento_cmv(venta, costo_total):
        """
        Contra-asiento del CMV al anular una venta:
            Debe  1.1.8 Mercadería de reventa
            Haber 5.10 Costo de Mercadería Vendida
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_cmv = CuentaContableResolver.resolver_o_fallar(
            venta.id_empresa, 'cmv'
        )
        cuenta_mercaderia = CuentaContableResolver.resolver_o_fallar(
            venta.id_empresa, 'compra_mercaderia'
        )

        asiento = AsientoContable(
            fecha=datetime.utcnow(),
            descripcion=f'Anulación CMV - Venta #{venta.id}',
            id_movimiento=None,
            id_empresa=venta.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_mercaderia.id,
            debe=costo_total, haber=0,
        ))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_cmv.id,
            debe=0, haber=costo_total,
        ))
        return asiento