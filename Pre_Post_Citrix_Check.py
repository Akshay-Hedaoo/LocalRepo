#Automates pre and post change captures for Citrix ADC load balancers via SSH. Collects version, 
#HA status, and LB/CS/VPN vServer statistics and summaries from Primary and Secondary loadbalancer, 
#saving outputs to separate timestamped text files for change validation and comparison.

"""
Citrix ADC Pre/Post Capture Automation
Connects to Primary and Secondary ADC appliances over SSH.
Saves each appliance's command output to a separate text file.
"""

# IMPORT THE LIBRARIES AND MODULES
import os
import sys
import time
import getpass
import socket
from datetime import datetime

import paramiko


# Commands to execute on each Citrix ADC appliance
COMMANDS = [
    "show version",
    "show ha node",
    "show lb vserver -summary | grep UP -c",
    "show lb vserver -summary | grep DOWN -c",
    "show cs vserver -summary | grep UP -c",
    "show cs vserver -summary | grep DOWN -c",
    "show vpn vserver -summary | grep UP -c",
    "show vpn vserver -summary | grep DOWN -c",
    "show lb vserver -summary -fullvalues",
    "show cs vserver -summary -fullvalues",
    "show vpn vserver -summary -fullvalues",
]
# DEFINED VALUES WHILE CONNECTING TO END HOST VIA SSH 

SSH_PORT = 22
CONNECT_TIMEOUT = 15
COMMAND_TIMEOUT = 120

# COLLECT THE INFORMATION FROM USER BEFORE CONNECTING TO THE DEVICES

def prompt_capture_details():
    """Collect capture details from the user."""

    while True:
        capture_type = input(
            "Capture type (Pre/Post): "
        ).strip().capitalize()

        if capture_type in ("Pre", "Post"):
            break

        print("Invalid capture type. Enter Pre or Post.")

    primary_ip = input("Primary management IP: ").strip()
    secondary_ip = input("Secondary management IP: ").strip()
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    change_number = input("Change number: ").strip()

    if not all([
        primary_ip,
        secondary_ip,
        username,
        password,
        change_number,
    ]):
        raise ValueError("All fields are mandatory.")

    if primary_ip == secondary_ip:
        raise ValueError(
            "Primary and Secondary management IPs must be different."
        )

    return {
        "capture_type": capture_type,
        "primary_ip": primary_ip,
        "secondary_ip": secondary_ip,
        "username": username,
        "password": password,
        "change_number": change_number,
    }


def execute_command(ssh, command):
    """Execute one command and return output and any errors."""

    stdin, stdout, stderr = ssh.exec_command(
        command,
        timeout=COMMAND_TIMEOUT,
        get_pty=False,
    )

    # Read both output streams to completion.
    output = stdout.read().decode("utf-8", errors="replace")
    error_output = stderr.read().decode("utf-8", errors="replace")
    exit_status = stdout.channel.recv_exit_status()

    return output, error_output, exit_status


def capture_appliance(ip, role, details, capture_date):
    """Capture command output from one ADC appliance."""

    filename = (
        f"{details['capture_type']}_{ip}_{capture_date}.txt"
    )

    filepath = os.path.abspath(filename)

    print(f"\nConnecting to {role} ADC: {ip}")

    with open(filepath, "w", encoding="utf-8") as report:
        report.write("=" * 80 + "\n")
        report.write("CITRIX ADC PRE/POST CAPTURE REPORT\n")
        report.write("=" * 80 + "\n")
        report.write(f"Capture type : {details['capture_type']}\n")
        report.write(f"Appliance    : {role}\n")
        report.write(f"Management IP: {ip}\n")
        report.write(f"Change number: {details['change_number']}\n")
        report.write(
            f"Capture date : {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}\n"
        )
        report.write("=" * 80 + "\n\n")

        ssh = paramiko.SSHClient()

        # Uses locally configured known_hosts entries and accepts
        # unknown host keys with a warning. See security note below.
        ssh.load_system_host_keys()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            report.write("SSH CONNECTION\n")
            report.write("-" * 80 + "\n")

            ssh.connect(
                hostname=ip,
                port=SSH_PORT,
                username=details["username"],
                password=details["password"],
                timeout=CONNECT_TIMEOUT,
                banner_timeout=CONNECT_TIMEOUT,
                auth_timeout=CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )

            print(f"Connected to {role} ADC: {ip}")
            report.write("Connection status: SUCCESS\n\n")

        except (
            paramiko.SSHException,
            socket.timeout,
            socket.error,
            OSError,
        ) as exc:
            message = f"SSH connection failed: {exc}"
            print(f"ERROR - {role} ({ip}): {message}")
            report.write(f"Connection status: FAILED\n{message}\n")
            report.write("\nCapture could not be completed.\n")
            return False

        try:
            for index, command in enumerate(COMMANDS, start=1):
                timestamp = datetime.now().strftime(
                    "%d-%m-%Y %H:%M:%S"
                )

                section = (
                    "\n"
                    + "=" * 80
                    + f"\nCOMMAND {index}: {command}\n"
                    + f"Timestamp: {timestamp}\n"
                    + "=" * 80
                    + "\n"
                )

                report.write(section)
                print(f"[{role}] Executing: {command}")

                try:
                    output, error_output, exit_status = (
                        execute_command(ssh, command)
                    )

                    report.write(output)

                    if not output.endswith("\n"):
                        report.write("\n")

                    if error_output.strip():
                        report.write("\n--- STDERR ---\n")
                        report.write(error_output)
                        if not error_output.endswith("\n"):
                            report.write("\n")

                    report.write(
                        f"\nExit status: {exit_status}\n"
                    )

                    if exit_status != 0:
                        report.write(
                            "WARNING: Command returned a non-zero "
                            "exit status.\n"
                        )
                        print(
                            f"WARNING: Command returned status "
                            f"{exit_status}"
                        )

                except (
                    paramiko.SSHException,
                    socket.timeout,
                    socket.error,
                    OSError,
                    EOFError,
                ) as exc:
                    report.write(
                        f"\nCOMMAND EXECUTION ERROR: {exc}\n"
                    )
                    print(
                        f"ERROR executing command on {ip}: {exc}"
                    )

                    # Continue with the next command as requested.
                    continue

                # Flush each section so partial captures are retained.
                report.flush()

        finally:
            ssh.close()

        report.write("\n" + "=" * 80 + "\n")
        report.write("END OF CAPTURE\n")
        report.write("=" * 80 + "\n")

    print(f"Capture file saved: {filepath}")
    return True


def main():
    print("=" * 60)
    print("CITRIX ADC PRE/POST CAPTURE AUTOMATION")
    print("=" * 60)

    try:
        details = prompt_capture_details()
    except (ValueError, KeyboardInterrupt) as exc:
        print(f"\nInput error: {exc}")
        sys.exit(1)

    capture_date = datetime.now().strftime("%d-%m-%Y")

    appliances = [
        ("Primary", details["primary_ip"]),
        ("Secondary", details["secondary_ip"]),
    ]

    print("\nStarting capture for both appliances.")
    print(f"Capture type: {details['capture_type']}")
    print(f"Change number: {details['change_number']}")
    print(f"Output directory: {os.getcwd()}")

    results = []

    for role, ip in appliances:
        try:
            success = capture_appliance(
                ip=ip,
                role=role,
                details=details,
                capture_date=capture_date,
            )
            results.append((role, ip, success))

        except Exception as exc:
            # Unexpected errors must not prevent the other appliance
            # from being attempted.
            print(f"ERROR - {role} ({ip}): {exc}")
            results.append((role, ip, False))

        # Small pause between appliance connections.
        time.sleep(1)

    print("\n" + "=" * 60)
    print("CAPTURE SUMMARY")
    print("=" * 60)

    for role, ip, success in results:
        status = "COMPLETED" if success else "FAILED / INCOMPLETE"
        print(f"{role} ({ip}): {status}")

    print(f"\nOutput directory: {os.getcwd()}")
    print("Capture process finished.")

#RUN THE MAIN FUNCTION ONLY IF THIS SCRIPT IS CALLED

if __name__ == "__main__":
    main()
