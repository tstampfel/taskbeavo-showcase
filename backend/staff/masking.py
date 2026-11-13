def mask(value: str, keep: int) -> str:
    """`value` with all but its last `keep` characters hidden: "•••• 1234". Spaces are
    dropped first, so a formatted IBAN or phone number keeps its real last characters."""
    value = value.replace(" ", "")
    if not value:
        return ""
    return "•••• " + value[-keep:] if len(value) > keep else "••••"
