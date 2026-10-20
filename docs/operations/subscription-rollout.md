# Approved subscriptions rollout

## Scope

Publish only billing/catalog, subscription UI, expiry enforcement, invoice duration options and their tests. Preserve each branch's AI routing, private egress, role rules and unrelated product work.

The active Coolify applications are Stage (`codex/stage-context-pricing-verified`), main, bonyad and demo. `release` receives the code but currently has no separate running application. Archived/work branches are outside this rollout.

## Approved catalog

Prices are toman before 10% VAT. One conversation credit represents 2,000 combined input/output tokens.

- Public, 30 days: 2,500 / 500,000; 5,000 / 1,299,000; 18,000 / 2,999,000.
- Each eligible expert profession, 30 days: 1,500 / 1,490,000; 3,000 / 2,990,000; 6,000 / 5,590,000.
- Each eligible expert profession, 180 days: 20,000 / 18,880,000; 30,000 / 26,880,000; 40,000 / 33,880,000; 50,000 / 39,880,000.
- Credits cover the complete purchased term, not a recurring monthly grant.
- A 30-day purchase can include a server-priced 500,000-toman extension to 60 days, with unchanged credits. The invoice persists its purchased duration and surcharge.

## Safety and deployment

1. Back up each environment's database/configuration and retain exact running image IDs before publishing branch updates.
2. Use environment-specific backend/frontend image tags; never share a mutable `prod` image tag across independent Coolify applications.
3. Run backend regression tests with a disposable test database and isolated local-memory cache. Run `pnpm exec tsc --noEmit` and the normal frontend build.
4. Deploy Stage first, then other applications only after its migration and API checks pass.
5. Migrate the additive invoice fields (migration 0014). Do not rewrite historical wallet expiry or balances.
6. Sync **only** plans/products, not all definitions: `python manage.py shell -c 'from definitions.sync import DefinitionSync; DefinitionSync.sync_plans_and_products()'`. This does not reset users, prompts, billing conversion, payment config or discounts.
7. The transactional sync retires only recognized legacy plan slugs and top-ups; it preserves historical records and independent custom/organization plans.
8. Verify eligible public/expert catalog responses, exact prices, 30/180-day terms, unchanged tokens-per-credit and AI settings, and frontend/backend health. Test-only invoices must not be paid through a real gateway.

## Rollback

Retain prior commit IDs, per-application compose/env backups and backend/frontend image tags. Restore application images without deleting volumes. Migration 0014 is additive and old code ignores its fields; reversing it or restoring the entire database merely to roll back code would lose new invoice information. Storefront rollback should reactivate only the backed-up prior offers and deactivate only the newly introduced offers, leaving historical wallets/invoices intact.
