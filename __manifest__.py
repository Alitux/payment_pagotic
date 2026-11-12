{
    'name': "Payment Provider: PagoTIC",
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "A payment provider for PagoTIC (Argentina).",
    'description': """
PagoTIC Payment Provider
========================

Integrates the PagoTIC payment gateway into Odoo using the official
``pagotic-sdk`` Python package.

The module provides a hosted checkout flow, webhook processing and full
refunds. It is designed for Argentine companies collecting payments in ARS.
    """,
    'author': "Alejandro Fábrega (alitux@disroot.org)",
    'website': "https://alitux.com.ar",
    'license': 'GPL-3',
    'depends': ['payment'],
    'external_dependencies': {
        'python': ['pagotic'],
    },
    'data': [
        'views/payment_pagotic_templates.xml',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',

        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
