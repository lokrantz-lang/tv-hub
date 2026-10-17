"""Förklaringar av felklasser på svenska."""

ERROR_TEXT = {
    "dns": "adressen kunde inte slås upp (DNS)",
    "refused": "servern vägrade anslutningen (porten stängd eller tjänsten nere)",
    "timeout": "servern svarade inte i tid",
    "network": "nätverksfel på vägen till servern",
    "reset": "anslutningen bröts av servern",
    "bad_response": "servern svarade med något som inte är giltig data",
    "auth_failed": "inloggningen nekades (fel användarnamn/lösenord, eller kontot är spärrat)",
    "empty_list": "källan skickade en tom lista",
    "shrunk": "källan skickade en kraftigt förkortad lista",
}


def explain(kind):
    if not kind:
        return "okänt fel"
    if kind.startswith("http_5"):
        return f"källans server hade ett internt fel ({kind[5:]})"
    if kind.startswith("http_"):
        return f"källan svarade med HTTP {kind[5:]}"
    if kind.startswith("account_"):
        return f"kontot har status {kind[8:]}"
    return ERROR_TEXT.get(kind, kind)
