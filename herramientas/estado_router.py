import os
import json
import sys

from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException
from netmiko.exceptions import NetmikoTimeoutException


# 1. Cargar las variables del archivo .env
load_dotenv()


# 2. Obtener los datos de conexión
ROUTER = {
    "device_type": "cisco_ios",
    "host": os.getenv("R1_HOST"),
    "username": os.getenv("R1_USERNAME"),
    "password": os.getenv("R1_PASSWORD"),
    "conn_timeout": 15,
    "banner_timeout": 20,
    "auth_timeout": 20,
    "fast_cli": False,
}


def validar_configuracion():
    """Comprueba que existan los datos necesarios para conectarse."""

    campos = ["host", "username", "password"]

    for campo in campos:
        if not ROUTER.get(campo):
            raise ValueError(
                f"Falta configurar la variable correspondiente a: {campo}"
            )


def consultar_router():
    """Se conecta al router y obtiene información de interfaces y rutas."""

    resultado = {
        "dispositivo": "R1",
        "ip": ROUTER["host"],
        "estado": "error",
        "interfaces": None,
        "rutas": None,
        "error": None,
    }

    try:
        validar_configuracion()

        print("Conectando al router Cisco...", file=sys.stderr)

        # 3. Establecer la conexión SSH
        with ConnectHandler(**ROUTER) as conexion:

            print("Conexión SSH establecida.", file=sys.stderr)

            # 4. Consultar las interfaces
            interfaces = conexion.send_command(
                "show ip interface brief"
            )

            # 5. Consultar la tabla de enrutamiento
            rutas = conexion.send_command(
                "show ip route"
            )

            # 6. Guardar los resultados
            resultado["interfaces"] = interfaces
            resultado["rutas"] = rutas
            resultado["estado"] = "exitoso"

    except NetmikoAuthenticationException:
        resultado["error"] = "Error de autenticación SSH."

    except NetmikoTimeoutException:
        resultado["error"] = "Tiempo de espera agotado al conectar."

    except ValueError as error:
        resultado["error"] = str(error)

    except Exception as error:
        resultado["error"] = f"Error inesperado: {error}"

    return resultado


# 7. Punto de entrada del programa
if __name__ == "__main__":

    datos = consultar_router()

    print(json.dumps(datos, indent=4, ensure_ascii=False))

    if datos["estado"] != "exitoso":
        sys.exit(1)
