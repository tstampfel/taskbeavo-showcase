#!/bin/sh
# Entry point of the stripe-cli compose service: forwards Stripe test-mode webhooks
# to Django. Refuses anything but a test-mode key, so it can never listen to a live account.
set -eu

key="${STRIPE_SECRET_KEY:-}"

case "$key" in
  "")
    echo "stripe-cli: STRIPE_SECRET_KEY is not set in backend/.env; webhook forwarding is off."
    echo "stripe-cli: add a test-mode key (sk_test_... or rk_test_...) and run: docker compose up stripe-cli"
    exit 0
    ;;
  sk_test_*|rk_test_*)
    ;;
  *)
    echo "stripe-cli: refusing to start: STRIPE_SECRET_KEY is not a test-mode key." >&2
    exit 1
    ;;
esac

# Since stripe-cli 1.2x `listen` needs an event filter; --all-snapshot forwards every event,
# as before, and the webhook stores and ignores the types it doesn't handle.
exec stripe listen --api-key "$key" --all-snapshot \
  --forward-to "${STRIPE_FORWARD_TO:-django:8000/webhooks/stripe}"
