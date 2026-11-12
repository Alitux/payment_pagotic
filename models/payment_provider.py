import logging

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.payment_pagotic import const

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('pagotic', "PagoTIC")],
        ondelete={'pagotic': 'set default'},
    )

    # Credentials.
    pagotic_username = fields.Char(
        string="PagoTIC Username",
        required_if_provider='pagotic',
        copy=False,
    )
    pagotic_password = fields.Char(
        string="PagoTIC Password",
        required_if_provider='pagotic',
        copy=False,
    )
    pagotic_client_id = fields.Char(
        string="PagoTIC Client ID",
        required_if_provider='pagotic',
        copy=False,
    )
    pagotic_client_secret = fields.Char(
        string="PagoTIC Client Secret",
        required_if_provider='pagotic',
        copy=False,
    )

    # Endpoints.
    pagotic_auth_url = fields.Char(
        string="Authentication URL",
        default=lambda self: self._default_pagotic_auth_url(),
        required_if_provider='pagotic',
        copy=False,
        help="Base URL of the PagoTIC authentication server. "
             "Default: https://a.paypertic.com",
    )
    pagotic_api_url = fields.Char(
        string="API URL",
        default=lambda self: self._default_pagotic_api_url(),
        required_if_provider='pagotic',
        copy=False,
        help="Base URL of the PagoTIC API. Default: https://api.paypertic.com",
    )
    pagotic_realm = fields.Char(
        string="Realm",
        default=lambda self: self._default_pagotic_realm(),
        required_if_provider='pagotic',
        copy=False,
        help="Keycloak realm used by PagoTIC. Default: entidades",
    )

    # Checkout configuration.
    pagotic_concept_id = fields.Char(
        string="Concept ID",
        required_if_provider='pagotic',
        help="Concept identifier sent to PagoTIC in every payment detail. "
             "It is required by the API in practice.",
    )
    pagotic_concept_description = fields.Char(
        string="Concept Description",
        default="Pago en Odoo",
        help="Optional description sent to PagoTIC with the payment detail.",
    )
    pagotic_media_payment_ids = fields.Char(
        string="Checkout Payment Method IDs",
        help="Optional comma-separated list of PagoTIC media_payment_id values "
             "used to restrict the hosted checkout (for example: 9,5,85). "
             "Leave empty to show every method enabled for the merchant.",
    )
    pagotic_collector_id = fields.Char(
        string="Collector ID",
        readonly=True,
        copy=False,
        help="Merchant identifier discovered from the PagoTIC access token. "
             "It can be left empty; it is fetched automatically on the first payment.",
    )

    def _default_pagotic_auth_url(self):
        return 'https://a.paypertic.com'

    def _default_pagotic_api_url(self):
        return 'https://api.paypertic.com'

    def _default_pagotic_realm(self):
        return 'entidades'

    def _compute_feature_support_fields(self):
        """Declare the features supported by PagoTIC."""
        res = super()._compute_feature_support_fields()
        self.filtered(lambda provider: provider.code == 'pagotic').update({
            'support_tokenization': False,
            'support_manual_capture': None,
            'support_express_checkout': False,
            'support_refund': 'full_only',
        })
        return res

    def _get_supported_currencies(self):
        """Restrict PagoTIC to Argentine pesos."""
        supported_currencies = super()._get_supported_currencies()
        if self.code != 'pagotic':
            return supported_currencies
        ars = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', const.SUPPORTED_CURRENCY)], limit=1
        )
        return ars or supported_currencies

    def _get_default_payment_method_codes(self):
        """Return the default payment method codes for PagoTIC."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'pagotic':
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_removal_values(self):
        """Clear the credentials when the module is uninstalled."""
        res = super()._get_removal_values()
        res.update({
            'pagotic_username': False,
            'pagotic_password': False,
            'pagotic_client_id': False,
            'pagotic_client_secret': False,
            'pagotic_collector_id': False,
        })
        return res

    def _pagotic_validate_configuration(self):
        """Ensure that the provider is ready to create payments."""
        self.ensure_one()
        if self.code != 'pagotic':
            return
        required_fields = {
            'pagotic_username': self.pagotic_username,
            'pagotic_password': self.pagotic_password,
            'pagotic_client_id': self.pagotic_client_id,
            'pagotic_client_secret': self.pagotic_client_secret,
            'pagotic_concept_id': self.pagotic_concept_id,
        }
        missing = [self._fields[field_name].string for field_name, value in required_fields.items()
                   if not value]
        if missing:
            raise UserError(_(
                "PagoTIC is not correctly configured. Please fill in: %(fields)s.",
                fields=", ".join(missing),
            ))

    def _pagotic_get_client(self):
        """Return a configured PagoTIC SDK client.

        The caller is responsible for closing the client. Prefer using it as a
        context manager::

            with provider._pagotic_get_client() as client:
                ...
        """
        self.ensure_one()
        self._pagotic_validate_configuration()
        try:
            from pagotic import PagoTIC
        except ImportError as exc:
            raise UserError(_(
                "The PagoTIC SDK is not installed. Install the Python package "
                "`pagotic-sdk` (see the module's requirements.txt) and restart Odoo."
            )) from exc
        try:
            return PagoTIC(
                username=self.pagotic_username,
                password=self.pagotic_password,
                client_id=self.pagotic_client_id,
                client_secret=self.pagotic_client_secret,
                auth_url=self.pagotic_auth_url or None,
                api_url=self.pagotic_api_url or None,
                realm=self.pagotic_realm or None,
            )
        except Exception as exc:  # The SDK validates credentials eagerly.
            raise UserError(_(
                "Could not initialize the PagoTIC client: %(error)s", error=exc
            )) from exc

    def _pagotic_get_collector_id(self):
        """Fetch the merchant identifier from the PagoTIC token."""
        self.ensure_one()
        with self._pagotic_get_client() as client:
            return client.get_collector_id()

    def _pagotic_parse_media_payment_ids(self):
        """Return the configured checkout payment method IDs as integers."""
        self.ensure_one()
        raw_value = (self.pagotic_media_payment_ids or '').replace(' ', '')
        if not raw_value:
            return []
        media_payment_ids = []
        for token in raw_value.split(','):
            if not token:
                continue
            try:
                media_payment_ids.append(int(token))
            except ValueError as exc:
                raise ValidationError(_(
                    "The PagoTIC checkout payment method IDs must be a comma-separated "
                    "list of integers. Invalid value: %(value)s", value=token
                )) from exc
        return media_payment_ids

    def _pagotic_get_payment(self, payment_id):
        """Fetch an authoritative payment from the PagoTIC API."""
        self.ensure_one()
        with self._pagotic_get_client() as client:
            return client.payments.get(payment_id)

    def action_pagotic_fetch_collector_id(self):
        """Fetch and store the collector ID from the PagoTIC token."""
        self.ensure_one()
        if self.code != 'pagotic':
            return
        try:
            collector_id = self._pagotic_get_collector_id()
        except Exception as exc:
            _logger.exception("Could not fetch the PagoTIC collector ID")
            raise UserError(_(
                "Could not fetch the PagoTIC collector ID: %(error)s", error=exc
            )) from exc
        self.pagotic_collector_id = collector_id
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("PagoTIC"),
                'message': _("Collector ID: %(collector_id)s", collector_id=collector_id),
                'type': 'success',
                'sticky': False,
            },
        }
