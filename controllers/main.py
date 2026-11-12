import json
import logging

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)


class PagoticController(http.Controller):
    _webhook_url = '/payment/pagotic/webhook'
    _return_url = '/payment/pagotic/return'

    @http.route(
        _webhook_url,
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def pagotic_webhook(self, **kwargs):
        """Process a PagoTIC notification.

        PagoTIC does not sign its notifications. The body is only used to
        identify the payment; the authoritative status is always fetched from
        the PagoTIC API before updating the transaction.
        """
        payload = self._pagotic_get_payload()
        if not payload:
            return self._pagotic_json_response({'status': 'invalid'}, status=400)

        payment_id = self._pagotic_extract_payment_id(payload)
        if not payment_id:
            _logger.warning("PagoTIC webhook without a payment ID: %s", payload)
            return self._pagotic_json_response({'status': 'ignored'})

        tx = self._pagotic_find_transaction(payment_id, payload)
        if not tx:
            _logger.warning("PagoTIC webhook for unknown payment %s", payment_id)
            return self._pagotic_json_response({'status': 'ignored'})

        try:
            payment = tx.provider_id._pagotic_get_payment(payment_id)
        except Exception:
            _logger.exception("Could not fetch PagoTIC payment %s", payment_id)
            return self._pagotic_json_response({'status': 'error'}, status=500)

        try:
            tx._pagotic_validate_notification_payment(payment)
        except ValidationError as exc:
            _logger.warning("Rejected PagoTIC notification for %s: %s", tx.reference, exc)
            try:
                tx._set_error(str(exc))
            except Exception:
                _logger.exception("Could not mark transaction %s as error", tx.reference)
            return self._pagotic_json_response({'status': 'rejected'})

        try:
            tx._process('pagotic', tx._pagotic_payment_to_notification(payment))
        except Exception:
            _logger.exception("Could not process PagoTIC notification for %s", tx.reference)
            return self._pagotic_json_response({'status': 'error'}, status=500)

        return self._pagotic_json_response({'status': 'ok'})

    @http.route(
        _return_url,
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False,
    )
    def pagotic_return(self, reference=None, **kwargs):
        """Synchronize the transaction when the customer returns from checkout."""
        payment_id = kwargs.get('payment_id') or kwargs.get('id')
        tx = self._pagotic_find_transaction(payment_id, {'reference': reference})
        if tx and tx.provider_reference:
            try:
                payment = tx.provider_id._pagotic_get_payment(tx.provider_reference)
                tx._pagotic_validate_notification_payment(payment)
                tx._process('pagotic', tx._pagotic_payment_to_notification(payment))
            except Exception:
                _logger.exception(
                    "Could not synchronize PagoTIC transaction %s on return", tx.reference
                )
        return request.redirect('/payment/status')

    # === HELPERS === #

    def _pagotic_get_payload(self):
        """Return the JSON body of the request, or None if it is invalid."""
        try:
            raw_body = request.httprequest.get_data(as_text=True)
            payload = json.loads(raw_body) if raw_body else None
        except (TypeError, ValueError):
            _logger.warning("Invalid JSON received on the PagoTIC webhook")
            return None
        if not isinstance(payload, dict):
            return None
        if 'id' not in payload and isinstance(payload.get('payment'), dict):
            return payload['payment']
        return payload

    def _pagotic_extract_payment_id(self, payload):
        """Extract the PagoTIC payment ID from a notification body."""
        payment_id = payload.get('id') or payload.get('payment_id')
        return str(payment_id) if payment_id else None

    def _pagotic_find_transaction(self, payment_id, payload):
        """Find the Odoo transaction matching a PagoTIC notification."""
        domain = [('provider_code', '=', 'pagotic')]
        if payment_id:
            tx = request.env['payment.transaction'].sudo().search(
                domain + [('provider_reference', '=', str(payment_id))], limit=1
            )
            if tx:
                return tx
        reference = payload.get('external_transaction_id') or payload.get('reference')
        if reference:
            return request.env['payment.transaction'].sudo().search(
                domain + [('reference', '=', reference)], limit=1
            )
        return request.env['payment.transaction']

    def _pagotic_json_response(self, data, status=200):
        """Return a JSON response with the given HTTP status."""
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )
