"""T2: configura y verifica la ruta por defecto del caso 1 de NetAgent-Ops."""

import argparse
import json
import os
import re
import sys
from getpass import getpass

from netmiko import ConnectHandler
from netmiko.exceptions import (
    ConfigInvalidException,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
)
from paramiko import SSHException

from estado_router import analizar_interfaces, analizar_ruta_por_defecto, obtener_host


DESTINO = "0.0.0.0/0"
SIGUIENTE_SALTO = "10.0.0.1"
COMANDO_RUTA = f"ip route 0.0.0.0 0.0.0.0 {SIGUIENTE_SALTO}"
CONSULTA_CONFIG = "show running-config | include ^ip route 0[.]0[.]0[.]0 +0[.]0[.]0[.]0"
PATRON_ERROR_IOS = r"(?m)^\s*%"
PATRON_RUTA_ACTIVA = re.compile(
    r"(?m)^\s*S\*\s+0\.0\.0\.0/0\s+\[\d+/\d+\]\s+via\s+10\.0\.0\.1(?:\s|,|$)"
)


def consultar_ruta(router) -> bool:
    """Devuelve si la ruta está instalada. Rechaza configuraciones en conflicto."""
    tabla = router.send_command("show ip route")
    salto_activo = analizar_ruta_por_defecto(tabla)
    configuracion = router.send_command(CONSULTA_CONFIG)
    lineas = [" ".join(linea.split()) for linea in configuracion.splitlines() if linea.strip()]

    if not lineas and salto_activo is None:
        return False
    if (
        lineas == [COMANDO_RUTA]
        and salto_activo == SIGUIENTE_SALTO
        and PATRON_RUTA_ACTIVA.search(tabla)
    ):
        return True
    raise ValueError("La ruta por defecto existente difiere o su instalación no se pudo confirmar.")


def validar_enlaces(router) -> None:
    interfaces = analizar_interfaces(router.send_command("show ip interface brief"))
    ips_activas = {
        interfaz["ip"]
        for interfaz in interfaces
        if interfaz["estado"] == "up" and interfaz["protocolo"] == "up"
    }
    if not {"10.0.0.2", "10.1.1.1"}.issubset(ips_activas):
        raise ValueError("R1 debe tener up/up los enlaces a GW (10.0.0.2) y R2 (10.1.1.1).")

    ruta_al_gateway = router.send_command(f"show ip route {SIGUIENTE_SALTO}")
    if "Routing entry for 10.0.0.0/30" not in ruta_al_gateway or not re.search(
        r'(?m)^\s*Known via "connected"(?:,|\s|$)', ruta_al_gateway
    ):
        raise ValueError(f"El siguiente salto {SIGUIENTE_SALTO} debe estar en la red conectada 10.0.0.0/30.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configura la ruta por defecto del caso 1.")
    parser.add_argument("--equipo", required=True, choices=("r1",))
    parser.add_argument("--destino", required=True, choices=(DESTINO,))
    parser.add_argument("--siguiente-salto", required=True, choices=(SIGUIENTE_SALTO,))
    args = parser.parse_args()

    resultado = {
        "ok": False,
        "equipo": args.equipo,
        "destino": args.destino,
        "siguiente_salto": args.siguiente_salto,
        "changed": False,
        "verificada": False,
        "errores": [],
    }
    escribiendo = False

    try:
        host = obtener_host(args.equipo)
        usuario = os.getenv("NETAGENT_SSH_USERNAME", "netagent")
        clave = os.getenv("NETAGENT_SSH_PASSWORD")
        if not clave and sys.stdin.isatty():
            clave = getpass(f"Contraseña SSH de {args.equipo}: ")
        if not clave:
            raise ValueError("Ingresá la contraseña por terminal o usá NETAGENT_SSH_PASSWORD.")

        with ConnectHandler(
            device_type="cisco_ios",
            host=host,
            username=usuario,
            password=clave,
            ssh_strict=True,
            system_host_keys=True,
        ) as router:
            if not consultar_ruta(router):
                validar_enlaces(router)
                if not sys.stdin.isatty():
                    raise ValueError("La aplicación requiere confirmación desde una terminal interactiva.")
                print(
                    f"R1 ({host}) está sin ruta por defecto. "
                    f"Se propone configurar {DESTINO} vía {SIGUIENTE_SALTO}. "
                    "Escribí SI para aplicar: ",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                if input().strip() != "SI":
                    raise ValueError("Operación cancelada por el operador.")

                if not consultar_ruta(router):
                    validar_enlaces(router)
                    escribiendo = True
                    router.send_config_set([COMANDO_RUTA], error_pattern=PATRON_ERROR_IOS)
                    resultado["changed"] = True
                    if not consultar_ruta(router):
                        raise ValueError("La lectura posterior indica que la ruta sigue ausente.")

            resultado.update(ok=True, verificada=True)

    except (EOFError, KeyboardInterrupt):
        resultado["errores"].append("El operador interrumpió la operación.")
    except NetmikoAuthenticationException:
        resultado["errores"].append("R1 rechazó las credenciales SSH.")
    except (NetmikoTimeoutException, ReadTimeout):
        resultado["errores"].append("Se agotó el tiempo de espera de SSH o de la consulta.")
    except ConfigInvalidException:
        resultado["errores"].append("Cisco IOS rechazó el comando de configuración.")
    except (SSHException, OSError):
        resultado["errores"].append("Falló SSH. Revisá la conectividad y la clave en known_hosts.")
    except ValueError as error:
        resultado["errores"].append(str(error))

    if escribiendo and not resultado["verificada"]:
        resultado["errores"].append("Verificá el estado real con T1 antes de volver a intentar.")
    print(json.dumps(resultado, ensure_ascii=False))
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
