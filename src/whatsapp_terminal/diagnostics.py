def backend_error(error: Exception) -> str:
    detail = str(error)
    if "GLIBC_" in detail and "not found" in detail:
        advice = (
            "Neonize's native library requires a newer glibc than this host provides.\n"
            "Changing Python versions or installing libmagic will not fix this error.\n"
            "From the project directory on your host, run:\n"
            "  bash scripts/wat-container.sh\n"
            "This requires Podman or Docker and uses a container with a newer glibc."
        )
    elif "magic" in detail.lower():
        advice = (
            "Neonize requires the system libmagic library "
            "(Debian/Ubuntu: libmagic1; Fedora/RHEL: file-libs; macOS: libmagic)."
        )
    else:
        advice = (
            "The WhatsApp native backend could not load. Check the error above for a missing "
            "library or unsupported platform. See README.md for the container option."
        )
    return f"Cannot load WhatsApp backend: {detail}\n{advice}\nTry offline: uv run wat --demo\n"
