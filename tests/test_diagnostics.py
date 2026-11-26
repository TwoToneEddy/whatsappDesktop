from whatsapp_terminal.diagnostics import backend_error


def test_backend_error_explains_setup():
    message = backend_error(OSError("Node.js is missing"))
    assert "Node.js is missing" in message
    assert "wat --setup-backend" in message
    assert "uv/uvx cannot replace" in message
