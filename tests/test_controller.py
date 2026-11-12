import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.payment_pagotic.models.payment_provider import PaymentProvider


@tagged('post_install', '-at_install')
class TestPagoticController(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref('payment_pagotic.payment_provider_pagotic')
        cls.provider.write({
            'state': 'test',
            'pagotic_username': 'pagotic-user',
            'pagotic_password': 'pagotic-password',
            'pagotic_client_id': 'pagotic-client-id',
            'pagotic_client_secret': 'pagotic-client-secret',
            'pagotic_concept_id': '123',
            'pagotic_collector_id': '14020',
        })
        cls.currency = cls.env.ref('base.ARS')
        cls.currency.active = True
        cls.partner = cls.env['res.partner'].create({'name': 'PagoTIC HTTP Test'})
        cls.tx = cls.env['payment.transaction'].create({
            'provider_id': cls.provider.id,
            'payment_method_id': cls.provider.payment_method_ids[:1].id,
            'reference': 'PAGOTIC-HTTP-1',
            'amount': 123.45,
            'currency_id': cls.currency.id,
            'partner_id': cls.partner.id,
            'operation': 'online_redirect',
            'provider_reference': 'pay_http_1',
        })

    def _payment(self):
        return SimpleNamespace(
            id='pay_http_1',
            external_transaction_id='PAGOTIC-HTTP-1',
            collector_id='14020',
            currency_id='ARS',
            details=[SimpleNamespace(amount=Decimal('123.45'))],
            final_amount=Decimal('123.45'),
            status='approved',
            status_detail='OK',
        )

    def test_webhook_approved(self):
        """The webhook fetches the payment and confirms the transaction."""
        with patch.object(
            PaymentProvider, '_pagotic_get_payment', return_value=self._payment()
        ):
            response = self.url_open(
                '/payment/pagotic/webhook',
                data=json.dumps({'id': 'pay_http_1'}),
                headers={'Content-Type': 'application/json'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.tx.invalidate_recordset()
        self.assertEqual(self.tx.state, 'done')

    def test_webhook_rejects_amount_mismatch(self):
        """The webhook does not confirm a payment with a different amount."""
        payment = self._payment()
        payment.details = [SimpleNamespace(amount=Decimal('1.00'))]
        payment.final_amount = Decimal('1.00')
        with patch.object(PaymentProvider, '_pagotic_get_payment', return_value=payment):
            response = self.url_open(
                '/payment/pagotic/webhook',
                data=json.dumps({'id': 'pay_http_1'}),
                headers={'Content-Type': 'application/json'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'rejected'})
        self.tx.invalidate_recordset()
        self.assertEqual(self.tx.state, 'error')

    def test_return_synchronizes_transaction(self):
        """The return route synchronizes the payment before redirecting."""
        with patch.object(
            PaymentProvider, '_pagotic_get_payment', return_value=self._payment()
        ):
            response = self.url_open(
                '/payment/pagotic/return?reference=PAGOTIC-HTTP-1',
                allow_redirects=False,
            )

        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers.get('Location'), '/payment/status')
        self.tx.invalidate_recordset()
        self.assertEqual(self.tx.state, 'done')
