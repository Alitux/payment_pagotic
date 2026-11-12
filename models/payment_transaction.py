import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, quote, urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.payment_pagotic import const

_logger = logging.getLogger(__name__)


def _pagotic_status_value(value):
    """Return the string value of a PagoTIC status enum or string."""
    return str(getattr(value, 'value', value) or '').strip().lower()


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    pagotic_form_url = fields.Char(
        string="PagoTIC Checkout URL",
        readonly=True,
        copy=False,
    )
    pagotic_status = fields.Char(
        string="PagoTIC Status",
        readonly=True,
        copy=False,
    )
    pagotic_status_detail = fields.Char(
        string="PagoTIC Status Detail",
        readonly=True,
        copy=False,
    )

    # === PROVIDER-SPECIFIC PROCESSING === #

    def _get_specific_rendering_values(self, processing_values):
        """Return the values used to render the redirect form."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'pagotic' or self.operation != 'online_redirect':
            return res

        form_url = self._pagotic_get_or_create_checkout()
        form_action, form_params = self._pagotic_split_form_url(form_url)
        return {
            **res,
            'form_url': form_url,
            'form_action': form_action,
            'form_params': form_params,
            'reference': self.reference,
        }

    def _pagotic_get_or_create_checkout(self):
        """Return the hosted checkout URL, creating the PagoTIC payment once."""
        self.ensure_one()
        self._ensure_provider_is_not_disabled()

        if self.pagotic_form_url:
            return self.pagotic_form_url

        if self.provider_reference:
            # The payment exists but its checkout URL was not persisted. Reusing
            # the same external_transaction_id would be rejected by PagoTIC, and
            # creating another payment would duplicate the charge attempt.
            raise UserError(_(
                "The PagoTIC checkout URL is missing for this transaction. "
                "Please cancel it and start a new payment."
            ))

        payment = self._pagotic_create_payment()
        if not payment.form_url:
            raise UserError(_(
                "PagoTIC did not return a checkout URL. Please try again or contact "
                "the payment provider."
            ))
        self.write({
            'provider_reference': str(payment.id),
            'pagotic_form_url': payment.form_url,
            'pagotic_status': _pagotic_status_value(payment.status) or False,
            'pagotic_status_detail': payment.status_detail or False,
        })
        return payment.form_url

    def _pagotic_create_payment(self):
        """Create the payment in PagoTIC and return the SDK Payment object."""
        self.ensure_one()
        provider = self.provider_id
        provider._pagotic_validate_configuration()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url').rstrip('/')
        reference = quote(self.reference, safe='')
        return_url = f"{base_url}/payment/pagotic/return?reference={reference}"
        notification_url = f"{base_url}/payment/pagotic/webhook"

        amount = float(self.currency_id.round(self.amount))
        detail = {
            'external_reference': self.reference,
            'concept_id': provider.pagotic_concept_id,
            'amount': amount,
        }
        if provider.pagotic_concept_description:
            detail['concept_description'] = provider.pagotic_concept_description

        payer = {
            'name': self.partner_id.name or self.partner_id.display_name or _("Consumidor Final"),
            'external_reference': str(self.partner_id.id),
        }
        if self.partner_id.email:
            payer['email'] = self.partner_id.email

        payload = {
            'external_transaction_id': self.reference,
            'currency_id': self.currency_id.name,
            'return_url': return_url,
            'back_url': return_url,
            'notification_url': notification_url,
            'details': [detail],
            'payer': payer,
            'metadata': {
                'odoo_transaction_id': self.id,
                'odoo_reference': self.reference,
                'odoo_company_id': self.company_id.id,
            },
        }

        media_payment_ids = provider._pagotic_parse_media_payment_ids()
        if media_payment_ids:
            payload['presets'] = {'media_payment_ids': media_payment_ids}

        with provider._pagotic_get_client() as client:
            collector_id = provider.pagotic_collector_id
            if not collector_id:
                collector_id = client.get_collector_id()
                provider.pagotic_collector_id = collector_id
            payload['collector_id'] = collector_id
            return client.payments.create(payload)

    def _pagotic_split_form_url(self, form_url):
        """Split a checkout URL into an action and hidden form parameters.

        PagoTIC returns the checkout URL with a query string. A GET form with
        no inputs would discard that query string in the browser, so the
        parameters are rendered as hidden inputs instead.
        """
        self.ensure_one()
        parts = urlsplit(form_url)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise UserError(_("PagoTIC returned an invalid checkout URL."))
        form_action = f"{parts.scheme}://{parts.netloc}{parts.path}"
        form_params = [
            {'name': name, 'value': value}
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return form_action, form_params

    # === NOTIFICATION HANDLING === #

    @api.model
    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Find the transaction matching a PagoTIC notification."""
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'pagotic':
            return tx

        payment_id = notification_data.get('payment_id') or notification_data.get('id')
        if payment_id:
            tx = self.search([
                ('provider_code', '=', 'pagotic'),
                ('provider_reference', '=', str(payment_id)),
            ], limit=1)
            if tx:
                return tx

        reference = (
            notification_data.get('external_transaction_id')
            or notification_data.get('reference')
        )
        if reference:
            tx = self.search([
                ('provider_code', '=', 'pagotic'),
                ('reference', '=', reference),
            ], limit=1)
            if tx:
                return tx

        raise ValidationError(_(
            "PagoTIC: no transaction found for the notification data."
        ))

    def _process_notification_data(self, notification_data):
        """Update the transaction from a PagoTIC notification."""
        super()._process_notification_data(notification_data)
        if self.provider_code != 'pagotic':
            return

        payment_id = notification_data.get('payment_id') or notification_data.get('id')
        status = _pagotic_status_value(notification_data.get('status'))
        status_detail = notification_data.get('status_detail') or False

        values = {}
        if payment_id:
            values['provider_reference'] = str(payment_id)
        if status:
            values['pagotic_status'] = status
        values['pagotic_status_detail'] = status_detail
        if values:
            self.write(values)

        self._pagotic_update_state(status, status_detail)

    def _pagotic_update_state(self, status, status_detail=None):
        """Map a PagoTIC status to an Odoo transaction state."""
        self.ensure_one()
        status = _pagotic_status_value(status)
        message = _("PagoTIC status: %(status)s", status=status or '-')
        if status_detail:
            message = f"{message} ({status_detail})"

        if status in const.DONE_STATUSES:
            self._set_done(state_message=message)
        elif status in const.PENDING_STATUSES:
            self._set_pending(state_message=message)
        elif status in const.CANCELED_STATUSES:
            self._set_canceled(state_message=message)
        elif status in const.ERROR_STATUSES:
            self._set_error(message)
        elif status == 'refunded':
            # The payment was collected and later refunded from PagoTIC. The
            # Odoo transaction remains confirmed; a refund created from Odoo
            # is tracked in a separate child transaction.
            self._set_done(state_message=message, extra_allowed_states=('pending',))
        else:
            _logger.warning(
                "Unhandled PagoTIC status %r for transaction %s", status, self.reference
            )

    def _pagotic_validate_notification_payment(self, payment):
        """Validate an authoritative payment against the Odoo transaction."""
        self.ensure_one()

        payment_id = str(payment.id)
        if self.provider_reference and str(self.provider_reference) != payment_id:
            raise ValidationError(_(
                "PagoTIC: the payment ID does not match the transaction."
            ))

        external_reference = payment.external_transaction_id
        if external_reference and external_reference != self.reference:
            raise ValidationError(_(
                "PagoTIC: the external transaction ID %(external_reference)s does not "
                "match the transaction reference %(reference)s.",
                external_reference=external_reference,
                reference=self.reference,
            ))

        provider_collector_id = self.provider_id.pagotic_collector_id
        payment_collector_id = str(payment.collector_id or '')
        if provider_collector_id and payment_collector_id:
            if provider_collector_id != payment_collector_id:
                raise ValidationError(_(
                    "PagoTIC: the payment collector does not match the configured collector."
                ))
        elif payment_collector_id and not provider_collector_id:
            self.provider_id.pagotic_collector_id = payment_collector_id

        if payment.currency_id and self.currency_id.name:
            if payment.currency_id.upper() != self.currency_id.name.upper():
                raise ValidationError(_(
                    "PagoTIC: the payment currency %(payment_currency)s does not match "
                    "the transaction currency %(currency)s.",
                    payment_currency=payment.currency_id,
                    currency=self.currency_id.name,
                ))

        paid_amount = self._pagotic_get_payment_amount(payment)
        if paid_amount is not None and self.currency_id.compare_amounts(
            float(paid_amount), self.amount
        ) != 0:
            raise ValidationError(_(
                "PagoTIC: the payment amount %(paid_amount)s does not match the "
                "transaction amount %(amount)s.",
                paid_amount=paid_amount,
                amount=self.amount,
            ))

    def _pagotic_get_payment_amount(self, payment):
        """Return the amount effectively created in PagoTIC, if available."""
        self.ensure_one()
        details = payment.details or []
        if details:
            total = Decimal('0')
            for detail in details:
                try:
                    total += Decimal(str(detail.amount))
                except (InvalidOperation, TypeError, ValueError):
                    continue
            return total
        if payment.final_amount is not None:
            return Decimal(str(payment.final_amount))
        return None

    def _pagotic_payment_to_notification(self, payment):
        """Convert an SDK Payment object into Odoo notification data."""
        self.ensure_one()
        amount = self._pagotic_get_payment_amount(payment)
        return {
            'payment_id': str(payment.id),
            'reference': self.reference,
            'external_transaction_id': payment.external_transaction_id,
            'amount': float(amount) if amount is not None else self.amount,
            'currency_code': (payment.currency_id or self.currency_id.name).upper(),
            'status': _pagotic_status_value(payment.status),
            'status_detail': payment.status_detail,
        }

    def action_pagotic_sync_status(self):
        """Fetch the current payment status from PagoTIC and update the transaction."""
        self.ensure_one()
        if self.provider_code != 'pagotic':
            return False
        if not self.provider_reference:
            raise UserError(_("This transaction has no PagoTIC payment ID yet."))
        payment = self.provider_id._pagotic_get_payment(self.provider_reference)
        try:
            self._pagotic_validate_notification_payment(payment)
        except ValidationError as exc:
            self._set_error(str(exc))
            raise
        self._handle_notification_data(
            'pagotic', self._pagotic_payment_to_notification(payment)
        )
        return True

    # === REFUNDS === #

    def _send_refund_request(self, amount_to_refund=None):
        """Request a full refund from PagoTIC."""
        refund_tx = super()._send_refund_request(amount_to_refund)
        if self.provider_code != 'pagotic':
            return refund_tx

        self.ensure_one()
        self._ensure_provider_is_not_disabled()
        if not self.provider_reference:
            raise UserError(_("The transaction has no PagoTIC payment ID."))
        if self.currency_id.compare_amounts(
            amount_to_refund or self.amount, self.amount
        ) != 0:
            raise UserError(_("PagoTIC only supports full refunds."))

        response_status = None
        try:
            with self.provider_id._pagotic_get_client() as client:
                response = client.payments.refund(
                    self.provider_reference,
                    {
                        'type': 'online',
                        'reason': _("Refund requested from Odoo"),
                        'external_reference': refund_tx.reference,
                        'metadata': {
                            'odoo_refund_transaction_id': refund_tx.id,
                            'odoo_source_transaction_id': self.id,
                        },
                    },
                )
                response_status = _pagotic_status_value(response.status)
        except Exception as exc:
            _logger.exception("PagoTIC refund failed for transaction %s", self.reference)
            raise UserError(_(
                "The PagoTIC refund could not be processed: %(error)s", error=exc
            )) from exc

        refund_tx.write({
            'provider_reference': str(response.id or self.provider_reference),
            'pagotic_status': response_status or False,
            'pagotic_status_detail': response.status_detail or False,
        })
        message = _("PagoTIC refund status: %(status)s", status=response_status or '-')
        if response_status == 'approved':
            refund_tx._set_done(state_message=message)
        elif response_status == 'rejected':
            refund_tx._set_error(message)
        else:
            refund_tx._set_pending(state_message=message)
        return refund_tx
