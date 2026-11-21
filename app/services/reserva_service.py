# app/services/reserva_service.py

from datetime import datetime
from decimal import Decimal
from app import db
from app.models import Instalacion, Reserva, PagoAlquiler


class ReservaService:

    @staticmethod
    def verificar_disponibilidad(instalacion_id, fecha_inicio, fecha_fin, reserva_id=None):
        """Verifica si una instalación está disponible en el rango de fechas."""
        query = Reserva.query.filter(
            Reserva.id_instalacion == instalacion_id,
            Reserva.estado != 'cancelada',
            Reserva.fecha_inicio < fecha_fin,
            Reserva.fecha_fin > fecha_inicio,
        )
        if reserva_id:
            query = query.filter(Reserva.id != reserva_id)
        return query.count() == 0

    @staticmethod
    def calcular_monto_total(instalacion_id, fecha_inicio, fecha_fin):
        """
        Calcula el monto total según el tipo de cobro de la instalación.

        - por_hora: precio × horas (redondeado a 0.5)
        - por_uso:  precio fijo por reserva (sin importar duración)
        """
        from decimal import Decimal

        instalacion = Instalacion.query.get(instalacion_id)
        if not instalacion:
            return Decimal('0.00')

        if instalacion.tipo_cobro == 'por_uso':
            return instalacion.precio_por_uso or Decimal('0.00')

        # --- por_hora ---
        delta = fecha_fin - fecha_inicio
        horas = delta.total_seconds() / 3600
        if horas <= 0:
            return Decimal('0.00')

        # Redondeo a 0.5
        horas_redondeadas = (horas * 2) // 1 / 2
        if horas > horas_redondeadas:
            horas_redondeadas += 0.5

        horas_decimal = Decimal(str(horas_redondeadas))
        precio = instalacion.precio_por_hora or Decimal('0.00')
        return precio * horas_decimal

    @staticmethod
    def registrar_pago(reserva_id, monto, metodo_pago, fecha_pago=None,
                       comprobante=None, observaciones=None, id_cuenta=None,
                       id_cheque=None):
        """
        Registra un pago de alquiler.

        - Crea el PagoAlquiler.
        - Si se especifica id_cuenta, crea el Movimiento en caja con
          contrapartida 4.2 Alquileres y genera el asiento.
        - Actualiza el estado de la reserva a 'confirmada' si estaba pendiente.

        Todo en una sola transacción: si algo falla, se revierte todo.
        """
        from app.models import CuentaBancaria, Cheque
        from app.services.caja_service import CajaService
        from app.services.contabilidad_service import ContabilidadService
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        reserva = Reserva.query.get(reserva_id)
        if not reserva:
            raise ValueError('Reserva no encontrada')

        # --- Validación anti-cobro-doble ---
        if reserva.esta_pagada:
            raise ValueError(
                f'La reserva ya está totalmente pagada '
                f'(${reserva.total_pagado}). No se puede registrar otro pago.'
            )
        if monto is None or monto <= 0:
            raise ValueError('El monto debe ser mayor a 0.')
        if monto > reserva.saldo_pendiente:
            raise ValueError(
                f'El monto (${monto}) supera el saldo pendiente de la '
                f'reserva (${reserva.saldo_pendiente}).'
            )

        if not fecha_pago:
            fecha_pago = datetime.utcnow()

        empresa_id = reserva.instalacion.id_empresa

        # 1. Contrapartida 4.2 Alquileres
        cuenta_contable = CuentaContableResolver.resolver_o_fallar(
            empresa_id, 'alquiler'
        )

        # 2. Crear PagoAlquiler
        pago = PagoAlquiler(
            id_reserva=reserva_id,
            id_cuenta=id_cuenta,
            id_cuenta_contable=cuenta_contable.id,
            monto = Decimal(str(monto)),
            fecha_pago=fecha_pago,
            metodo_pago=metodo_pago,
            comprobante=comprobante,
            observaciones=observaciones,
        )
        db.session.add(pago)
        db.session.flush()

        # 3. Movimiento en caja (opcional)
        if id_cuenta:
            cuenta_bancaria = CuentaBancaria.query.get(id_cuenta)
            if not cuenta_bancaria or cuenta_bancaria.id_empresa != empresa_id:
                raise ValueError('Cuenta bancaria inválida para esta reserva.')

            movimiento_data = {
                'id_cuenta': id_cuenta,
                'id_cuenta_contable': cuenta_contable.id,
                'fecha': fecha_pago,
                'tipo': 'ingreso',
                'monto': Decimal(str(monto)),
                'descripcion': (
                    f'Alquiler {reserva.instalacion.nombre} - '
                    f'{reserva.socio.apellido} {reserva.socio.nombre}' if reserva.socio else f'Alquiler {reserva.instalacion.nombre} - No Socio'
                ),
                'metodo_pago': metodo_pago,
                'comprobante': comprobante or f'Reserva #{reserva.id}',
                'id_empresa': empresa_id,
            }
            if id_cheque and str(id_cheque).isdigit() and int(id_cheque) > 0:
                cheque = Cheque.query.get(int(id_cheque))
                if cheque:
                    movimiento_data['id_cheque'] = int(id_cheque)
                    movimiento_data['comprobante'] = (
                        f'Reserva #{reserva.id} - Cheque #{cheque.numero}'
                    )

            movimiento = CajaService.registrar_movimiento(movimiento_data, commit=False)
            if movimiento:
                ContabilidadService.generar_asiento(movimiento)

        # 4. Actualizar estado de la reserva
        if reserva.estado == 'pendiente':
            reserva.estado = 'confirmada'

        return pago

    @staticmethod
    def get_reservas_activas():
        return Reserva.query.filter(
            Reserva.estado.in_(['pendiente', 'confirmada'])
        ).order_by(Reserva.fecha_inicio).all()

    @staticmethod
    def registrar_devolucion(reserva_id, monto, id_cuenta, motivo=None):
        """
        Registra una devolución de fondos al socio por cancelación.
        
        - Crea PagoAlquiler con tipo='devolucion'.
        - Crea Movimiento de EGRESO con contrapartida 4.2.
        - Genera asiento: Debe 4.2 Alquileres / Haber Caja/Banco.
        - NO cambia el estado de la reserva (eso lo hace el caller).
        
        No commitea.
        """
        from decimal import Decimal
        from app.models import CuentaBancaria, PagoAlquiler
        from app.services.caja_service import CajaService
        from app.services.contabilidad_service import ContabilidadService
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        monto = Decimal(str(monto))
        if monto <= 0:
            raise ValueError('El monto a devolver debe ser mayor a 0.')

        reserva = Reserva.query.get(reserva_id)
        if not reserva:
            raise ValueError('Reserva no encontrada.')

        if monto > reserva.total_pagado:
            raise ValueError(
                f'No se puede devolver más de lo cobrado '
                f'(${reserva.total_pagado}).'
            )

        empresa_id = reserva.instalacion.id_empresa

        cuenta = CuentaBancaria.query.get(id_cuenta)
        if not cuenta or cuenta.id_empresa != empresa_id:
            raise ValueError('Cuenta bancaria inválida.')
        if not cuenta.activo:
            raise ValueError(f'La cuenta "{cuenta.nombre}" está inactiva.')
        if cuenta.tipo not in ('efectivo', 'banco'):
            raise ValueError(
                'La devolución debe salir de una cuenta de caja o banco, '
                'no de la Cartera de Cheques.'
            )

        cuenta_contable = CuentaContableResolver.resolver_o_fallar(
            empresa_id, 'alquiler'
        )

        # 1. PagoAlquiler tipo devolución
        devolucion = PagoAlquiler(
            id_reserva=reserva_id,
            id_cuenta=id_cuenta,
            id_cuenta_contable=cuenta_contable.id,
            monto=monto,
            fecha_pago=datetime.utcnow(),
            metodo_pago='devolucion',
            comprobante=None,
            observaciones=motivo,
            tipo='devolucion',
        )
        db.session.add(devolucion)
        db.session.flush()

        # 2. Movimiento de egreso
        movimiento = CajaService.registrar_movimiento({
            'id_cuenta': id_cuenta,
            'id_cuenta_contable': cuenta_contable.id,
            'fecha': datetime.utcnow(),
            'tipo': 'egreso',
            'monto': monto,
            'descripcion': (
                f'Devolución reserva #{reserva_id} - '
                f'{reserva.socio.apellido} {reserva.socio.nombre}' if reserva.socio else f'Devolución reserva #{reserva_id} - No Socio'
            ),
            'metodo_pago': 'devolucion',
            'comprobante': None,
            'id_empresa': empresa_id,
        }, commit=False)

        # 3. Asiento
        if movimiento:
            ContabilidadService.generar_asiento(movimiento)

        return devolucion, movimiento