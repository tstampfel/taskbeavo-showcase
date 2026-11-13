# Launch VAT position

TaskBeavo launches **outside the VAT system**, under the small-business exemption of the Croatian VAT Act (Zakon o porezu na dodanu vrijednost, ZPDV, čl. 90 st. 1). The user decided this on 2026-11-07 after three tax opinions (the accountant, Claude and Gemini; summary on BEAV-39, section A): in year 1 both readings of the fee put its place of supply in Croatia (BEAV-194). A binding opinion from Porezna uprava and the accountant will confirm it later (BEAV-26).

Not covered here: getting the PDV ID number, which TaskBeavo needs before launch even outside the VAT system (ZPDV čl. 77 st. 5 t. b/d), is an accountant task for the user.

## Configuration

`FEE_VAT=NOT_CHARGED` in both Render env groups (`render.yaml`), and in `backend/.env.example`. Deployed environments refuse to start while `FEE_VAT` is missing or `UNDECIDED` (BEAV-191); local and test runs may stay `UNDECIDED`. Entering the VAT system later means `FEE_VAT=INCLUDED`, a new price list in `backend/bookings/money.py`, and a 30-day notice.

## What says so

Every surface that states the fee carries the exemption with its statutory citation. In Croatian, as given by the user: **"PDV nije obračunat temeljem čl. 90. st. 1. Zakona o PDV-u."** The en, de and fr sentences are drafts the accountant or lawyer must confirm (`packages/i18n/REVIEW.md`, "VAT exemption note").

| Surface | Where the sentence comes from |
|---|---|
| `/fees` and pricelist.csv | `fees.vatNotCharged` in `packages/i18n` (all four locales); pricelist.csv says `not_charged` |
| Fiscal receipt (BEAV-128, not built yet) | `vat.wording.fee_vat_note("hr")`: always Croatian |
| Fee receipt PDF and email (BEAV-130, not built yet) | `vat.wording.fee_vat_note(<poster's locale>)` |

`fee_vat_note` returns `None` unless `FEE_VAT` is `NOT_CHARGED`; with `INCLUDED` a receipt shows the VAT amount instead.

## Threshold monitor

`backend/vat/monitor.py`, run by Celery Beat every day at 06:00 Europe/Zagreb (`vat.check_thresholds`, a crontab, so deploys that restart Beat don't delay it). Totals run per calendar year in Europe/Zagreb.

| Limit | Law | What it would change | Alerts |
|---|---|---|---|
| EUR 10,000 a year of fees to **private posters living in another EU Member State** | ZPDV čl. 24.b st. 2, VAT Directive Art. 59c | If the fee is classed as an electronically supplied service, its place of supply moves to the poster's Member State above this limit, with VAT due there (OSS). Most commentators class it as intermediation of a physical service, which stays in Croatia (PLAN.md, "Agent, not merchant of record: what the choice does to VAT, tax and liability"); the binding opinion settles it | EUR 7,000 and EUR 9,000 |
| EUR 60,000 a year of total turnover | ZPDV čl. 90 | The small-business exemption ends; TaskBeavo enters the VAT system | EUR 50,000 |

**What counts.** A booking's fee counts in the year Stripe confirmed its payment (the first `funds_held` booking event). The part TaskBeavo keeps is counted: the kept fee of a cancellation settlement, nothing for a booking refunded in full, otherwise the fee charged (`Booking.fee_cents`, after any TaskBeavo credit). A full refund is read from the booking's history (its `refunded` event), so the fee doesn't count again when the booking later closes. A refund not yet confirmed counts as kept, which errs towards alerting early. **The counting date is payment confirmation, not the receipt or supply date.** Under BEAV-128's working assumption the fee receipt is issued at release, so a fee paid in late December and released in January counts in December's year here and in January's on the fiscal records. That is open with the accountant (BEAV-30 (e)). Bookings that were never paid don't count.

**Where the poster lives.** The source of truth is `User.country`: the country of residence (ISO 3166-1 alpha-2) the poster gives in the account details at sign-up. The monitor never guesses from the IP address, phone number or card. Croatia (`HR`) is domestic; the other EU Member States (`helpers.countries.EU_MEMBER_STATES`) are cross-border; any other valid code is outside the EU. A missing or malformed country counts as **unknown**: it adds to the total turnover but not to the cross-border total, and every alert says how many bookings and how much that is, so support can check them. Every poster is treated as a private person (consumer): there are no business poster accounts.

**Alerts.** Each threshold alerts once per calendar year (`vat_threshold_alerts`, unique per year and threshold): an email to `VAT_ALERT_EMAILS` (support and the founder; unset means `TASKBEAVO_CONTACT_EMAIL`) and a Sentry warning tagged `support_flag=vat` and `vat.threshold`. If the email can't be sent, the alert isn't recorded and the next day's run tries again. Alerts carry amounts and counts only, never names, emails or user IDs (CLAUDE.md, observability). The email is an internal staff alert in English, like the Sentry alerts.
