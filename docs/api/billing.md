# Billing APIs

Billing APIs live under:

```text
/api/billing/
```
 
They expose billing config, storefront products, invoices, payment callbacks, discounts, manual payment, FAQ, and transaction history.

## Endpoint Table

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/billing/config/` | Public | Economy, support, and payment config. |
| `GET` | `/api/billing/faqs/` | Public | Active FAQ entries. |
| `GET` | `/api/billing/products/` | Bearer | Eligible storefront products. |
| `GET` | `/api/billing/admin/products/` | Staff/admin | All active products for admins. |
| `GET` | `/api/billing/history/` | Bearer | Wallet transactions or invoices with `?type=invoice`. |
| `POST` | `/api/billing/purchase/` | Bearer | Create pending invoice for product. |
| `GET` | `/api/billing/invoices/{id}/` | Bearer | Read owned invoice detail. |
| `POST` | `/api/billing/invoices/{invoice_id}/apply_discount/` | Bearer | Apply discount to pending invoice. |
| `POST` | `/api/billing/pay/{invoice_id}/` | Bearer | Start online payment. |
| `GET` | `/api/billing/callback/` | Public | Payment gateway callback. |
| `GET` | `/api/billing/zibal/callback/` | Public | Zibal callback. |
| `POST` | `/api/billing/pay/manual/{invoice_id}/` | Bearer | Submit manual payment reference. |

## Product Filtering

`GET /products/` returns active products filtered by `is_user_eligible_for_plan` when a product links to a plan.

Credit top-ups are visible independent of plan eligibility.

## Purchase

`POST /purchase/` accepts:

```json
{
  "id": 123
}
```

It creates a pending invoice and returns:

```json
{
  "invoice_id": "...",
  "status": "created",
  "redirect_url": "/dashboard/invoices/..."
}
```

Plan product purchase rejects ineligible users with `403`.

## History

`GET /history/` returns wallet transactions.

`GET /history/?type=invoice` returns invoices.

## Fulfillment

Paid invoices are fulfilled by `FulfillmentService`, which activates plans, adds credits, records transactions, and bumps service access cache.

## Frontend Consumers

- dashboard billing page
- invoice detail page
- config provider
- FAQ/support surfaces
- payment callback route
