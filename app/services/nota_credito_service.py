# app/services/nota_credito_service.py
from datetime import datetime
from decimal import Decimal

from app import db
from app.models import (
    Compra, DetalleCompra, Producto, NotaCreditoCompra,
    DetalleNotaCredito, AsientoContable, LineaAsiento,
)


class NotaCreditoService:

    @staticmethod
    def calcular_devolucion_maxima(detalle_compra):
        """
        Devuelve la cantidad máxima devolvible de un detalle:
        mínimo entre (cantidad original - ya devuelto) y stock actual.
        """
        ya_devuelto = 0
        for nota in detalle_compra.compra.notas_credito:
            if nota.anulada:
                continue
            for dn in nota.detalles:
                if dn.id_producto == detalle_compra.id_producto:
                    ya_devuelto += dn.cantidad

        restante = detalle_compra.cantidad - ya_devuelto
        stock = detalle_compra.producto.stock_actual or 0
        return max(0, min(restante, stock))

    @staticmethod
    def crear_nota(id_compra, items, motivo, commit=True):
        """
        Crea una nota de crédito.

        `items` es una lista de dicts:
            [{'id_producto': 1, 'cantidad': 2, 'precio_unitario': '150.00'}, ...]

        - Revierte stock de cada producto devuelto.
        - Genera asiento:
            Debe  2.1.1 Proveedores (total)
            Haber 1.1.8 Mercadería (total)
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        compra = Compra.query.get(id_compra)
        if not compra:
            raise ValueError('Compra no encontrada.')
        if compra.estado == 'anulada':
            raise ValueError(
                'No se puede crear una nota de crédito sobre una '
                'compra anulada.'
            )
        if not motivo or not motivo.strip():
            raise ValueError('El motivo es obligatorio.')
        if not items:
            raise ValueError('Agregá al menos un producto a devolver.')

        # --- Validar items y armar detalles ---
        detalles = []
        total = Decimal('0')
        for item in items:
            id_producto = item.get('id_producto')
            cantidad = int(item.get('cantidad') or 0)
            precio = Decimal(str(item.get('precio_unitario') or 0))

            if cantidad <= 0:
                raise ValueError(
                    'Todas las cantidades deben ser mayores a 0.'
                )
            if precio <= 0:
                raise ValueError(
                    'Todos los precios deben ser mayores a 0.'
                )

            # Verificar que el producto esté en la compra
            detalle_compra = next(
                (d for d in compra.detalles
                 if d.id_producto == id_producto),
                None,
            )
            if not detalle_compra:
                raise ValueError(
                    f'El producto no pertenece a esta compra.'
                )

            maximo = NotaCreditoService.calcular_devolucion_maxima(
                detalle_compra
            )
            if cantidad > maximo:
                raise ValueError(
                    f'Solo podés devolver {maximo} unidad(es) de '
                    f'"{detalle_compra.producto.nombre}". '
                    f'(Pediste devolver {cantidad}.)'
                )

            subtotal = precio * cantidad
            total += subtotal
            detalles.append({
                'producto': detalle_compra.producto,
                'cantidad': cantidad,
                'precio_unitario': precio,
                'subtotal': subtotal,
            })

        # --- Crear la nota ---
        nota = NotaCreditoCompra(
            id_compra=compra.id,
            fecha=datetime.utcnow(),
            monto=total,
            motivo=motivo.strip()[:300],
            id_empresa=compra.id_empresa,
        )
        db.session.add(nota)
        db.session.flush()

        # --- Detalles + revertir stock ---
        for d in detalles:
            db.session.add(DetalleNotaCredito(
                id_nota=nota.id,
                id_producto=d['producto'].id,
                cantidad=d['cantidad'],
                precio_unitario=d['precio_unitario'],
                subtotal=d['subtotal'],
            ))
            d['producto'].stock_actual -= d['cantidad']

        # --- Asiento ---
        cuenta_proveedores = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'proveedores'
        )
        cuenta_mercaderia = CuentaContableResolver.resolver_o_fallar(
            compra.id_empresa, 'compra_mercaderia'
        )

        asiento = AsientoContable(
            fecha=nota.fecha,
            descripcion=f'Nota de crédito #{nota.id} - Compra #{compra.id}',
            id_movimiento=None,
            id_empresa=compra.id_empresa,
        )
        db.session.add(asiento)
        db.session.flush()

        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_proveedores.id,
            debe=total, haber=0,
        ))
        db.session.add(LineaAsiento(
            id_asiento=asiento.id, id_cuenta=cuenta_mercaderia.id,
            debe=0, haber=total,
        ))

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return nota

    @staticmethod
    def anular_nota(nota_id, motivo=None, commit=True):
        """Anula una nota de crédito (revierte stock + contra-asiento)."""
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        nota = NotaCreditoCompra.query.get(nota_id)
        if not nota:
            raise ValueError('Nota de crédito no encontrada.')
        if nota.anulada:
            raise ValueError('Esta nota de crédito ya está anulada.')

        # --- Revertir stock ---
        for d in nota.detalles:
            if d.producto:
                d.producto.stock_actual += d.cantidad

        # --- Contra-asiento ---
        cuenta_proveedores = CuentaContableResolver.resolver_o_fallar(
            nota.id_empresa, 'proveedores'
        )
        cuenta_mercaderia = CuentaContableResolver.resolver_o_fallar(
            nota.id_empresa, 'compra_mercaderia'
        )

        contra = AsientoContable(
            fecha=datetime.utcnow(),
            descripcion=(
                f'Anulación Nota de crédito #{nota.id} - '
                f'Compra #{nota.id_compra}'
            ),
            id_movimiento=None,
            id_empresa=nota.id_empresa,
        )
        db.session.add(contra)
        db.session.flush()

        # Invertido respecto al original
        db.session.add(LineaAsiento(
            id_asiento=contra.id, id_cuenta=cuenta_mercaderia.id,
            debe=nota.monto, haber=0,
        ))
        db.session.add(LineaAsiento(
            id_asiento=contra.id, id_cuenta=cuenta_proveedores.id,
            debe=0, haber=nota.monto,
        ))

        # --- Marcar nota como anulada ---
        nota.anulada = True
        nota.fecha_anulacion = datetime.utcnow()
        nota.motivo_anulacion = (motivo or 'Sin motivo especificado')[:200]

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return nota