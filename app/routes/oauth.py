# app/routes/oauth.py
import secrets
from datetime import datetime, timedelta

from flask import (
    Blueprint, request, redirect, url_for, flash, current_app, session, render_template,
)

from app import db
from app.models import ChytaPayToken
from app.services.chytapay_service import ChytaPayService
from app.utils import current_empresa


oauth_bp = Blueprint('oauth', __name__, url_prefix='/oauth')


@oauth_bp.route('/iniciar')
def iniciar_oauth():
    """Inicia el flujo OAuth redirigiendo al usuario a ChytaPay."""
    empresa = current_empresa()
    if not empresa:
        flash('Primero crea un club.', 'warning')
        return redirect(url_for('main.index'))

    # --- ChytaPay bloqueado en trial ---
    if not empresa.puede_usar_chytapay:
        flash(
            'ChytaPay no está disponible en el período de prueba. '
            'Cuando pases a un plan pago vas a poder activarlo.',
            'warning',
        )
        return redirect(url_for('main.index'))

    auth_url = current_app.config.get('CHYTAPAY_AUTH_API_URL')
    client_id = current_app.config.get('CHYTAPAY_CLIENT_ID')

    if not auth_url or not client_id:
        flash(
            'ChytaPay no está configurado en este entorno '
            '(faltan CHYTAPAY_AUTH_API_URL o CHYTAPAY_CLIENT_ID).',
            'danger'
        )
        return redirect(url_for('main.index'))

    # En desarrollo (ngrok) usamos BASE_URL; en producción el dominio real.
    base_url = current_app.config.get('BASE_URL', '').rstrip('/')
    if base_url:
        redirect_uri = f'{base_url}/oauth/callback'
    else:
        redirect_uri = url_for('oauth.oauth_callback', _external=True)

    # State random para prevenir CSRF en el callback.
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    session['oauth_id_empresa'] = empresa.id

    authorize_url = (
        f"{auth_url}/integration/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&state={state}"
    )
    return redirect(authorize_url)


@oauth_bp.route('/callback')
def oauth_callback():
    """Callback al que ChytaPay redirige después de la autorización."""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')

    if error:
        return _responder_callback(False, f'Autorización cancelada: {error}')

    if not code:
        return _responder_callback(False, 'No se recibió código de autorización.')

    # Validar state (CSRF)
    state_guardado = session.pop('oauth_state', None)
    if not state or state != state_guardado:
        return _responder_callback(False, 'Verificación de seguridad fallida (state inválido).')

    id_empresa = session.pop('oauth_id_empresa', None)
    if not id_empresa:
        return _responder_callback(False, 'La sesión expiró antes de completar la conexión.')

    # Canjear código por tokens
    try:
        current_app.logger.info(
            f'OAuth callback: canjeando code para empresa {id_empresa}'
        )
        tokens = ChytaPayService.exchange_code_for_tokens(code)
        current_app.logger.info(
            f'OAuth callback: tokens recibidos, keys={list(tokens.keys())}'
        )

        token = ChytaPayToken.query.filter_by(id_empresa=id_empresa).first()
        if not token:
            token = ChytaPayToken(id_empresa=id_empresa)

        token.access_token = (
            tokens.get('idToken') or tokens.get('access_token')
        )
        token.refresh_token = (
            tokens.get('refreshToken') or tokens.get('refresh_token')
        )
        token.expires_at = datetime.utcnow() + timedelta(hours=1)

        db.session.add(token)
        db.session.commit()
        current_app.logger.info(
            f'OAuth callback: token guardado para empresa {id_empresa}, '
            f'expires_at={token.expires_at}'
        )
        return _responder_callback(True, None)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error en OAuth callback')
        return _responder_callback(False, f'Error al obtener tokens: {str(e)}')

def _responder_callback(exito, error_msg=None):
    """Devuelve un HTML que cierra el popup y avisa al opener."""
    return render_template(
        'oauth/callback_popup.html',
        exito=exito,
        error_msg=error_msg,
        redirect_url=url_for('config.chytapay_panel'),
    )