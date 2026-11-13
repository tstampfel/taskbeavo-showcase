"""Which countries a helper may declare as their DAC7 member state of residence."""

# The EU member states, ISO 3166-1 alpha-2 (Greece is GR here, not the VAT prefix EL).
EU_MEMBER_STATES = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
        "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    }
)  # fmt: skip

# The residence countries a helper may declare: validation and the canSendOffers check
# both read this one constant.
RESIDENCE_COUNTRIES = EU_MEMBER_STATES
