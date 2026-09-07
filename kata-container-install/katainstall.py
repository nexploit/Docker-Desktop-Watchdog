#!/usr/bin/env python3

"""
install_kata_containers.py

Installiert und konfiguriert Kata Containers für Docker auf
Debian-/Ubuntu-basierten CI-Runnern.

Features:
    - KVM-/Virtualisierungsprüfung
    - Kata Containers Installation
    - Docker Runtime Integration
    - persistentes Logging
    - Debug-Modus
    - Exception-Logging mit Stacktrace
    - Backup von /etc/docker/daemon.json
    - Kata-Testcontainer

Ausführung:
    sudo python3 install_kata_containers.py

Debug:
    sudo python3 install_kata_containers.py --debug

Test überspringen:
    sudo python3 install_kata_containers.py --skip-test

Eigenes Logverzeichnis:
    sudo python3 install_kata_containers.py --log-dir /var/log/kata-installer
"""

import argparse
import json
import logging
import logging.handlers
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path


KATA_GITHUB_API = (
    "https://api.github.com/repos/"
    "kata-containers/kata-containers/releases/latest"
)

KATA_RELEASE_BASE = (
    "https://github.com/kata-containers/"
    "kata-containers/releases/download"
)

DOCKER_CONFIG = Path("/etc/docker/daemon.json")
MODULES_CONFIG = Path("/etc/modules-load.d/kata-containers.conf")

KATA_SHIM = Path(
    "/opt/kata/runtime-rs/bin/containerd-shim-kata-v2"
)

KATA_CONFIG = Path(
    "/opt/kata/share/defaults/"
    "kata-containers/runtime-rs/"
    "configuration-qemu-runtime-rs.toml"
)

DEFAULT_LOG_DIR = Path("/var/log/kata-installer")

logger = logging.getLogger("kata-installer")


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

def setup_logging(log_dir: Path, debug: bool = False):
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    log_file = log_dir / f"kata-install-{timestamp}.log"
    latest_log = log_dir / "kata-install-latest.log"

    logger.setLevel(logging.DEBUG)

    logger.handlers.clear()

    file_formatter = logging.Formatter(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s: "
        "%(message)s"
    )

    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    file_handler = logging.FileHandler(
        log_file,
        encoding="utf-8"
    )

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler(sys.stdout)

    if debug:
        console_handler.setLevel(logging.DEBUG)
    else:
        console_handler.setLevel(logging.INFO)

    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Rotierende "latest"-Logdatei
    rotating_handler = logging.handlers.RotatingFileHandler(
        latest_log,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )

    rotating_handler.setLevel(logging.DEBUG)
    rotating_handler.setFormatter(file_formatter)

    logger.addHandler(rotating_handler)

    logger.info("Logging initialisiert")
    logger.info("Logdatei: %s", log_file)
    logger.info("Latest Log: %s", latest_log)

    if debug:
        logger.warning("Debug-Modus aktiviert")

    return log_file


# ---------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------

def fail(message, exit_code=1):
    logger.error(message)
    raise RuntimeError(message)


def run(cmd, check=True, capture=False, env=None):
    logger.debug("Führe Kommando aus: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            env=env
        )

    except Exception:
        logger.exception(
            "Ausführung des Befehls fehlgeschlagen: %s",
            " ".join(cmd)
        )
        raise

    logger.debug(
        "Exit-Code: %s",
        result.returncode
    )

    if result.stdout:
        logger.debug(
            "stdout:\n%s",
            result.stdout.rstrip()
        )

    if result.stderr:
        logger.debug(
            "stderr:\n%s",
            result.stderr.rstrip()
        )

    if check and result.returncode != 0:
        logger.error(
            "Befehl fehlgeschlagen: %s",
            " ".join(cmd)
        )

        logger.error(
            "Exit-Code: %s",
            result.returncode
        )

        if result.stdout:
            logger.error(
                "stdout:\n%s",
                result.stdout.rstrip()
            )

        if result.stderr:
            logger.error(
                "stderr:\n%s",
                result.stderr.rstrip()
            )

        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr
        )

    if capture:
        return result

    return result


# ---------------------------------------------------------
# Systemchecks
# ---------------------------------------------------------

def require_root():
    if os.geteuid() != 0:
        fail(
            "Das Skript muss als root ausgeführt werden. "
            "Beispiel: sudo python3 install_kata_containers.py"
        )

    logger.info("Root-Rechte vorhanden")


def check_linux():
    system = platform.system()

    logger.info(
        "Betriebssystem: %s",
        system
    )

    if system != "Linux":
        fail("Kata Containers benötigt Linux.")

    logger.info(
        "Kernel: %s",
        platform.release()
    )


def read_os_release():
    path = Path("/etc/os-release")

    if not path.exists():
        logger.warning(
            "/etc/os-release wurde nicht gefunden"
        )
        return {}

    data = {}

    for line in path.read_text(
        encoding="utf-8",
        errors="ignore"
    ).splitlines():

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        data[key] = value.strip('"')

    return data


def check_distribution():
    data = read_os_release()

    distro = data.get("ID", "unknown")
    version = data.get(
        "VERSION_ID",
        "unknown"
    )

    pretty = data.get(
        "PRETTY_NAME",
        f"{distro} {version}"
    )

    logger.info(
        "Distribution: %s",
        pretty
    )

    if distro not in (
        "debian",
        "ubuntu",
        "linuxmint",
        "pop"
    ):
        logger.warning(
            "Distribution '%s' wurde nicht explizit getestet.",
            distro
        )


def detect_arch():
    machine = platform.machine()

    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "s390x": "s390x",
        "ppc64le": "ppc64le"
    }

    arch = arch_map.get(machine)

    if not arch:
        fail(
            f"Nicht unterstützte Architektur: {machine}"
        )

    logger.info(
        "Architektur: %s -> %s",
        machine,
        arch
    )

    return arch


def detect_virtual_machine():
    if not shutil.which(
        "systemd-detect-virt"
    ):
        logger.warning(
            "systemd-detect-virt nicht gefunden"
        )
        return

    result = run(
        ["systemd-detect-virt"],
        check=False,
        capture=True
    )

    virt = result.stdout.strip()

    if virt and virt != "none":

        logger.warning(
            "System läuft innerhalb einer VM: %s",
            virt
        )

        logger.warning(
            "Nested Virtualization muss aktiviert sein."
        )

    else:

        logger.info(
            "Bare-Metal-System erkannt"
        )


def check_cpu_virtualization():
    cpuinfo = Path("/proc/cpuinfo")

    if not cpuinfo.exists():
        fail(
            "/proc/cpuinfo konnte nicht gelesen werden."
        )

    content = cpuinfo.read_text(
        errors="ignore"
    )

    intel = "vmx" in content
    amd = "svm" in content

    if intel:
        logger.info(
            "Intel VT-x erkannt"
        )
        return "intel"

    if amd:
        logger.info(
            "AMD-V erkannt"
        )
        return "amd"

    fail(
        "Keine Hardware-Virtualisierung gefunden. "
        "Intel VT-x bzw. AMD-V muss aktiviert sein."
    )


# ---------------------------------------------------------
# Kernel / KVM
# ---------------------------------------------------------

def load_kernel_modules(cpu_vendor):
    modules = [
        "kvm",
        "vhost_vsock",
        "vhost_net"
    ]

    if cpu_vendor == "intel":
        modules.append("kvm_intel")

    elif cpu_vendor == "amd":
        modules.append("kvm_amd")

    for module in modules:

        logger.info(
            "Lade Kernelmodul: %s",
            module
        )

        result = run(
            ["modprobe", module],
            check=False,
            capture=True
        )

        if result.returncode != 0:
            logger.warning(
                "Kernelmodul '%s' konnte nicht geladen werden.",
                module
            )

    MODULES_CONFIG.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    persistent_modules = [
        "vhost_vsock",
        "vhost_net"
    ]

    if cpu_vendor == "intel":

        persistent_modules.insert(
            0,
            "kvm_intel"
        )

    else:

        persistent_modules.insert(
            0,
            "kvm_amd"
        )

    MODULES_CONFIG.write_text(
        "\n".join(
            persistent_modules
        ) + "\n"
    )

    logger.info(
        "Persistente Modulkonfiguration geschrieben: %s",
        MODULES_CONFIG
    )


def check_kvm():
    kvm = Path("/dev/kvm")

    if not kvm.exists():
        fail(
            "/dev/kvm existiert nicht. "
            "KVM ist auf diesem Host nicht verfügbar."
        )

    logger.info(
        "/dev/kvm vorhanden"
    )

    result = run(
        ["ls", "-l", "/dev/kvm"],
        capture=True
    )

    logger.info(
        "/dev/kvm Berechtigungen: %s",
        result.stdout.strip()
    )


# ---------------------------------------------------------
# Abhängigkeiten
# ---------------------------------------------------------

def install_dependencies():
    logger.info(
        "Installiere Abhängigkeiten"
    )

    run([
        "apt-get",
        "update"
    ])

    packages = [
        "curl",
        "jq",
        "zstd",
        "tar",
        "ca-certificates"
    ]

    run([
        "apt-get",
        "install",
        "-y",
        *packages
    ])

    logger.info(
        "Abhängigkeiten installiert"
    )


# ---------------------------------------------------------
# Docker
# ---------------------------------------------------------

def get_docker_version():
    if not shutil.which("docker"):
        fail(
            "Docker wurde nicht gefunden."
        )

    result = run(
        [
            "docker",
            "version",
            "--format",
            "{{.Server.Version}}"
        ],
        capture=True,
        check=False
    )

    version = result.stdout.strip()

    if not version:
        fail(
            "Docker-Daemon ist nicht erreichbar."
        )

    logger.info(
        "Docker-Version: %s",
        version
    )

    try:

        major = int(
            version.split(".")[0]
        )

    except ValueError:

        logger.warning(
            "Docker-Version konnte nicht ausgewertet werden."
        )

        return

    if major < 26:
        fail(
            f"Docker {version} ist zu alt. "
            "Docker >= 26 wird benötigt."
        )


# ---------------------------------------------------------
# Kata Release
# ---------------------------------------------------------

def get_latest_kata_version():
    logger.info(
        "Ermittle aktuelle Kata Containers Version"
    )

    request = urllib.request.Request(
        KATA_GITHUB_API,
        headers={
            "User-Agent":
            "kata-ci-runner-installer"
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            data = json.loads(
                response.read().decode()
            )

    except Exception:

        logger.exception(
            "GitHub Release API konnte nicht abgefragt werden."
        )

        raise

    version = data.get("tag_name")

    if not version:
        fail(
            "Kata-Version konnte nicht ermittelt werden."
        )

    logger.info(
        "Kata-Version: %s",
        version
    )

    return version


def download_kata(
    version,
    arch,
    temp_dir
):

    filename = (
        f"kata-static-{version}-{arch}.tar.zst"
    )

    url = (
        f"{KATA_RELEASE_BASE}/"
        f"{version}/"
        f"{filename}"
    )

    destination = (
        Path(temp_dir) /
        filename
    )

    logger.info(
        "Download: %s",
        url
    )

    run([
        "curl",
        "-fL",
        "--retry",
        "3",
        "--retry-delay",
        "2",
        "-o",
        str(destination),
        url
    ])

    logger.info(
        "Download abgeschlossen: %s",
        destination
    )

    return destination


# ---------------------------------------------------------
# Kata Installation
# ---------------------------------------------------------

def install_kata(archive):
    logger.info(
        "Installiere Kata Containers"
    )

    run([
        "tar",
        "--zstd",
        "-xf",
        str(archive),
        "-C",
        "/"
    ])

    if not KATA_SHIM.exists():
        fail(
            f"Kata Shim nicht gefunden: {KATA_SHIM}"
        )

    if not KATA_CONFIG.exists():
        fail(
            f"Kata Konfiguration nicht gefunden: "
            f"{KATA_CONFIG}"
        )

    logger.info(
        "Kata Shim: %s",
        KATA_SHIM
    )

    logger.info(
        "Kata Config: %s",
        KATA_CONFIG
    )


# ---------------------------------------------------------
# Docker Konfiguration
# ---------------------------------------------------------

def backup_docker_config():
    if not DOCKER_CONFIG.exists():

        logger.info(
            "Keine bestehende daemon.json vorhanden"
        )

        return None

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    backup = Path(
        f"{DOCKER_CONFIG}."
        f"backup-{timestamp}"
    )

    shutil.copy2(
        DOCKER_CONFIG,
        backup
    )

    logger.info(
        "Docker-Konfiguration gesichert: %s",
        backup
    )

    return backup


def load_docker_config():
    if not DOCKER_CONFIG.exists():
        return {}

    try:

        return json.loads(
            DOCKER_CONFIG.read_text()
        )

    except json.JSONDecodeError:

        logger.exception(
            "Ungültige Docker daemon.json"
        )

        raise


def configure_docker():
    logger.info(
        "Konfiguriere Kata Docker Runtime"
    )

    backup_docker_config()

    config = load_docker_config()

    runtimes = config.setdefault(
        "runtimes",
        {}
    )

    runtimes["kata"] = {
        "runtimeType": str(
            KATA_SHIM
        ),
        "options": {
            "ConfigPath": str(
                KATA_CONFIG
            )
        }
    }

    DOCKER_CONFIG.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    DOCKER_CONFIG.write_text(
        json.dumps(
            config,
            indent=2
        ) + "\n"
    )

    logger.info(
        "Docker-Konfiguration geschrieben: %s",
        DOCKER_CONFIG
    )

    logger.debug(
        "daemon.json:\n%s",
        json.dumps(
            config,
            indent=2
        )
    )


def validate_docker_json():
    try:

        json.loads(
            DOCKER_CONFIG.read_text()
        )

    except Exception:

        logger.exception(
            "Docker-Konfiguration ist ungültig"
        )

        raise

    logger.info(
        "daemon.json JSON-Prüfung erfolgreich"
    )


def restart_docker():
    logger.info(
        "Starte Docker neu"
    )

    run([
        "systemctl",
        "daemon-reload"
    ])

    try:

        run([
            "systemctl",
            "restart",
            "docker"
        ])

    except Exception:

        logger.error(
            "Docker konnte nicht gestartet werden."
        )

        journal = run(
            [
                "journalctl",
                "-u",
                "docker",
                "-n",
                "100",
                "--no-pager"
            ],
            check=False,
            capture=True
        )

        logger.error(
            "Docker Journal:\n%s",
            journal.stdout
        )

        raise

    result = run(
        [
            "systemctl",
            "is-active",
            "docker"
        ],
        check=False,
        capture=True
    )

    if result.stdout.strip() != "active":
        fail(
            "Docker ist nach Neustart nicht aktiv."
        )

    logger.info(
        "Docker läuft"
    )


# ---------------------------------------------------------
# Runtime Prüfung
# ---------------------------------------------------------

def verify_runtime():
    result = run(
        [
            "docker",
            "info",
            "--format",
            "{{json .Runtimes}}"
        ],
        capture=True
    )

    logger.info(
        "Docker Runtimes: %s",
        result.stdout.strip()
    )

    if "kata" not in result.stdout:
        fail(
            "Docker meldet Kata Runtime nicht."
        )

    logger.info(
        "Docker erkennt Kata Runtime"
    )


# ---------------------------------------------------------
# Funktionstest
# ---------------------------------------------------------

def test_kata():
    logger.info(
        "Starte Kata-Testcontainer"
    )

    host_kernel = platform.release()

    result = run(
        [
            "docker",
            "run",
            "--runtime",
            "kata",
            "--rm",
            "ubuntu:24.04",
            "uname",
            "-r"
        ],
        capture=True,
        check=False
    )

    if result.returncode != 0:

        logger.error(
            "Kata-Testcontainer fehlgeschlagen"
        )

        logger.error(
            "stdout:\n%s",
            result.stdout
        )

        logger.error(
            "stderr:\n%s",
            result.stderr
        )

        raise RuntimeError(
            "Kata-Testcontainer konnte nicht gestartet werden."
        )

    guest_kernel = (
        result.stdout.strip()
    )

    logger.info(
        "Host Kernel: %s",
        host_kernel
    )

    logger.info(
        "Kata Guest Kernel: %s",
        guest_kernel
    )

    if guest_kernel == host_kernel:

        logger.warning(
            "Host- und Kata-Kernel sind identisch."
        )

    else:

        logger.info(
            "Kata VM Isolation erfolgreich bestätigt"
        )


# ---------------------------------------------------------
# Systeminformationen
# ---------------------------------------------------------

def log_system_information():
    logger.info(
        "===== Systeminformationen ====="
    )

    logger.info(
        "Hostname: %s",
        platform.node()
    )

    logger.info(
        "Python: %s",
        platform.python_version()
    )

    logger.info(
        "Kernel: %s",
        platform.release()
    )

    logger.info(
        "Architektur: %s",
        platform.machine()
    )

    logger.info(
        "CPU: %s",
        platform.processor()
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Kata Containers Installer "
            "für Docker CI Runner"
        )
    )

    parser.add_argument(
        "--skip-test",
        action="store_true",
        help="Testcontainer überspringen"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Ausgabe aktivieren"
    )

    parser.add_argument(
        "--log-dir",
        default=str(
            DEFAULT_LOG_DIR
        ),
        help=(
            "Logverzeichnis "
            "(Standard: /var/log/kata-installer)"
        )
    )

    args = parser.parse_args()

    log_file = None

    try:

        require_root()

        log_file = setup_logging(
            Path(args.log_dir),
            args.debug
        )

        logger.info(
            "=========================================="
        )

        logger.info(
            "Kata Containers CI Runner Installer"
        )

        logger.info(
            "=========================================="
        )

        log_system_information()

        check_linux()

        check_distribution()

        arch = detect_arch()

        detect_virtual_machine()

        cpu_vendor = (
            check_cpu_virtualization()
        )

        install_dependencies()

        load_kernel_modules(
            cpu_vendor
        )

        check_kvm()

        get_docker_version()

        version = (
            get_latest_kata_version()
        )

        with tempfile.TemporaryDirectory(
            prefix="kata-install-"
        ) as temp_dir:

            logger.debug(
                "Temporäres Verzeichnis: %s",
                temp_dir
            )

            archive = download_kata(
                version,
                arch,
                temp_dir
            )

            install_kata(
                archive
            )

        configure_docker()

        validate_docker_json()

        restart_docker()

        verify_runtime()

        if not args.skip_test:

            test_kata()

        logger.info(
            "=========================================="
        )

        logger.info(
            "Kata Containers Installation erfolgreich"
        )

        logger.info(
            "=========================================="
        )

        print()
        print(
            "Installation erfolgreich."
        )

        print(
            f"Logdatei: {log_file}"
        )

        print()
        print(
            "Kata testen:"
        )

        print(
            "docker run --runtime kata "
            "--rm ubuntu:24.04 uname -a"
        )

        print()

    except KeyboardInterrupt:

        logger.warning(
            "Installation durch Benutzer abgebrochen"
        )

        sys.exit(130)

    except Exception as exc:

        if logger.handlers:

            logger.critical(
                "Installation fehlgeschlagen: %s",
                exc
            )

            logger.critical(
                "Stacktrace:\n%s",
                traceback.format_exc()
            )

        else:

            print(
                f"FEHLER: {exc}",
                file=sys.stderr
            )

            traceback.print_exc()

        if log_file:

            print()
            print(
                f"Fehlerdetails siehe: {log_file}",
                file=sys.stderr
            )

        sys.exit(1)


if __name__ == "__main__":
    main()
