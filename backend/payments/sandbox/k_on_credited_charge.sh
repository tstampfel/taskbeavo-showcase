#!/bin/zsh -l
# BEAV-123, user decision 3 (2026-11-07): TaskBeavo keeps K = 12% of C on a late cancel even
# when a credit lowered the application fee F' below K. Runs the settlement against the Stripe
# SANDBOX with the logged-in Stripe CLI (test mode only) and checks the helper keeps C,
# TaskBeavo K, and the poster gets R, with one formula for every F':
#   Y = max(0, F' - K)  fee refund     X = R + max(0, K - F')  transfer reversal
# Same as bookings/cancellation.py. Usage: k_on_credited_charge.sh FEE_CENTS RUN_ID [C_CENTS]
# K is 12% of C, never more than what is left of the charge after C. When that leaves R = 0
# (a nearly finished withdrawal on a credited booking) there is no refund, and the transfer
# reversal's transfer.reversed event confirms the split (user decision 2026-11-07).
# Last run 2026-11-07 on the TaskBeavo sandbox: F'=0, 50 and 360 with C=750, and F'=0 with
# C=2700 (R = 0, X = K = 300), all OK.
set -e
# Only the TaskBeavo sandbox account, in test mode (the CLI's default; never --live). The IDs come
# from the environment so they stay out of the repo: STRIPE_SANDBOX_ACCOUNT (the platform sandbox)
# and STRIPE_SANDBOX_HELPER_ACCOUNT (a test Express account in it).
: ${STRIPE_SANDBOX_ACCOUNT:?set STRIPE_SANDBOX_ACCOUNT} ${STRIPE_SANDBOX_HELPER_ACCOUNT:?set STRIPE_SANDBOX_HELPER_ACCOUNT}
$HOME/.local/bin/stripe config --list | grep -q "account_id = '$STRIPE_SANDBOX_ACCOUNT'" || { echo 'not the sandbox'; exit 1; }
S=~/.local/bin/stripe; ACCT=$STRIPE_SANDBOX_HELPER_ACCOUNT; P=3000; F=$1; RUN=$2; C=${3:-750}
K=$(( C * 12 / 100 )); (( K > P + F - C )) && K=$(( P + F - C ))
Y=$(( F > K ? F - K : 0 )); R=$(( P + F - C - K )); X=$(( R + (K > F ? K - F : 0) ))
echo "P=$P F'=$F C=$C K=$K -> fee_refund Y=$Y reversal X=$X refund R=$R"
args=(-d amount=$((P+F)) -d currency=eur -d "automatic_payment_methods[enabled]=true" -d "automatic_payment_methods[allow_redirects]=never" -d "transfer_data[destination]=$ACCT" -d on_behalf_of=$ACCT -d payment_method=pm_card_bypassPending -d confirm=true -d "metadata[test]=beav123-k-$RUN" -d "expand[]=latest_charge")
(( F > 0 )) && args+=(-d application_fee_amount=$F)
PI=$($S payment_intents create "${args[@]}" -i "beav123:k:${RUN}:pi:v1" 2>/dev/null)
CH=$(echo $PI | python3 -c 'import json,sys;print(json.load(sys.stdin)["latest_charge"]["id"])')
for i in 1 2 3 4 5 6 7 8 9 10; do
  CJ=$($S charges retrieve $CH 2>/dev/null)
  TR=$(echo $CJ | python3 -c 'import json,sys;print(json.load(sys.stdin).get("transfer") or "")')
  FEEID=$(echo $CJ | python3 -c 'import json,sys;print(json.load(sys.stdin).get("application_fee") or "")')
  [[ -n $TR ]] && { (( F == 0 )) || [[ -n $FEEID ]] } && break; sleep 2
done
echo "charge $CH transfer $TR fee ${FEEID:-none}"
if (( Y > 0 )); then $S post /v1/application_fees/$FEEID/refunds -d amount=$Y -i "beav123:k:${RUN}:fee_refund:v1" >/dev/null 2>&1; fi
$S post /v1/transfers/$TR/reversals -d amount=$X -d "metadata[booking_id]=beav123-$RUN" -d "metadata[action]=transfer_reversal" -i "beav123:k:${RUN}:transfer_reversal:v1" >/dev/null 2>&1
(( R > 0 )) && $S post /v1/refunds -d payment_intent=$(echo $PI | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])') -d amount=$R -d reverse_transfer=false -d refund_application_fee=false -i "beav123:k:${RUN}:refund:v1" >/dev/null 2>&1
TRJ=$($S transfers retrieve $TR 2>/dev/null); CHJ=$($S charges retrieve $CH 2>/dev/null)
python3 - "$TRJ" "$CHJ" $P $F $Y $C $K $R <<'PY'
import json,sys
tr,ch=json.loads(sys.argv[1]),json.loads(sys.argv[2]); P,F,Y,C,K,R=map(int,sys.argv[3:])
helper=tr["amount"]-F+Y-tr["amount_reversed"]  # Stripe transfers the whole charge, then takes the fee
platform=ch["amount"]-tr["amount"]+F-Y+tr["amount_reversed"]-ch["amount_refunded"]
print(f"transfer {tr['amount']} reversed {tr['amount_reversed']}; charge {ch['amount']} refunded {ch['amount_refunded']}")
print(f"helper keeps {helper} (want C={C}); platform keeps {platform} (want K={K}); poster refunded {ch['amount_refunded']} (want R={R})")
assert (helper,platform,ch['amount_refunded'])==(C,K,R); print("OK")
PY
