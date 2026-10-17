
import os
import re
import logging
import ipaddress
from datetime import datetime

from openpyxl import load_workbook
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException


# ============================================================
# PATH CONFIGURATION
# ============================================================

BASE_DIR = r"H:\Automation"
EXCEL_FILE = os.path.join(BASE_DIR, "DEVICES.xlsx")
OUTPUT_DIR = os.path.join(BASE_DIR, "OUTPUT")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# SNMP TRAP RECEIVER IP ADDRESSES
# REPLACE THESE EXAMPLES WITH YOUR ACTUAL RECEIVER IPs
# ============================================================

TRAP_RECEIVER_IPS = [
    "192.0.2.10",
    "192.0.2.11",
    "192.0.2.12",
    "192.0.2.13",
]


# ============================================================
# LOGGING
# ============================================================

RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

logging.basicConfig(
    filename=os.path.join(
        OUTPUT_DIR, f"execution_{RUN_TIMESTAMP}.log"
    ),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def safe_filename(value):
    """Remove characters that are invalid in Windows filenames."""
    value = str(value).strip()
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)


def validate_ip(ip):
    """Validate an IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(str(ip).strip())
        return True
    except ValueError:
        return False


def load_devices():
    """Read device details from the first Excel worksheet."""
    if not os.path.isfile(EXCEL_FILE):
        raise FileNotFoundError(
            f"Excel file not found: {EXCEL_FILE}"
        )

    workbook = load_workbook(
        EXCEL_FILE,
        read_only=True,
        data_only=True,
    )
    worksheet = workbook.active

    rows = worksheet.iter_rows(values_only=True)
    headers = next(rows, None)

    if not headers:
        workbook.close()
        raise ValueError("The Excel worksheet is empty.")

    # Ignore capitalization and spaces in column names.
    header_map = {
        str(value).strip().lower(): index
        for index, value in enumerate(headers)
        if value is not None
    }

    required = ["devicename", "ip", "username", "password"]
    missing = [name for name in required if name not in header_map]

    if missing:
        workbook.close()
        raise ValueError(
            f"Missing Excel columns: {', '.join(missing)}. "
            "Required: DeviceName, IP, Username, Password."
        )

    devices = []

    for row_number, row in enumerate(rows, start=2):
        if not row or not any(value is not None for value in row):
            continue

        def get_value(column):
            index = header_map[column]
            return row[index] if index < len(row) else None

        device = {
            "name": str(get_value("devicename") or "").strip(),
            "ip": str(get_value("ip") or "").strip(),
            "username": str(get_value("username") or "").strip(),
            "password": (
                str(get_value("password"))
                if get_value("password") is not None
                else ""
            ),
        }

        if not all(device.values()):
            logging.error(
                "Skipping Excel row %s: missing required values.",
                row_number,
            )
            continue

        if not validate_ip(device["ip"]):
            logging.error(
                "Skipping %s: invalid IP address.",
                device["name"],
            )
            continue

        devices.append(device)

    workbook.close()
    return devices


def build_config_commands():
    """Create specific and generic trap commands for each receiver."""
    commands = []

    for receiver_ip in TRAP_RECEIVER_IPS:
        if not validate_ip(receiver_ip):
            raise ValueError(
                f"Invalid trap receiver IP: {receiver_ip}"
            )

        commands.append(
            f"add snmp trap specific {receiver_ip} "
            f"-version V2 -destPort 162"
        )
        commands.append(
            f"add snmp trap generic {receiver_ip} "
            f"-version V2 -destPort 162"
        )

    return commands


# ============================================================
# CONFIGURE ONE CITRIX DEVICE
# ============================================================

def configure_device(device, config_commands):
    device_name = safe_filename(device["name"])
    output_file = os.path.join(
        OUTPUT_DIR,
        f"{device_name}_{RUN_TIMESTAMP}.txt",
    )

    connection = None
    success = False

    with open(output_file, "w", encoding="utf-8") as output:
        def write_output(message):
            output.write(message + "\n")
            output.flush()

        write_output("=" * 70)
        write_output(f"Device Name : {device['name']}")
        write_output(f"Device IP   : {device['ip']}")
        write_output(f"Start Time  : {datetime.now().isoformat()}")
        write_output("=" * 70)

        try:
            logging.info(
                "Connecting to %s (%s)",
                device["name"],
                device["ip"],
            )
            write_output("\nSTATUS: Connecting over SSH...")

            connection = ConnectHandler(
                device_type="netscaler",
                host=device["ip"],
                username=device["username"],
                password=device["password"],
                port=22,
                conn_timeout=20,
                banner_timeout=30,
                auth_timeout=30,
                fast_cli=False,
            )

            write_output("SSH connection successful.")

            # ------------------------------------------------
            # PUSH SNMP TRAP CONFIGURATION
            # ------------------------------------------------
            write_output("\n### SNMP TRAP CONFIGURATION ###")

            for command in config_commands:
                write_output(f"\nCOMMAND: {command}")

                try:
                    result = connection.send_command(
                        command,
                        read_timeout=30,
                        strip_prompt=False,
                        strip_command=True,
                    )
                    write_output(result or "(No command output)")
                    logging.info(
                        "%s | %s | %s",
                        device["name"],
                        command,
                        result,
                    )

                    # Record common CLI error indicators.
                    if re.search(
                        r"(^|\n)\s*(ERROR|FAILED|INVALID)\b",
                        result,
                        re.IGNORECASE,
                    ):
                        write_output(
                            "WARNING: Possible CLI error; "
                            "check the command output."
                        )

                except Exception as exc:
                    write_output(f"COMMAND ERROR: {exc}")
                    logging.exception(
                        "%s: command failed: %s",
                        device["name"],
                        command,
                    )
                    # Continue so remaining commands and show
                    # commands can still be attempted.

            # ------------------------------------------------
            # SAVE CONFIGURATION
            # ------------------------------------------------
            write_output("\n### SAVE CONFIGURATION ###")
            write_output("COMMAND: save ns config")

            try:
                result = connection.send_command(
                    "save ns config",
                    read_timeout=30,
                    strip_prompt=False,
                    strip_command=True,
                )
                write_output(result or "(No command output)")
            except Exception as exc:
                write_output(f"SAVE ERROR: {exc}")
                logging.exception(
                    "%s: save ns config failed",
                    device["name"],
                )

            # ------------------------------------------------
            # VERIFY CONFIGURATION
            # ------------------------------------------------
            verification_commands = [
                "show snmp trap",
                "show snmp manager",
            ]

            for command in verification_commands:
                write_output(f"\n### VERIFICATION: {command} ###")

                try:
                    result = connection.send_command(
                        command,
                        read_timeout=30,
                        strip_prompt=False,
                        strip_command=True,
                    )
                    write_output(result or "(No command output)")
                except Exception as exc:
                    write_output(
                        f"VERIFICATION ERROR: {exc}"
                    )
                    logging.exception(
                        "%s: verification failed: %s",
                        device["name"],
                        command,
                    )

            success = True
            write_output("\nSTATUS: Execution completed.")
            logging.info(
                "Execution completed for %s",
                device["name"],
            )

        except NetmikoAuthenticationException as exc:
            write_output(f"\nAUTHENTICATION FAILED: {exc}")
            logging.error(
                "Authentication failed for %s",
                device["name"],
            )

        except NetmikoTimeoutException as exc:
            write_output(f"\nCONNECTION TIMEOUT: {exc}")
            logging.error(
                "Connection timed out for %s",
                device["name"],
            )

        except Exception as exc:
            write_output(f"\nUNEXPECTED ERROR: {exc}")
            logging.exception(
                "Unexpected error for %s",
                device["name"],
            )

        finally:
            if connection:
                try:
                    connection.disconnect()
                    write_output("\nSSH connection closed.")
                except Exception as exc:
                    write_output(
                        f"\nDISCONNECT WARNING: {exc}"
                    )

            write_output(
                f"\nEnd Time: {datetime.now().isoformat()}"
            )
            write_output("=" * 70)

    return success, output_file


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    print("=" * 60)
    print("CITRIX ADC - SNMP TRAP CONFIGURATION AUTOMATION")
    print("=" * 60)

    try:
        config_commands = build_config_commands()
        devices = load_devices()
    except Exception as exc:
        print(f"PRECHECK FAILED: {exc}")
        logging.exception("Precheck failed")
        return

    if not devices:
        print("No valid devices found in DEVICES.xlsx.")
        return

    print(f"Devices found: {len(devices)}")
    print(f"Output folder: {OUTPUT_DIR}")
    print(f"Trap receivers: {len(TRAP_RECEIVER_IPS)}")
    print("\nStarting device processing...\n")

    results = []

    for number, device in enumerate(devices, start=1):
        print(
            f"[{number}/{len(devices)}] "
            f"Connecting to {device['name']} ({device['ip']})..."
        )

        success, output_file = configure_device(
            device,
            config_commands,
        )

        results.append(
            (device["name"], device["ip"], success, output_file)
        )

        print(f"Output saved: {output_file}")
        print(f"Connection/execution status: "
              f"{'COMPLETED' if success else 'FAILED'}")
        print("-" * 60)

    # --------------------------------------------------------
    # SUMMARY REPORT
    # --------------------------------------------------------
    summary_file = os.path.join(
        OUTPUT_DIR,
        f"summary_{RUN_TIMESTAMP}.txt",
    )

    with open(summary_file, "w", encoding="utf-8") as summary:
        summary.write("CITRIX ADC SNMP CONFIGURATION SUMMARY\n")
        summary.write("=" * 70 + "\n")

        for name, ip, success, output_file in results:
            status = "COMPLETED" if success else "FAILED"
            summary.write(
                f"{name} | {ip} | {status} | {output_file}\n"
            )

        completed = sum(1 for item in results if item[2])
        summary.write("\n")
        summary.write(f"Total devices: {len(results)}\n")
        summary.write(f"Completed: {completed}\n")
        summary.write(f"Failed: {len(results) - completed}\n")

    print("\nPROCESSING FINISHED")
    print(f"Completed: {sum(1 for item in results if item[2])}")
    print(f"Failed: {sum(1 for item in results if not item[2])}")
    print(f"Summary report: {summary_file}")


if __name__ == "__main__":
    main()
