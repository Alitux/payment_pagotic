from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.payment.tests.common import PaymentCommon
from odoo.addons.payment_pagotic.models.payment_provider import PaymentProvider


@tagged('post_install', '-at_install')
class TestPaymentPagotic(PaymentCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.currency = cls._prepare_currency('ARS')
        cls.provider = cls._prepare_provider(
            code='pagotic',
            update_values={
                'pagotic_username': 'pagotic-user',
                'pagotic_password': 'pagotic-password',
                'pagotic_client_id': 'pagotic-client-id',
                'pagotic_client_secret': 'pagotic-client-secret',
                'pagotic_concept_id': '123',
                'pagotic_collector_id': '14020',
            },
        )
        cls.payment_method = cls.provider.payment_method_ids.filtered(
            lambda payment_method: payment_method.code == 'pagotic'
        )[:1]
        cls.payment_method_id = cls.payment_method.id
        cls.payment_method_code = cls.payment_method.code
        cls.amount = 1500.50
        cls.reference = 'PAGOTIC-TEST'

    def _mock_client(self, *, payment=None, refund=None, collector_id='14020'):
        client = MagicMock()
        client.get_collector_id.return_value = collector_id
        client.payments.create.return_value = payment
        client.payments.refund.return_value = refund
        context_manager = MagicMock()
        context_manager.__enter__.return_value = client
        return context_manager, client

    def test_create_checkout(self):
        """The checkout payment is created once and its URL is persisted."""
        payment = SimpleNamespace(
            id='pay_1',
            form_url='https://checkout.example/pay?token=abc&foo=bar',
            status='pending',
            status_detail=None,
        )
        context_manager, client = self._mock_client(payment=payment)
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-CHECKOUT')

        with patch.object(PaymentProvider, '_pagotic_get_client', return_value=context_manager):
            values = tx._get_specific_rendering_values({'reference': tx.reference})

        self.assertEqual(tx.provider_reference, 'pay_1')
        self.assertEqual(tx.pagotic_form_url, payment.form_url)
        self.assertEqual(values['form_action'], 'https://checkout.example/pay')
        self.assertEqual(
            values['form_params'],
            [
                {'name': 'token', 'value': 'abc'},
                {'name': 'foo', 'value': 'bar'},
            ],
        )
        html = self.env['ir.qweb']._render(self.provider.redirect_form_view_id.id, values)
        self.assertIn('action="https://checkout.example/pay"', html)
        self.assertIn('name="token"', html)
        self.assertIn('value="abc"', html)
        payload = client.payments.create.call_args.args[0]
        self.assertEqual(payload['external_transaction_id'], tx.reference)
        self.assertEqual(payload['collector_id'], '14020')
        self.assertEqual(payload['currency_id'], 'ARS')
        self.assertEqual(payload['details'][0]['concept_id'], '123')
        self.assertEqual(payload['details'][0]['amount'], 1500.5)

        # A second call must reuse the persisted checkout URL, not create another payment.
        with patch.object(PaymentProvider, '_pagotic_get_client', return_value=context_manager):
            tx._get_specific_rendering_values({'reference': tx.reference})
        self.assertEqual(client.payments.create.call_count, 1)

    def test_notification_status_mapping(self):
        """PagoTIC statuses are mapped to Odoo transaction states."""
        cases = [
            ('approved', 'done'),
            ('pending', 'pending'),
            ('in_process', 'pending'),
            ('cancelled', 'cancel'),
            ('objected', 'cancel'),
            ('rejected', 'error'),
            ('overdue', 'error'),
        ]
        for index, (pago_status, odoo_state) in enumerate(cases):
            tx = self._create_transaction(
                flow='redirect', reference=f'PAGOTIC-STATUS-{index}'
            )
            tx._handle_notification_data('pagotic', {
                'payment_id': f'pay_{index}',
                'reference': tx.reference,
                'external_transaction_id': tx.reference,
                'status': pago_status,
                'status_detail': 'detail',
            })
            self.assertEqual(
                tx.state,
                odoo_state,
                f"PagoTIC status {pago_status!r} should map to {odoo_state!r}",
            )
            self.assertEqual(tx.provider_reference, f'pay_{index}')
            self.assertEqual(tx.pagotic_status, pago_status)

    def test_notification_accepts_status_enum(self):
        """The SDK returns statuses as enums; their value must be persisted."""
        from pagotic import PaymentStatus

        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-ENUM')
        tx._handle_notification_data('pagotic', {
            'payment_id': 'pay_enum',
            'reference': tx.reference,
            'external_transaction_id': tx.reference,
            'status': PaymentStatus.APPROVED,
            'status_detail': 'OK',
        })
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.pagotic_status, 'approved')

    def test_find_transaction_by_payment_id(self):
        """Notifications can be matched by the PagoTIC payment ID."""
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-FIND')
        tx.provider_reference = 'pay_find'
        found_tx = self.env['payment.transaction']._get_tx_from_notification_data(
            'pagotic', {'payment_id': 'pay_find'}
        )
        self.assertEqual(found_tx, tx)

    def test_find_transaction_by_reference(self):
        """Notifications can be matched by the external transaction ID."""
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-FIND-REF')
        found_tx = self.env['payment.transaction']._get_tx_from_notification_data(
            'pagotic', {'external_transaction_id': 'PAGOTIC-FIND-REF'}
        )
        self.assertEqual(found_tx, tx)

    def test_amount_validation(self):
        """A notification with a different amount is rejected."""
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-AMOUNT')
        tx.provider_reference = 'pay_amount'
        valid_payment = SimpleNamespace(
            id='pay_amount',
            external_transaction_id=tx.reference,
            collector_id='14020',
            currency_id='ARS',
            details=[SimpleNamespace(amount=Decimal('1500.50'))],
            final_amount=Decimal('1500.50'),
        )
        tx._pagotic_validate_notification_payment(valid_payment)

        invalid_payment = SimpleNamespace(
            id='pay_amount',
            external_transaction_id=tx.reference,
            collector_id='14020',
            currency_id='ARS',
            details=[SimpleNamespace(amount=Decimal('10.00'))],
            final_amount=Decimal('10.00'),
        )
        with self.assertRaises(ValidationError):
            tx._pagotic_validate_notification_payment(invalid_payment)

    def test_external_reference_validation(self):
        """A notification for another Odoo reference is rejected."""
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-EXTERNAL')
        tx.provider_reference = 'pay_external'
        payment = SimpleNamespace(
            id='pay_external',
            external_transaction_id='OTHER-REFERENCE',
            collector_id='14020',
            currency_id='ARS',
            details=[SimpleNamespace(amount=Decimal('1500.50'))],
            final_amount=Decimal('1500.50'),
        )
        with self.assertRaises(ValidationError):
            tx._pagotic_validate_notification_payment(payment)

    def test_full_refund(self):
        """A full refund creates a confirmed child transaction."""
        tx = self._create_transaction(flow='redirect', reference='PAGOTIC-REFUND')
        tx.provider_reference = 'pay_refund'
        tx._handle_notification_data('pagotic', {
            'payment_id': 'pay_refund',
            'reference': tx.reference,
            'external_transaction_id': tx.reference,
            'status': 'approved',
        })

        refund = SimpleNamespace(id='refund_1', status='approved', status_detail='OK')
        context_manager, client = self._mock_client(refund=refund)
        with patch.object(PaymentProvider, '_pagotic_get_client', return_value=context_manager):
            refund_tx = tx._send_refund_request()

        self.assertEqual(refund_tx.amount, -tx.amount)
        self.assertEqual(refund_tx.state, 'done')
        self.assertEqual(refund_tx.source_transaction_id, tx)
        self.assertEqual(refund_tx.provider_reference, 'refund_1')
        client.payments.refund.assert_called_once()

    def test_parse_media_payment_ids(self):
        """Checkout media IDs are parsed from the comma-separated configuration."""
        self.provider.pagotic_media_payment_ids = '9, 5,85'
        self.assertEqual(self.provider._pagotic_parse_media_payment_ids(), [9, 5, 85])
        self.provider.pagotic_media_payment_ids = '9,invalid'
        with self.assertRaises(ValidationError):
            self.provider._pagotic_parse_media_payment_ids()
