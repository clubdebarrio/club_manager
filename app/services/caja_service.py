# app/services/caja_service.py

from datetime import datetime
from decimal import Decimal
from sqlalchemy import func, case, or_, and_
from app import db
from app.models import CuentaBancaria, Movimiento, Cheque
from app.services.contabilidad_service import ContabilidadService

class CajaService:

    @staticmethod
    def registrar_movimiento(data, commit=True):
        """
        Registra un movimiento en caja y (opcionalmente) su asiento contable.

        Flags de control:
        - `_forzar_cuenta`: respeta la id_cuenta recibida (cheques).
        - `_generar_asiento`: si es False, no genera asiento.
        - `_skip_mirror`: si es True, no crea movimiento espejo en caso de
            transferencia interna (útil cuando el caller ya lo hace a mano).

        Reglas:
        - Ingreso con método 'cheque' => va a Cartera de Cheques.
        - Caja/Cartera no admiten saldo negativo.
        - Si la contrapartida es la PlanCuenta de otra CuentaBancaria,
            se crea un movimiento espejo en esa cuenta (transferencia interna).
        """
        forzar_cuenta = data.pop('_forzar_cuenta', False)
        generar_asiento = data.pop('_generar_asiento', True)
        skip_mirror = data.pop('_skip_mirror', False)

        cuenta = CuentaBancaria.query.with_for_update().get(data.get('id_cuenta'))
        if not cuenta:
            raise ValueError("Cuenta no encontrada")

        # Cheque recibido => Cartera
        if (data.get('tipo') == 'ingreso'
                and data.get('metodo_pago') == 'cheque'
                and not forzar_cuenta):
            cartera = CuentaBancaria.query.filter_by(
                id_empresa=cuenta.id_empresa, tipo='cartera', activo=True
            ).first()
            if cartera:
                data['id_cuenta'] = cartera.id
                cuenta = cartera

        # Saldo no negativo en caja/cartera
        if data.get('tipo') == 'egreso' and cuenta.tipo in ('efectivo', 'cartera'):
            saldo_disponible = cuenta.saldo_actual or Decimal('0')
            if saldo_disponible < Decimal(str(data.get('monto') or 0)):
                raise ValueError(
                    f'Saldo insuficiente en "{cuenta.nombre}". '
                    f'Disponible: ${saldo_disponible:.2f}. '
                    f'Las cajas de efectivo y la cartera de cheques '
                    f'no pueden quedar en negativo.'
                )

        if 'conciliado' not in data:
            metodo = data.get('metodo_pago')
            data['conciliado'] = metodo in ['efectivo', 'deposito_efectivo',
                                            'debito', 'credito']

        movimiento = Movimiento(**data)
        db.session.add(movimiento)
        db.session.flush()

        if movimiento.tipo == 'ingreso':
            cuenta.saldo_actual += movimiento.monto
        else:
            cuenta.saldo_actual -= movimiento.monto

        # --- Transferencia interna ---
        if not skip_mirror and movimiento.id_cuenta_contable:
            cuenta_destino = CuentaBancaria.query.filter(
                CuentaBancaria.id_cuenta_contable == movimiento.id_cuenta_contable,
                CuentaBancaria.id_empresa == cuenta.id_empresa,
                CuentaBancaria.id != cuenta.id,
            ).first()
            if cuenta_destino:
                tipo_espejo = 'ingreso' if movimiento.tipo == 'egreso' else 'egreso'
                espejo = Movimiento(
                    id_cuenta=cuenta_destino.id,
                    id_cuenta_contable=None,
                    fecha=movimiento.fecha,
                    tipo=tipo_espejo,
                    monto=movimiento.monto,
                    descripcion=(
                        f'Transferencia interna desde {cuenta.nombre}: '
                        f'{movimiento.descripcion}'
                    ),
                    metodo_pago=movimiento.metodo_pago,
                    comprobante=movimiento.comprobante,
                    id_empresa=cuenta.id_empresa,
                    conciliado=False,
                )
                db.session.add(espejo)
                db.session.flush()
                if espejo.tipo == 'ingreso':
                    cuenta_destino.saldo_actual += espejo.monto
                else:
                    cuenta_destino.saldo_actual -= espejo.monto

        if commit:
            if generar_asiento:
                ContabilidadService.generar_asiento(movimiento)
            db.session.commit()

        return movimiento
    
    @staticmethod
    def registrar_cheque(data, commit=True):
        """Registra un cheque en la cartera."""
        cheque = Cheque(**data)
        db.session.add(cheque)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return cheque
    
    @staticmethod
    def actualizar_estado_cheque(cheque_id, nuevo_estado, fecha_cobro=None,
                                id_cuenta=None, commit=True):
        """
        Actualiza el estado de un cheque.

        Si pasa a 'cobrado' y es de tipo 'recibido':
        - Genera un Movimiento de EGRESO de la Cartera de Cheques por el
            monto. Este movimiento NO genera asiento contable: es un espejo
            para que el saldo de la Cartera refleje que el cheque ya no está
            en mano. Contablemente, el asiento lo produce el siguiente punto.
        - Genera un Movimiento de INGRESO a la cuenta bancaria elegida,
            con contrapartida 1.1.6 (Cheques a Cobrar). Este movimiento SÍ
            genera asiento: Debe Banco / Haber 1.1.6.
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver
        from app.services.contabilidad_service import ContabilidadService

        cheque = Cheque.query.get(cheque_id)
        if not cheque:
            raise ValueError('Cheque no encontrado')

        cheque.estado = nuevo_estado
        if fecha_cobro:
            cheque.fecha_cobro = fecha_cobro

        if nuevo_estado == 'cobrado' and cheque.tipo == 'recibido':
            if not id_cuenta:
                raise ValueError(
                    'Debe especificar la cuenta bancaria donde se cobra el cheque.'
                )
            banco = CuentaBancaria.query.get(id_cuenta)
            if not banco or banco.id_empresa != cheque.id_empresa:
                raise ValueError('Cuenta bancaria inválida.')

            cartera = CuentaBancaria.query.filter_by(
                id_empresa=cheque.id_empresa, tipo='cartera', activo=True
            ).first()
            if not cartera:
                raise ValueError(
                    'No existe la Cartera de Cheques. '
                    'Corré: flask caja:seed-cartera-cheques'
                )

            cuenta_contable = CuentaContableResolver.resolver_o_fallar(
                cheque.id_empresa, 'cheques_a_cobrar'
            )

            ahora = datetime.utcnow()

            # --- 1. Egreso de la Cartera (movimiento espejo, sin asiento) ---
            CajaService.registrar_movimiento({
                'id_cuenta': cartera.id,
                'id_cuenta_contable': None,
                'fecha': ahora,
                'tipo': 'egreso',
                'monto': cheque.monto,
                'descripcion': (
                    f'Cobro de cheque {cheque.numero} - salida de cartera'
                ),
                'metodo_pago': 'cheque',
                'comprobante': cheque.numero,
                'id_cheque': cheque.id,
                'id_empresa': cheque.id_empresa,
                'conciliado': False,
                '_forzar_cuenta': True,
                '_generar_asiento': False,   # <-- no genera asiento
            }, commit=False)

            # --- 2. Ingreso al banco elegido (con asiento contra 1.1.6) ---
            mov_ingreso = CajaService.registrar_movimiento({
                'id_cuenta': banco.id,
                'id_cuenta_contable': cuenta_contable.id,
                'fecha': ahora,
                'tipo': 'ingreso',
                'monto': cheque.monto,
                'descripcion': (
                    f'Cobro de cheque {cheque.numero} - {cheque.banco_emisor}'
                ),
                'metodo_pago': 'cheque',
                'comprobante': cheque.numero,
                'id_cheque': cheque.id,
                'id_empresa': cheque.id_empresa,
                'conciliado': False,
                '_forzar_cuenta': True,
                '_skip_mirror': True
            }, commit=False)

            if mov_ingreso:
                ContabilidadService.generar_asiento(mov_ingreso)

        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return cheque

    @staticmethod
    def pagar_cheque_emitido(cheque_id, id_cuenta, fecha_pago=None, commit=True):
        """
        Paga un cheque emitido: crea el Movimiento egreso desde el banco
        con contrapartida 2.1.6 y su asiento. Marca el cheque como 'pagado'.
        """
        from app.services.cuenta_contable_resolver import CuentaContableResolver
        from app.services.contabilidad_service import ContabilidadService

        cheque = Cheque.query.get(cheque_id)
        if not cheque:
            raise ValueError('Cheque no encontrado')
        if cheque.tipo != 'emitido':
            raise ValueError('Solo se pueden pagar cheques de tipo "emitido"')
        if cheque.estado == 'pagado':
            raise ValueError('El cheque ya está pagado')

        cuenta = CuentaBancaria.query.get(id_cuenta)
        if not cuenta or cuenta.id_empresa != cheque.id_empresa:
            raise ValueError('Cuenta bancaria inválida')
        if cuenta.tipo != 'banco':
            raise ValueError(
                'El pago de un cheque debe salir de una cuenta bancaria.'
            )

        cuenta_contable = CuentaContableResolver.resolver_o_fallar(
            cheque.id_empresa, 'cheques_a_pagar'
        )

        if not fecha_pago:
            fecha_pago = datetime.utcnow()

        movimiento_data = {
            'id_cuenta': cuenta.id,
            'id_cuenta_contable': cuenta_contable.id,
            'fecha': fecha_pago,
            'tipo': 'egreso',
            'monto': cheque.monto,
            'descripcion': (
                f'Pago de cheque {cheque.numero} - {cheque.banco_emisor}'
            ),
            'metodo_pago': 'cheque',
            'comprobante': cheque.numero,
            'id_cheque': cheque.id,
            'id_empresa': cheque.id_empresa,
            'conciliado': False,
            '_forzar_cuenta': True,
        }

        movimiento = CajaService.registrar_movimiento(movimiento_data, commit=False)
        if movimiento:
            ContabilidadService.generar_asiento(movimiento)

        cheque.estado = 'pagado'
        cheque.fecha_cobro = (
            fecha_pago.date() if hasattr(fecha_pago, 'date') else fecha_pago
        )

        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return cheque

    @staticmethod
    def reemplazar_cheque(cheque_viejo_id, datos_nuevo, motivo=None,
                           commit=True):
        """
        Reemplaza un cheque pendiente por otro.

        Escenarios cubiertos:
        - Cheque EMITIDO: se le da al proveedor uno nuevo con la
          obligación intacta en 2.1.6.
        - Cheque RECIBIDO: el tercero nos da uno nuevo. El activo
          en 1.1.6 sigue igual.

        En AMBOS casos NO se genera asiento: es un cambio de instrumento
        sin modificar la obligación/activo subyacente. El asiento original
        (Debe Gasto / Haber 2.1.6, o Debe 1.1.6 / Haber Recurso) sigue
        vigente.

        Retorna (cheque_viejo, cheque_nuevo). No commitea si commit=False.
        """
        from app.models import Cheque

        viejo = Cheque.query.get(cheque_viejo_id)
        if not viejo:
            raise ValueError('Cheque no encontrado.')

        # --- Validación de estado ---
        estados_reemplazables = ('pendiente', 'en_cartera')
        if viejo.estado not in estados_reemplazables:
            raise ValueError(
                f'El cheque #{viejo.numero} no se puede reemplazar '
                f'(estado actual: "{viejo.estado}"). Solo se pueden '
                f'reemplazar cheques en estado "pendiente" o "en cartera".'
            )

        # --- Validaciones de los datos del nuevo cheque ---
        numero = (datos_nuevo.get('numero') or '').strip()
        banco = (datos_nuevo.get('banco_emisor') or '').strip()
        titular = (datos_nuevo.get('titular') or '').strip()
        fecha_emision = datos_nuevo.get('fecha_emision')
        fecha_cobro = datos_nuevo.get('fecha_cobro')

        if not numero:
            raise ValueError('Falta el número del cheque nuevo.')
        if not banco:
            raise ValueError('Falta el banco emisor del cheque nuevo.')
        if not fecha_emision:
            raise ValueError('Falta la fecha de emisión del cheque nuevo.')
        if not fecha_cobro:
            fecha_cobro = fecha_emision

        # Si es emitido, el titular somos nosotros
        if viejo.tipo == 'emitido':
            titular = viejo.empresa.nombre if viejo.empresa else titular
        if not titular:
            raise ValueError('Falta el titular del cheque nuevo.')

        # --- El monto NO puede cambiar (si cambia, no es reemplazo) ---
        monto_nuevo = datos_nuevo.get('monto')
        if monto_nuevo is not None:
            from decimal import Decimal
            monto_dec = Decimal(str(monto_nuevo))
            if monto_dec != viejo.monto:
                raise ValueError(
                    f'El monto del cheque nuevo (${monto_dec}) debe ser '
                    f'igual al del cheque reemplazado (${viejo.monto}). '
                    f'Si necesitás cambiar el monto, cancelá el cheque '
                    f'y registrá una operación nueva.'
                )

        # --- Crear el cheque nuevo ---
        nuevo = Cheque(
            numero=numero,
            banco_emisor=banco,
            titular=titular,
            monto=viejo.monto,
            fecha_emision=fecha_emision,
            fecha_cobro=fecha_cobro,
            tipo=viejo.tipo,
            estado=(
                'en_cartera' if viejo.tipo == 'recibido' else 'pendiente'
            ),
            observaciones=datos_nuevo.get('observaciones'),
            id_empresa=viejo.id_empresa,
        )
        db.session.add(nuevo)
        db.session.flush()

        # --- Marcar el viejo como reemplazado ---
        viejo.estado = 'reemplazado'
        viejo.id_cheque_reemplazo = nuevo.id
        viejo.motivo_reemplazo = (
            motivo or f'Reemplazado por cheque #{nuevo.numero}'
        )[:200]

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return viejo, nuevo

    @staticmethod
    def cancelar_cheque_en_efectivo(cheque_id, id_cuenta, fecha_pago=None,
                                     motivo=None, commit=True):
        """
        Cancela un cheque pagando/cobrando en efectivo o transferencia.

        - Cheque EMITIDO: Movimiento EGRESO del banco, contra 2.1.6.
          Asiento: Debe 2.1.6 / Haber Banco.
          Estado final: 'pagado_en_efectivo'.

        - Cheque RECIBIDO: Movimiento EGRESO de la Cartera (espejo, sin
          asiento) + Movimiento INGRESO al banco, contra 1.1.6.
          Asiento: Debe Banco / Haber 1.1.6.
          Estado final: 'cobrado_en_efectivo'.

        Retorna (cheque, movimiento_principal). No commitea si commit=False.
        """
        from app.models import Cheque, CuentaBancaria
        from app.services.contabilidad_service import ContabilidadService
        from app.services.cuenta_contable_resolver import CuentaContableResolver

        cheque = Cheque.query.get(cheque_id)
        if not cheque:
            raise ValueError('Cheque no encontrado.')

        # --- Validación de estado ---
        estados_cancelables = ('pendiente', 'en_cartera')
        if cheque.estado not in estados_cancelables:
            raise ValueError(
                f'El cheque #{cheque.numero} no se puede cancelar '
                f'(estado actual: "{cheque.estado}"). Solo se pueden '
                f'cancelar cheques en estado "pendiente" o "en cartera".'
            )

        # --- Validación de la cuenta destino ---
        cuenta = CuentaBancaria.query.get(id_cuenta)
        if not cuenta or cuenta.id_empresa != cheque.id_empresa:
            raise ValueError('Cuenta bancaria inválida.')
        if not cuenta.activo:
            raise ValueError(f'La cuenta "{cuenta.nombre}" está inactiva.')
        if not cuenta.id_cuenta_contable:
            raise ValueError(
                f'La cuenta "{cuenta.nombre}" no tiene cuenta contable '
                f'asignada. Asignala antes de usarla.'
            )

        if not fecha_pago:
            fecha_pago = datetime.utcnow()

        empresa_id = cheque.id_empresa
        motivo_txt = motivo or 'Cancelación en efectivo/transferencia'

        if cheque.tipo == 'emitido':
            # ==================================================
            # EMITIDO: pagamos el cheque con plata de una cuenta
            # ==================================================
            cuenta_contable = CuentaContableResolver.resolver_o_fallar(
                empresa_id, 'cheques_a_pagar'
            )

            movimiento = CajaService.registrar_movimiento({
                'id_cuenta': id_cuenta,
                'id_cuenta_contable': cuenta_contable.id,
                'fecha': fecha_pago,
                'tipo': 'egreso',
                'monto': cheque.monto,
                'descripcion': (
                    f'Cancelación cheque #{cheque.numero} en efectivo/'
                    f'transferencia'
                ),
                'metodo_pago': 'efectivo',
                'comprobante': cheque.numero,
                'id_cheque': cheque.id,
                'id_empresa': empresa_id,
                'conciliado': False,
                '_forzar_cuenta': True,
            }, commit=False)

            if movimiento:
                ContabilidadService.generar_asiento(movimiento)

            cheque.estado = 'pagado_en_efectivo'

        else:
            # ==================================================
            # RECIBIDO: cobramos el cheque en efectivo/transferencia
            # ==================================================
            cartera = CuentaBancaria.query.filter_by(
                id_empresa=empresa_id, tipo='cartera', activo=True
            ).first()
            if not cartera:
                raise ValueError(
                    'No existe la Cartera de Cheques. '
                    'Corré: flask caja:seed-cartera-cheques'
                )

            cuenta_contable = CuentaContableResolver.resolver_o_fallar(
                empresa_id, 'cheques_a_cobrar'
            )

            # --- 1. Egreso de la Cartera (espejo, sin asiento) ---
            CajaService.registrar_movimiento({
                'id_cuenta': cartera.id,
                'id_cuenta_contable': None,
                'fecha': fecha_pago,
                'tipo': 'egreso',
                'monto': cheque.monto,
                'descripcion': (
                    f'Cheque #{cheque.numero} cobrado en efectivo - '
                    f'salida de cartera'
                ),
                'metodo_pago': 'cheque',
                'comprobante': cheque.numero,
                'id_cheque': cheque.id,
                'id_empresa': empresa_id,
                'conciliado': False,
                '_forzar_cuenta': True,
                '_generar_asiento': False,
            }, commit=False)

            # --- 2. Ingreso al banco/caja elegido, contra 1.1.6 ---
            movimiento = CajaService.registrar_movimiento({
                'id_cuenta': id_cuenta,
                'id_cuenta_contable': cuenta_contable.id,
                'fecha': fecha_pago,
                'tipo': 'ingreso',
                'monto': cheque.monto,
                'descripcion': (
                    f'Cobro cheque #{cheque.numero} en efectivo/'
                    f'transferencia'
                ),
                'metodo_pago': 'efectivo',
                'comprobante': cheque.numero,
                'id_cheque': cheque.id,
                'id_empresa': empresa_id,
                'conciliado': False,
                '_forzar_cuenta': True,
                '_skip_mirror': True,
            }, commit=False)

            if movimiento:
                ContabilidadService.generar_asiento(movimiento)

            cheque.estado = 'cobrado_en_efectivo'

        # Guardar motivo (si se indicó) o el default
        if motivo:
            cheque.motivo_reemplazo = motivo[:200]

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return cheque, movimiento

    @staticmethod
    def obtener_saldos_por_cuenta(empresa_id):
        """Retorna el saldo actual de todas las cuentas activas."""
        cuentas = CuentaBancaria.query.filter_by(id_empresa=empresa_id, activo=True).all()
        return {c.nombre: c.saldo_actual for c in cuentas}
    
    @staticmethod
    def get_movimientos_por_periodo(empresa_id, fecha_desde=None, fecha_hasta=None, cuenta_id=None):
        """Retorna movimientos filtrados por período y/o cuenta."""
        query = Movimiento.query.filter_by(id_empresa=empresa_id)
        if fecha_desde:
            query = query.filter(Movimiento.fecha >= fecha_desde)
        if fecha_hasta:
            query = query.filter(Movimiento.fecha <= fecha_hasta)
        if cuenta_id:
            query = query.filter(Movimiento.id_cuenta == cuenta_id)
        return query.order_by(Movimiento.fecha.desc()).all()
    
    @staticmethod
    def conciliar_movimientos(ids_movimientos, empresa_id=None, commit=True):
        """Marca una lista de movimientos como conciliados."""
        query = Movimiento.query.filter(Movimiento.id.in_(ids_movimientos))
        if empresa_id is not None:
            query = query.filter(Movimiento.id_empresa == empresa_id)
        movimientos = query.all()
        for m in movimientos:
            m.conciliado = True
            m.fecha_conciliacion = datetime.utcnow()
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        return len(movimientos)

    @staticmethod
    def obtener_movimientos_con_saldo(cuenta_id, limite=20):
        """Retorna los últimos movimientos de una cuenta con saldo acumulado.
        Formato: FECHA, DESCRIPCIÓN, DEBE, HABER, SALDO
        """
        movimientos = Movimiento.query.filter_by(id_cuenta=cuenta_id).order_by(Movimiento.fecha.desc()).limit(limite).all()
        movimientos = movimientos[::-1]  # Orden ascendente para calcular saldo
        
        saldo_inicial = CuentaBancaria.query.get(cuenta_id).saldo_actual - sum(
            m.monto if m.tipo == 'ingreso' else -m.monto for m in movimientos
        )
        saldo = saldo_inicial
        resultados = []
        for m in movimientos:
            if m.tipo == 'ingreso':
                saldo += m.monto
                debe = m.monto
                haber = 0
            else:
                saldo -= m.monto
                debe = 0
                haber = m.monto
            resultados.append({
                'fecha': m.fecha,
                'descripcion': m.descripcion,
                'debe': debe,
                'haber': haber,
                'saldo': saldo,
                'conciliado': m.conciliado,
                'id': m.id
            })
        return resultados

    @staticmethod
    def obtener_movimientos_con_saldo_completo(cuenta_id):
        """
        Retorna todos los movimientos de una cuenta con saldo acumulado.
        Formato: FECHA, DESCRIPCIÓN, DEBE, HABER, SALDO
        """
        movimientos = Movimiento.query.filter_by(id_cuenta=cuenta_id).order_by(Movimiento.fecha.asc()).all()
        saldo = 0
        resultados = []
        for m in movimientos:
            if m.tipo == 'ingreso':
                saldo += m.monto
                debe = m.monto
                haber = 0
            else:
                saldo -= m.monto
                debe = 0
                haber = m.monto
            resultados.append({
                'fecha': m.fecha,
                'descripcion': m.descripcion,
                'debe': debe,
                'haber': haber,
                'saldo': saldo,
                'conciliado': m.conciliado,
                'id': m.id
            })
        return resultados

    # ---------- Extracto de cuenta ----------

    @staticmethod
    def _movimientos_query(cuenta, fecha_desde=None, fecha_hasta=None):
        """
        Query base de movimientos de una cuenta con filtros opcionales.
        En el futuro multiempresa, agregar aquí `Movimiento.id_empresa == ...`
        o filtrar por la empresa de la cuenta.
        """
        q = Movimiento.query.filter(Movimiento.id_cuenta == cuenta.id)
        if fecha_desde:
            q = q.filter(Movimiento.fecha >= fecha_desde)
        if fecha_hasta:
            # fecha_hasta es EXCLUSIVO (se pasa ya con +1 día)
            q = q.filter(Movimiento.fecha < fecha_hasta)
        return q

    @staticmethod
    def _saldo_antes_de(cuenta, movimiento, ver_anulados=False):
        """
        Saldo ANTES del movimiento dado, considerando todo el historial.
        """
        delta = func.coalesce(
            func.sum(
                case(
                    (Movimiento.tipo == 'ingreso', Movimiento.monto),
                    else_=-Movimiento.monto
                )
            ),
            0
        )

        q = db.session.query(delta).filter(
            Movimiento.id_cuenta == cuenta.id,
            or_(
                Movimiento.fecha < movimiento.fecha,
                and_(Movimiento.fecha == movimiento.fecha,
                     Movimiento.id < movimiento.id)
            )
        )

        if not ver_anulados:
            q = q.filter(Movimiento.anulado == False)
            q = q.filter(Movimiento.es_anulacion == False)

        total = q.scalar() or Decimal('0')
        return (cuenta.saldo_inicial or Decimal('0')) + total

    @staticmethod
    def obtener_extracto(cuenta, page=1, per_page=20,
                         fecha_desde=None, fecha_hasta=None,
                         ver_anulados=False):
        """
        Devuelve el extracto de una cuenta listo para renderizar.

        `ver_anulados=False` (default) oculta los movimientos anulados
        y los espejos de anulación, para una vista limpia.
        """
        con_filtros = bool(fecha_desde or fecha_hasta)

        q = CajaService._movimientos_query(cuenta, fecha_desde, fecha_hasta)

        # Excluir anulados y espejos, salvo que el usuario los pida
        if not ver_anulados:
            q = q.filter(Movimiento.anulado == False)
            q = q.filter(Movimiento.es_anulacion == False)

        if con_filtros:
            q = q.order_by(Movimiento.fecha.asc(), Movimiento.id.asc())
        else:
            q = q.order_by(Movimiento.fecha.desc(), Movimiento.id.desc())

        pagination = q.paginate(page=page, per_page=per_page, error_out=False)
        movimientos_pagina = pagination.items

        movimientos_asc = (
            movimientos_pagina if con_filtros
            else list(reversed(movimientos_pagina))
        )

        if movimientos_asc:
            saldo_inicial = CajaService._saldo_antes_de(
                cuenta, movimientos_asc[0], ver_anulados=ver_anulados
            )
        else:
            saldo_inicial = cuenta.saldo_actual or Decimal('0')

        saldo = saldo_inicial
        movimientos_con_saldo = []
        for m in movimientos_asc:
            if m.tipo == 'ingreso':
                saldo += m.monto
                debe, haber = m.monto, Decimal('0')
            else:
                saldo -= m.monto
                debe, haber = Decimal('0'), m.monto

            movimientos_con_saldo.append({
                'id': m.id,
                'fecha': m.fecha,
                'descripcion': m.descripcion,
                'debe': debe,
                'haber': haber,
                'saldo': saldo,
                'conciliado': m.conciliado,
                'anulado': m.anulado,
                'es_anulacion': m.es_anulacion,
                'id_movimiento_anulacion': m.id_movimiento_anulacion,
                'motivo_anulacion': m.motivo_anulacion,
                'id_operacion': m.id_operacion,  
                'puede_anularse': m.puede_anularse,
                'motivo_no_anulable': m.motivo_no_anulable,
            })

        saldo_final = saldo

        if not con_filtros:
            movimientos_con_saldo = list(reversed(movimientos_con_saldo))

        return {
            'movimientos': movimientos_con_saldo,
            'saldo_inicial': saldo_inicial,
            'saldo_final': saldo_final,
            'page': page,
            'total_pages': pagination.pages,
            'hay_filtros': con_filtros,
        }

    @staticmethod
    def calcular_saldo_real(cuenta):
        """
        Calcula el saldo real de una cuenta a partir de:
            saldo_inicial + Σ(ingresos) - Σ(egresos)

        No usa `cuenta.saldo_actual` (ese es el valor cacheado que
        queremos verificar). Es la fuente de verdad para auditoría.
        """
        delta = db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (Movimiento.tipo == 'ingreso', Movimiento.monto),
                        else_=-Movimiento.monto
                    )
                ),
                Decimal('0')
            )
        ).filter(Movimiento.id_cuenta == cuenta.id).scalar() or Decimal('0')

        return (cuenta.saldo_inicial or Decimal('0')) + delta
    
    @staticmethod
    def saldos_reales_por_cuenta(empresa_id=None):
        """
        Devuelve {cuenta_id: saldo_real} para todas las cuentas (opcionalmente
        filtradas por empresa), en UNA sola query agregada.

        saldo_real = saldo_inicial + Σ(ingresos) - Σ(egresos)

        No usa `CuentaBancaria.saldo_actual`; ese es el valor cacheado que
        queremos comparar contra este.
        """
        delta_subq = (
            db.session.query(
                Movimiento.id_cuenta.label('id_cuenta'),
                func.coalesce(
                    func.sum(
                        case(
                            (Movimiento.tipo == 'ingreso', Movimiento.monto),
                            else_=-Movimiento.monto
                        )
                    ),
                    Decimal('0')
                ).label('delta')
            )
            .group_by(Movimiento.id_cuenta)
            .subquery()
        )

        q = (
            db.session.query(
                CuentaBancaria.id,
                CuentaBancaria.saldo_inicial,
                func.coalesce(delta_subq.c.delta, Decimal('0')),
            )
            .outerjoin(delta_subq, CuentaBancaria.id == delta_subq.c.id_cuenta)
        )
        if empresa_id is not None:
            q = q.filter(CuentaBancaria.id_empresa == empresa_id)

        resultado = {}
        for cid, saldo_ini, delta in q.all():
            resultado[cid] = (saldo_ini or Decimal('0')) + delta
        return resultado

    @staticmethod
    def cuentas_con_estado_saldo(empresa_id):
        """
        Devuelve una lista de dicts con cada cuenta y su estado de
        sincronización:

            [ {'cuenta': <CuentaBancaria>,
               'saldo_real': Decimal,
               'desincronizada': bool}, ... ]
        """
        cuentas = (
            CuentaBancaria.query
            .filter_by(id_empresa=empresa_id)
            .order_by(CuentaBancaria.nombre)
            .all()
        )
        saldos_reales = CajaService.saldos_reales_por_cuenta(empresa_id)

        items = []
        for c in cuentas:
            real = saldos_reales.get(c.id, c.saldo_actual or Decimal('0'))
            items.append({
                'cuenta': c,
                'saldo_real': real,
                'desincronizada': real != (c.saldo_actual or Decimal('0')),
            })
        return items

    @staticmethod
    def registrar_operacion(data, commit=True):
        """
        Registra una operación con múltiples métodos de pago.

        `data` debe traer:
            - fecha: datetime
            - tipo: 'ingreso' | 'egreso'
            - id_cuenta_contable: int (contrapartida común)
            - descripcion: str
            - id_empresa: int
            - splits: [ {metodo_pago, monto, id_cuenta, comprobante?,
                         conciliado?, id_cheque?, _forzar_cuenta?}, ... ]

        Crea N Movimiento (uno por split) con el mismo `id_operacion` y
        genera un único asiento contable a través de
        `ContabilidadService.generar_asiento_operacion`.

        Los splits no generan espejo de transferencia interna: si querés
        mover plata entre dos cuentas, usá el modo simple (un split).
        """
        import uuid
        from app.models import Movimiento

        splits = data.get('splits') or []
        if not splits:
            raise ValueError('La operación debe tener al menos un método de pago.')

        tipo = data.get('tipo')
        if tipo not in ('ingreso', 'egreso'):
            raise ValueError('Tipo de operación inválido.')

        id_operacion = str(uuid.uuid4())

        movimientos = []
        for idx, split in enumerate(splits):
            if not split.get('id_cuenta'):
                raise ValueError(f'Split #{idx + 1}: falta la cuenta de caja/banco.')
            try:
                monto = Decimal(str(split.get('monto') or 0))
            except Exception:
                raise ValueError(f'Split #{idx + 1}: monto inválido.')
            if monto <= 0:
                raise ValueError(f'Split #{idx + 1}: el monto debe ser mayor a 0.')

            mov_data = {
                'id_cuenta': split['id_cuenta'],
                'id_cuenta_contable': data['id_cuenta_contable'],
                'fecha': data['fecha'],
                'tipo': tipo,
                'monto': monto,
                'descripcion': data['descripcion'],
                'metodo_pago': split['metodo_pago'],
                'comprobante': split.get('comprobante'),
                'id_empresa': data['id_empresa'],
                'id_operacion': id_operacion,
                '_generar_asiento': False,
                '_skip_mirror': True,
            }
            if 'conciliado' in split:
                mov_data['conciliado'] = split['conciliado']
            if split.get('id_cheque'):
                mov_data['id_cheque'] = split['id_cheque']
            if split.get('_forzar_cuenta'):
                mov_data['_forzar_cuenta'] = True

            mov = CajaService.registrar_movimiento(mov_data, commit=False)
            movimientos.append(mov)

        ContabilidadService.generar_asiento_operacion(
            movimientos=movimientos,
            tipo=tipo,
            descripcion=data['descripcion'],
            fecha=data['fecha'],
            id_empresa=data['id_empresa'],
        )

        if commit:
            db.session.commit()

        return movimientos

    @staticmethod
    def anular_movimiento(movimiento_id, motivo=None, forzar=False, commit=True):
        from app.services.contabilidad_service import ContabilidadService

        mov = Movimiento.query.get(movimiento_id)
        if not mov:
            raise ValueError('Movimiento no encontrado.')

        # `forzar=True` permite anular movimientos asociados a cheques
        # desde otros módulos (ej. anulación de ventas). El caller se
        # responsabiliza de validar el estado del cheque.
        if not forzar and not mov.puede_anularse:
            raise ValueError(
                mov.motivo_no_anulable or 'Este movimiento no se puede anular.'
            )

        # Tipo invertido
        tipo_inverso = 'egreso' if mov.tipo == 'ingreso' else 'ingreso'

        # --- Validación de saldo ---
        # Si el espejo es un EGRESO, hay que verificar que haya saldo.
        # Aplica solo a cuentas de efectivo y cartera (no admiten negativo).
        cuenta = CuentaBancaria.query.with_for_update().get(mov.id_cuenta)
        if cuenta and tipo_inverso == 'egreso' \
                and cuenta.tipo in ('efectivo', 'cartera'):
            saldo_actual = cuenta.saldo_actual or Decimal('0')
            if saldo_actual < mov.monto:
                raise ValueError(
                    f'No se puede anular: la cuenta "{cuenta.nombre}" '
                    f'quedaría en negativo. '
                    f'Saldo actual: ${saldo_actual:.2f} · '
                    f'Monto a revertir: ${mov.monto:.2f}. '
                    f'Si querés anular este ingreso, primero ingresá '
                    f'plata a la cuenta o anulá otros egresos que '
                    f'dependan de este ingreso.'
                )

        # Crear movimiento espejo
        espejo = Movimiento(
            id_cuenta=mov.id_cuenta,
            id_cuenta_contable=mov.id_cuenta_contable,
            fecha=datetime.utcnow(),
            tipo=tipo_inverso,
            monto=mov.monto,
            descripcion=f'Anulación del movimiento #{mov.id}',
            metodo_pago=mov.metodo_pago,
            comprobante=mov.comprobante,
            conciliado=False,
            id_empresa=mov.id_empresa,
            es_anulacion=True,
        )
        db.session.add(espejo)
        db.session.flush()

        # Revertir saldo de la cuenta
        if cuenta:
            if espejo.tipo == 'ingreso':
                cuenta.saldo_actual += espejo.monto
            else:
                cuenta.saldo_actual -= espejo.monto

        # Generar contra-asiento
        ContabilidadService.generar_asiento(espejo)

        # Marcar el original como anulado
        mov.anulado = True
        mov.fecha_anulacion = datetime.utcnow()
        mov.motivo_anulacion = (motivo or 'Sin motivo especificado')[:200]
        mov.id_movimiento_anulacion = espejo.id

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return mov, espejo    

    @staticmethod
    def anular_operacion(id_operacion, motivo=None, commit=True):
        """
        Anula una operación completa (todos los movimientos que comparten
        el mismo id_operacion).

        - Valida saldo para cada cuenta afectada.
        - Crea un espejo por cada split.
        - Genera un único contra-asiento unificado.
        - Marca todos los originales como anulados.
        - Los espejos comparten un nuevo id_operacion.

        Retorna (movs_originales, espejos).
        """
        import uuid
        from app.services.contabilidad_service import ContabilidadService

        movs = Movimiento.query.filter_by(id_operacion=id_operacion).all()
        if not movs:
            raise ValueError('Operación no encontrada.')

        if any(m.anulado for m in movs):
            raise ValueError('Esta operación ya fue anulada.')

        if any(m.es_anulacion for m in movs):
            raise ValueError('Esta operación es una anulación.')

        if any(m.id_cheque for m in movs):
            raise ValueError(
                'La operación contiene movimientos asociados a cheques. '
                'Anulalos desde el módulo de Cheques.'
            )

        # --- Validación de saldo por cuenta ---
        # Agrupamos los montos a revertir por cuenta, solo para
        # movimientos que generarían un egreso (los que eran ingreso).
        reversiones = {}
        for m in movs:
            if m.tipo != 'ingreso':
                continue
            cuenta = CuentaBancaria.query.get(m.id_cuenta)
            if not cuenta or cuenta.tipo not in ('efectivo', 'cartera'):
                continue
            info = reversiones.setdefault(
                cuenta.id, {'cuenta': cuenta, 'total': Decimal('0')}
            )
            info['total'] += m.monto

        for info in reversiones.values():
            cuenta = info['cuenta']
            saldo = cuenta.saldo_actual or Decimal('0')
            if saldo < info['total']:
                raise ValueError(
                    f'No se puede anular: la cuenta "{cuenta.nombre}" '
                    f'quedaría en negativo. '
                    f'Saldo actual: ${saldo:.2f} · '
                    f'Monto a revertir: ${info["total"]:.2f}.'
                )

        # --- Crear los espejos con un nuevo id_operacion ---
        nuevo_id_operacion = str(uuid.uuid4())
        espejos = []
        for m in movs:
            tipo_inverso = 'egreso' if m.tipo == 'ingreso' else 'ingreso'
            espejo = Movimiento(
                id_cuenta=m.id_cuenta,
                id_cuenta_contable=m.id_cuenta_contable,
                fecha=datetime.utcnow(),
                tipo=tipo_inverso,
                monto=m.monto,
                descripcion=f'Anulación de operación: {m.descripcion}',
                metodo_pago=m.metodo_pago,
                comprobante=m.comprobante,
                conciliado=False,
                id_empresa=m.id_empresa,
                id_operacion=nuevo_id_operacion,
                es_anulacion=True,
            )
            db.session.add(espejo)
            espejos.append(espejo)

        db.session.flush()

        # --- Revertir saldos ---
        for e in espejos:
            cuenta = CuentaBancaria.query.get(e.id_cuenta)
            if not cuenta:
                continue
            if e.tipo == 'ingreso':
                cuenta.saldo_actual += e.monto
            else:
                cuenta.saldo_actual -= e.monto

        # --- Contra-asiento unificado ---
        # El tipo del asiento es el inverso al del primer movimiento.
        tipo_original = movs[0].tipo
        tipo_asiento = 'egreso' if tipo_original == 'ingreso' else 'ingreso'

        ContabilidadService.generar_asiento_operacion(
            movimientos=espejos,
            tipo=tipo_asiento,
            descripcion=f'Anulación: {movs[0].descripcion}',
            fecha=datetime.utcnow(),
            id_empresa=movs[0].id_empresa,
        )

        # --- Marcar originales como anulados ---
        ahora = datetime.utcnow()
        for i, m in enumerate(movs):
            m.anulado = True
            m.fecha_anulacion = ahora
            m.motivo_anulacion = (
                motivo or 'Anulación de operación completa'
            )[:200]
            m.id_movimiento_anulacion = espejos[i].id

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return movs, espejos