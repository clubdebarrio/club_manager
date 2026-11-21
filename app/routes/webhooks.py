# app/routes/webhooks.py
from flask import Blueprint, request, jsonify, current_app
from app.services.chytapay_service import ChytaPayService


webhooks_bp = Blueprint('webhooks', __name__, url_prefix='/webhooks')


@webhooks_bp.route('/chytapay', methods=['POST'])
def chytapay_webhook():
    """
    Endpoint de webhooks de ChytaPay.

    La autenticación se hace por firma HMAC-SHA256:
      - Header X-Chytapay-Signature: "t=<timestamp>,v1=<hmac_hex>"
      - Se firma el string "{t}.{cuerpo_crudo}".
      - Se valida con el CHYTAPAY_WEBHOOK_SECRET (con prefijo whsec_).

    La deduplicación se hace por X-Chytapay-Webhook-Id.
    """
    try:
        # --- Cuerpo crudo (¡ANTES de parsear!) ---
        raw_body = request.get_data()

        # --- Headers de ChytaPay ---
        signature_header = request.headers.get('X-Chytapay-Signature')
        webhook_id = request.headers.get('X-Chytapay-Webhook-Id')
        validation_token = request.headers.get('validation-token')  # legacy

        if not raw_body:
            return jsonify({"error": "Empty body"}), 400

        # --- Payload parseado para el service ---
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Invalid JSON"}), 400

        # --- Delegar al service ---
        ChytaPayService.process_webhook(
            payload=payload,
            signature_header=signature_header,
            raw_body=raw_body,
            webhook_id=webhook_id,
            validation_token=validation_token,
        )
        return jsonify({"status": "ok"}), 200

    except ValueError as e:
        # Errores esperados (firma inválida, body inválido, etc.)
        current_app.logger.warning(f"Webhook rechazado: {e}")
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        # Errores inesperados → 500 → ChytaPay reintenta (y deduplicamos)
        current_app.logger.exception(f"Webhook error: {e}")
        return jsonify({"error": "Internal server error"}), 500