# PagoTIC para Odoo 18

Módulo de pasarela de pago para [PagoTIC](https://www.pagotic.com.ar/) basado en
el SDK oficial [`pagotic-sdk`](https://alitux.github.io/pagotic-sdk/).

Implementa un checkout alojado (redirección), procesamiento de notificaciones
(webhook), sincronización manual de estados y devoluciones totales.

## Autor

Alejandro Fábrega — [alitux@disroot.org](mailto:alitux@disroot.org)

## Requisitos

- Odoo 18.0.
- Python 3.10 o superior.
- `pagotic-sdk` (se declara en `requirements.txt`).
- Credenciales de PagoTIC: usuario, contraseña, `client_id` y `client_secret`.
- Un `concept_id` configurado en PagoTIC para identificar el concepto de cobro.
- Moneda de la compañía: ARS.

## Instalación

1. Copiar la carpeta `payment_pagotic` dentro de un directorio de addons de Odoo.
2. Instalar la dependencia Python:

   ```bash
   pip install -r payment_pagotic/requirements.txt
   ```

3. Reiniciar Odoo y actualizar la lista de aplicaciones.
4. Instalar **Payment Provider: PagoTIC**.
5. Ir a **Contabilidad → Configuración → Proveedores de pago → PagoTIC**.

## Configuración

Completar en la pestaña **Credentials**:

| Campo | Descripción |
|---|---|
| PagoTIC Username | Usuario de la entidad. |
| PagoTIC Password | Contraseña del usuario. |
| PagoTIC Client ID | Client ID de la aplicación. |
| PagoTIC Client Secret | Client Secret de la aplicación. |
| Authentication URL | Por defecto `https://a.paypertic.com`. |
| API URL | Por defecto `https://api.paypertic.com`. |
| Realm | Por defecto `entidades`. |

Completar en **PagoTIC Checkout**:

| Campo | Descripción |
|---|---|
| Concept ID | Obligatorio en la práctica. Identifica el concepto en PagoTIC. |
| Concept Description | Descripción opcional que se envía con el pago. |
| Checkout Payment Method IDs | Lista opcional de `media_payment_id` separados por coma (por ejemplo `9,5,85`) para restringir el checkout. Vacío = todos los medios habilitados para el comercio. |
| Collector ID | Se completa automáticamente desde el token. También se puede obtener con el botón **Fetch Collector ID**. |

Finalmente, cambiar el estado del proveedor a **Enabled** (o **Test Mode**, ver
limitaciones) y publicarlo si se usa desde el portal o e-commerce.

### Webhook

PagoTIC envía las notificaciones a:

```text
https://<dominio-de-odoo>/payment/pagotic/webhook
```

La URL se envía automáticamente al crear cada pago, pero el dominio debe ser
público y accesible desde Internet. Si Odoo está detrás de un proxy, configurar
correctamente `web.base.url`.

La URL de retorno del checkout es:

```text
https://<dominio-de-odoo>/payment/pagotic/return
```

## Flujo de pago

1. Odoo crea una transacción (`payment.transaction`) con el proveedor PagoTIC.
2. Al iniciar el pago, el módulo crea el pago en PagoTIC con
   `POST /pagos`, usando la referencia de Odoo como
   `external_transaction_id`.
3. Se guarda el ID de PagoTIC en `provider_reference` y la URL del checkout en
   `pagotic_form_url`.
4. El cliente es redirigido al checkout alojado de PagoTIC.
5. PagoTIC envía una notificación al webhook. El módulo **no confía en el
   cuerpo**: vuelve a consultar el pago con `GET /pagos/{id}` y valida
   collector, referencia externa, moneda e importe antes de actualizar la
   transacción.
6. Al volver el cliente, `/payment/pagotic/return` sincroniza el estado y
   redirige a `/payment/status`.

## Estados

| Estado PagoTIC | Estado Odoo |
|---|---|
| `approved` | `done` |
| `pending`, `issued`, `in_process`, `review`, `validate`, `deferred` | `pending` |
| `cancelled`, `objected` | `cancel` |
| `rejected`, `overdue` | `error` |
| `refunded` | Se mantiene `done` (la devolución se gestiona como transacción hija si se inicia desde Odoo) |

Las notificaciones son idempotentes: Odoo ignora las transiciones al mismo
estado o desde estados no permitidos.

## Devoluciones

PagoTIC solo permite devoluciones totales. El botón de devolución de Odoo
crea una transacción hija (`R-<referencia>`) y llama a
`POST /pagos/devolucion/{id}`. Si la API responde `approved`, la transacción
hija queda confirmada; si responde `rejected`, queda en error.

## Sincronización manual

En el formulario de la transacción hay un botón **Sync PagoTIC Status** para
consultar el estado actual en PagoTIC sin esperar al webhook.

## Limitaciones

- No se implementa tokenización ni pagos por token.
- No se implementan suscripciones de PagoTIC.
- Solo se soportan devoluciones totales.
- Solo se soporta ARS.
- PagoTIC no documenta firma de webhooks. Por eso el módulo siempre vuelve a
  consultar el pago en la API antes de procesarlo. Aun así, se recomienda
  restringir el acceso al webhook por red/proxy si es posible.
- **Test Mode**: PagoTIC no documenta un sandbox. En Odoo, el estado
  `Test Mode` no simula pagos: usa las credenciales configuradas. Usar
  credenciales de prueba de PagoTIC si están disponibles.
- Las credenciales se guardan en la base de datos de Odoo. Restringir el acceso
  a la configuración de proveedores de pago a usuarios confiables.

## Pruebas

El módulo incluye pruebas de Odoo que mockean el cliente del SDK (no hacen
llamadas reales a PagoTIC):

```bash
odoo -d <base> -i payment_pagotic --test-enable --test-tags /payment_pagotic --stop-after-init
```

Para ejecutar las pruebas sobre una base ya instalada:

```bash
odoo -d <base> -u payment_pagotic --test-enable --test-tags /payment_pagotic --stop-after-init
```

## Ramas y versiones

La versión actual es `18.0.1.0.0` y la rama de trabajo es `18.0`. La rama
`19.0` contiene la implementación para Odoo 19, que reemplazó el procesamiento
de notificaciones por `payment.transaction._process(provider_code, payment_data)`.

Esta rama implementa el flujo de notificaciones de Odoo 18:

- `_get_tx_from_notification_data()` para localizar la transacción por
  `provider_reference` o por referencia externa.
- `_process_notification_data()` para persistir el estado de PagoTIC y
  actualizar la transacción.
- `_send_refund_request(amount_to_refund)` sobre la transacción original, que
  crea la transacción hija de devolución.

## Licencia

GPL-3. La dependencia `pagotic-sdk` es GPL-3.0-or-later.

Copyright (C) Alejandro Fábrega.
