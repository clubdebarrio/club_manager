# app/services/cuota_service.py

from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from app import db
from app.models import Socio, ConfiguracionCuota, CuotaSocial
from app.services.chytapay_service import ChytaPayService
from flask import current_app
from app.models import CuentaBancaria
from decimal import Decimal
from app.utils import utc_to_local

class CuotaService:

    @staticmethod
    def obtener_monto_para_socio(socio):
        # Usamos la fecha de HOY en Argentina, no en UTC
        hoy_local = utc_to_local(datetime.utcnow()).date()
        
        config = ConfiguracionCuota.query.filter(
            ConfiguracionCuota.id_tipo_socio == socio.id_tipo_socio,
            ConfiguracionCuota.id_categoria_socio == socio.id_categoria_socio,
            ConfiguracionCuota.id_empresa == socio.id_empresa, # <-- De paso, agregamos esta red de seguridad
            ConfiguracionCuota.fecha_desde <= hoy_local,
            (ConfiguracionCuota.fecha_hasta.is_(None) |
             (ConfiguracionCuota.fecha_hasta >= hoy_local))
        ).first()

        if not config:
            raise ValueError(
                f"No hay configuración de cuota vigente para tipo "
                f"{socio.id_tipo_socio} y categoría {socio.id_categoria_socio}"
            )
        return config.monto

    @staticmethod
    def calcular_adicionales(socio, fecha_inicio_mes, fecha_fin_mes):
        from app.models import SocioAdicional
        total = Decimal('0')
        detalles = []

        def es_valido(adicional):
            # El adicional debe empezar antes de que termine el mes
            if adicional.fecha_desde > fecha_fin_mes:
                return False
            # Si tiene fecha de fin, debe terminar después de que empiece el mes
            if adicional.fecha_hasta and adicional.fecha_hasta < fecha_inicio_mes:
                return False
            return True

        # 1. Adicionales propios
        for sa in socio.adicionales_asignados:
            if sa.activo and sa.adicional.activo and es_valido(sa.adicional):
                total += sa.adicional.monto
                detalles.append(f"{sa.adicional.nombre} ({socio.nombre})")

        # 2. Adicionales de la familia
        if socio.id_familia is None:
            for familiar in socio.miembros_familia:
                if familiar.fecha_baja is None:
                    for sa_fam in familiar.adicionales_asignados:
                        if sa_fam.activo and sa_fam.adicional.activo and es_valido(sa_fam.adicional):
                            total += sa_fam.adicional.monto
                            detalles.append(f"{sa_fam.adicional.nombre} ({familiar.nombre})")

        return total, detalles

    @staticmethod
    def generar_cuotas_para_mes(ano, mes, id_empresa=None):
        """
        Genera cuotas optimizando las consultas a la base de datos.
        """
        import time
        t0 = time.time()
        
        from datetime import date
        import calendar
        from sqlalchemy import or_, insert
        from sqlalchemy.orm import selectinload
        from app.models import ConfiguracionCuota, SocioAdicional, Adicional

        fecha_inicio_mes = date(ano, mes, 1)
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        fecha_fin_mes = date(ano, mes, ultimo_dia)
        periodo = f"{ano}-{mes:02d}"
        fecha_vencimiento = date(ano, mes, 10)

        # 1. SOCIOS PRINCIPALES (Usamos selectinload que es más rápido que joinedload)
        query = Socio.query.options(
            selectinload(Socio.miembros_familia),
            selectinload(Socio.tipo_socio_rel),
            selectinload(Socio.categoria_socio_rel)
        ).filter(
            Socio.id_familia.is_(None),
            or_(Socio.fecha_alta.is_(None), Socio.fecha_alta <= fecha_fin_mes),
            or_(Socio.fecha_baja.is_(None), Socio.fecha_baja >= fecha_inicio_mes)
        )

        if id_empresa:
            query = query.filter(Socio.id_empresa == id_empresa)

        socios_principales = query.all()
        print(f"⏱️ Tiempo consulta socios: {time.time() - t0:.2f}s")
        t1 = time.time()
        
        # 2. ADICIONALES
        adicionales_query = (
            SocioAdicional.query
            .join(Socio, SocioAdicional.id_socio == Socio.id)
            .options(selectinload(SocioAdicional.adicional))
            .filter(Socio.id_empresa == id_empresa, SocioAdicional.activo == True)
            .all()
        )
        
        adicionales_por_socio = {}
        for sa in adicionales_query:
            if sa.adicional and sa.adicional.activo:
                if sa.id_socio not in adicionales_por_socio:
                    adicionales_por_socio[sa.id_socio] = []
                adicionales_por_socio[sa.id_socio].append(sa)
                
        print(f"⏱️ Tiempo consulta adicionales: {time.time() - t1:.2f}s")
        t2 = time.time()

        def _calcular_adicionales_rapido(socio, fecha_inicio, fecha_fin):
            total = Decimal('0')
            detalles = []
            def es_valido(adicional):
                if adicional.fecha_desde > fecha_fin: return False
                if adicional.fecha_hasta and adicional.fecha_hasta < fecha_inicio: return False
                return True

            for sa in adicionales_por_socio.get(socio.id, []):
                if es_valido(sa.adicional):
                    total += sa.adicional.monto
                    detalles.append(f"{sa.adicional.nombre} ({socio.nombre})")
            
            if socio.id_familia is None:
                for familiar in socio.miembros_familia:
                    if familiar.fecha_baja is None:
                        for sa in adicionales_por_socio.get(familiar.id, []):
                            if es_valido(sa.adicional):
                                total += sa.adicional.monto
                                detalles.append(f"{sa.adicional.nombre} ({familiar.nombre})")
            return total, detalles

        # 3. CONFIGURACIONES
        configs = ConfiguracionCuota.query.filter(
            ConfiguracionCuota.id_empresa == id_empresa,
            ConfiguracionCuota.fecha_desde <= fecha_fin_mes,
            or_(
                ConfiguracionCuota.fecha_hasta.is_(None),
                ConfiguracionCuota.fecha_hasta >= fecha_inicio_mes
            )
        ).all()
        mapa_configs = {(c.id_tipo_socio, c.id_categoria_socio): c.monto for c in configs}
        
        print(f"⏱️ Tiempo consulta configs: {time.time() - t2:.2f}s")
        t3 = time.time()

        # 4. CUOTAS EXISTENTES (Optimizado para traer solo IDs)
        socios_con_cuota = {
            c[0] for c in db.session.query(CuotaSocial.id_socio).join(Socio).filter(
                CuotaSocial.periodo == periodo,
                Socio.id_empresa == id_empresa
            ).all()
        }
        
        print(f"⏱️ Tiempo consulta cuotas existentes: {time.time() - t3:.2f}s")
        t4 = time.time()

        contador = 0
        errores = []
        cuotas_a_insertar = []

        # 5. BUCLE EN MEMORIA (No consulta DB)
        for socio in socios_principales:
            if socio.id in socios_con_cuota:
                continue
                
            try:
                monto_base = mapa_configs.get((socio.id_tipo_socio, socio.id_categoria_socio))
                if not monto_base:
                    tipo_nombre = socio.tipo_socio_rel.nombre if socio.tipo_socio_rel else socio.id_tipo_socio
                    cat_nombre = socio.categoria_socio_rel.nombre if socio.categoria_socio_rel else socio.id_categoria_socio
                    raise ValueError(f"No hay configuración vigente para tipo {tipo_nombre} y categoría {cat_nombre}")
                
                monto_adicional, detalle_adic = _calcular_adicionales_rapido(socio, fecha_inicio_mes, fecha_fin_mes)
                monto_final = monto_base + monto_adicional
                detalle_texto = ", ".join(detalle_adic) if detalle_adic else None

                cuotas_a_insertar.append({
                    'id_socio': socio.id,
                    'periodo': periodo,
                    'monto': monto_final,
                    'detalle_adicionales': detalle_texto,
                    'fecha_vencimiento': fecha_vencimiento,
                    'estado': 'pendiente'
                })
                contador += 1
            except ValueError as e:
                errores.append(f"Socio {socio.numero_socio} - {socio.apellido}, {socio.nombre}: {str(e)}")
                continue

        print(f"⏱️ Tiempo bucle: {time.time() - t4:.2f}s")
        t5 = time.time()

        # 6. INSERCIÓN MASIVA (Usando execute con insert de SQLAlchemy Core para máxima velocidad)
        if cuotas_a_insertar:
            db.session.execute(insert(CuotaSocial), cuotas_a_insertar)
        db.session.commit()
        
        print(f"⏱️ Tiempo inserción y commit: {time.time() - t5:.2f}s")
        print(f"✅ TOTAL: {time.time() - t0:.2f}s")

        return {
            'cantidad': contador,
            'errores': errores
        }
    
    @staticmethod
    def marcar_cuota_pagada(cuota_id, fecha_pago=None, metodo='manual',
                            id_cuenta=None, id_cheque=None):
        """
        Marca una cuota como pagada y genera el movimiento en caja + asiento.

        La contrapartida es 4.1 Cuotas Sociales (resuelta vía CuentaContableResolver).

        Retorna True si se marcó, False si ya estaba pagada.
        """
        from app.models import CuentaBancaria, Cheque
        from app.services.caja_service import CajaService
        from app.services.cuenta_contable_resolver import CuentaContableResolver
        from app.services.contabilidad_service import ContabilidadService

        cuota = CuotaSocial.query.get_or_404(cuota_id)
        if cuota.estado == 'pagada':
            return False

        # Una cuota pagada SIEMPRE tiene que tener un movimiento de caja y su
        # asiento. Si no se indica la cuenta, es un error de programación.
        if not id_cuenta:
            raise ValueError(
                'Para marcar una cuota como pagada hay que indicar la cuenta de '
                'caja/banco donde entró el dinero. Sin esto, la cuota quedaría '
                'marcada como pagada pero sin asiento contable.'
            )

        cuenta = CuentaBancaria.query.get(id_cuenta)
        if not cuenta:
            raise ValueError('Cuenta no encontrada.')
        if not cuenta.activo:
            raise ValueError(f'La cuenta "{cuenta.nombre}" está inactiva.')
        if cuenta.id_empresa != cuota.socio.id_empresa:
            raise ValueError('La cuenta no pertenece a la empresa del socio.')
        if not cuenta.id_cuenta_contable:
            raise ValueError(
                f'La cuenta "{cuenta.nombre}" no tiene asignada una cuenta '
                f'contable de activo. Asignala antes de registrar cobros.'
            )

        # Contrapartida: 4.1 Cuotas Sociales
        cuenta_contable = CuentaContableResolver.resolver_o_fallar(
            cuota.socio.id_empresa, 'cuota_social'
        )

        movimiento_data = {
            'id_cuenta': id_cuenta,
            'id_cuenta_contable': cuenta_contable.id,
            'fecha': fecha_pago or datetime.utcnow(),
            'tipo': 'ingreso',
            'monto': cuota.monto,
            'descripcion': (
                f'Cobro de cuota {cuota.periodo} - '
                f'{cuota.socio.apellido} {cuota.socio.nombre}'
            ),
            'metodo_pago': metodo,
            'comprobante': f'Cuota #{cuota.id}',
            'id_empresa': cuota.socio.id_empresa,
        }

        if id_cheque and str(id_cheque).isdigit() and int(id_cheque) > 0:
            cheque = Cheque.query.get(int(id_cheque))
            if cheque:
                if cheque.id_empresa != cuota.socio.id_empresa:
                    raise ValueError('El cheque no pertenece a la empresa del socio.')
                movimiento_data['id_cheque'] = int(id_cheque)
                movimiento_data['comprobante'] = (
                    f'Cuota #{cuota.id} - Cheque #{cheque.numero}'
                )

        movimiento = CajaService.registrar_movimiento(movimiento_data, commit=False)

        cuota.estado = 'pagada'
        cuota.fecha_pago = fecha_pago or datetime.utcnow()

        if movimiento:
            ContabilidadService.generar_asiento(movimiento)

        return True