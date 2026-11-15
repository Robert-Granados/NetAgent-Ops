"""T1: consulta de solo lectura para routers Cisco IOS de NetAgent-Ops."""

import argparse
import json
import os
import re
import sys
from getpass import getpass
from ipaddress import IPv4Address
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
)
from paramiko import SSHException


INVENTARIO = Path(__file__).resolve().parents[1] / "ansible" / "inventory" / "hosts.yml"
ENCABEZADO_INTERFACES = ["Interface", "IP-Address", "OK?", "Method", "Status", "Protocol"]
PATRON_GATEWAY = re.compile(
    r"Gateway of last resort is (\S+) to network 0\.0\.0\.0"
)


def obtener_host(equipo: str) -> str:
    try:
        with INVENTARIO.open(encoding="utf-8") as archivo:
            inventario = yaml.safe_load(archivo)
        hosts = inventario["all"]["children"]["routers_ios"]["hosts"]
        direccion = hosts[equipo]["ansible_host"]
        if not isinstance(direccion, str):
            raise ValueError("ansible_host debe contener una dirección IPv4")
        return str(IPv4Address(direccion))
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Revisá ansible/inventory/hosts.yml: el alias {equipo} debe tener "
            "una IPv4 en all.children.routers_ios.hosts."
        ) from error


def analizar_interfaces(salida: str) -> list[dict[str, str | None]]:
    lineas = [linea.strip() for linea in salida.splitlines() if linea.strip()]
    if not lineas or lineas[0].split() != ENCABEZADO_INTERFACES:
        raise ValueError("La salida de show ip interface brief no tiene el encabezado esperado.")

    interfaces = []
    for linea in lineas[1:]:
        try:
            nombre, direccion, indicador, _metodo, descripcion = linea.split(maxsplit=4)
            estado, protocolo = descripcion.rsplit(maxsplit=1)
            if indicador not in {"YES", "NO"} or protocolo not in {"up", "down"}:
                raise ValueError
            ip = None if direccion == "unassigned" else str(IPv4Address(direccion))
        except ValueError as error:
            raise ValueError(
                "No se pudo interpretar una fila de show ip interface brief."
            ) from error

        interfaces.append(
            {"nombre": nombre, "estado": estado, "protocolo": protocolo, "ip": ip}
        )

    if not interfaces:
        raise ValueError("La consulta no devolvió interfaces.")
    return interfaces


def analizar_ruta_por_defecto(salida: str) -> str | None:
    resumen = [
        linea.strip()
        for linea in salida.splitlines()
        if linea.strip().startswith("Gateway of last resort")
    ]
    if resumen == ["Gateway of last resort is not set"]:
        return None

    coincidencia = PATRON_GATEWAY.fullmatch(resumen[0]) if len(resumen) == 1 else None
    if coincidencia:
        return str(IPv4Address(coincidencia[1]))

    raise ValueError("No se pudo determinar la ruta por defecto a partir de show ip route.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta el estado de un router del laboratorio.")
    parser.add_argument("--equipo", required=True, choices=("r1", "r2"))
    equipo = parser.parse_args().equipo

    resultado = {
        "ok": False,
        "equipo": equipo,
        "plataforma": "cisco_ios",
        "interfaces": [],
        "ruta_por_defecto": None,
        "errores": [],
    }

    try:
        host = obtener_host(equipo)
        usuario = os.getenv("NETAGENT_SSH_USERNAME", "netagent")
        clave = os.getenv("NETAGENT_SSH_PASSWORD")
        if not clave and sys.stdin.isatty():
            clave = getpass(f"Contraseña SSH de {equipo}: ")
        if not clave:
            raise ValueError("NETAGENT_SSH_PASSWORD es necesaria para la ejecución no interactiva.")

        with ConnectHandler(
            device_type="cisco_ios",
            host=host,
            username=usuario,
            password=clave,
            ssh_strict=True,
            system_host_keys=True,
        ) as router:
            interfaces = analizar_interfaces(router.send_command("show ip interface brief"))
            ruta = analizar_ruta_por_defecto(router.send_command("show ip route"))
        resultado.update(ok=True, interfaces=interfaces, ruta_por_defecto=ruta)

    except NetmikoAuthenticationException:
        resultado["errores"].append("El router rechazó las credenciales SSH.")
    except (NetmikoTimeoutException, ReadTimeout):
        resultado["errores"].append("Se agotó el tiempo de espera de la conexión o de la consulta.")
    except (SSHException, OSError):
        resultado["errores"].append("Falló SSH. Revisá la conectividad y la clave en known_hosts.")
    except ValueError as error:
        resultado["errores"].append(str(error))

    print(json.dumps(resultado, ensure_ascii=False))
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
