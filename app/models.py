from app import db
from datetime import datetime
from dateutil.relativedelta import relativedelta
from werkzeug.security import generate_password_hash, check_password_hash

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), nullable=False)  # ← sin unique global
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default='admin')
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    email_verificado = db.Column(db.Boolean, default=False, nullable=False)
    es_super_admin = db.Column(db.Boolean, default=False, nullable=False)
    es_super_admin_sistema = db.Column(db.Boolean, default=False, nullable=False)
    id_rol = db.Column(db.Integer, db.ForeignKey('roles.id'), nullable=True)

    empresa = db.relationship('Empresa', backref='usuarios')
    rol_rel = db.relationship('Rol', foreign_keys=[id_rol])

    __table_args__ = (
        db.UniqueConstraint('id_empresa', 'username',
                            name='uq_usuarios_empresa_username'),
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def tiene_permiso(self, clave):
        """
        True si el usuario puede ejecutar la acción asociada al permiso.

        - Super admin: siempre True.
        - Usuario con rol: depende del rol.
        - Usuario sin rol y sin super admin: False.
        """
        if self.es_super_admin:
            return True
        if self.rol_rel:
            return self.rol_rel.tiene_permiso(clave)
        return False

    def puede_ver(self, modulo):
        return self.tiene_permiso(f'{modulo}.ver')

    def puede_editar(self, modulo):
        return self.tiene_permiso(f'{modulo}.editar')

    def __repr__(self):
        return f'<Usuario {self.username}>'

class Rol(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    # Permisos guardados como JSON array de strings:
    # '["socios.ver", "socios.editar", "cuotas.ver"]'
    permisos = db.Column(db.Text, nullable=False, default='[]')
    activo = db.Column(db.Boolean, default=True, nullable=False)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('id_empresa', 'nombre',
                            name='uq_roles_empresa_nombre'),
    )

    empresa = db.relationship('Empresa', backref='roles')

    @property
    def permisos_list(self):
        """Devuelve la lista de permisos activos."""
        import json
        try:
            return json.loads(self.permisos or '[]')
        except Exception:
            return []

    def tiene_permiso(self, clave):
        """True si el rol tiene el permiso dado."""
        return clave in self.permisos_list

    def set_permisos(self, lista):
        """Guarda la lista de permisos (deduplicada y ordenada)."""
        import json
        self.permisos = json.dumps(sorted(set(lista)))

    def __repr__(self):
        return f'<Rol {self.nombre} ({len(self.permisos_list)} permisos)>'
    
class Empresa(db.Model):
    __tablename__ = 'empresa'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    cuit = db.Column(db.String(20), unique=True, nullable=False)
    direccion = db.Column(db.String(200))
    telefono = db.Column(db.String(20))
    email = db.Column(db.String(100))
    logo = db.Column(db.String(200))

    # --- Estado de suscripción ---
    activo = db.Column(db.Boolean, default=True, nullable=False)
    es_club_fantasma = db.Column(db.Boolean, default=False, nullable=False)
    trial_usado = db.Column(db.Boolean, default=False, nullable=False)
    fecha_alta = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    fecha_vencimiento_prueba = db.Column(db.DateTime, nullable=True)
    plan = db.Column(
        db.String(20), default='trial', nullable=False
    )
    # 'trial' | 'basico' | 'pro' | 'cancelado'
    estado_pago = db.Column(
        db.String(20), default='trial', nullable=False
    )
    # 'trial' | 'al_dia' | 'vencido' | 'suspendido'
    id_tarifa = db.Column(
        db.Integer, db.ForeignKey('tarifas.id'), nullable=True
    )
    socios_al_cierre = db.Column(db.Integer, nullable=True)

    socios = db.relationship('Socio', back_populates='empresa', lazy=True)
    tarifa = db.relationship('Tarifa', foreign_keys=[id_tarifa])

    @property
    def trial_vencido(self):
        if self.es_club_fantasma:
            return False
        if self.plan != 'trial':
            return False
        if not self.fecha_vencimiento_prueba:
            return False
        return datetime.utcnow() > self.fecha_vencimiento_prueba

    @property
    def dias_restantes_trial(self):
        if self.plan != 'trial' or not self.fecha_vencimiento_prueba:
            return None
        delta = self.fecha_vencimiento_prueba - datetime.utcnow()
        return delta.days

    @property
    def puede_escribir(self):
        """False si la empresa está suspendida o con trial vencido."""
        if self.es_club_fantasma:
            return True
        if self.estado_pago == 'suspendido':
            return False
        if self.trial_vencido:
            return False
        return True

    @property
    def puede_usar_chytapay(self):
        """
        ChytaPay NO está disponible en trial ni en clubes suspendidos.
        El club fantasma (Messi FC) siempre puede.
        """
        if self.es_club_fantasma:
            return True
        if self.plan == 'trial':
            return False
        if self.estado_pago != 'al_dia':
            return False
        return True
    
    def __repr__(self):
        return f'<Empresa {self.nombre}>'
    
class TipoSocio(db.Model):
    __tablename__ = 'tipo_socio'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(200))
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    empresa = db.relationship('Empresa', backref='tipos_socio')
    socios = db.relationship('Socio', foreign_keys='Socio.id_tipo_socio', backref='tipo_socio_rel')

    def __repr__(self):
        return f'<TipoSocio {self.nombre}>'

class CategoriaSocio(db.Model):
    __tablename__ = 'categoria_socio'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    descripcion = db.Column(db.String(200))
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    empresa = db.relationship('Empresa', backref='categorias_socio')
    socios = db.relationship('Socio', foreign_keys='Socio.id_categoria_socio', backref='categoria_socio_rel')

    def __repr__(self):
        return f'<CategoriaSocio {self.nombre}>'

class Socio(db.Model):
    __tablename__ = 'socios'
    id = db.Column(db.Integer, primary_key=True)
    numero_socio = db.Column(db.String(20), nullable=False)
    nombre = db.Column(db.String(80), nullable=False)
    apellido = db.Column(db.String(80), nullable=False)
    tipo_documento = db.Column(db.String(10), nullable=False)
    nro_documento = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100))
    telefono = db.Column(db.String(20))
    fecha_alta = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_baja = db.Column(db.DateTime, nullable=True)
    fecha_nacimiento = db.Column(db.Date, nullable=True) 
    id_tipo_socio = db.Column(db.Integer, db.ForeignKey('tipo_socio.id'), nullable=False)
    id_categoria_socio = db.Column(db.Integer, db.ForeignKey('categoria_socio.id'), nullable=False)
    id_familia = db.Column(db.Integer, db.ForeignKey('socios.id'), nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    enviar_cuota_whatsapp = db.Column(db.Boolean, default=False)
    foto_actualizada_at = db.Column(db.DateTime, nullable=True)    
    empresa = db.relationship('Empresa', back_populates='socios')
    familia = db.relationship('Socio', remote_side=[id], backref='miembros_familia')

    __table_args__ = (
        db.UniqueConstraint('id_empresa', 'numero_socio',
                            name='uq_socios_empresa_numero_socio'),
        db.UniqueConstraint('id_empresa', 'nro_documento',
                            name='uq_socios_empresa_nro_documento'),
    )

    @property
    def tiene_foto(self):
        return self.foto_actualizada_at is not None

    def __repr__(self):
        return f'<Socio {self.apellido}, {self.nombre}>'
# Modificar ConfiguracionCuota
class ConfiguracionCuota(db.Model):
    __tablename__ = 'configuracion_cuota'
    id = db.Column(db.Integer, primary_key=True)
    id_tipo_socio = db.Column(db.Integer, db.ForeignKey('tipo_socio.id'), nullable=False)
    id_categoria_socio = db.Column(db.Integer, db.ForeignKey('categoria_socio.id'), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_desde = db.Column(db.Date, nullable=False)
    fecha_hasta = db.Column(db.Date, nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    tipo_socio = db.relationship('TipoSocio')
    categoria_socio = db.relationship('CategoriaSocio')
    empresa = db.relationship('Empresa', backref='configuraciones_cuota')

    def __repr__(self):
        return f'<ConfigCuota {self.tipo_socio.nombre}-{self.categoria_socio.nombre}: ${self.monto}>'

class CuotaSocial(db.Model):
    __tablename__ = 'cuota_social'
    id = db.Column(db.Integer, primary_key=True)
    id_socio = db.Column(db.Integer, db.ForeignKey('socios.id'), nullable=False)
    periodo = db.Column(db.String(7), nullable=False)  # formato 'YYYY-MM' (ej. '2026-07')
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    detalle_adicionales = db.Column(db.String(500), nullable=True)
    fecha_vencimiento = db.Column(db.Date, nullable=False)
    fecha_pago = db.Column(db.DateTime, nullable=True)
    estado = db.Column(db.String(20), nullable=False, default='pendiente')  # pendiente, pagada, vencida, 'cancelada'
    # Datos para integración con ChytaPay (opcional)
    chytapay_cvu = db.Column(db.String(50), nullable=True)  # CVU asignado para este pago
    chytapay_pago_id = db.Column(db.String(100), nullable=True)  # ID del pago en ChytaPay
    
    socio = db.relationship('Socio', backref='cuotas')
    
    def __repr__(self):
        return f'<Cuota {self.socio.apellido} {self.periodo} - {self.estado}>'

class Instalacion(db.Model):
    __tablename__ = 'instalaciones'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(500))
    capacidad = db.Column(db.Integer, nullable=False, default=1)

    # Tipo de cobro: 'por_hora' | 'por_uso'
    tipo_cobro = db.Column(
        db.String(20), nullable=False, default='por_hora'
    )

    # Precios. Solo uno de los dos es obligatorio según tipo_cobro.
    # Se permiten nullables para simplificar la migración.
    precio_por_hora = db.Column(db.Numeric(10, 2), nullable=True)
    precio_por_uso = db.Column(db.Numeric(10, 2), nullable=True)

    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=False
    )

    empresa = db.relationship('Empresa', backref='instalaciones')
    reservas = db.relationship('Reserva', backref='instalacion', lazy=True)

    @property
    def precio_sugerido(self):
        """Precio unitario según el tipo de cobro."""
        if self.tipo_cobro == 'por_uso':
            return self.precio_por_uso
        return self.precio_por_hora

    def __repr__(self):
        return f'<Instalacion {self.nombre} ({self.tipo_cobro})>'

class Reserva(db.Model):
    __tablename__ = 'reservas'
    id = db.Column(db.Integer, primary_key=True)
    id_instalacion = db.Column(db.Integer, db.ForeignKey('instalaciones.id'), nullable=False)
    id_socio = db.Column(db.Integer, db.ForeignKey('socios.id'), nullable=True)
    fecha_inicio = db.Column(db.DateTime, nullable=False)
    fecha_fin = db.Column(db.DateTime, nullable=False)
    motivo = db.Column(db.String(200), nullable=False)
    estado = db.Column(db.String(20), nullable=False, default='pendiente')
    monto_total = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_reserva = db.Column(db.DateTime, default=datetime.utcnow)
    observaciones = db.Column(db.String(500))

    socio = db.relationship('Socio', backref='reservas')
    # pagos: viene por backref desde PagoAlquiler (NO declarar acá)

    @property
    def total_pagado(self):
        """Suma de los pagos (sin contar devoluciones)."""
        from decimal import Decimal
        return sum(
            (p.monto for p in self.pagos if p.tipo == 'pago'),
            Decimal('0')
        )

    @property
    def total_devuelto(self):
        """Suma de las devoluciones registradas."""
        from decimal import Decimal
        return sum(
            (p.monto for p in self.pagos if p.tipo == 'devolucion'),
            Decimal('0')
        )

    @property
    def saldo_pendiente(self):
        """Lo que falta cobrar (pagos - devoluciones)."""
        from decimal import Decimal
        return (self.monto_total or Decimal('0')) - self.total_pagado

    @property
    def esta_pagada(self):
        return self.saldo_pendiente <= 0

    ESTADOS_VALIDOS = ('confirmada', 'cancelada', 'finalizada')

    @property
    def estado_efectivo(self):
        """
        Estado real de la reserva, considerando el paso del tiempo.

        - Si el estado guardado es 'cancelada' o 'finalizada', se respeta.
        - Si el estado es 'confirmada' pero la fecha_fin ya pasó,
          devuelve 'finalizada' (aunque en la DB siga como confirmada).
        - Si la reserva es 'pendiente' (legacy), se trata como 'confirmada'.
        """
        if self.estado == 'pendiente':
            return 'confirmada'
        if self.estado in ('cancelada', 'finalizada'):
            return self.estado
        if self.fecha_fin and self.fecha_fin < datetime.utcnow():
            return 'finalizada'
        return 'confirmada'

    @property
    def esta_activa(self):
        """True si la reserva está confirmada y todavía no finalizó."""
        return self.estado_efectivo == 'confirmada'

    def __repr__(self):
        return f'<Reserva {self.id} - {self.instalacion.nombre}>'

class PagoAlquiler(db.Model):
    __tablename__ = 'pagos_alquiler'
    id = db.Column(db.Integer, primary_key=True)
    id_reserva = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=False)
    id_cuenta = db.Column(db.Integer, db.ForeignKey('cuentas_bancarias.id'), nullable=True)
    id_cuenta_contable = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=True)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_pago = db.Column(db.DateTime, default=datetime.utcnow)
    metodo_pago = db.Column(db.String(20), nullable=False)
    comprobante = db.Column(db.String(100), nullable=True)
    observaciones = db.Column(db.String(200))
    tipo = db.Column(
        db.String(20), nullable=False, default='pago'
    )  # 'pago' | 'devolucion'

    reserva = db.relationship('Reserva', backref='pagos')
    cuenta = db.relationship('CuentaBancaria', foreign_keys=[id_cuenta])
    cuenta_contable = db.relationship('PlanCuenta', foreign_keys=[id_cuenta_contable])

    def __repr__(self):
        return f'<PagoAlquiler {self.id} - ${self.monto}>'
    
class CuentaBancaria(db.Model):
    __tablename__ = 'cuentas_bancarias'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    saldo_inicial = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    saldo_actual = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    numero_cuenta = db.Column(db.String(50), nullable=True)
    cbu = db.Column(db.String(50), nullable=True)
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    id_cuenta_contable = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=True)

    empresa = db.relationship('Empresa', backref='cuentas')
    movimientos = db.relationship('Movimiento', backref='cuenta', lazy=True)
    cuenta_contable = db.relationship('PlanCuenta', foreign_keys=[id_cuenta_contable])  # <--- NUEVO

class Movimiento(db.Model):
    __tablename__ = 'movimientos'
    id = db.Column(db.Integer, primary_key=True)
    id_cuenta = db.Column(db.Integer, db.ForeignKey('cuentas_bancarias.id'), nullable=False)
    id_cuenta_contable = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tipo = db.Column(db.String(10), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    descripcion = db.Column(db.String(200), nullable=False)
    metodo_pago = db.Column(db.String(20), nullable=False)
    comprobante = db.Column(db.String(100), nullable=True)
    conciliado = db.Column(db.Boolean, default=False)
    fecha_conciliacion = db.Column(db.DateTime, nullable=True)
    id_cheque = db.Column(db.Integer, db.ForeignKey('cheques.id'), nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    id_operacion = db.Column(db.String(36), nullable=True, index=True)
    anulado = db.Column(db.Boolean, default=False, nullable=False)
    es_anulacion = db.Column(db.Boolean, default=False, nullable=False)
    fecha_anulacion = db.Column(db.DateTime, nullable=True)
    motivo_anulacion = db.Column(db.String(200), nullable=True)
    id_movimiento_anulacion = db.Column(
        db.Integer, db.ForeignKey('movimientos.id'), nullable=True
    )    

    empresa = db.relationship('Empresa', backref='movimientos')
    cheque = db.relationship('Cheque', backref='movimiento', foreign_keys=[id_cheque])
    cuenta_contable = db.relationship('PlanCuenta', foreign_keys=[id_cuenta_contable])

    def __repr__(self):
        return f'<Movimiento {self.tipo} ${self.monto} - {self.descripcion}>'
    @property
    def es_movimiento_espejo(self):
        """Heurística: transferencias internas generan un espejo."""
        return (
            self.descripcion
            and self.descripcion.startswith('Transferencia interna desde')
        )

    @property
    def puede_anularse(self):
        """True si este movimiento puede ser anulado."""
        if self.anulado:
            return False
        if self.es_anulacion:
            return False
        if self.id_cheque:
            return False
        if self.es_movimiento_espejo:
            return False
        # Los movimientos con id_operacion SÍ se pueden anular,
        # pero arrastran toda la operación completa (todos los splits).
        return True

    @property
    def motivo_no_anulable(self):
        """Mensaje explicando por qué no se puede anular."""
        if self.anulado:
            return 'Este movimiento ya fue anulado.'
        if self.es_anulacion:
            return 'Este movimiento es una anulación, no se puede anular a su vez.'
        if self.id_cheque:
            return (
                'Este movimiento está asociado a un cheque. '
                'Anulalo desde el módulo de Cheques.'
            )
        if self.es_movimiento_espejo:
            return (
                'Este movimiento es parte de una transferencia interna. '
                'Anulá la transferencia desde la cuenta origen.'
            )
        return None

class Cheque(db.Model):
    __tablename__ = 'cheques'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), nullable=False)
    banco_emisor = db.Column(db.String(100), nullable=False)
    titular = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_emision = db.Column(db.Date, nullable=False)
    fecha_cobro = db.Column(db.Date, nullable=True)
    estado = db.Column(db.String(20), nullable=False, default='en_cartera')  # 'en_cartera', 'depositado', 'cobrado', 'rechazado', 'vencido'
    tipo = db.Column(db.String(10), nullable=False)  # 'emitido' (nosotros damos un cheque), 'recibido' (nos pagan con cheque)
    observaciones = db.Column(db.String(200), nullable=True)
    id_cheque_reemplazo = db.Column(db.Integer, db.ForeignKey('cheques.id'), nullable=True)
    motivo_reemplazo = db.Column(db.String(200), nullable=True)    
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    empresa = db.relationship('Empresa', backref='cheques')

    @property
    def es_al_dia(self):
        """
        True si el cheque no tiene fecha de cobro futura.
        Se considera 'al día' si fecha_cobro es igual a fecha_emision
        o si fecha_cobro es None (por compatibilidad con datos viejos).
        """
        if not self.fecha_cobro:
            return True
        return self.fecha_cobro <= self.fecha_emision

    @property
    def fecha_vencimiento(self):
        """
        Fecha en la que el cheque caduca y deja de poder cobrarse.
        Regla de negocio: 1 mes después de la fecha de cobro
        (o de emisión, si es al día).
        """
        base = self.fecha_cobro or self.fecha_emision
        if not base:
            return None
        return base + relativedelta(months=1)

    @property
    def esta_vencido(self):
        """
        True si el cheque pasó su fecha de vencimiento y todavía no fue
        cobrado ni pagado. Un cheque vencido ya no se puede cobrar.
        """
        if self.estado in ('cobrado', 'pagado', 'rechazado'):
            return False
        venc = self.fecha_vencimiento
        if not venc:
            return False
        from datetime import date
        return date.today() > venc

    @property
    def dias_para_vencer(self):
        """Días restantes hasta el vencimiento. Negativo si ya venció."""
        venc = self.fecha_vencimiento
        if not venc:
            return None
        from datetime import date
        return (venc - date.today()).days

    @property
    def fue_reemplazado(self):
        """True si este cheque ya fue reemplazado por otro."""
        return self.estado == 'reemplazado'

    @property
    def fue_cancelado_en_efectivo(self):
        """True si este cheque fue cancelado con efectivo/transferencia."""
        return self.estado in ('pagado_en_efectivo', 'cobrado_en_efectivo')

    def __repr__(self):
        return f'<Cheque {self.numero} - ${self.monto} ({self.estado})>'
    
class PlanCuenta(db.Model):
    __tablename__ = 'plan_cuentas'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(30), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    id_padre = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    activo = db.Column(db.Boolean, default=True)
    es_automatica = db.Column(db.Boolean, default=False, nullable=False)  

    __table_args__ = (
        db.UniqueConstraint('id_empresa', 'codigo', name='uq_plancuenta_empresa_codigo'),
    )

    padre = db.relationship('PlanCuenta', remote_side=[id], backref='subcuentas')
    empresa = db.relationship('Empresa', backref='plan_cuentas')

    MAX_NIVEL = 6
    NIVEL_AVISO = 5

    # Regla de negocio: el segundo nivel de Activo y Pasivo solo
    # admite "Corriente" (código .1) y "No Corriente" (código .2).
    SEGUNDO_NIVEL_FIJO = {
        'Activo': ['1', '2'],
        'Pasivo': ['1', '2'],
    }
    
    @property
    def es_hoja(self):
        return len(self.subcuentas) == 0

    @property
    def es_imputable(self):
        return self.activo and self.es_hoja

    @property
    def nivel(self):
        return self.codigo.count('.') + 1

    @property
    def naturaleza(self):
        if self.tipo in ('Activo', 'Gasto'):
            return 'deudora'
        return 'acreedora'

    @property
    def nombre_completo(self):
        indent = '    ' * (self.nivel - 1)
        return f"{self.codigo} {indent}{self.nombre}"

    @property
    def puede_tener_subcuentas(self):
        """
        True si esta cuenta admite que se le cuelguen subcuentas:
          - Estar activa.
          - No haber alcanzado el nivel máximo.
          - Si es Activo/Pasivo de nivel 1, no tener ya ambos hijos
            (.1 y .2) creados.
        """
        if not self.activo:
            return False
        if self.nivel >= self.MAX_NIVEL:
            return False

        fijos = self.SEGUNDO_NIVEL_FIJO.get(self.tipo)
        if self.nivel == 1 and fijos:
            # Contar cuántos de los códigos fijos ya existen
            existentes = set()
            for h in self.subcuentas:
                partes = (h.codigo or '').split('.')
                if partes:
                    existentes.add(partes[-1])
            if all(s in existentes for s in fijos):
                return False

        return True

    def siguiente_codigo_hijo(self):
        """
        Calcula el próximo código disponible para una subcuenta.
        - Para Activo/Pasivo de nivel 1, usa los códigos fijos .1 o .2
          (rellena el que falte, o None si ya están ambos).
        - Para el resto, usa max(hijos) + 1.
        Retorna None si la cuenta no admite subcuentas.
        """
        if not self.puede_tener_subcuentas:
            return None

        partes_hijos = []
        for h in self.subcuentas:
            partes = (h.codigo or '').split('.')
            if not partes:
                continue
            partes_hijos.append(partes[-1])

        # Regla de negocio: Activo/Pasivo nivel 1 => solo .1 y .2
        fijos = self.SEGUNDO_NIVEL_FIJO.get(self.tipo)
        if self.nivel == 1 and fijos:
            for s in fijos:
                if s not in partes_hijos:
                    return f"{self.codigo}.{s}"
            return None  # ya están ambos

        ultimos = []
        for p in partes_hijos:
            try:
                ultimos.append(int(p))
            except (ValueError, TypeError):
                continue

        siguiente = (max(ultimos) + 1) if ultimos else 1
        return f"{self.codigo}.{siguiente}"

    @classmethod
    def imputables(cls, empresa_id, tipos=None, solo_manuales=False):
        """
        Cuentas imputables (activas y hoja).
        - tipos: filtra por tipo contable.
        - solo_manuales: excluye las que se generan automáticamente
          desde un módulo (cuotas, ventas, alquileres, cheques).
        """
        q = cls.query.filter_by(id_empresa=empresa_id, activo=True)
        if tipos:
            q = q.filter(cls.tipo.in_(tipos))
        if solo_manuales:
            q = q.filter(cls.es_automatica == False)
        return [c for c in q.order_by(cls.codigo).all() if c.es_hoja]

    def __repr__(self):
        return f'<PlanCuenta {self.codigo} - {self.nombre}>'


class AsientoContable(db.Model):
    __tablename__ = 'asientos_contables'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    descripcion = db.Column(db.String(200), nullable=False)
    id_movimiento = db.Column(db.Integer, db.ForeignKey('movimientos.id'), nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    movimiento = db.relationship('Movimiento', backref='asiento')
    empresa = db.relationship('Empresa', backref='asientos')
    lineas = db.relationship('LineaAsiento', backref='asiento', lazy=True)

    def __repr__(self):
        return f'<Asiento {self.id} - {self.descripcion}>'


class LineaAsiento(db.Model):
    __tablename__ = 'lineas_asiento'
    id = db.Column(db.Integer, primary_key=True)
    id_asiento = db.Column(db.Integer, db.ForeignKey('asientos_contables.id'), nullable=False)
    id_cuenta = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=False)
    debe = db.Column(db.Numeric(10, 2), default=0)
    haber = db.Column(db.Numeric(10, 2), default=0)

    cuenta = db.relationship('PlanCuenta')

    def __repr__(self):
        return f'<LineaAsiento {self.id} - Cuenta {self.cuenta.codigo}>'
    

class ChytaPayToken(db.Model):
    """Token de acceso de ChytaPay. Uno por empresa."""
    __tablename__ = 'chytapay_tokens'
    id = db.Column(db.Integer, primary_key=True)
    id_empresa = db.Column(
        db.Integer,
        db.ForeignKey('empresa.id'),
        nullable=False,
        unique=True,   # ← una sola conexión por club
    )
    access_token = db.Column(db.String(500), nullable=False)
    refresh_token = db.Column(db.String(500), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    empresa = db.relationship('Empresa', backref='chytapay_token')

    def is_expired(self):
        return datetime.utcnow() >= self.expires_at

    def __repr__(self):
        return f'<ChytaPayToken empresa={self.id_empresa}>'

class ChytaPayPaymentRequest(db.Model):
    """Vincula nuestro ID de cuota con el referenceId de ChytaPay."""
    __tablename__ = 'chytapay_payment_requests'
    id = db.Column(db.Integer, primary_key=True)
    reference_id = db.Column(db.String(100), nullable=False, unique=True)  # El ID que enviamos a ChytaPay
    cuota_social_id = db.Column(db.Integer, db.ForeignKey('cuota_social.id'), nullable=False)
    chytapay_status = db.Column(db.String(50), nullable=True)  # pending, total_payment, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    intentos = db.Column(db.Integer, default=0, nullable=False)
    ultimo_error = db.Column(db.String(300), nullable=True)
    ultimo_intento = db.Column(db.DateTime, nullable=True)

    cuota = db.relationship('CuotaSocial', backref='chytapay_request')

class ChytaPayWebhookEvent(db.Model):
    """
    Log idempotente de webhooks recibidos de ChytaPay.
    Cada evento se procesa UNA sola vez, aunque ChytaPay reintente
    el mismo payload varias veces.
    """
    __tablename__ = 'chytapay_webhook_events'
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(150), nullable=False, unique=True)
    reference_id = db.Column(
        db.String(100), nullable=True, index=True
    )
    state = db.Column(db.String(50), nullable=True)
    payload = db.Column(db.Text, nullable=True)  # JSON serializado
    id_empresa = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=True
    )
    procesado = db.Column(db.Boolean, default=False, nullable=False)
    resultado = db.Column(db.String(200), nullable=True)
    error = db.Column(db.Text, nullable=True)
    fecha_recepcion = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    fecha_procesamiento = db.Column(db.DateTime, nullable=True)

    empresa = db.relationship('Empresa')

    def __repr__(self):
        return f'<ChytaPayWebhookEvent {self.event_id} ({self.state})>'

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(500))
    categoria = db.Column(db.String(50))  # Ej: Remeras, Pantalones, Accesorios
    precio_venta = db.Column(db.Numeric(10, 2), nullable=False)
    precio_costo = db.Column(db.Numeric(10, 2), nullable=True)  # Opcional
    stock_actual = db.Column(db.Integer, default=0)
    stock_minimo = db.Column(db.Integer, default=0)
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    foto_actualizada_at = db.Column(db.DateTime, nullable=True)
    
    @property
    def tiene_foto(self):
        return self.foto_actualizada_at is not None
        
    empresa = db.relationship('Empresa', backref='productos')
    detalles = db.relationship('DetalleVenta', backref='producto', lazy=True)
    
    def __repr__(self):
        return f'<Producto {self.nombre}>'

class PagoCompra(db.Model):
    __tablename__ = 'pagos_compra'
    id = db.Column(db.Integer, primary_key=True)
    id_compra = db.Column(db.Integer, db.ForeignKey('compras.id'), nullable=False)
    metodo = db.Column(db.String(20), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    fecha_pago = db.Column(db.DateTime, nullable=True)   # <-- NUEVO
    id_cuenta = db.Column(db.Integer, db.ForeignKey('cuentas_bancarias.id'), nullable=True)
    id_cheque = db.Column(db.Integer, db.ForeignKey('cheques.id'), nullable=True)
    observaciones = db.Column(db.String(200), nullable=True)

    compra = db.relationship('Compra', back_populates='pagos')
    cuenta = db.relationship('CuentaBancaria', foreign_keys=[id_cuenta])
    cheque = db.relationship('Cheque', foreign_keys=[id_cheque])

    def __repr__(self):
        return f'<PagoCompra {self.id} - {self.metodo} ${self.monto}>'
    
class Venta(db.Model):
    __tablename__ = 'ventas'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    id_socio = db.Column(db.Integer, db.ForeignKey('socios.id'), nullable=True)  # Opcional
    total = db.Column(db.Numeric(10, 2), nullable=False)
    metodo_pago = db.Column(db.String(20), nullable=False)  # efectivo, transferencia, cheque, debito, credito
    id_cuenta = db.Column(db.Integer, db.ForeignKey('cuentas_bancarias.id'), nullable=False)
    comprobante = db.Column(db.String(50), nullable=True)  # Número de ticket o factura
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    estado = db.Column(db.String(20), default='completada')  # completada, cancelada
    fecha_anulacion = db.Column(db.DateTime, nullable=True)
    motivo_anulacion = db.Column(db.String(200), nullable=True)    
    
    socio = db.relationship('Socio', backref='ventas')
    cuenta = db.relationship('CuentaBancaria', backref='ventas')
    empresa = db.relationship('Empresa', backref='ventas')
    detalles = db.relationship('DetalleVenta', backref='venta', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Venta {self.id} - ${self.total}>'

class DetalleVenta(db.Model):
    __tablename__ = 'detalle_ventas'
    id = db.Column(db.Integer, primary_key=True)
    id_venta = db.Column(db.Integer, db.ForeignKey('ventas.id'), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)  # Precio de venta al momento
    costo_unitario = db.Column(db.Numeric(10, 2), nullable=True)   # <--- NUEVO: costo al momento
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)  # cantidad * precio_unitario
        
    def __repr__(self):
        return f'<DetalleVenta {self.id} - {self.cantidad}x {self.producto.nombre}>'

class Compra(db.Model):
    __tablename__ = 'compras'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    proveedor = db.Column(db.String(100), nullable=True)      # legacy
    id_proveedor = db.Column(db.Integer, db.ForeignKey('proveedores.id'), nullable=True)  # NUEVO
    total = db.Column(db.Numeric(10, 2), nullable=False)
    metodo_pago = db.Column(db.String(20), nullable=True)
    id_cuenta = db.Column(db.Integer, db.ForeignKey('cuentas_bancarias.id'), nullable=True)
    id_cuenta_contable = db.Column(db.Integer, db.ForeignKey('plan_cuentas.id'), nullable=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    estado = db.Column(db.String(20), default='completada')
    fecha_anulacion = db.Column(db.DateTime, nullable=True)
    motivo_anulacion = db.Column(db.String(200), nullable=True)

    empresa = db.relationship('Empresa', backref='compras')
    cuenta = db.relationship('CuentaBancaria', foreign_keys=[id_cuenta])
    cuenta_contable = db.relationship('PlanCuenta', foreign_keys=[id_cuenta_contable])
    proveedor_rel = db.relationship('Proveedor', foreign_keys=[id_proveedor], backref='compras')
    detalles = db.relationship('DetalleCompra', backref='compra', lazy=True,
                               cascade='all, delete-orphan')

    pagos = db.relationship('PagoCompra', back_populates='compra',
                            lazy=True, cascade='all, delete-orphan')

    @property
    def proveedor_nombre(self):
        """Nombre a mostrar: prioriza la FK, cae al string legacy."""
        if self.proveedor_rel:
            return self.proveedor_rel.nombre
        return self.proveedor or ''

    @property
    def total_pagado(self):
        from decimal import Decimal
        return sum((p.monto for p in self.pagos), Decimal('0'))

    @property
    def total_notas_credito(self):
        """Suma de las notas de crédito activas (no anuladas)."""
        from decimal import Decimal
        return sum(
            (n.monto for n in self.notas_credito if not n.anulada),
            Decimal('0'),
        )

    @property
    def total_neto(self):
        """Total de la compra menos las notas de crédito."""
        from decimal import Decimal
        return (self.total or Decimal('0')) - self.total_notas_credito

    @property
    def saldo_pendiente(self):
        from decimal import Decimal
        return self.total_neto - self.total_pagado

    @property
    def saldo_a_favor(self):
        """Saldo a favor si pagamos más de lo que quedó neto."""
        from decimal import Decimal
        saldo = self.saldo_pendiente
        return -saldo if saldo < 0 else Decimal('0')

    @property
    def esta_pagada(self):
        return self.saldo_pendiente <= 0

    def __repr__(self):
        return f'<Compra {self.id} - ${self.total}>'

class DetalleCompra(db.Model):
    __tablename__ = 'detalle_compras'
    id = db.Column(db.Integer, primary_key=True)
    id_compra = db.Column(db.Integer, db.ForeignKey('compras.id'), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_compra = db.Column(db.Numeric(10, 2), nullable=False)  # Precio al momento de la compra
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)  # cantidad * precio_compra

    producto = db.relationship('Producto', backref='detalles_compra')

    def __repr__(self):
        return f'<DetalleCompra {self.id} - {self.cantidad}x {self.producto.nombre}>'

class CategoriaProducto(db.Model):
    __tablename__ = 'categoria_producto'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    activo = db.Column(db.Boolean, default=True)

    empresa = db.relationship('Empresa', backref='categorias_producto')

    def __repr__(self):
        return f'<CategoriaProducto {self.nombre}>'

class Proveedor(db.Model):
    __tablename__ = 'proveedores'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    cuit = db.Column(db.String(20), nullable=True)
    condicion_iva = db.Column(db.String(30), nullable=True)
    direccion = db.Column(db.String(200), nullable=True)
    telefono = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(100), nullable=True)
    notas = db.Column(db.String(500), nullable=True)
    activo = db.Column(db.Boolean, default=True)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    empresa = db.relationship('Empresa', backref='proveedores')

    def __repr__(self):
        return f'<Proveedor {self.nombre}>'

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    id_empresa = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=False
    )
    id_usuario = db.Column(
        db.Integer, db.ForeignKey('usuarios.id'), nullable=True
    )
    id_usuario_efectivo = db.Column(
        db.Integer, db.ForeignKey('usuarios.id'), nullable=True
    )
    timestamp = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    accion = db.Column(db.String(30), nullable=False)
    entidad = db.Column(db.String(50), nullable=False)
    entidad_id = db.Column(db.Integer, nullable=True)
    datos_antes = db.Column(db.JSON, nullable=True)
    datos_despues = db.Column(db.JSON, nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    nota = db.Column(db.String(500), nullable=True)

    __table_args__ = (
        db.Index('ix_audit_log_empresa_timestamp', 'id_empresa', 'timestamp'),
        db.Index('ix_audit_log_entidad', 'entidad', 'entidad_id'),
        db.Index('ix_audit_log_usuario', 'id_usuario'),
        db.Index('ix_audit_log_accion', 'accion'),
    )

    empresa = db.relationship('Empresa', foreign_keys=[id_empresa])
    usuario = db.relationship('Usuario', foreign_keys=[id_usuario])
    usuario_efectivo = db.relationship(
        'Usuario', foreign_keys=[id_usuario_efectivo]
    )

    def __repr__(self):
        return (
            f'<AuditLog {self.timestamp} {self.accion} '
            f'{self.entidad}#{self.entidad_id} u={self.id_usuario}>'
        )

class Tarifa(db.Model):
    __tablename__ = 'tarifas'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    socios_desde = db.Column(db.Integer, nullable=False)
    socios_hasta = db.Column(db.Integer, nullable=True)  # NULL = sin tope
    precio_mensual = db.Column(db.Numeric(10, 2), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    orden = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return f'<Tarifa {self.nombre}: ${self.precio_mensual}>'

class NotaCreditoCompra(db.Model):
    __tablename__ = 'notas_credito_compra'
    id = db.Column(db.Integer, primary_key=True)
    id_compra = db.Column(
        db.Integer, db.ForeignKey('compras.id'), nullable=False
    )
    fecha = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    motivo = db.Column(db.String(300), nullable=False)
    id_empresa = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=False
    )

    # --- Anulación ---
    anulada = db.Column(db.Boolean, default=False, nullable=False)
    fecha_anulacion = db.Column(db.DateTime, nullable=True)
    motivo_anulacion = db.Column(db.String(200), nullable=True)

    compra = db.relationship('Compra', backref='notas_credito')
    empresa = db.relationship('Empresa', backref='notas_credito_compra')

    def __repr__(self):
        return f'<NotaCreditoCompra #{self.id} - ${self.monto}>'


class DetalleNotaCredito(db.Model):
    __tablename__ = 'detalle_notas_credito'
    id = db.Column(db.Integer, primary_key=True)
    id_nota = db.Column(
        db.Integer, db.ForeignKey('notas_credito_compra.id'),
        nullable=False,
    )
    id_producto = db.Column(
        db.Integer, db.ForeignKey('productos.id'), nullable=False
    )
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    nota = db.relationship('NotaCreditoCompra', backref='detalles')
    producto = db.relationship('Producto')

    def __repr__(self):
        return f'<DetalleNotaCredito {self.cantidad}x {self.producto.nombre}>'

class Adicional(db.Model):
    __tablename__ = 'adicionales'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Numeric(10, 2), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    id_empresa = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    fecha_desde = db.Column(db.Date, nullable=False)
    fecha_hasta = db.Column(db.Date, nullable=True)

    empresa = db.relationship('Empresa', backref='adicionales')

    def __repr__(self):
        return f'<Adicional {self.nombre} - ${self.monto}>'

class SocioAdicional(db.Model):
    __tablename__ = 'socio_adicionales'
    id = db.Column(db.Integer, primary_key=True)
    id_socio = db.Column(db.Integer, db.ForeignKey('socios.id'), nullable=False)
    id_adicional = db.Column(db.Integer, db.ForeignKey('adicionales.id'), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    fecha_baja = db.Column(db.DateTime, nullable=True)

    # Relaciones
    adicional = db.relationship('Adicional')
    socio = db.relationship('Socio', backref='adicionales_asignados')

    __table_args__ = (
        db.UniqueConstraint('id_socio', 'id_adicional', name='uq_socio_adicional'),
    )