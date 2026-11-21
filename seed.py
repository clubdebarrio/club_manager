# seed.py
"""
Carga inicial de datos del sistema + datos de prueba.

Idempotente: se puede correr varias veces sin duplicar datos.

Uso:
    python seed.py
    python reset.py   (recomendado: borra todo y re-seedea)
"""
from datetime import datetime, date

from app import create_app, db
from app.models import (
    Empresa, Socio, TipoSocio, CategoriaSocio, Usuario,
    PlanCuenta, CuentaBancaria, Proveedor, Producto,
    CategoriaProducto, Instalacion, ConfiguracionCuota,
)
from app.services.socio_service import SocioService
from app.cli import PLAN_SEED, CODIGOS_AUTOMATICOS


def _seed_empresa():
    empresa = Empresa.query.first()
    if empresa:
        print("Empresa ya existe.")
        return empresa
    empresa = Empresa(
        nombre="Club Deportivo Barrio",
        cuit="30-12345678-9",
        direccion="Calle Falsa 123",
        telefono="1234-5678",
        email="club@ejemplo.com",
    )
    db.session.add(empresa)
    db.session.commit()
    print("✓ Empresa creada.")
    return empresa


def _seed_usuario_admin(empresa):
    if Usuario.query.first():
        print("Usuarios ya existen.")
        return
    admin = Usuario(
        nombre="Administrador",
        username="admin",
        rol="admin",
        id_empresa=empresa.id,
        email_verificado=True,
        es_super_admin=True,
    )
    admin.set_password("admin123")
    db.session.add(admin)
    db.session.commit()
    print("✓ Usuario admin creado (admin / admin123).")


def _seed_tipos_y_categorias(empresa):
    for nombre in ['Activo', 'Familiar', 'Vitalicio']:
        if not TipoSocio.query.filter_by(
            nombre=nombre, id_empresa=empresa.id
        ).first():
            db.session.add(TipoSocio(nombre=nombre, id_empresa=empresa.id))
    for nombre in ['Pleno', 'Adherente', 'Cadete', 'Infantil']:
        if not CategoriaSocio.query.filter_by(
            nombre=nombre, id_empresa=empresa.id
        ).first():
            db.session.add(CategoriaSocio(nombre=nombre, id_empresa=empresa.id))
    db.session.commit()
    print("✓ Tipos y categorías de socio.")


def _seed_categorias_producto(empresa):
    for nombre in ['Remeras', 'Pantalones', 'Accesorios', 'Calzado']:
        if not CategoriaProducto.query.filter_by(
            nombre=nombre, id_empresa=empresa.id
        ).first():
            db.session.add(
                CategoriaProducto(nombre=nombre, id_empresa=empresa.id)
            )
    db.session.commit()
    print("✓ Categorías de producto.")


def _seed_socios(empresa):
    if Socio.query.count() > 0:
        print("Socios ya existen.")
        return

    tipo_activo = TipoSocio.query.filter_by(
        nombre='Activo', id_empresa=empresa.id
    ).first()
    tipo_familiar = TipoSocio.query.filter_by(
        nombre='Familiar', id_empresa=empresa.id
    ).first()
    cat_pleno = CategoriaSocio.query.filter_by(
        nombre='Pleno', id_empresa=empresa.id
    ).first()
    cat_adherente = CategoriaSocio.query.filter_by(
        nombre='Adherente', id_empresa=empresa.id
    ).first()
    cat_cadete = CategoriaSocio.query.filter_by(
        nombre='Cadete', id_empresa=empresa.id
    ).first()

    socios_data = [
        # (apellido, nombre, tipo_doc, nro_doc, email, tel, id_tipo, id_cat)
        ("Pérez", "Juan Carlos", "DNI", "12345678",
         "juan@mail.com", "1111-1111", tipo_activo.id, cat_pleno.id),
        ("Gómez", "María", "DNI", "87654321",
         "maria@mail.com", "2222-2222", tipo_familiar.id, cat_adherente.id),
        ("López", "Carlos", "DNI", "11223344",
         "carlos@mail.com", "3333-3333", tipo_activo.id, cat_adherente.id),
        ("Fernández", "Ana", "DNI", "55667788",
         "ana@mail.com", "4444-4444", tipo_activo.id, cat_pleno.id),
        ("Rodríguez", "Diego", "DNI", "99887766",
         "diego@mail.com", "5555-5555", tipo_familiar.id, cat_pleno.id),
        ("Martínez", "Lucía", "DNI", "44556677",
         "lucia@mail.com", "6666-6666", tipo_activo.id, cat_cadete.id),
    ]

    for (apellido, nombre, tipo_doc, nro_doc, email, tel,
         id_tipo, id_cat) in socios_data:
        numero_socio = SocioService.generar_numero_socio(empresa.id)
        db.session.add(Socio(
            numero_socio=numero_socio,
            nombre=nombre,
            apellido=apellido,
            tipo_documento=tipo_doc,
            nro_documento=nro_doc,
            email=email,
            telefono=tel,
            id_tipo_socio=id_tipo,
            id_categoria_socio=id_cat,
            id_empresa=empresa.id,
            fecha_alta=datetime.utcnow(),
        ))
    db.session.commit()
    print(f"✓ {len(socios_data)} socios creados.")


def _seed_plan_cuentas(empresa):
    existentes = {
        c.codigo: c
        for c in PlanCuenta.query.filter_by(id_empresa=empresa.id).all()
    }
    creadas = 0
    for codigo, nombre, tipo, codigo_padre in PLAN_SEED:
        es_auto = codigo in CODIGOS_AUTOMATICOS
        if codigo in existentes:
            if existentes[codigo].es_automatica != es_auto:
                existentes[codigo].es_automatica = es_auto
            continue
        id_padre = None
        if codigo_padre and codigo_padre in existentes:
            id_padre = existentes[codigo_padre].id
        nueva = PlanCuenta(
            codigo=codigo, nombre=nombre, tipo=tipo,
            id_padre=id_padre, id_empresa=empresa.id,
            activo=True, es_automatica=es_auto,
        )
        db.session.add(nueva)
        db.session.flush()
        existentes[codigo] = nueva
        creadas += 1
    db.session.commit()
    print(f"✓ Plan de cuentas: {creadas} cuentas creadas.")


def _seed_cuentas_bancarias(empresa):
    # --- Caja Chica ---
    if not CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, nombre='Caja Chica'
    ).first():
        plan = PlanCuenta.query.filter_by(
            id_empresa=empresa.id, codigo='1.1.1.1'
        ).first()
        if plan:
            db.session.add(CuentaBancaria(
                nombre='Caja Chica', tipo='efectivo',
                saldo_inicial=0, saldo_actual=0, activo=True,
                id_empresa=empresa.id, id_cuenta_contable=plan.id,
            ))
            db.session.commit()
            print("✓ Caja Chica creada.")

    # --- Cartera de Cheques ---
    if not CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, nombre='Cartera de Cheques'
    ).first():
        plan = PlanCuenta.query.filter_by(
            id_empresa=empresa.id, codigo='1.1.6'
        ).first()
        if plan:
            db.session.add(CuentaBancaria(
                nombre='Cartera de Cheques', tipo='cartera',
                saldo_inicial=0, saldo_actual=0, activo=True,
                id_empresa=empresa.id, id_cuenta_contable=plan.id,
            ))
            db.session.commit()
            print("✓ Cartera de Cheques creada.")

    # --- Banco Nación CC ---
    if not CuentaBancaria.query.filter_by(
        id_empresa=empresa.id, nombre='Banco Nación CC'
    ).first():
        plan = PlanCuenta.query.filter_by(
            id_empresa=empresa.id, codigo='1.1.1.2'
        ).first()
        if not plan:
            # No existe 1.1.1.2 por default en el plan, la creamos
            padre_1_1_1 = PlanCuenta.query.filter_by(
                id_empresa=empresa.id, codigo='1.1.1'
            ).first()
            plan = PlanCuenta(
                codigo='1.1.1.2', nombre='Banco Nación CC',
                tipo='Activo', id_padre=padre_1_1_1.id if padre_1_1_1 else None,
                id_empresa=empresa.id, activo=True, es_automatica=False,
            )
            db.session.add(plan)
            db.session.flush()

        db.session.add(CuentaBancaria(
            nombre='Banco Nación CC', tipo='banco',
            saldo_inicial=0, saldo_actual=0, activo=True,
            numero_cuenta='1234567890',
            cbu='0110000000000000000000',
            id_empresa=empresa.id, id_cuenta_contable=plan.id,
        ))
        db.session.commit()
        print("✓ Banco Nación CC creado.")


def _seed_proveedores(empresa):
    if Proveedor.query.filter_by(id_empresa=empresa.id).count() > 0:
        print("Proveedores ya existen.")
        return
    proveedores = [
        ('Distribuidora Deportiva SA', '30-11111111-1', 'Responsable Inscripto'),
        ('Indumentaria Sur', '30-22222222-2', 'Monotributo'),
        ('Papelería Central', '30-33333333-3', 'Responsable Inscripto'),
    ]
    for nombre, cuit, condicion in proveedores:
        db.session.add(Proveedor(
            nombre=nombre, cuit=cuit, condicion_iva=condicion,
            id_empresa=empresa.id, activo=True,
        ))
    db.session.commit()
    print(f"✓ {len(proveedores)} proveedores creados.")


def _seed_productos(empresa):
    if Producto.query.filter_by(id_empresa=empresa.id).count() > 0:
        print("Productos ya existen.")
        return
    productos = [
        # (nombre, categoria, precio_venta, precio_costo, stock, stock_min)
        ('Remera titular', 'Remeras', 18000, 9000, 20, 5),
        ('Remera alternativa', 'Remeras', 18000, 9000, 15, 5),
        ('Pantalón corto', 'Pantalones', 22000, 12000, 10, 3),
        ('Campera de abrigo', 'Pantalones', 45000, 28000, 8, 2),
        ('Gorra con escudo', 'Accesorios', 12000, 6000, 25, 5),
        ('Bufanda', 'Accesorios', 8000, 4000, 30, 10),
        ('Medias deportivas', 'Calzado', 5000, 2500, 40, 10),
        ('Botines nº 42', 'Calzado', 85000, 55000, 3, 1),
    ]
    for nombre, cat, pv, pc, sa, sm in productos:
        db.session.add(Producto(
            nombre=nombre, categoria=cat,
            precio_venta=pv, precio_costo=pc,
            stock_actual=sa, stock_minimo=sm,
            activo=True, id_empresa=empresa.id,
        ))
    db.session.commit()
    print(f"✓ {len(productos)} productos creados.")


def _seed_instalaciones(empresa):
    if Instalacion.query.filter_by(id_empresa=empresa.id).count() > 0:
        print("Instalaciones ya existen.")
        return
    instalaciones = [
        ('Cancha de Fútbol 5', 'Cancha de césped sintético', 10, 8000),
        ('Cancha de Paddle', 'Cancha techada con iluminación', 4, 5000),
        ('Salón de Eventos', 'Salón con capacidad para 100 personas', 100, 15000),
    ]
    for nombre, desc, cap, precio in instalaciones:
        db.session.add(Instalacion(
            nombre=nombre, descripcion=desc, capacidad=cap,
            precio_por_hora=precio, activo=True, id_empresa=empresa.id,
        ))
    db.session.commit()
    print(f"✓ {len(instalaciones)} instalaciones creadas.")


def _seed_configuraciones_cuota(empresa):
    if ConfiguracionCuota.query.filter_by(id_empresa=empresa.id).count() > 0:
        print("Configuraciones de cuota ya existen.")
        return

    # Buscar tipos y categorías
    tipos = {
        t.nombre: t.id for t in TipoSocio.query.filter_by(
            id_empresa=empresa.id
        ).all()
    }
    cats = {
        c.nombre: c.id for c in CategoriaSocio.query.filter_by(
            id_empresa=empresa.id
        ).all()
    }

    # (tipo, categoria, monto)
    configs = [
        ('Activo', 'Pleno', 8000),
        ('Activo', 'Adherente', 6000),
        ('Activo', 'Cadete', 4000),
        ('Activo', 'Infantil', 2500),
        ('Familiar', 'Pleno', 12000),
        ('Familiar', 'Adherente', 10000),
        ('Vitalicio', 'Pleno', 0),
    ]

    creadas = 0
    for tipo_nombre, cat_nombre, monto in configs:
        if tipo_nombre not in tipos or cat_nombre not in cats:
            continue
        db.session.add(ConfiguracionCuota(
            id_tipo_socio=tipos[tipo_nombre],
            id_categoria_socio=cats[cat_nombre],
            monto=monto,
            fecha_desde=date.today().replace(day=1),
            fecha_hasta=None,
            id_empresa=empresa.id,
        ))
        creadas += 1
    db.session.commit()
    print(f"✓ {creadas} configuraciones de cuota creadas.")


def seed():
    app = create_app()
    with app.app_context():
        print()
        empresa = _seed_empresa()
        _seed_usuario_admin(empresa)
        _seed_tipos_y_categorias(empresa)
        _seed_categorias_producto(empresa)
        _seed_socios(empresa)
        _seed_plan_cuentas(empresa)
        _seed_cuentas_bancarias(empresa)
        _seed_proveedores(empresa)
        _seed_productos(empresa)
        _seed_instalaciones(empresa)
        _seed_configuraciones_cuota(empresa)
        print()
        print("✅ Seed completado exitosamente.")


if __name__ == '__main__':
    seed()