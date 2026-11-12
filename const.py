# The codes of the payment methods to activate when PagoTIC is activated.
DEFAULT_PAYMENT_METHOD_CODES = {'pagotic'}

#: Currency used by PagoTIC.
SUPPORTED_CURRENCY = 'ARS'

#: PagoTIC statuses that still require a confirmation from the gateway.
PENDING_STATUSES = {
    'pending',
    'issued',
    'in_process',
    'review',
    'validate',
    'deferred',
}

#: PagoTIC statuses that confirm that the payment was approved.
DONE_STATUSES = {'approved'}

#: PagoTIC statuses that mean the payment will not be collected.
CANCELED_STATUSES = {'cancelled', 'objected'}

#: PagoTIC statuses that mean the payment failed.
ERROR_STATUSES = {'rejected', 'overdue'}
