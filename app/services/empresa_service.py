# app/services/empresa_service.py
"""
Servicio para crear empresas con todos sus datos base.

Usado por:
  - Comando CLI `flask empresa:crear`
  - Ruta pública `/signup`
"""

from app import db


class EmpresaService:

    @staticmethod
    def crear_empresa_completa(nombre, cuit, email_admin, password,
                                username_admin=None,
                                nombre_admin='Administrador',
                                direccion=None, telefono=None):
        """
        Crea una empresa con:
          - Empresa
          - Usuario admin (super admin)
          - Tipos y categorías de socio base
          - Categorías de productos base
          - Plan de cuentas completo
          - Caja Chica + Cartera de Cheques

        Lanza ValueError si el CUIT o el username ya existen.
        Retorna {'empresa': ..., 'admin': ...}.
        """
        from app.models import (
            Empresa, Usuario, TipoSocio, CategoriaSocio,
            CategoriaProducto, PlanCuenta, CuentaBancaria,
        )
        from app.cli import PLAN_SEED, CODIGOS_AUTOMATICOS

        cuit = (cuit or '').strip()
        email_admin = (email_admin or '').strip()
        username = (username_admin or email_admin).strip()
        nombre = (nombre or '').strip()

        if not nombre or not cuit or not email_admin or not password:
            raise ValueError('Faltan datos obligatorios.')

        if Empresa.query.filter_by(cuit=cuit).first():
            raise ValueError(f'Ya existe un club con el CUIT "{cuit}".')


        # ---------- 1. Empresa ----------
        from datetime import datetime, timedelta

        empresa = Empresa(
            nombre=nombre,
            cuit=cuit,
            direccion=(direccion or '').strip() or None,
            telefono=(telefono or '').strip() or None,
            email=email_admin,
            activo=True,
            plan='trial',
            estado_pago='trial',
            fecha_alta=datetime.utcnow(),
            fecha_vencimiento_prueba=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(empresa)
        db.session.flush()

        # ---------- 2. Usuario admin (super admin) ----------
        admin = Usuario(
            nombre=(nombre_admin or 'Administrador').strip(),
            username=username,
            activo=True,
            id_empresa=empresa.id,
            es_super_admin=True,
            id_rol=None,
            email_verificado=False,
        )
        admin.set_password(password)
        db.session.add(admin)

        # ---------- 3. Tipos y categorías de socio base ----------
        for n in ('Individual', 'Familiar'):
            db.session.add(TipoSocio(
                nombre=n, id_empresa=empresa.id, activo=True,
            ))
        for n in ('Simple', 'Pleno', 'Vitalicio'):
            db.session.add(CategoriaSocio(
                nombre=n, id_empresa=empresa.id, activo=True,
            ))

        # ---------- 4. Categorías de producto base ----------
        for n in ('Remeras', 'Pantalones', 'Accesorios'):
            db.session.add(CategoriaProducto(
                nombre=n, id_empresa=empresa.id, activo=True,
            ))

        db.session.flush()

        # ---------- 5. Plan de cuentas ----------
        existentes = {}
        for codigo, nombre_c, tipo_c, codigo_padre in PLAN_SEED:
            id_padre = None
            if codigo_padre and codigo_padre in existentes:
                id_padre = existentes[codigo_padre].id

            c = PlanCuenta(
                codigo=codigo,
                nombre=nombre_c,
                tipo=tipo_c,
                id_padre=id_padre,
                id_empresa=empresa.id,
                activo=True,
                es_automatica=(codigo in CODIGOS_AUTOMATICOS),
            )
            db.session.add(c)
            db.session.flush()
            existentes[codigo] = c

        # ---------- 6. Caja Chica + Cartera de Cheques ----------
        plan_caja = existentes.get('1.1.1.1')
        plan_cartera = existentes.get('1.1.6')

        if plan_caja:
            db.session.add(CuentaBancaria(
                nombre='Caja Chica', tipo='efectivo',
                saldo_inicial=0, saldo_actual=0, activo=True,
                id_empresa=empresa.id, id_cuenta_contable=plan_caja.id,
            ))
        if plan_cartera:
            db.session.add(CuentaBancaria(
                nombre='Cartera de Cheques', tipo='cartera',
                saldo_inicial=0, saldo_actual=0, activo=True,
                id_empresa=empresa.id, id_cuenta_contable=plan_cartera.id,
            ))

        # ---------- 7. Rol Cobrador ----------
        from app.models import Rol
        rol_cobrador = Rol(
            nombre='Cobrador',
            id_empresa=empresa.id,
            activo=True
        )
        # Le damos permisos básicos (ver socios y cobrar). 
        # Asegurate de tener estos permisos creados en permisos.py
        rol_cobrador.set_permisos(['socios.ver', 'cuotas.ver', 'cobros.ver', 'cobros.editar'])
        db.session.add(rol_cobrador)

        return {'empresa': empresa, 'admin': admin}