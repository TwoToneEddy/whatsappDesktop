def backend_error(error: Exception) -> str:
    return (
        f"Cannot load WhatsApp backend: {error}\n"
        "This branch uses Baileys, not Neonize or libmagic.\n"
        "Install a host-compatible Node.js 22+ runtime with npm, then run:\n"
        "  uv run wat --setup-backend\n"
        "uv/uvx cannot replace your system libraries or install Node.js.\n"
        "Try offline: uv run wat --demo\n"
    )
