# app/services/chytapay_service.py
import requests
from datetime import datetime, timedelta, date
from flask import current_app, url_for
from app import db
from app.models import (
    ChytaPayToken, ChytaPayPaymentRequest, ChytaPayWebhookEvent,
)


class ChytaPayService:
    AUTH_URL = None
    INTEGRATION_URL = None

    @classmethod
    def _get_urls(cls):
        if not cls.AUTH_URL:
            cls.AUTH_URL = current_app.config['CHYTAPAY_AUTH_API_URL']
            cls.INTEGRATION_URL = current_app.config['CHYTAPAY_INTEGRATION_API_URL']
        return cls.AUTH_URL, cls.INTEGRATION_URL

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    @classmethod
    def get_access_token(cls, empresa_id, force_refresh=False):
        """Obtiene un token de acceso válido para la empresa dada."""
        token = ChytaPayToken.query.filter_by(id_empresa=empresa_id).first()

        if not token:
            raise ValueError(
                f'No hay token para la empresa {empresa_id}. '
                f'Es necesario conectar la cuenta de ChytaPay.'
            )

        # Si el token está vigente, usarlo
        if not token.is_expired() and not force_refresh:
            return token.access_token

        # Si expiró, intentar refrescar
        try:
            return cls._refresh_token(empresa_id, token)
        except Exception as e:
            current_app.logger.error(
                f'Error refrescando token para empresa {empresa_id}: {e}'
            )
            raise ValueError(
                'El token de ChytaPay expiró y no se pudo renovar. '
                'Es necesario reconectar la cuenta de ChytaPay.'
            )

    @classmethod
    def _refresh_token(cls, empresa_id, token):
        """
        Refresca el idToken usando el refreshToken.
        El refreshToken NO rota: se sigue usando el mismo hasta que
        venza (180 días) o se revoque.
        """
        auth_url, _ = cls._get_urls()
        client_id = current_app.config['CHYTAPAY_CLIENT_ID']
        client_secret = current_app.config['CHYTAPAY_CLIENT_SECRET']

        response = requests.post(
            f"{auth_url}/integration/oauth2/refresh",
            json={
                "grantType": "refresh_token",
                "refreshToken": token.refresh_token,
                "clientId": client_id,
                "clientSecret": client_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        token.access_token = data.get('idToken') or data.get('access_token')
        # El refresh token no se devuelve, se mantiene el actual
        token.expires_at = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()

        current_app.logger.info(
            f'✅ Token refrescado para empresa {empresa_id}'
        )
        return token.access_token

    @classmethod
    def _save_token(cls, empresa_id, data):
        token = ChytaPayToken.query.filter_by(id_empresa=empresa_id).first()
        if not token:
            token = ChytaPayToken(id_empresa=empresa_id)
            db.session.add(token)

        token.access_token = data.get('idToken') or data.get('access_token')
        token.refresh_token = (
            data.get('refreshToken') or data.get('refresh_token')
        )
        token.expires_at = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        return token.access_token

    # ------------------------------------------------------------------
    # Payment request
    # ------------------------------------------------------------------

    @classmethod
    def create_payment_request(cls, cuota, send_whatsapp=False):
        """Crea una solicitud de pago en ChytaPay para una cuota."""
        access_token = cls.get_access_token(cuota.socio.id_empresa)
        if not access_token:
            raise ValueError("No se pudo obtener un token de acceso")

        hoy = date.today()
        if cuota.fecha_vencimiento and cuota.fecha_vencimiento > hoy:
            fecha_vencimiento = cuota.fecha_vencimiento
        else:
            fecha_vencimiento = hoy + timedelta(days=10)
        due_dates = [fecha_vencimiento.strftime('%Y-%m-%d')]

        reference_id = (
            f"CLUB-{cuota.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )

        payload = {
            "referenceId": reference_id,
            "amount": float(cuota.monto),
            "dueDates": due_dates,
            "description": (
                f"Cuota social {cuota.periodo} - "
                f"{cuota.socio.apellido} {cuota.socio.nombre}"
            ),
            "customer": {
                "name": f"{cuota.socio.apellido} {cuota.socio.nombre}",
                "email": cuota.socio.email or "test@club.com",
                "phoneNumber": {
                    "countryCode": "+54",
                    "number": cuota.socio.telefono or "2222-2222",
                },
                "taxDocument": cuota.socio.nro_documento,
            },
            "conciliationType": "cuil",
            "sendWhatsappNotification": send_whatsapp,
            "sendEmailNotification": False,
        }

        _, integration_url = cls._get_urls()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                f"{integration_url}/payment-request",
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            payment_request = ChytaPayPaymentRequest(
                reference_id=reference_id,
                cuota_social_id=cuota.id,
                chytapay_status=data.get('state', 'pending'),
                intentos=1,
            )
            db.session.add(payment_request)
            db.session.commit()

            return data

        except requests.exceptions.HTTPError as e:
            error_body = response.text[:500]
            current_app.logger.error(
                f'HTTPError creando payment request para cuota '
                f'{cuota.id}: {error_body}'
            )
            cls._registrar_error_creacion(cuota, error_body)
            raise Exception(f"Error de ChytaPay: {error_body}") from e
        except Exception as e:
            current_app.logger.error(
                f'Error creando payment request para cuota '
                f'{cuota.id}: {e}'
            )
            cls._registrar_error_creacion(cuota, str(e)[:500])
            raise

    @classmethod
    def _registrar_error_creacion(cls, cuota, mensaje):
        """Guarda el error de creación en el payment_request si existe."""
        pr = ChytaPayPaymentRequest.query.filter_by(
            cuota_social_id=cuota.id
        ).first()
        if pr:
            pr.intentos = (pr.intentos or 0) + 1
            pr.ultimo_error = mensaje[:300]
            pr.ultimo_intento = datetime.utcnow()
            db.session.commit()

    # ------------------------------------------------------------------
    # Movimiento en caja por cobro de cuota (total o parcial)
    # ------------------------------------------------------------------

    @classmethod
    def _crear_movimiento_cobro_cuota(cls, cuota, monto, reference_id):
        """
        Crea el movimiento en caja y su asiento para un cobro por ChytaPay.
        No commitea: el caller debe commitear (process_webhook lo hace al final).
        Devuelve el Movimiento creado o None si no hay cuenta bancaria.
        """
        from app.services.caja_service import CajaService
        from app.services.contabilidad_service import ContabilidadService
        from app.services.cuenta_contable_resolver import CuentaContableResolver
        from app.models import CuentaBancaria

        empresa_id = cuota.socio.id_empresa

        cuenta_destino = CuentaBancaria.query.filter_by(
            id_empresa=empresa_id, tipo='banco', activo=True
        ).first()

        if not cuenta_destino:
            current_app.logger.warning(
                f"⚠️ No hay cuenta bancaria activa para empresa {empresa_id}. "
                f"No se registra movimiento en caja."
            )
            return None

        # Contrapartida: 4.1 Cuotas Sociales
        cuenta_contable = CuentaContableResolver.resolver_o_fallar(
            empresa_id, 'cuota_social'
        )

        movimiento_data = {
            'id_cuenta': cuenta_destino.id,
            'id_cuenta_contable': cuenta_contable.id,
            'fecha': datetime.utcnow(),
            'tipo': 'ingreso',
            'monto': monto,
            'descripcion': (
                f"Cobro ChytaPay - Cuota {cuota.periodo} - "
                f"{cuota.socio.apellido} {cuota.socio.nombre}"
            ),
            'metodo_pago': 'transferencia',
            'comprobante': f"ChytaPay-{reference_id}",
            'id_empresa': empresa_id,
            'conciliado': False,
        }

        movimiento = CajaService.registrar_movimiento(movimiento_data, commit=False)
        if movimiento:
            ContabilidadService.generar_asiento(movimiento)
        return movimiento

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Validación de firma
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_signature(raw_body: bytes, header: str, secret: str,
                          tolerance_seconds: int = 300) -> bool:
        """
        Verifica la firma X-Chytapay-Signature.

        Formato del header: "t=<timestamp>,v1=<hmac_hex>"
        Firma = HMAC-SHA256(secret, "{t}.{raw_body}") en hex.
        """
        import hashlib
        import hmac
        import time

        if not header or not secret:
            return False

        # Parsear "t=...,v1=..."
        parts = [p.partition('=') for p in header.split(',')]
        t_value = next((v for k, _, v in parts if k == 't'), '')
        signatures = [v for k, _, v in parts if k == 'v1']

        if not t_value.isdigit() or not signatures:
            return False

        t_int = int(t_value)
        now = int(time.time())

        # Rechazar si el timestamp está fuera de tolerancia
        if abs(now - t_int) > tolerance_seconds:
            return False

        # Calcular firma esperada
        # OJO: el secreto incluye el prefijo "whsec_" completo
        secret_bytes = secret.encode('utf-8')
        expected = hmac.new(
            secret_bytes,
            f'{t_int}.'.encode('utf-8') + raw_body,
            hashlib.sha256,
        ).hexdigest()

        # Comparación en tiempo constante contra cada firma
        for sig in signatures:
            if hmac.compare_digest(expected.encode(), sig.encode()):
                return True

        return False

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    @classmethod
    def process_webhook(cls, payload, signature_header=None,
                        raw_body=None, webhook_id=None,
                        validation_token=None):
        """
        Procesa un webhook de ChytaPay.

        - Valida la firma HMAC (nuevo sistema).
        - Fallback a validation-token (sistema legacy).
        - Deduplica por X-Chytapay-Webhook-Id.
        - Idempotencia adicional por hash del payload.

        Devuelve True si se procesó (o ya estaba procesado).
        """
        import hashlib
        import json as _json

        # --- 1. Validación de firma HMAC ---
        expected_secret = current_app.config.get(
            'CHYTAPAY_WEBHOOK_SECRET', ''
        ) or ''

        firma_valida = False
        if signature_header and raw_body is not None:
            firma_valida = cls._verify_signature(
                raw_body, signature_header, expected_secret
            )

        # --- Fallback: validation-token legacy ---
        if not firma_valida and validation_token is not None:
            import hmac
            firma_valida = hmac.compare_digest(
                (validation_token or '').encode(),
                expected_secret.encode(),
            )

        if not firma_valida:
            raise ValueError('Firma inválida o token inválido.')

        # --- 2. Webhook de prueba (ChytaPay manda {"test": true}) ---
        if payload.get('test') is True:
            current_app.logger.info('Webhook de prueba recibido.')
            return True

        # --- 3. Event ID (para idempotencia) ---
        # Preferimos el header oficial; fallback a hash del payload.
        event_id = webhook_id or cls._extract_event_id(payload)
        if not event_id:
            raise ValueError('No se pudo determinar event_id.')

        # --- 4. ¿Ya fue procesado? ---
        existente = ChytaPayWebhookEvent.query.filter_by(
            event_id=event_id
        ).first()
        if existente and existente.procesado:
            current_app.logger.info(
                f'Webhook {event_id} ya procesado. Ignorado.'
            )
            return True

        # --- 5. Crear o recuperar el registro del evento ---
        if existente:
            evento = existente
        else:
            data = payload.get('data', payload)
            reference_id = (
                data.get('referenceId') or data.get('reference_id')
            )
            state = (
                data.get('state') or data.get('estado')
                or data.get('status') or payload.get('stateType')
            )

            id_empresa = None
            if reference_id:
                pr = ChytaPayPaymentRequest.query.filter_by(
                    reference_id=reference_id
                ).first()
                if pr and pr.cuota:
                    id_empresa = pr.cuota.socio.id_empresa

            evento = ChytaPayWebhookEvent(
                event_id=event_id,
                reference_id=reference_id,
                state=state,
                payload=_json.dumps(payload, ensure_ascii=False)[:5000],
                id_empresa=id_empresa,
            )
            
            try:
                # Usamos begin_nested() para crear un SAVEPOINT.
                # Si falla por 'unique', solo revierte este insert.
                with db.session.begin_nested():
                    db.session.add(evento)
                    db.session.flush()
            except Exception as e:
                # Si el error es por duplicado, significa que otra petición
                # lo insertó milisegundos antes que nosotros.
                if 'unique constraint' in str(e).lower() or 'duplicate key' in str(e).lower():
                    current_app.logger.warning(f"Webhook {event_id} duplicado (Race Condition). Ignorando.")
                    db.session.rollback() # Limpiamos el estado de la sesión
                    return True
                else:
                    # Si es otro error de DB, lo propagamos
                    raise e
                
        # --- 6. Procesar ---
        try:
            resultado = cls._procesar_evento(payload, evento)
            evento.procesado = True
            evento.resultado = resultado[:200]
            evento.fecha_procesamiento = datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            evento = ChytaPayWebhookEvent.query.filter_by(
                event_id=event_id
            ).first()
            if evento:
                evento.error = str(e)[:5000]
                evento.fecha_procesamiento = datetime.utcnow()
                db.session.commit()
            current_app.logger.error(
                f'Error procesando webhook {event_id}: {e}'
            )
            raise

    @classmethod
    def _extract_event_id(cls, payload):
        """
        Extrae un event_id único del payload.
        ChytaPay puede mandarlo en distintos lugares según el tipo.
        Fallback: hash determinístico del payload completo.
        """
        import hashlib
        import json as _json

        # Búsqueda en varios lugares posibles
        for key in ('eventId', 'event_id', 'id', 'notificationId'):
            val = payload.get(key)
            if val:
                return f'chytapay-{val}'

        # Dentro de data
        data = payload.get('data', {})
        if isinstance(data, dict):
            for key in ('eventId', 'event_id', 'id'):
                val = data.get(key)
                if val:
                    return f'chytapay-{val}'

        # Fallback: hash del payload (determinístico para reintentos)
        raw = _json.dumps(payload, sort_keys=True, ensure_ascii=False)
        hash_val = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]
        return f'chytapay-hash-{hash_val}'

    @classmethod
    def _procesar_evento(cls, payload, evento):
        """
        Procesa el evento según el tipo. ChytaPay manda:
          - payload['event']: 'payment_request.payment_received' |
            'payment_request.expired' | 'payment.unmatched'
          - payload['stateType']: 'total_payment' | 'partial_payment' |
            'overdue'
          - payload['referenceId']: el reference que enviamos al crear
        """
        tipo_evento = payload.get('event', '')
        reference_id = payload.get('referenceId')
        state_type = payload.get('stateType')

        if not reference_id:
            raise ValueError('Webhook sin referenceId.')

        # --- payment.unmatched: pago sin imputar ---
        if tipo_evento == 'payment.unmatched':
            current_app.logger.warning(
                f'Pago sin imputar: reason={payload.get("reason")}, '
                f'referenceId={reference_id}'
            )
            return f'Pago sin imputar registrado ({reference_id}).'

        # --- payment_request.* ---
        payment_request = ChytaPayPaymentRequest.query.filter_by(
            reference_id=reference_id
        ).first()
        if not payment_request:
            raise ValueError(
                f'Payment request no encontrado: {reference_id}'
            )

        payment_request.chytapay_status = state_type
        payment_request.updated_at = datetime.utcnow()

        cuota = payment_request.cuota
        if not cuota:
            raise ValueError(f'Cuota no encontrada para {reference_id}')

        # ---------- payment_received ----------
        if tipo_evento == 'payment_request.payment_received':
            from app.models import Movimiento
            from sqlalchemy import func
            from decimal import Decimal

            # 1. ¿Cuánta plata ya entró para esta cuota?
            # Buscamos los movimientos de esta empresa cuyo comprobante coincida
            # con el reference_id de ChytaPay.
            pagos_previos = db.session.query(
                func.coalesce(func.sum(Movimiento.monto), 0)
            ).filter(
                Movimiento.id_empresa == cuota.socio.id_empresa,
                Movimiento.comprobante.like(f"ChytaPay-{reference_id}%"),
                Movimiento.tipo == 'ingreso',
                Movimiento.anulado == False
            ).scalar() or Decimal('0')

            monto_faltante = cuota.monto - pagos_previos

            # Si por alguna razón ya estaba paga, no hacemos nada
            if monto_faltante <= 0:
                if cuota.estado != 'pagada':
                    cuota.estado = 'pagada'
                    cuota.fecha_pago = cuota.fecha_pago or datetime.utcnow()
                return 'Cuota ya estaba totalmente pagada. Ignorado.'

            if state_type == 'total_payment':
                # ChytaPay recibió el total. Nosotros registramos solo el faltante.
                monto_a_cobrar = monto_faltante
            elif state_type == 'partial_payment':
                # ChytaPay recibió un parcial. 
                monto_parcial = Decimal(str(payload.get('amount') or 0))
                # El monto a cobrar es el menor entre lo que avisa ChytaPay
                # y lo que realmente falta para completar la cuota.
                monto_a_cobrar = min(monto_parcial, monto_faltante)
            else:
                return f'stateType "{state_type}" sin acción.'

            if monto_a_cobrar > 0:
                mov = cls._crear_movimiento_cobro_cuota(
                    cuota, float(monto_a_cobrar), reference_id
                )
                if not mov:
                    raise ValueError('No se pudo crear el movimiento de caja.')

            # 2. Re-evaluar el estado final de la cuota
            nuevo_total_pagado = pagos_previos + monto_a_cobrar
            if nuevo_total_pagado >= cuota.monto:
                cuota.estado = 'pagada'
                cuota.fecha_pago = datetime.utcnow()
                return f'Cuota {cuota.id} marcada como pagada (total completado).'
            else:
                return f'Pago parcial de ${monto_a_cobrar} registrado. Faltan ${cuota.monto - nuevo_total_pagado}.'

        # ---------- payment_request.expired ----------
        elif tipo_evento == 'payment_request.expired':
            if cuota.estado == 'pendiente':
                cuota.estado = 'vencida'
                return f'Cuota {cuota.id} marcada como vencida.'
            return f'Cuota {cuota.id} ya estaba {cuota.estado}. Ignorado.'

        # ---------- Otros ----------
        else:
            return f'Evento "{tipo_evento}" sin acción.'

    @classmethod
    def reintentar_creacion(cls, cuota):
        """
        Reintenta crear un payment request para una cuota que no tiene CVU.

        Retorna (ok: bool, mensaje: str).
        """
        if cuota.chytapay_cvu:
            return True, 'Ya tiene CVU.'

        if cuota.estado != 'pendiente':
            return False, f'Cuota no está pendiente ({cuota.estado}).'

        if not cuota.socio or not cuota.socio.enviar_cuota_whatsapp:
            return False, 'Socio sin notificación WhatsApp activada.'

        # Evitar duplicados: verificar si ya hay un payment request previo
        existing = ChytaPayPaymentRequest.query.filter_by(
            cuota_social_id=cuota.id
        ).first()
        if existing:
            # Ya tiene un payment request, sincronizar el CVU si es posible
            return False, 'Ya existe un payment request previo.'

        try:
            response = cls.create_payment_request(cuota, send_whatsapp=True)
            if response and response.get('CVU'):
                cuota.chytapay_cvu = response.get('CVU')
                db.session.commit()
                return True, f'CVU generado: {response.get("CVU")}'
            return False, 'Respuesta sin CVU.'
        except Exception as e:
            return False, f'Error: {str(e)[:200]}'

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    @classmethod
    def exchange_code_for_tokens(cls, code):
        """Canjea un código de autorización por tokens de acceso."""
        auth_url, _ = cls._get_urls()
        client_id = current_app.config['CHYTAPAY_CLIENT_ID']
        client_secret = current_app.config['CHYTAPAY_CLIENT_SECRET']

        # IMPORTANTE: usar el mismo redirect_uri que se mandó en /authorize.
        # Si usamos url_for(..., _external=True), Flask arma la URL con el
        # Host header del request actual, que detrás de ngrok es
        # 127.0.0.1:5000. Eso NO coincide con el redirect_uri registrado.
        base_url = (current_app.config.get('BASE_URL') or '').rstrip('/')
        if base_url:
            redirect_uri = f'{base_url}/oauth/callback'
        else:
            redirect_uri = url_for('oauth.oauth_callback', _external=True)

        payload = {
            "grantType": "authorization_code",
            "code": code,
            "clientId": client_id,
            "clientSecret": client_secret,
            "redirectUri": redirect_uri,
        }

        current_app.logger.info(
            f'OAuth token exchange → redirectUri: {redirect_uri}'
        )

        response = requests.post(
            f"{auth_url}/integration/oauth2/token",
            json=payload,
            timeout=30,
        )

        if response.status_code >= 400:
            current_app.logger.error(
                f'OAuth token exchange → error: {response.text[:500]}'
            )

        response.raise_for_status()
        return response.json()