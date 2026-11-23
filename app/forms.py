from datetime import datetime, date
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, EmailField, DateField, IntegerField, 
    DecimalField, BooleanField, HiddenField, TextAreaField, DateTimeField,
    PasswordField, SubmitField,SelectMultipleField, widgets
)
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, ValidationError, EqualTo
from app.models import Socio, PlanCuenta
from flask_wtf.file import FileField, FileAllowed, FileRequired
import re
from app.utils import utc_to_local

class LoginForm(FlaskForm):
    username = StringField('Usuario', validators=[DataRequired(), Length(max=50)])
    password = PasswordField('Contraseña', validators=[DataRequired()])
    submit = SubmitField('Ingresar')

class EmpresaForm(FlaskForm):
    nombre = StringField('Nombre del Club', validators=[DataRequired(), Length(max=100)])
    cuit = StringField('CUIT', validators=[DataRequired(), Length(max=20)])
    direccion = StringField('Dirección', validators=[Length(max=200)])
    telefono = StringField('Teléfono', validators=[Length(max=20)])
    email = EmailField('Email', validators=[Optional(), Email()])
    logo = StringField('Logo (URL)', validators=[Optional(), Length(max=200)])

class SocioForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=80)])
    apellido = StringField('Apellido', validators=[DataRequired(), Length(max=80)])
    tipo_documento = SelectField('Tipo de Documento', choices=[
        ('DNI', 'DNI'),
        ('CI', 'Cédula de Identidad'),
        ('Pasaporte', 'Pasaporte'),
        ('Otro', 'Otro')
    ], validators=[DataRequired()])
    nro_documento = StringField('Número de Documento', validators=[DataRequired(), Length(max=20)])
    email = EmailField('Email', validators=[Optional(), Email(), Length(max=100)])
    telefono = StringField('Teléfono', validators=[Length(max=20)])
    fecha_nacimiento = DateField('Fecha de Nacimiento', validators=[Optional()])
    id_tipo_socio = SelectField('Tipo de Socio', coerce=int, validators=[DataRequired()])
    id_categoria_socio = SelectField('Categoría', coerce=int, validators=[DataRequired()])
    id_familia = SelectField('Socio Principal (si es familiar)', coerce=int, validators=[Optional()])
    id_empresa = HiddenField('ID de Empresa', validators=[DataRequired()])
    enviar_cuota_whatsapp = BooleanField('Enviar cuota por WhatsApp al generar', default=False)
    id_cobrador = SelectField('Cobrador asignado', coerce=int, validators=[Optional()]) 
    adicionales = SelectMultipleField(
        'Adicionales y Actividades', 
        coerce=int, 
        validators=[Optional()],
        option_widget=widgets.CheckboxInput(),
        widget=widgets.ListWidget(prefix_label=False)
    )

    def validate_nro_documento(self, field):
        socio_id = getattr(self, '_socio_id', None)
        empresa_id = self.id_empresa.data

        # El HiddenField devuelve un string; castearlo a int para que
        # PostgreSQL no intente comparar integer contra varchar.
        try:
            empresa_id = int(empresa_id) if empresa_id is not None else None
        except (ValueError, TypeError):
            empresa_id = None

        query = Socio.query.filter_by(nro_documento=field.data)

        if empresa_id:
            query = query.filter(Socio.id_empresa == empresa_id)

        if socio_id:
            query = query.filter(Socio.id != socio_id)

        socio_existente = query.first()
        if socio_existente:
            if socio_existente.fecha_baja is not None:
                raise ValidationError(
                    'Este DNI pertenece a un socio que fue dado de baja. '
                    'Puede reactivarlo desde el listado de inactivos.'
                )
            else:
                raise ValidationError(
                    'Este número de documento ya está registrado.'
                )

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.id_familia.data and self.id_familia.data != 0:
            socio_principal = Socio.query.get(self.id_familia.data)
            if socio_principal:
                self.id_tipo_socio.data = socio_principal.id_tipo_socio
                self.id_categoria_socio.data = socio_principal.id_categoria_socio
        return True

class ConfiguracionCuotaForm(FlaskForm):
    id_tipo_socio = SelectField('Tipo de Socio', coerce=int, validators=[DataRequired()])
    id_categoria_socio = SelectField('Categoría', coerce=int, validators=[DataRequired()])
    monto = DecimalField('Monto de la Cuota', validators=[DataRequired(), NumberRange(min=0)], places=2)
    fecha_desde = DateField('Vigencia Desde', validators=[DataRequired()])
    fecha_hasta = DateField('Vigencia Hasta (opcional)', validators=[Optional()])

class PagoCuotaForm(FlaskForm):
    fecha_pago = DateField('Fecha de Pago', validators=[DataRequired()])
    metodo_pago = SelectField('Método de Pago', choices=[
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('cheque', 'Cheque'),
        ('debito', 'Débito'),
        ('credito', 'Crédito')
    ], validators=[DataRequired()])
    id_cuenta = SelectField('Cuenta de destino', coerce=int, validators=[DataRequired()])

class TipoSocioForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=50)])
    descripcion = StringField('Descripción', validators=[Optional(), Length(max=200)])
    activo = BooleanField('Activo', default=True)

class CategoriaSocioForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=50)])
    descripcion = StringField('Descripción', validators=[Optional(), Length(max=200)])
    activo = BooleanField('Activo', default=True)

class InstalacionForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=100)])
    descripcion = TextAreaField('Descripción',
                                validators=[Optional(), Length(max=500)])
    capacidad = IntegerField('Capacidad (personas)',
                             validators=[DataRequired(), NumberRange(min=1)])

    tipo_cobro = SelectField(
        'Tipo de cobro',
        choices=[
            ('por_hora', 'Por hora (se cobra según duración)'),
            ('por_uso', 'Por uso (precio fijo por reserva)'),
        ],
        validators=[DataRequired()],
    )

    precio_por_hora = DecimalField(
        'Precio por hora',
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    precio_por_uso = DecimalField(
        'Precio por uso',
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )

    activo = BooleanField('Activo', default=True)

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        if self.tipo_cobro.data == 'por_hora':
            if not self.precio_por_hora.data or self.precio_por_hora.data <= 0:
                self.precio_por_hora.errors.append(
                    'Indicá el precio por hora.'
                )
                return False
            # Ignoramos precio_por_uso si el tipo es por_hora
            self.precio_por_uso.data = None
        elif self.tipo_cobro.data == 'por_uso':
            if not self.precio_por_uso.data or self.precio_por_uso.data <= 0:
                self.precio_por_uso.errors.append(
                    'Indicá el precio por uso.'
                )
                return False
            self.precio_por_hora.data = None

        return True

class ReservaForm(FlaskForm):
    id_instalacion = SelectField(
        'Instalación', coerce=int, validators=[DataRequired()]
    )
    from wtforms.validators import InputRequired
    id_socio = SelectField(
        'Socio', coerce=int, validators=[InputRequired()]
    )

    fecha_inicio_fecha = DateField(
        'Fecha de inicio', validators=[DataRequired()]
    )
    fecha_inicio_hora = SelectField(
        'Hora de inicio', validators=[DataRequired()]
    )

    fecha_fin_fecha = DateField(
        'Fecha de fin', validators=[DataRequired()]
    )
    fecha_fin_hora = SelectField(
        'Hora de fin', validators=[DataRequired()]
    )

    motivo = StringField(
        'Motivo', validators=[DataRequired(), Length(max=200)]
    )
    monto_total = DecimalField(
        'Monto total',
        validators=[DataRequired(), NumberRange(min=0)],
        places=2,
    )
    # Monto sugerido por el sistema (hidden). Sirve para auditar cambios.
    precio_sugerido = HiddenField('Precio sugerido')

    observaciones = TextAreaField(
        'Observaciones', validators=[Optional(), Length(max=500)]
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.utils import generar_opciones_horas
        opciones = generar_opciones_horas()
        self.fecha_inicio_hora.choices = opciones
        self.fecha_fin_hora.choices = opciones

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        from datetime import datetime
        from app.utils import parse_hora

        if not all([
            self.fecha_inicio_fecha.data, self.fecha_inicio_hora.data,
            self.fecha_fin_fecha.data, self.fecha_fin_hora.data,
        ]):
            return True  # ya falló por DataRequired

        fi = datetime.combine(
            self.fecha_inicio_fecha.data,
            parse_hora(self.fecha_inicio_hora.data),
        )
        ff = datetime.combine(
            self.fecha_fin_fecha.data,
            parse_hora(self.fecha_fin_hora.data),
        )

        if ff <= fi:
            self.fecha_fin_hora.errors.append(
                'La fecha/hora de fin debe ser posterior a la de inicio.'
            )
            return False

        return True

    def get_datetime_inicio(self):
        from datetime import datetime
        from app.utils import parse_hora
        if not (self.fecha_inicio_fecha.data and self.fecha_inicio_hora.data):
            return None
        return datetime.combine(
            self.fecha_inicio_fecha.data,
            parse_hora(self.fecha_inicio_hora.data),
        )

    def get_datetime_fin(self):
        from datetime import datetime
        from app.utils import parse_hora
        if not (self.fecha_fin_fecha.data and self.fecha_fin_hora.data):
            return None
        return datetime.combine(
            self.fecha_fin_fecha.data,
            parse_hora(self.fecha_fin_hora.data),
        )  

class PagoAlquilerForm(FlaskForm):
    monto = DecimalField('Monto', validators=[DataRequired(), NumberRange(min=0.01)], places=2)
    fecha_pago = DateTimeField(
        'Fecha de pago',
        validators=[DataRequired()],
        default=datetime.utcnow,
        format='%Y-%m-%dT%H:%M'
    )
    metodo_pago = SelectField('Método de pago', choices=[
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('cheque', 'Cheque'),
        ('debito', 'Débito'),
        ('credito', 'Crédito')
    ], validators=[DataRequired()])
    id_cuenta = SelectField('Cuenta de destino', coerce=int,
                            validators=[DataRequired()])
    comprobante = StringField('Nº de comprobante', validators=[Optional(), Length(max=100)])
    observaciones = TextAreaField('Observaciones', validators=[Optional(), Length(max=200)])

class CuentaForm(FlaskForm):
    nombre = StringField('Nombre de la cuenta', validators=[DataRequired(), Length(max=100)])
    tipo = SelectField('Tipo', choices=[
        ('efectivo', 'Efectivo (Caja chica)'),
        ('banco', 'Banco / Cuenta bancaria')
    ], validators=[DataRequired()])
    saldo_inicial = DecimalField('Saldo inicial',
                                 validators=[Optional(), NumberRange(min=0)],
                                 places=2, default=0)
    numero_cuenta = StringField('Número de cuenta', validators=[Optional(), Length(max=50)])
    cbu = StringField('CBU / Alias', validators=[Optional(), Length(max=50)])
    activo = BooleanField('Activa', default=True)

class MovimientoForm(FlaskForm):
    fecha = DateTimeField('Fecha', validators=[DataRequired()], default=lambda: utc_to_local(datetime.utcnow()), format='%Y-%m-%dT%H:%M')
    id_cuenta_contable = SelectField(
        'Cuenta contable (contrapartida)',
        coerce=int,
        validators=[DataRequired()]
    )
    tipo = SelectField(
        'Tipo de operación',
        choices=[('ingreso', 'Ingreso'), ('egreso', 'Egreso')],
        validators=[DataRequired()]
    )
    monto = DecimalField('Monto',
                         validators=[Optional(), NumberRange(min=0.01)],
                         places=2)
    descripcion = StringField('Descripción',
                              validators=[DataRequired(), Length(max=200)])
    metodo_pago = SelectField('Método de pago', choices=[
        ('efectivo', 'Efectivo'),
        ('cheque', 'Cheque'),
        ('transferencia', 'Transferencia'),
        ('deposito_efectivo', 'Depósito en efectivo'),
        ('deposito_cheque', 'Depósito de cheque')
    ], validators=[Optional()])
    comprobante = StringField('Nº de comprobante',
                              validators=[Optional(), Length(max=100)])
    id_cheque = SelectField('Cheque asociado (opcional)', coerce=int,
                            choices=[], default=0, validators=[Optional()],
                            validate_choice=False)
    conciliado = BooleanField('Conciliado', default=False)

    # --- Campos de cheque (solo se usan si metodo_pago == 'cheque') ---
    cheque_numero = StringField(
        'Número de cheque',
        validators=[Optional(), Length(max=50)],
    )
    cheque_banco_emisor = StringField(
        'Banco emisor',
        validators=[Optional(), Length(max=100)],
    )
    cheque_titular = StringField(
        'Titular',
        validators=[Optional(), Length(max=100)],
    )
    cheque_fecha_emision = DateField(
        'Fecha de emisión',
        validators=[Optional()],
    )
    cheque_fecha_cobro = DateField(
        'Fecha de cobro (para posdatados)',
        validators=[Optional()],
    )

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        # --- Validaciones de la cuenta contable ---
        if self.id_cuenta_contable.data and self.tipo.data:
            cuenta = PlanCuenta.query.get(self.id_cuenta_contable.data)
            if cuenta:
                if cuenta.tipo == 'Recurso' and self.tipo.data != 'ingreso':
                    self.tipo.errors.append(
                        'Una contrapartida de tipo Recurso solo admite Ingreso.'
                    )
                    return False
                if cuenta.tipo == 'Gasto' and self.tipo.data != 'egreso':
                    self.tipo.errors.append(
                        'Una contrapartida de tipo Gasto solo admite Egreso.'
                    )
                    return False

        # --- Validaciones de cheque (solo si el método es 'cheque') ---
        if self.metodo_pago.data == 'cheque':
            if not self.cheque_numero.data:
                self.cheque_numero.errors.append(
                    'El número de cheque es obligatorio.'
                )
                return False
            if not self.cheque_banco_emisor.data:
                self.cheque_banco_emisor.errors.append(
                    'El banco emisor es obligatorio.'
                )
                return False
            if self.tipo.data == 'ingreso' and not self.cheque_titular.data:
                self.cheque_titular.errors.append(
                    'El titular es obligatorio para cheques recibidos.'
                )
                return False
            if not self.cheque_fecha_emision.data:
                self.cheque_fecha_emision.errors.append(
                    'La fecha de emisión es obligatoria.'
                )
                return False

            # --- Validación de fecha de cobro según tipo de operación ---
            from dateutil.relativedelta import relativedelta
            hoy = date.today()
            es_ingreso = (self.tipo.data == 'ingreso')

            if es_ingreso:
                # Cheque recibido: no puede estar vencido
                referencia = (
                    self.cheque_fecha_cobro.data
                    or self.cheque_fecha_emision.data
                )
                if referencia:
                    fecha_venc = referencia + relativedelta(months=1)
                    if fecha_venc < hoy:
                        self.cheque_fecha_cobro.errors.append(
                            f'Este cheque ya está vencido (venció el '
                            f'{fecha_venc.strftime("%d/%m/%Y")}).'
                        )
                        return False
            else:
                # Cheque emitido: no puede tener fecha pasada
                if (self.cheque_fecha_cobro.data
                        and self.cheque_fecha_cobro.data < hoy):
                    self.cheque_fecha_cobro.errors.append(
                        'No podés emitir un cheque con fecha de cobro pasada.'
                    )
                    return False

        return True
    
class ChequeForm(FlaskForm):
    numero = StringField('Número de cheque', validators=[DataRequired(), Length(max=50)])
    banco_emisor = StringField('Banco emisor', validators=[DataRequired(), Length(max=100)])
    titular = StringField('Titular', validators=[DataRequired(), Length(max=100)])
    monto = DecimalField('Monto', validators=[DataRequired(), NumberRange(min=0.01)], places=2)
    fecha_emision = DateField('Fecha de emisión', validators=[DataRequired()])
    fecha_cobro = DateField('Fecha de cobro / vencimiento', validators=[DataRequired()])
    tipo = SelectField('Tipo', choices=[
        ('emitido', 'Cheque emitido (nosotros damos)'),
        ('recibido', 'Cheque recibido (nos pagan)')
    ], validators=[DataRequired()])
    observaciones = TextAreaField('Observaciones', validators=[Optional(), Length(max=200)])

    def validate_fecha_cobro(self, field):
        from datetime import date
        from dateutil.relativedelta import relativedelta

        hoy = date.today()

        # --- Emitido: fecha_cobro obligatoria y no pasada ---
        if self.tipo.data == 'emitido':
            if not field.data:
                raise ValidationError(
                    'La fecha de cobro es obligatoria para cheques emitidos.'
                )
            if field.data < hoy:
                raise ValidationError(
                    'No podés emitir un cheque con fecha de cobro anterior a hoy.'
                )
            return

        # --- Recibido: puede tener atraso pero no estar vencido ---
        # Referencia: si hay fecha_cobro, usarla; si no, usar fecha_emision.
        referencia = field.data or self.fecha_emision.data
        if not referencia:
            return

        fecha_vencimiento = referencia + relativedelta(months=1)
        if fecha_vencimiento < hoy:
            raise ValidationError(
                f'Este cheque ya está vencido. La fecha de referencia '
                f'({referencia.strftime("%d/%m/%Y")}) + 1 mes de gracia '
                f'vence el {fecha_vencimiento.strftime("%d/%m/%Y")}, '
                f'que ya pasó.'
            )
        
class ConciliacionForm(FlaskForm):
    fecha_desde = DateField('Desde', validators=[DataRequired()])
    fecha_hasta = DateField('Hasta', validators=[DataRequired()])
    # No hay campos adicionales, solo un botón de conciliar que recibe IDs de movimientos por POST

class ImportacionForm(FlaskForm):
    archivo = FileField('Archivo Excel o CSV', validators=[
        FileRequired('Selecciona un archivo para importar.'),
        FileAllowed(['xlsx', 'xls', 'csv'], 'Solo se permiten archivos Excel (.xlsx, .xls) o CSV (.csv)')
    ])

class CuentaContableForm(FlaskForm):
    id_cuenta_contable = SelectField('Cuenta Contable', coerce=int, validators=[Optional()])

class PlanCuentaForm(FlaskForm):
    codigo = StringField('Código', validators=[DataRequired(), Length(max=30)])
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=100)])
    tipo = SelectField('Tipo', choices=[
        ('Activo', 'Activo'),
        ('Pasivo', 'Pasivo'),
        ('Patrimonio', 'Patrimonio'),
        ('Recurso', 'Recurso'),
        ('Gasto', 'Gasto')
    ], validators=[DataRequired()])
    id_padre = SelectField('Cuenta Padre', coerce=int, validators=[Optional()])
    activo = BooleanField('Activo', default=True)

    def __init__(self, *args, **kwargs):
        # La vista puede pasar la instancia que se está editando para validar
        # reglas que dependen del estado actual (no cambiar código si tiene hijos, etc.)
        self._cuenta_editando = kwargs.pop('cuenta_editando', None)
        super().__init__(*args, **kwargs)

    # ---------- Validaciones de campo ----------

    def validate_codigo(self, field):
        codigo = (field.data or '').strip()
        if not codigo:
            return
        if not re.match(r'^\d+(\.\d+)*$', codigo):
            raise ValidationError(
                'El código debe ser numérico, con niveles separados por puntos '
                '(ej. 1, 1.1, 5.2.1, 5.2.1.1).'
            )
        nivel = codigo.count('.') + 1
        if nivel > PlanCuenta.MAX_NIVEL:
            raise ValidationError(
                f'El código supera el nivel máximo permitido '
                f'({PlanCuenta.MAX_NIVEL} niveles).'
            )

    # ---------- Validaciones cruzadas ----------

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False

        # Si hay padre, el código debe colgar de él y el tipo coincidir
        if self.id_padre.data and self.id_padre.data != 0:
            padre = PlanCuenta.query.get(self.id_padre.data)
            if padre:
                prefijo = padre.codigo + '.'
                if not self.codigo.data.startswith(prefijo):
                    self.codigo.errors.append(
                        f'El código debe comenzar con "{prefijo}" '
                        f'(padre: {padre.codigo} - {padre.nombre}).'
                    )
                    return False
                if self.tipo.data != padre.tipo:
                    self.tipo.errors.append(
                        f'El tipo debe coincidir con el del padre ({padre.tipo}).'
                    )
                    return False
                
                # Regla de negocio: Activo/Pasivo nivel 1 => solo .1 o .2
                fijos = PlanCuenta.SEGUNDO_NIVEL_FIJO.get(padre.tipo)
                if padre.nivel == 1 and fijos:
                    resto = self.codigo.data[len(prefijo):]
                    if resto not in fijos:
                        esperados = ' o '.join(
                            f'"{padre.codigo}.{s}"' for s in fijos
                        )
                        self.codigo.errors.append(
                            f'El segundo nivel de {padre.tipo} solo admite '
                            f'{esperados} (Corriente / No Corriente). '
                            f'Recibido: "{self.codigo.data}".'
                        )
                        return False
                    
        # Reglas específicas cuando estamos editando
        if self._cuenta_editando:
            # No puede ser su propio padre
            if self.id_padre.data == self._cuenta_editando.id:
                self.id_padre.errors.append(
                    'Una cuenta no puede ser padre de sí misma.'
                )
                return False

            # No se puede cambiar el código si tiene subcuentas
            if (self.codigo.data != self._cuenta_editando.codigo
                    and not self._cuenta_editando.es_hoja):
                self.codigo.errors.append(
                    'No se puede cambiar el código de una cuenta con subcuentas. '
                    'Renumerá o eliminá primero las subcuentas.'
                )
                return False

            # No se puede cambiar el tipo si tiene subcuentas
            if (self.tipo.data != self._cuenta_editando.tipo
                    and not self._cuenta_editando.es_hoja):
                self.tipo.errors.append(
                    'No se puede cambiar el tipo de una cuenta con subcuentas.'
                )
                return False

        return True

class ProductoForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=100)])
    descripcion = TextAreaField('Descripción', validators=[Optional(), Length(max=500)])
    categoria = SelectField('Categoría', coerce=int, validators=[Optional()])  # coerce a int
    precio_venta = DecimalField('Precio de venta', validators=[DataRequired(), NumberRange(min=0)], places=2)
    precio_costo = DecimalField('Precio de costo', validators=[Optional(), NumberRange(min=0)], places=2)
    stock_actual = IntegerField('Stock actual', validators=[DataRequired(), NumberRange(min=0)], default=0)
    stock_minimo = IntegerField('Stock mínimo (alerta)', validators=[DataRequired(), NumberRange(min=0)], default=0)
    activo = BooleanField('Activo', default=True)

    def __init__(self, *args, **kwargs):
        # Obtener empresa_id del argumento o de la sesión
        # Para simplificar, pasamos empresa_id al instanciar
        # Podemos usar un parámetro extra, o mejor, obtener la empresa desde la sesión/request
        # En Flask, podemos usar current_app, pero en el formulario no tenemos acceso directo.
        # Usaremos un parámetro en la vista: form = ProductoForm(empresa_id=empresa.id)
        # Para eso, modificamos __init__ para aceptar empresa_id
        empresa_id = kwargs.pop('empresa_id', None)
        super().__init__(*args, **kwargs)
        if empresa_id:
            from app.models import CategoriaProducto
            categorias = CategoriaProducto.query.filter_by(id_empresa=empresa_id, activo=True).order_by(CategoriaProducto.nombre).all()
            self.categoria.choices = [(0, 'Sin categoría')] + [(c.id, c.nombre) for c in categorias]

class VentaForm(FlaskForm):
    id_socio = SelectField('Socio (opcional)', coerce=int, validators=[Optional()])
    fecha = DateField('Fecha', validators=[DataRequired()], default=datetime.utcnow)
    metodo_pago = SelectField('Método de pago', choices=[
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('cheque', 'Cheque'),
        ('debito', 'Débito'),
        ('credito', 'Crédito')
    ], validators=[DataRequired()])
    id_cuenta = SelectField('Cuenta de destino', coerce=int, validators=[DataRequired()])

class AgregarProductoCarritoForm(FlaskForm):
    id_producto = SelectField('Producto', coerce=int, validators=[DataRequired()])
    cantidad = IntegerField('Cantidad', validators=[DataRequired(), NumberRange(min=1)], default=1)

class CompraForm(FlaskForm):
    fecha = DateField('Fecha', validators=[DataRequired()], default=datetime.utcnow)
    proveedor = StringField('Proveedor', validators=[Optional(), Length(max=100)])

class AgregarProductoCompraForm(FlaskForm):
    id_producto = SelectField('Producto', coerce=int, validators=[DataRequired()])
    cantidad = IntegerField('Cantidad', validators=[DataRequired(), NumberRange(min=1)], default=1)
    precio_compra = DecimalField('Precio de compra', validators=[DataRequired(), NumberRange(min=0)], places=2)

class ProveedorForm(FlaskForm):
    nombre = StringField('Nombre / Razón Social', validators=[DataRequired(), Length(max=100)])
    cuit = StringField('CUIT', validators=[Optional(), Length(max=20)])
    condicion_iva = SelectField('Condición frente al IVA', choices=[
        ('', '— Sin especificar —'),
        ('Responsable Inscripto', 'Responsable Inscripto'),
        ('Monotributo', 'Monotributo'),
        ('Exento', 'Exento'),
        ('Consumidor Final', 'Consumidor Final'),
    ], validators=[Optional()])
    direccion = StringField('Dirección', validators=[Optional(), Length(max=200)])
    telefono = StringField('Teléfono', validators=[Optional(), Length(max=20)])
    email = EmailField('Email', validators=[Optional(), Email(), Length(max=100)])
    notas = TextAreaField('Notas', validators=[Optional(), Length(max=500)])
    activo = BooleanField('Activo', default=True)

# ============================================================
# Roles y usuarios
# ============================================================

class RolForm(FlaskForm):
    nombre = StringField('Nombre del rol',
                         validators=[DataRequired(), Length(max=50)])
    activo = BooleanField('Activo', default=True)


class UsuarioForm(FlaskForm):
    nombre = StringField('Nombre completo',
                         validators=[DataRequired(), Length(max=100)])
    username = StringField('Usuario',
                           validators=[DataRequired(), Length(max=50)])
    password = PasswordField('Contraseña',
                             validators=[Optional(), Length(min=6)])
    id_rol = SelectField('Rol', coerce=int, validators=[Optional()])
    activo = BooleanField('Activo', default=True)

# ============================================================
# Creación de empresa y super admin (signup)
# ============================================================

class SignupForm(FlaskForm):
    nombre_club = StringField(
        'Nombre del club',
        validators=[DataRequired(), Length(max=100)]
    )
    cuit = StringField(
        'CUIT',
        validators=[DataRequired(), Length(max=20)]
    )
    nombre_admin = StringField(
        'Tu nombre completo',
        validators=[DataRequired(), Length(max=100)]
    )
    email = EmailField(
        'Email',
        validators=[DataRequired(), Email(), Length(max=100)]
    )
    password = PasswordField(
        'Contraseña',
        validators=[DataRequired(), Length(min=6, max=100)]
    )
    confirmar_password = PasswordField(
        'Confirmar contraseña',
        validators=[
            DataRequired(),
            EqualTo('password', message='Las contraseñas no coinciden.'),
        ]
    )

class ReenviarVerificacionForm(FlaskForm):
    email = StringField(
        'Correo electrónico',
        validators=[DataRequired(), Email()],
        render_kw={'placeholder': 'tucorreo@ejemplo.com'},
    )
    submit = SubmitField('Reenviar correo de verificación')

class SolicitarResetForm(FlaskForm):
    email = StringField(
        'Correo electrónico',
        validators=[DataRequired(), Email()],
        render_kw={'placeholder': 'tucorreo@ejemplo.com'},
    )
    submit = SubmitField('Enviar enlace de recuperación')


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        'Nueva contraseña',
        validators=[DataRequired(), Length(min=6)],
        render_kw={'placeholder': 'Mínimo 6 caracteres'},
    )
    confirmar_password = PasswordField(
        'Repetir contraseña',
        validators=[DataRequired(), EqualTo('password', message='Las contraseñas no coinciden.')],
        render_kw={'placeholder': 'Repetí la contraseña'},
    )
    submit = SubmitField('Cambiar contraseña')


class AdicionalForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=100)])
    monto = DecimalField('Monto mensual', validators=[DataRequired(), NumberRange(min=0)], places=2)
    fecha_desde = DateField('Vigencia Desde', validators=[DataRequired()])
    fecha_hasta = DateField('Vigencia Hasta (opcional)', validators=[Optional()])
    activo = BooleanField('Activo', default=True)

class CambiarPasswordForm(FlaskForm):
    password_actual = PasswordField(
        'Contraseña Actual', 
        validators=[DataRequired()]
    )
    password_nuevo = PasswordField(
        'Nueva Contraseña', 
        validators=[DataRequired(), Length(min=6, message='Mínimo 6 caracteres.')]
    )
    confirmar_password = PasswordField(
        'Repetir Nueva Contraseña', 
        validators=[DataRequired(), EqualTo('password_nuevo', message='Las contraseñas no coinciden.')]
    )
    submit = SubmitField('Actualizar Contraseña')