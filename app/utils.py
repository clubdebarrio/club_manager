# app/utils.py
import unicodedata
from datetime import datetime, date, time, timezone
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app, request
from flask_limiter.util import get_remote_address
from zoneinfo import ZoneInfo

def quitar_acentos(texto):
    """Elimina los acentos de un texto."""
    if not texto:
        return texto
    return ''.join(
        c for c in unicodedata.normalize('NFKD', texto)
        if not unicodedata.combining(c)
    )

def formato_pesos(valor, decimales=0, simbolo=True):
    """
    Formatea un número al estilo argentino:
      - Separador de miles: punto (.)
      - Separador decimal: coma (,)
      - Cantidad de decimales configurable (default 0)
      - Opción de incluir el símbolo '$'

    Ejemplos:
        formato_pesos(1234567)        -> '$1.234.567'
        formato_pesos(1234.5, 2)      -> '$1.234,50'
        formato_pesos(-45000)         -> '-$45.000'
        formato_pesos(1234, simbolo=False) -> '1.234'
    """
    if valor is None:
        valor = 0

    # 1) Formatear con separadores "ingleses"
    formato = "{:,.%df}" % decimales
    texto = formato.format(valor)

    # 2) Invertir separadores: , -> . y . -> ,
    #    Usamos un placeholder "X" para no pisar uno con otro
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    # 3) Manejar el signo negativo adelante del $
    if simbolo:
        if texto.startswith("-"):
            return f"-${texto[1:]}"
        return f"${texto}"
    return texto


def registrar_filtros(app):
    """Registra todos los filtros Jinja personalizados."""
    app.jinja_env.filters["pesos"] = formato_pesos


def fecha_hora_efectiva(fecha_form):
    """
    Combina una fecha (date) con la hora actual si es hoy (en hora local),
    o con medianoche si es una fecha pasada.
    Devuelve SIEMPRE un datetime en hora LOCAL (Argentina).
    """
    if fecha_form is None:
        return utc_to_local(datetime.utcnow())
    if not isinstance(fecha_form, date):
        return fecha_form
        
    ahora_local = utc_to_local(datetime.utcnow())
    
    if fecha_form == ahora_local.date():
        return ahora_local  # Devuelve la hora local actual
    return datetime.combine(fecha_form, time.min) # Devuelve medianoche local

# --- Multiempresa ---

from flask import g, session


def current_empresa():
    """
    Devuelve la Empresa del usuario logueado.

    - Se cachea en `g` durante el request para no pegarle a la DB más de una vez.
    - Lee `session['id_empresa']` (que el login guarda al iniciar sesión).
    - Devuelve None si no hay sesión activa o la empresa no existe.
      Las rutas que requieran empresa deben manejar el None y redirigir
      al login o al index.
    """
    from app.models import Empresa  # import lazy para evitar circular

    if 'empresa_actual' in g:
        return g.empresa_actual

    id_empresa = session.get('id_empresa')
    empresa = None
    if id_empresa:
        empresa = Empresa.query.get(id_empresa)

    g.empresa_actual = empresa
    return empresa


# --- Token crear empresa y reset contraseña ---

def generar_token_verificacion(email: str) -> str:
    """Genera un token seguro con el email del usuario."""
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.dumps(email, salt='email-verificacion')

def verificar_token_verificacion(token: str, max_age: int = 86400) -> str | None:
    """
    Verifica el token y devuelve el email si es válido.
    max_age: 86400 segundos = 24 horas.
    """
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt='email-verificacion', max_age=max_age)
        return email
    except (BadSignature, SignatureExpired):
        return None

def generar_token_reset_password(email: str) -> str:
    """Genera un token para resetear la contraseña."""
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.dumps(email, salt='password-reset')


def verificar_token_reset_password(token: str, max_age: int = 3600):
    """
    Devuelve el email si el token es válido.
    max_age: 3600 segundos = 1 hora.
    """
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(
            token, salt='password-reset', max_age=max_age
        )
        return email
    except (BadSignature, SignatureExpired):
        return None


def key_func_email_o_ip():
    """Usa el email del form si está presente, si no la IP."""
    email = request.form.get('email') or request.form.get('username')
    if email:
        return f"{get_remote_address()}:{email.lower()}"
    return get_remote_address()

# --- Horas para reservas ---

# Rango horario del club. Formato 24hs, incluye el inicio, excluye el fin.
# Rango default: 07:00 a 23:59, cada 15 min.
HORA_INICIO_DIA = 0
HORA_FIN_DIA = 24
GRANULARIDAD_MINUTOS = 15


def generar_opciones_horas():
    """
    Devuelve una lista de tuplas (valor, etiqueta) para un select de horas.
    Ej: [('07:00', '07:00'), ('07:15', '07:15'), ...]
    """
    opciones = []
    for h in range(HORA_INICIO_DIA, HORA_FIN_DIA):
        for m in range(0, 60, GRANULARIDAD_MINUTOS):
            opciones.append((f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}'))
    return opciones


def parse_hora(valor):
    """Convierte 'HH:MM' a datetime.time. Devuelve None si falla."""
    from datetime import time
    if not valor:
        return None
    try:
        partes = valor.split(':')
        return time(int(partes[0]), int(partes[1]))
    except Exception:
        return None

# Zona horaria del sistema. Para Argentina es UTC-3.
TZ_LOCAL = ZoneInfo("America/Argentina/Buenos_Aires")

def utc_to_local(dt):
    """Convierte una fecha UTC (de la DB) a hora local de Argentina."""
    if not dt:
        return None
    # Si la fecha no tiene zona horaria, asumimos que es UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_LOCAL)

def local_to_utc(dt):
    """Convierte una fecha local (ingresada por el usuario) a UTC para guardar."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_LOCAL)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

def registrar_filtros(app):
    """Registra todos los filtros Jinja personalizados."""
    app.jinja_env.filters["pesos"] = formato_pesos
    
    # NUEVOS FILTROS DE FECHA:
    app.jinja_env.filters["localdate"] = lambda dt: utc_to_local(dt).strftime('%d/%m/%Y') if dt else ''
    app.jinja_env.filters["localdatetime"] = lambda dt: utc_to_local(dt).strftime('%d/%m/%Y %H:%M') if dt else ''