# app/services/contabilidad_service.py
import logging
from app import db
from datetime import datetime
from decimal import Decimal
from app.models import AsientoContable, LineaAsiento, PlanCuenta

logger = logging.getLogger(__name__)


class ContabilidadService:

    @staticmethod
    def _resolver_cuenta_activo(movimiento):
        """Cuenta de Activo asociada a la cuenta de caja/banco."""
        if movimiento.cuenta and movimiento.cuenta.id_cuenta_contable:
            return PlanCuenta.query.get(movimiento.cuenta.id_cuenta_contable)
        return None

    @staticmethod
    def generar_asiento(movimiento):
        """
        Genera asiento:
            ingreso  -> Debe activo / Haber contrapartida
            egreso   -> Debe contrapartida / Haber activo

        No commitea. Idempotente.
        """
        existente = AsientoContable.query.filter_by(
            id_movimiento=movimiento.id
        ).first()
        if existente:
            return existente

        cuenta_activo = ContabilidadService._resolver_cuenta_activo(movimiento)
        if not cuenta_activo:
            logger.warning(
                'Mov %s: cuenta de caja/banco sin PlanCuenta de Activo asociada.',
                movimiento.id,
            )
            return None

        cuenta_contrapartida = movimiento.cuenta_contable
        if not cuenta_contrapartida:
            logger.warning(
                'Mov %s: no tiene cuenta contable de contrapartida.', movimiento.id
            )
            return None

        if cuenta_activo.id == cuenta_contrapartida.id:
            raise ValueError(
                f'La contrapartida "{cuenta_contrapartida.codigo} '
                f'{cuenta_contrapartida.nombre}" coincide con la cuenta de activo '
                f'del banco/caja. Un asiento "Caja contra Caja" no tiene sentido. '
                f'Elegí otra contrapartida.'
            )

        asiento = AsientoContable(
            fecha=movimiento.fecha,
            descripcion=movimiento.descripcion,
            id_movimiento=movimiento.id,
            id_empresa=movimiento.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        if movimiento.tipo == 'ingreso':
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_activo.id,
                debe=movimiento.monto, haber=0))
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
                debe=0, haber=movimiento.monto))
        else:
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
                debe=movimiento.monto, haber=0))
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_activo.id,
                debe=0, haber=movimiento.monto))

        logger.info(
            'Asiento %s (mov %s): %s / %s por $%s',
            asiento.id, movimiento.id,
            cuenta_activo.codigo, cuenta_contrapartida.codigo, movimiento.monto,
        )
        return asiento

    @staticmethod
    def generar_asiento_pago_proveedor(pago, compra):
        """
        Asiento de un pago a cuenta de proveedor (deuda pendiente):

            Debe  2.1.1 Proveedores
            Haber <cuenta según método>

        - efectivo / transferencia / debito / credito -> cuenta de pago
        - cheque_propio  -> 2.1.6 Cheques a Pagar
        - cheque_tercero -> 1.1.6 Cheques a Cobrar
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_proveedores = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'proveedores'
        )

        if pago.metodo in ('efectivo', 'transferencia', 'debito', 'credito'):
            if not pago.cuenta or not pago.cuenta.id_cuenta_contable:
                logger.warning(
                    'Pago %s: cuenta sin PlanCuenta asociada.', pago.id
                )
                return None
            id_cuenta_haber = pago.cuenta.id_cuenta_contable
        elif pago.metodo == 'cheque_propio':
            id_cuenta_haber = CuentaContableResolver.resolver_o_fallar(
                compra.id_empresa, 'cheques_a_pagar'
            ).id
        elif pago.metodo == 'cheque_tercero':
            id_cuenta_haber = CuentaContableResolver.resolver_o_fallar(
                compra.id_empresa, 'cheques_a_cobrar'
            ).id
        else:
            logger.warning('Método desconocido: %s', pago.metodo)
            return None

        asiento = AsientoContable(
            fecha=pago.fecha_pago or compra.fecha,
            descripcion=(
                f'Pago de deuda - Compra #{compra.id}'
                + (f' - {compra.proveedor_nombre}' if compra.proveedor_nombre else '')
            ),
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_proveedores.id,
            debe=pago.monto, haber=0))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=id_cuenta_haber,
            debe=0, haber=pago.monto))

        logger.info(
            'Asiento %s (pago proveedor, compra %s): %s / %s por $%s',
            asiento.id, compra.id,
            cuenta_proveedores.codigo, id_cuenta_haber, pago.monto,
        )
        return asiento

    @staticmethod
    def generar_asiento_compra_con_cheque(compra, cheque):
        """
        Asiento de una compra pagada con cheque emitido:

            Debe  1.1.8 Mercadería de reventa   (contrapartida de la compra)
            Haber 2.1.6 Cheques a Pagar

        No se crea Movimiento en caja: la plata aún no salió.
        El movimiento se crea recién cuando el cheque se paga.
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_contrapartida = PlanCuenta.query.get(compra.id_cuenta_contable)
        if not cuenta_contrapartida:
            logger.warning(
                'Compra %s: no tiene cuenta contable de contrapartida.',
                compra.id,
            )
            return None

        cuenta_cheques_a_pagar = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_pagar'
        )

        asiento = AsientoContable(
            fecha=compra.fecha,
            descripcion=f"Compra #{compra.id} con cheque #{cheque.numero}",
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
            debe=compra.total, haber=0))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_cheques_a_pagar.id,
            debe=0, haber=compra.total))

        logger.info(
            'Asiento %s (compra %s con cheque): %s / %s por $%s',
            asiento.id, compra.id,
            cuenta_contrapartida.codigo, cuenta_cheques_a_pagar.codigo,
            compra.total,
        )
        return asiento 

    @staticmethod
    def generar_asiento_compra_con_cheque_tercero(compra, cheque):
        """
        Asiento de una compra pagada con un cheque de tercero (que ya
        estaba en nuestra cartera como recibido):

            Debe  1.1.8 Mercadería de reventa     (contrapartida de la compra)
            Haber 1.1.6 Cheques a Cobrar          (baja el activo de cartera)
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_contrapartida = PlanCuenta.query.get(compra.id_cuenta_contable)
        if not cuenta_contrapartida:
            logger.warning(
                'Compra %s: no tiene cuenta contable de contrapartida.',
                compra.id,
            )
            return None

        cuenta_cheques = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_cobrar'
        )

        asiento = AsientoContable(
            fecha=compra.fecha,
            descripcion=(
                f"Compra #{compra.id} con cheque de tercero #{cheque.numero}"
            ),
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
            debe=compra.total, haber=0))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_cheques.id,
            debe=0, haber=compra.total))

        logger.info(
            'Asiento %s (compra %s, cheque tercero): %s / %s por $%s',
            asiento.id, compra.id,
            cuenta_contrapartida.codigo, cuenta_cheques.codigo,
            compra.total,
        )
        return asiento

    @staticmethod
    def generar_asiento_compra_mixta(compra, pagos, deuda):
        """
        Asiento único de una compra con pagos mixtos.

        Debe:  1.1.8 Mercadería de reventa (total)
        Haber: una línea por cada pago, según método:
            - efectivo/transferencia/debito/credito -> cuenta bancaria
            - cheque_propio  -> 2.1.6 Cheques a Pagar
            - cheque_tercero -> 1.1.6 Cheques a Cobrar
        Haber: 2.1.1 Proveedores por el saldo pendiente (si deuda > 0)
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_mercaderia = PlanCuenta.query.get(compra.id_cuenta_contable)
        if not cuenta_mercaderia:
            logger.warning(
                'Compra %s sin cuenta contable de mercadería.', compra.id
            )
            return None

        cuenta_cheques_pagar = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_pagar'
        )
        cuenta_cheques_cobrar = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_cobrar'
        )
        cuenta_proveedores = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'proveedores'
        )

        asiento = AsientoContable(
            fecha=compra.fecha,
            descripcion=f'Compra #{compra.id}'
                        + (f' - {compra.proveedor}' if compra.proveedor else ''),
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        # Debe: mercadería por el total
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_mercaderia.id,
            debe=compra.total, haber=0))

        # Haber: una línea por cada pago
        for pago in pagos:
            if pago.metodo in ('efectivo', 'transferencia', 'debito', 'credito'):
                if not pago.cuenta or not pago.cuenta.id_cuenta_contable:
                    logger.warning(
                        'Compra %s: pago %s sin cuenta contable.',
                        compra.id, pago.id,
                    )
                    continue
                id_cuenta_linea = pago.cuenta.id_cuenta_contable
            elif pago.metodo == 'cheque_propio':
                id_cuenta_linea = cuenta_cheques_pagar.id
            elif pago.metodo == 'cheque_tercero':
                id_cuenta_linea = cuenta_cheques_cobrar.id
            else:
                logger.warning('Método desconocido: %s', pago.metodo)
                continue

            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=id_cuenta_linea,
                debe=0, haber=pago.monto))

        # Haber: deuda con proveedores
        if deuda and deuda > 0:
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_proveedores.id,
                debe=0, haber=deuda))

        logger.info('Asiento %s generado para compra %s (mixto)', asiento.id, compra.id)
        return asiento

    @staticmethod
    def generar_asiento_operacion(movimientos, tipo, descripcion, fecha, id_empresa):
        """
        Asiento único para una operación con múltiples movimientos.

        - ingreso: Debe activos (uno por movimiento) / Haber contrapartida (total)
        - egreso:  Debe contrapartida (total) / Haber activos (uno por movimiento)

        La contrapartida es la del primer movimiento (todos la comparten).
        Idempotente por `id_movimiento` del primer movimiento.
        """
        from decimal import Decimal

        if not movimientos:
            return None

        primero = movimientos[0]
        existente = AsientoContable.query.filter_by(
            id_movimiento=primero.id
        ).first()
        if existente:
            return existente

        cuenta_contrapartida = primero.cuenta_contable
        if not cuenta_contrapartida:
            logger.warning(
                'Operación %s: sin cuenta contrapartida.',
                primero.id_operacion,
            )
            return None

        # Validar que ninguna cuenta de activo coincida con la contrapartida
        for m in movimientos:
            activo = m.cuenta.cuenta_contable if m.cuenta else None
            if activo and activo.id == cuenta_contrapartida.id:
                raise ValueError(
                    f'La contrapartida "{cuenta_contrapartida.codigo} '
                    f'{cuenta_contrapartida.nombre}" coincide con la cuenta de '
                    f'activo del banco/caja de uno de los medios de pago. '
                    f'Elegí otra contrapartida.'
                )

    
        asiento = AsientoContable(
            fecha=fecha,
            descripcion=descripcion,
            id_movimiento=primero.id,
            id_empresa=id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        if tipo == 'ingreso':
            total = Decimal('0')
            for m in movimientos:
                activo = m.cuenta.cuenta_contable if m.cuenta else None
                if not activo:
                    logger.warning('Mov %s sin PlanCuenta de activo.', m.id)
                    continue
                db.session.add(LineaAsiento(
                    id_asiento=asiento.id, id_cuenta=activo.id,
                    debe=m.monto, haber=0))
                total += m.monto
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
                debe=0, haber=total))
        else:
            total = sum(m.monto for m in movimientos)
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_contrapartida.id,
                debe=total, haber=0))
            for m in movimientos:
                activo = m.cuenta.cuenta_contable if m.cuenta else None
                if not activo:
                    logger.warning('Mov %s sin PlanCuenta de activo.', m.id)
                    continue
                db.session.add(LineaAsiento(
                    id_asiento=asiento.id, id_cuenta=activo.id,
                    debe=0, haber=m.monto))

        logger.info(
            'Asiento %s (operación %s): %s splits, total $%s',
            asiento.id, primero.id_operacion, len(movimientos),
            sum(m.monto for m in movimientos),
        )
        return asiento

    @staticmethod
    def generar_asiento_cmv(venta, costo_total):
        """
        Asiento del Costo de Mercadería Vendida (CMV):

            Debe  5.10 Costo de Mercadería Vendida
            Haber 1.1.8 Mercadería de reventa

        Se genera al confirmar una venta. Sin `id_movimiento` asociado
        (no hay movimiento de caja detrás, es una baja de inventario).
        """
        from decimal import Decimal
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        if not costo_total or costo_total <= Decimal('0'):
            return None

        cuenta_cmv = CuentaContableResolver.resolver_o_fallar(
            venta.id_empresa, 'cmv'
        )
        cuenta_mercaderia = CuentaContableResolver.resolver_o_fallar(
            venta.id_empresa, 'compra_mercaderia'
        )

        asiento = AsientoContable(
            fecha=venta.fecha,
            descripcion=f'Costo de mercadería vendida - Venta #{venta.id}',
            id_movimiento=None,
            id_empresa=venta.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_cmv.id,
            debe=costo_total, haber=0))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_mercaderia.id,
            debe=0, haber=costo_total))

        logger.info(
            'Asiento %s (CMV venta %s): %s / %s por $%s',
            asiento.id, venta.id,
            cuenta_cmv.codigo, cuenta_mercaderia.codigo, costo_total,
        )
        return asiento

    @staticmethod
    def generar_contra_asiento_compra(compra, pagos):
        """
        Contra-asiento del asiento de compra generado por
        `generar_asiento_compra_mixta`.

        Original: Debe 1.1.8 (total) / Haber pagos + deuda (2.1.1)
        Contra:   Debe pagos + deuda (2.1.1) / Haber 1.1.8 (total)
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cuenta_mercaderia = PlanCuenta.query.get(compra.id_cuenta_contable)
        if not cuenta_mercaderia:
            logger.warning(
                'Compra %s sin cuenta de mercadería.', compra.id
            )
            return None

        cuenta_cheques_pagar = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_pagar'
        )
        cuenta_cheques_cobrar = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'cheques_a_cobrar'
        )
        cuenta_proveedores = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'proveedores'
        )

        asiento = AsientoContable(
            fecha=datetime.utcnow(),
            descripcion=f'Anulación Compra #{compra.id}',
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        # Debe: una línea por cada pago (invierte el Haber original)
        total_pagado = Decimal('0')
        for pago in pagos:
            if pago.metodo in ('efectivo', 'transferencia', 'debito', 'credito'):
                if not pago.cuenta or not pago.cuenta.id_cuenta_contable:
                    logger.warning(
                        'Compra %s: pago %s sin cuenta contable.',
                        compra.id, pago.id,
                    )
                    continue
                id_cuenta_linea = pago.cuenta.id_cuenta_contable
            elif pago.metodo == 'cheque_propio':
                id_cuenta_linea = cuenta_cheques_pagar.id
            elif pago.metodo == 'cheque_tercero':
                id_cuenta_linea = cuenta_cheques_cobrar.id
            else:
                continue

            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=id_cuenta_linea,
                debe=pago.monto, haber=0,
            ))
            total_pagado += pago.monto

        # Debe: deuda original con proveedores (si la había)
        deuda_original = compra.total - total_pagado
        if deuda_original > 0:
            db.session.add(LineaAsiento(
                id_asiento=asiento.id, id_cuenta=cuenta_proveedores.id,
                debe=deuda_original, haber=0,
            ))

        # Haber: mercadería por el total
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_mercaderia.id,
            debe=0, haber=compra.total,
        ))

        logger.info(
            'Contra-asiento %s (compra %s): %s líneas, total $%s',
            asiento.id, compra.id, len(pagos) + 2, compra.total,
        )
        return asiento