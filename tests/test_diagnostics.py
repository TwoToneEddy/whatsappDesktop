from whatsapp_terminal.diagnostics import backend_error


def test_glibc_error_gives_container_remedy():
    message = backend_error(OSError("/lib64/libc.so.6: version `GLIBC_2.33' not found"))
    assert "scripts/wat-container.sh" in message
    assert "Changing Python versions" in message
    assert "Neonize requires the system libmagic" not in message


def test_libmagic_error_gives_system_package_remedy():
    message = backend_error(ImportError("failed to find libmagic. Check your installation"))
    assert "libmagic1" in message
    assert "GLIBC" not in message


def test_unknown_error_does_not_blame_libmagic():
    message = backend_error(OSError("wrong ELF class: ELFCLASS32"))
    assert "wrong ELF class" in message
    assert "unsupported platform" in message
    assert "Neonize requires the system libmagic" not in message
