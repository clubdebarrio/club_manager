# app/cli.py
"""
Comandos de línea de comandos registrados en la app.

Uso:
    flask caja:recalcular-saldos
    flask caja:recalcular-saldos --dry-run
    flask caja:recalcular-saldos --cuenta-id 3
    flask caja:recalcular-saldos --empresa-id 1

    flask contabilidad:seed-plan
    flask contabilidad:seed-plan --dry-run
    flask contabilidad:seed-plan --empresa-id 1
"""
import click
from decimal import Decimal
from app import db
from app.models import CuentaBancaria
from app.services.caja_service import CajaService


# ============================================================
# Plan de cuentas: seed inicial
# ============================================================

# Cuentas que se generan únicamente desde su módulo (cuotas, ventas,
# alquileres, cheques). No deben aparecer en el dropdown de contrapartida
# del form manual de movimientos.
CODIGOS_AUTOMATICOS = {'4.1', '4.2', '4.3', '1.1.6', '2.1.6', '5.10'}

PLAN_SEED = [
    # (codigo, nombre, tipo, codigo_padre)
    # ============ 1 ACTIVO ============
    ('1',   'ACTIVO',                          'Activo',     None),
    ('1.1', 'Activo Corriente',                'Activo',     '1'),
    ('1.1.1',   'Caja y Bancos',                   'Activo', '1.1'),
    ('1.1.1.1', 'Caja Chica',                      'Activo', '1.1.1'),
    ('1.1.1.2', 'Cuenta Corriente',                'Activo', '1.1.1'),
    ('1.1.2',   'Créditos por Cuotas Sociales',    'Activo', '1.1'),
    ('1.1.3',   'Créditos por Alquileres',         'Activo', '1.1'),
    ('1.1.4',   'Créditos por Ventas',             'Activo', '1.1'),
    ('1.1.5',   'Anticipos a Proveedores',         'Activo', '1.1'),
    ('1.1.6',   'Cheques a Cobrar (en cartera)',   'Activo', '1.1'),
    ('1.1.7',   'IVA Crédito Fiscal',              'Activo', '1.1'),
    ('1.1.8',   'Mercadería de reventa',           'Activo', '1.1'),

    ('1.2', 'Activo No Corriente',             'Activo',     '1'),
    ('1.2.1',   'Bienes de Uso',                   'Activo', '1.2'),
    ('1.2.1.1', 'Muebles y Útiles',                'Activo', '1.2.1'),
    ('1.2.1.2', 'Instalaciones Deportivas',        'Activo', '1.2.1'),
    ('1.2.1.3', 'Equipos Informáticos',            'Activo', '1.2.1'),
    ('1.2.1.4', 'Vehículos',                       'Activo', '1.2.1'),

    # ============ 2 PASIVO ============
    ('2',   'PASIVO',                          'Pasivo',     None),
    ('2.1', 'Pasivo Corriente',                'Pasivo',     '2'),
    ('2.1.1', 'Proveedores',                       'Pasivo', '2.1'),
    ('2.1.2', 'Sueldos a Pagar',                   'Pasivo', '2.1'),
    ('2.1.3', 'Cargas Sociales a Pagar',           'Pasivo', '2.1'),
    ('2.1.4', 'IIBB a Pagar',                      'Pasivo', '2.1'),
    ('2.1.5', 'IVA Débito Fiscal / a Pagar',       'Pasivo', '2.1'),
    ('2.1.6', 'Cheques a Pagar (emitidos)',        'Pasivo', '2.1'),
    ('2.1.7', 'Préstamos Bancarios CP',            'Pasivo', '2.1'),
    ('2.2', 'Pasivo No Corriente',             'Pasivo',     '2'),
    ('2.2.1', 'Préstamos Bancarios LP',            'Pasivo', '2.2'),

    # ============ 3 PATRIMONIO ============
    ('3',   'PATRIMONIO',                      'Patrimonio', None),
    ('3.1', 'Capital Social',                  'Patrimonio', '3'),
    ('3.2', 'Resultados No Asignados',         'Patrimonio', '3'),
    ('3.3', 'Resultado del Ejercicio',         'Patrimonio', '3'),

    # ============ 4 RECURSOS ============
    ('4',   'RECURSOS',                        'Recurso',    None),
    ('4.1', 'Cuotas Sociales',                 'Recurso',    '4'),
    ('4.2', 'Alquileres de Instalaciones',     'Recurso',    '4'),
    ('4.3', 'Ventas de Indumentaria',          'Recurso',    '4'),
    ('4.4', 'Eventos y Espectáculos',          'Recurso',    '4'),
    ('4.5', 'Donaciones',                      'Recurso',    '4'),
    ('4.6', 'Subvenciones',                    'Recurso',    '4'),
    ('4.7', 'Resultados Financieros',          'Recurso',    '4'),
    ('4.8', 'Otros Recursos',                  'Recurso',    '4'),

    # ============ 5 GASTOS ============
    ('5',   'GASTOS',                          'Gasto',      None),
    ('5.1', 'Gastos en Personal',              'Gasto',      '5'),
    ('5.1.1', 'Sueldos y Jornales',                'Gasto', '5.1'),
    ('5.1.2', 'Cargas Sociales',                   'Gasto', '5.1'),
    ('5.1.3', 'Honorarios Profesionales',          'Gasto', '5.1'),

    ('5.2', 'Servicios',                       'Gasto',      '5'),
    ('5.2.1',   'Electricidad',                    'Gasto', '5.2'),
    ('5.2.1.1', 'Electricidad Sede',               'Gasto', '5.2.1'),
    ('5.2.1.2', 'Electricidad Parque',             'Gasto', '5.2.1'),
    ('5.2.2',   'Agua',                            'Gasto', '5.2'),
    ('5.2.3',   'Gas',                             'Gasto', '5.2'),
    ('5.2.4',   'Internet y Telefonía',            'Gasto', '5.2'),
    ('5.2.5',   'Limpieza',                        'Gasto', '5.2'),
    ('5.2.6',   'Seguridad',                       'Gasto', '5.2'),

    ('5.3', 'Mantenimiento y Reparaciones',    'Gasto',      '5'),
    ('5.4', 'Insumos y Materiales',            'Gasto',      '5'),
    ('5.5', 'Impuestos y Tasas',               'Gasto',      '5'),
    ('5.6', 'Gastos Bancarios',                'Gasto',      '5'),
    ('5.7', 'Gastos Administrativos',          'Gasto',      '5'),
    ('5.8', 'Amortizaciones',                  'Gasto',      '5'),
    ('5.9', 'Otros Gastos',                    'Gasto',      '5'),
    ('5.10', 'Costo de Mercadería Vendida',    'Gasto',      '5'),
]

# Códigos del seed, para no advertir "padre no encontrado" cuando el
# padre también va a ser creado en el mismo run (especialmente en dry-run).
_CODIGOS_SEED = {c[0] for c in PLAN_SEED}


def _fmt(v):
    """Formatea un Decimal como $1.234,56 (formato argentino)."""
    if v is None:
        v = Decimal('0')
    s = f"{v:,.2f}"
    return "$" + s.replace(",", "X").replace(".", ",").replace("X", ".")


def register_cli(app):

    # ============================================================
    # caja:recalcular-saldos
    # ============================================================
    @app.cli.command('caja:recalcular-saldos')
    @click.option('--cuenta-id', type=int, default=None,
                  help='Recalcular solo esta cuenta.')
    @click.option('--empresa-id', type=int, default=None,
                  help='Recalcular solo cuentas de esta empresa.')
    @click.option('--dry-run', is_flag=True,
                  help='Mostrar diferencias sin guardar cambios.')
    def recalcular_saldos(cuenta_id, empresa_id, dry_run):
        """Recalcula el saldo_actual de las cuentas a partir de
        saldo_inicial + Σ movimientos."""

        query = CuentaBancaria.query
        if cuenta_id is not None:
            query = query.filter(CuentaBancaria.id == cuenta_id)
        if empresa_id is not None:
            query = query.filter(CuentaBancaria.id_empresa == empresa_id)

        cuentas = query.order_by(CuentaBancaria.id_empresa,
                                 CuentaBancaria.id).all()

        if not cuentas:
            click.echo('No se encontraron cuentas con esos filtros.')
            return

        click.echo('')
        click.echo(f'{"ID":>4}  {"Empresa":>7}  '
                   f'{"Cuenta":<30}  {"Cacheado":>14}  '
                   f'{"Real":>14}  {"Dif.":>14}')
        click.echo('-' * 100)

        actualizadas = 0
        sin_cambios = 0

        for cta in cuentas:
            saldo_real = CajaService.calcular_saldo_real(cta)
            saldo_cache = cta.saldo_actual or Decimal('0')
            diff = saldo_real - saldo_cache

            nombre = (cta.nombre or '')[:30]

            if diff == 0:
                sin_cambios += 1
                marca = ''
            else:
                marca = '  ← DESYNC'
                actualizadas += 1
                if not dry_run:
                    cta.saldo_actual = saldo_real

            click.echo(
                f'{cta.id:>4}  {cta.id_empresa:>7}  '
                f'{nombre:<30}  {_fmt(saldo_cache):>14}  '
                f'{_fmt(saldo_real):>14}  {_fmt(diff):>14}{marca}'
            )

        click.echo('-' * 100)
        click.echo(f'Total de cuentas: {len(cuentas)}  |  '
                   f'Sin cambios: {sin_cambios}  |  '
                   f'Desincronizadas: {actualizadas}')

        if dry_run:
            click.echo('')
            click.echo('(dry-run) No se guardó ningún cambio.')
            return

        if actualizadas:
            try:
                db.session.commit()
                click.echo('')
                click.echo(f'✅ {actualizadas} cuenta(s) actualizada(s).')
            except Exception as e:
                db.session.rollback()
                click.echo(f'❌ Error al guardar: {e}', err=True)
                raise click.Abort()
        else:
            click.echo('')
            click.echo('✅ Todos los saldos están sincronizados.')

    # ============================================================
    # contabilidad:seed-plan
    # ============================================================
    @app.cli.command('contabilidad:seed-plan')
    @click.option('--empresa-id', type=int, default=None,
                  help='Sembrar solo esta empresa (por defecto: todas).')
    @click.option('--dry-run', is_flag=True,
                  help='Mostrar qué se crearía sin guardar cambios.')
    def seed_plan(empresa_id, dry_run):
        """Carga el plan de cuentas por defecto (idempotente)."""
        from app.models import Empresa, PlanCuenta

        q = Empresa.query
        if empresa_id is not None:
            q = q.filter(Empresa.id == empresa_id)
        empresas = q.order_by(Empresa.id).all()

        if not empresas:
            click.echo('No hay empresas para sembrar.')
            return

        total_creadas = 0

        for emp in empresas:
            click.echo('')
            click.echo(f'Empresa #{emp.id} — {emp.nombre}')
            click.echo('-' * 60)

            existentes = {
                c.codigo: c
                for c in PlanCuenta.query.filter_by(id_empresa=emp.id).all()
            }

            creadas = 0
            omitidas = 0

            for codigo, nombre, tipo, codigo_padre in PLAN_SEED:
                es_auto = codigo in CODIGOS_AUTOMATICOS

                if codigo in existentes:
                    cuenta_existente = existentes[codigo]
                    if cuenta_existente.es_automatica != es_auto:
                        if dry_run:
                            click.echo(
                                f'  ~ {codigo:<12} {nombre} '
                                f'(es_automatica: {cuenta_existente.es_automatica} -> {es_auto})'
                            )
                        else:
                            cuenta_existente.es_automatica = es_auto
                    omitidas += 1
                    continue

                id_padre = None
                if codigo_padre:
                    padre = existentes.get(codigo_padre)
                    if padre is not None:
                        id_padre = padre.id
                    elif codigo_padre not in _CODIGOS_SEED:
                        click.echo(
                            f'  ⚠️  {codigo} {nombre}: padre {codigo_padre} '
                            f'no encontrado, se crea como raíz.'
                        )

                if dry_run:
                    click.echo(f'  + {codigo:<12} {nombre}')
                else:
                    nueva = PlanCuenta(
                        codigo=codigo,
                        nombre=nombre,
                        tipo=tipo,
                        id_padre=id_padre,
                        id_empresa=emp.id,
                        activo=True,
                        es_automatica=es_auto,
                    )
                    db.session.add(nueva)
                    db.session.flush()
                    existentes[codigo] = nueva
                creadas += 1

            if not dry_run and creadas:
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    click.echo(f'  ❌ Error al guardar: {e}', err=True)
                    continue

            click.echo('')
            click.echo(f'  Creadas: {creadas}  |  '
                       f'Omitidas (ya existían): {omitidas}')
            total_creadas += creadas

        click.echo('')
        if dry_run:
            click.echo(f'(dry-run) Se crearían {total_creadas} cuentas en total.')
        else:
            click.echo(f'✅ {total_creadas} cuentas creadas en total.')

    @app.cli.command('caja:seed-cartera-cheques')
    @click.option('--empresa-id', type=int, default=None)
    def seed_cartera_cheques(empresa_id):
        """Crea (si no existe) la CuentaBancaria 'Cartera de Cheques'
        ligada a la PlanCuenta 1.1.6, para cada empresa."""
        from app.models import Empresa, PlanCuenta, CuentaBancaria

        q = Empresa.query
        if empresa_id is not None:
            q = q.filter(Empresa.id == empresa_id)
        empresas = q.all()
        if not empresas:
            click.echo('No hay empresas.')
            return

        creadas = 0
        for emp in empresas:
            existente = CuentaBancaria.query.filter_by(
                id_empresa=emp.id, nombre='Cartera de Cheques'
            ).first()
            if existente:
                click.echo(f'Empresa #{emp.id}: ya existe (id={existente.id})')
                continue

            plan = PlanCuenta.query.filter_by(
                id_empresa=emp.id, codigo='1.1.6', activo=True
            ).first()
            if not plan:
                click.echo(f'Empresa #{emp.id}: falta PlanCuenta 1.1.6. '
                           f'Corré primero contabilidad:seed-plan.')
                continue

            cuenta = CuentaBancaria(
                nombre='Cartera de Cheques',
                tipo='cartera',
                saldo_inicial=0,
                saldo_actual=0,
                activo=True,
                id_empresa=emp.id,
                id_cuenta_contable=plan.id,
            )
            db.session.add(cuenta)
            creadas += 1
            click.echo(f'Empresa #{emp.id}: creada (plan={plan.codigo})')

        db.session.commit()
        click.echo(f'✅ {creadas} Carteras creadas.')

    @app.cli.command('proveedores:migrar-desde-compras')
    @click.option('--dry-run', is_flag=True, help='Solo mostrar sin guardar.')
    def migrar_proveedores(dry_run):
        """Crea Proveedor a partir de los strings únicos de Compra.proveedor
        y asocia las compras existentes por FK."""
        from app.models import Empresa, Compra, Proveedor

        empresas = Empresa.query.all()
        total_creados = 0
        total_asociados = 0

        for emp in empresas:
            click.echo(f'\nEmpresa #{emp.id} — {emp.nombre}')

            # Compras con string de proveedor y sin FK
            compras = Compra.query.filter(
                Compra.id_empresa == emp.id,
                Compra.proveedor.isnot(None),
                Compra.id_proveedor.is_(None),
            ).all()

            if not compras:
                click.echo('  (nada para migrar)')
                continue

            # Agrupar nombres únicos
            nombres = set()
            for c in compras:
                n = (c.proveedor or '').strip()
                if n:
                    nombres.add(n)

            # Crear los que no existen
            mapa = {}
            existentes = {
                p.nombre.lower(): p
                for p in Proveedor.query.filter_by(id_empresa=emp.id).all()
            }
            for nombre in sorted(nombres):
                if nombre.lower() in existentes:
                    mapa[nombre] = existentes[nombre.lower()]
                    continue
                if dry_run:
                    click.echo(f'  + (crear) {nombre}')
                    mapa[nombre] = None
                else:
                    p = Proveedor(nombre=nombre, id_empresa=emp.id, activo=True)
                    db.session.add(p)
                    db.session.flush()
                    mapa[nombre] = p
                    existentes[nombre.lower()] = p
                    total_creados += 1
                    click.echo(f'  + {nombre} (id={p.id})')

            # Asociar compras
            for c in compras:
                n = (c.proveedor or '').strip()
                prov = mapa.get(n)
                if prov and not dry_run:
                    c.id_proveedor = prov.id
                    total_asociados += 1
                elif prov and dry_run:
                    total_asociados += 1

        if not dry_run:
            db.session.commit()

        click.echo('')
        if dry_run:
            click.echo(f'(dry-run) Se crearían {total_creados} proveedores '
                       f'y se asociarían {total_asociados} compras.')
        else:
            click.echo(f'✅ {total_creados} proveedores creados y '
                       f'{total_asociados} compras asociadas.')

    # ============================================================
    # empresa:crear
    # ============================================================
    @app.cli.command('empresa:crear')
    @click.option('--nombre', required=True, help='Nombre del club')
    @click.option('--cuit', required=True, help='CUIT del club (debe ser único)')
    @click.option('--email', required=True, help='Email del usuario administrador')
    @click.option('--password', required=True, help='Contraseña del administrador')
    @click.option('--username', default=None,
                  help='Usuario del admin (por defecto: el email)')
    @click.option('--direccion', default=None, help='Dirección del club')
    @click.option('--telefono', default=None, help='Teléfono del club')

    def crear_empresa(nombre, cuit, email, password, username,
                      direccion, telefono):
        """Crea una empresa con todos sus datos base."""
        from app.services.empresa_service import EmpresaService

        click.echo('')
        click.echo(f'Creando empresa "{nombre}"...')

        from app.utils_db import transaccion
        try:
            with transaccion():
                result = EmpresaService.crear_empresa_completa(
                    nombre=nombre,
                    cuit=cuit,
                    email_admin=email,
                    password=password,
                    username_admin=username,
                    nombre_admin='Administrador',
                    direccion=direccion,
                    telefono=telefono,
                )
        except ValueError as e:
            click.echo(f'❌ {e}', err=True)
            raise click.Abort()
        except Exception as e:
            click.echo(f'❌ Error al guardar: {e}', err=True)
            raise click.Abort()

        empresa = result['empresa']
        admin = result['admin']

        click.echo('')
        click.echo('=' * 60)
        click.echo(f'✅ Empresa "{empresa.nombre}" creada (ID #{empresa.id})')
        click.echo(f'   Podés loguearte con:')
        click.echo(f'     Usuario:   {admin.username}')
        click.echo(f'     Password:  (la que especificaste)')
        click.echo('=' * 60)

    # ============================================================
    # usuarios:marcar-super-admins
    # ============================================================
    @app.cli.command('usuarios:marcar-super-admins')
    @click.option('--dry-run', is_flag=True, help='Solo mostrar sin guardar.')
    def marcar_super_admins(dry_run):
        """
        Marca como super admin al usuario más antiguo de cada empresa.

        Así queda el admin original de cada club como Presidente.
        """
        from app.models import Usuario, Empresa

        actualizados = 0
        for emp in Empresa.query.all():
            primer_usuario = (
                Usuario.query
                .filter_by(id_empresa=emp.id)
                .order_by(Usuario.fecha_creacion.asc(), Usuario.id.asc())
                .first()
            )
            if not primer_usuario:
                click.echo(f'Empresa #{emp.id} ({emp.nombre}): sin usuarios.')
                continue

            if primer_usuario.es_super_admin:
                click.echo(f'Empresa #{emp.id}: {primer_usuario.username} ya es super admin.')
                continue

            click.echo(
                f'Empresa #{emp.id}: {primer_usuario.username} -> super admin'
            )
            if not dry_run:
                primer_usuario.es_super_admin = True
                actualizados += 1

        if not dry_run and actualizados:
            db.session.commit()

        click.echo('')
        if dry_run:
            click.echo(f'(dry-run) Se marcarían {actualizados} super admins.')
        else:
            click.echo(f'✅ {actualizados} usuarios marcados como super admin.')

    @app.cli.command('reservas:finalizar-vencidas')
    @click.option('--dry-run', is_flag=True,
                  help='Mostrar cuántas se finalizarían, sin guardar.')
    def finalizar_vencidas(dry_run):
        """Marca como finalizadas las reservas confirmadas cuya fecha_fin ya pasó."""
        from app.models import Reserva
        from datetime import datetime

        ahora = datetime.utcnow()
        reservas = Reserva.query.filter(
            Reserva.estado == 'confirmada',
            Reserva.fecha_fin < ahora,
        ).all()

        if not reservas:
            click.echo('No hay reservas vencidas para finalizar.')
            return

        click.echo(f'Reservas a finalizar: {len(reservas)}')
        for r in reservas[:10]:
            click.echo(
                f'  #{r.id} - {r.socio.apellido}, {r.socio.nombre} '
                f'(fin: {r.fecha_fin.strftime("%d/%m/%Y %H:%M")})'
            )
        if len(reservas) > 10:
            click.echo(f'  ... y {len(reservas) - 10} más')

        if dry_run:
            click.echo('')
            click.echo(f'(dry-run) Se finalizarían {len(reservas)} reservas.')
            return

        for r in reservas:
            r.estado = 'finalizada'
        db.session.commit()
        click.echo('')
        click.echo(f'✅ {len(reservas)} reservas finalizadas.')

    # ============================================================
    # usuarios:listar
    # ============================================================
    @app.cli.command('usuarios:listar')
    @click.option('--empresa-id', type=int, default=None,
                  help='Filtrar por empresa (por defecto: todas).')
    def listar_usuarios(empresa_id):
        """Lista usuarios con su estado de verificación y rol."""
        from app.models import Usuario

        q = Usuario.query
        if empresa_id:
            q = q.filter(Usuario.id_empresa == empresa_id)
        usuarios = q.order_by(Usuario.id_empresa, Usuario.username).all()

        if not usuarios:
            click.echo('No hay usuarios.')
            return

        click.echo('')
        click.echo(
            f'{"ID":>4}  {"Emp":>4}  {"Username":<32}  {"Nombre":<25}  '
            f'{"Verif":>6}  {"Activo":>7}  {"Super":>6}  {"Rol":<20}'
        )
        click.echo('-' * 130)
        for u in usuarios:
            rol_nombre = u.rol_rel.nombre if u.rol_rel else '—'
            click.echo(
                f'{u.id:>4}  {u.id_empresa:>4}  '
                f'{(u.username or "")[:32]:<32}  '
                f'{(u.nombre or "")[:25]:<25}  '
                f'{"si" if u.email_verificado else "NO":>6}  '
                f'{"si" if u.activo else "NO":>7}  '
                f'{"si" if u.es_super_admin else "":>6}  '
                f'{rol_nombre[:20]:<20}'
            )
        click.echo('')

    # ============================================================
    # usuarios:desbloquear
    # ============================================================
    @app.cli.command('usuarios:desbloquear')
    @click.option('--email', required=True,
                  help='Username del usuario (normalmente su email).')
    @click.option('--password', default=None,
                  help='Si se especifica, resetea la contraseña.')
    def desbloquear_usuario(email, password):
        """
        Verifica el email y activa un usuario. Útil en desarrollo
        para probar roles/permisos sin pasar por el flujo de mail.
        """
        from app.models import Usuario

        usuario = Usuario.query.filter_by(username=email).first()
        if not usuario:
            click.echo(f'❌ Usuario no encontrado: {email}', err=True)
            raise click.Abort()

        usuario.email_verificado = True
        usuario.activo = True

        if password:
            usuario.set_password(password)

        db.session.commit()

        click.echo('')
        click.echo(f'✅ Usuario "{usuario.username}" desbloqueado.')
        click.echo(f'   - email_verificado: True')
        click.echo(f'   - activo:           True')
        if password:
            click.echo(f'   - contraseña:       (la que especificaste)')
        click.echo('')

    # ============================================================
    # sistema:seed-tarifas
    # ============================================================
    @app.cli.command('sistema:seed-tarifas')
    def seed_tarifas_cmd():
        """Crea las tarifas por defecto (idempotente)."""
        from app.models import Tarifa

        tarifas_data = [
            ('Hasta 100 socios', 0, 100, 5000, 1),
            ('101 a 500 socios', 101, 500, 10000, 2),
            ('501 a 1000 socios', 501, 1000, 18000, 3),
            ('Más de 1000 socios', 1001, None, 30000, 4),
        ]

        creadas = 0
        for nombre, desde, hasta, precio, orden in tarifas_data:
            if Tarifa.query.filter_by(nombre=nombre).first():
                continue
            db.session.add(Tarifa(
                nombre=nombre,
                socios_desde=desde,
                socios_hasta=hasta,
                precio_mensual=precio,
                activo=True,
                orden=orden,
            ))
            creadas += 1

        db.session.commit()
        click.echo(f'✅ {creadas} tarifas creadas.')

    # ============================================================
    # sistema:crear-messi-fc
    # ============================================================
    @app.cli.command('sistema:crear-messi-fc')
    def crear_messi_fc_cmd():
        """
        Crea el club fantasma Messi FC con el usuario sos_messi.
        Solo para uso del dueño del sistema.
        """
        from app.models import Empresa, Usuario

        empresa = Empresa.query.filter_by(es_club_fantasma=True).first()
        if empresa:
            click.echo(f'Club fantasma ya existe: {empresa.nombre} '
                       f'(ID #{empresa.id}).')
            return

        # Crear empresa fantasma
        empresa = Empresa(
            nombre='Messi FC',
            cuit='00-00000000-0',
            email='sistema@clubmanager.local',
            es_club_fantasma=True,
            activo=True,
            plan='pro',
            estado_pago='al_dia',
        )
        db.session.add(empresa)
        db.session.flush()

        # Crear usuario sos_messi
        user = Usuario.query.filter_by(username='sos_messi').first()
        if user:
            click.echo(f'❌ Ya existe un usuario "sos_messi" '
                       f'(empresa ID #{user.id_empresa}).')
            db.session.rollback()
            return

        user = Usuario(
            nombre='Sistema',
            username='sos_messi',
            activo=True,
            id_empresa=empresa.id,
            email_verificado=True,
            es_super_admin=True,
            es_super_admin_sistema=True,
        )
        user.set_password('messi2026')
        db.session.add(user)
        db.session.commit()

        click.echo('')
        click.echo('=' * 60)
        click.echo(f'✅ Club fantasma "{empresa.nombre}" creado (ID #{empresa.id})')
        click.echo(f'✅ Usuario sos_messi creado (contraseña: messi2026)')
        click.echo('=' * 60)
        click.echo('')
        click.echo('⚠️  CAMBIÁ LA CONTRASEÑA DESPUÉS DEL PRIMER LOGIN.')
        click.echo('')

    # ============================================================
    # chytapay:reintentar-pendientes
    # ============================================================
    @app.cli.command('chytapay:reintentar-pendientes')
    @click.option('--empresa-id', type=int, default=None,
                  help='Limitar a una empresa específica.')
    @click.option('--dry-run', is_flag=True,
                  help='Mostrar qué se haría sin ejecutar.')
    def chytapay_reintentar_pendientes(empresa_id, dry_run):
        """
        Reintenta generar CVUs para cuotas que:
          - Están pendientes.
          - No tienen chytapay_cvu.
          - Su socio tiene enviar_cuota_whatsapp=True.
          - La empresa puede usar ChytaPay.
        """
        from app.models import CuotaSocial, Socio, Empresa
        from app.services.chytapay_service import ChytaPayService

        q = (
            CuotaSocial.query
            .join(Socio, CuotaSocial.id_socio == Socio.id)
            .filter(
                CuotaSocial.estado == 'pendiente',
                CuotaSocial.chytapay_cvu.is_(None),
                Socio.enviar_cuota_whatsapp == True,
            )
        )
        if empresa_id:
            q = q.filter(Socio.id_empresa == empresa_id)

        cuotas = q.all()

        if not cuotas:
            click.echo('No hay cuotas pendientes de reintento.')
            return

        # Filtrar por empresas que pueden usar ChytaPay
        candidatas = []
        for c in cuotas:
            empresa = Empresa.query.get(c.socio.id_empresa)
            if empresa and empresa.puede_usar_chytapay:
                candidatas.append(c)

        if not candidatas:
            click.echo(
                f'Se encontraron {len(cuotas)} cuotas pendientes, '
                f'pero ninguna pertenece a un club habilitado para ChytaPay.'
            )
            return

        click.echo(
            f'Se reintentarán {len(candidatas)} cuota(s).'
        )
        if dry_run:
            for c in candidatas[:20]:
                click.echo(
                    f'  Cuota #{c.id} ({c.periodo}, '
                    f'${c.monto}, socio={c.socio.numero_socio})'
                )
            if len(candidatas) > 20:
                click.echo(f'  ... y {len(candidatas) - 20} más.')
            click.echo('')
            click.echo('(dry-run) No se ejecutó nada.')
            return

        ok = 0
        fail = 0
        for c in candidatas:
            exito, mensaje = ChytaPayService.reintentar_creacion(c)
            if exito:
                ok += 1
                click.echo(f'✅ Cuota #{c.id}: {mensaje}')
            else:
                fail += 1
                click.echo(f'❌ Cuota #{c.id}: {mensaje}')

        click.echo('')
        click.echo(f'Resumen: {ok} exitosas, {fail} fallidas.')

    @app.cli.command('chytapay:test-pago')
    @click.argument('cuota_id', type=int)
    def test_pago(cuota_id):
        from app.models import CuotaSocial
        from app.services.chytapay_service import ChytaPayService
        cuota = CuotaSocial.query.get_or_404(cuota_id)
        try:
            res = ChytaPayService.create_payment_request(cuota)
            click.echo(f"OK. Ref: {res.get('referenceId')}")
        except Exception as e:
            click.echo(f"Error: {e}")