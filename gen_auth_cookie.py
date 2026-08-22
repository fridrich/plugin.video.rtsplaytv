#!/usr/bin/env python3
# Copyright (C) 2026 Fridrich Strba
#
# Standalone CLI tool to generate RTS (MaRTS) session cookies on a PC.
# Reuses the core login mechanism defined inside resources/lib/auth.py.

import argparse
import os
import sys

# Insert resources/lib into path to import auth.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "resources", "lib"))
import auth


def log_msg(msg):
    sys.stderr.write(f"[*] {msg}\n")
    sys.stderr.flush()


def error(msg):
    sys.stderr.write(f"[ERROR] {msg}\n")
    sys.stderr.flush()
    sys.exit(1)


def getpass_masked(prompt="[*] Enter MaRTS Password: ", mask="*"):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    password = []

    try:
        import msvcrt

        while True:
            ch = msvcrt.getch()
            if ch in (b'\r', b'\n'):
                sys.stderr.write('\n')
                sys.stderr.flush()
                break
            elif ch in (b'\x7f', b'\x08'):
                if password:
                    password.pop()
                    sys.stderr.write('\b \b')
                    sys.stderr.flush()
            elif ch == b'\x03':
                raise KeyboardInterrupt
            else:
                password.append(ch.decode('utf-8', errors='ignore'))
                sys.stderr.write(mask)
                sys.stderr.flush()
        return "".join(password)
    except ImportError:
        import termios
        import tty

        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            import getpass

            return getpass.getpass(prompt="", stream=sys.stderr)

        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ('\r', '\n'):
                    sys.stderr.write('\r\n')
                    sys.stderr.flush()
                    break
                elif ch in ('\x7f', '\x08'):
                    if password:
                        password.pop()
                        sys.stderr.write('\b \b')
                        sys.stderr.flush()
                elif ch == '\x03':
                    raise KeyboardInterrupt
                else:
                    password.append(ch)
                    sys.stderr.write(mask)
                    sys.stderr.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return "".join(password)


def main():
    description = "Generate RTS (MaRTS) session cookies securely on a PC."
    epilog = """
Password Input Methods:

  1. SECURE INTERACTIVE PROMPT (Recommended & 100% Shell-Immune):
     Omit -p/--password and --password-file. The script will prompt you:
     ./gen_auth_cookie.py -u "your_email@example.com" -o cookies.lwp

  2. PASSWORD FILE (100% Shell-Immune):
     Write your password exactly as-is into a local file:
     ./gen_auth_cookie.py -u "your_email@example.com" --password-file="pass.txt" -o cookies.lwp

  3. ENVIRONMENT VARIABLE (100% Shell-Immune):
     Export your password to MARTS_PASSWORD before running:
     export MARTS_PASSWORD="your_password"
     ./gen_auth_cookie.py -u "your_email@example.com" -o cookies.lwp

  4. SINGLE QUOTES:
     Wrap in single quotes to disable shell expansions:
     ./gen_auth_cookie.py -u "your_email@example.com" -p 'your_password' -o cookies.lwp
"""
    parser = argparse.ArgumentParser(
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-u", "--username", required=True, help="MaRTS account email"
    )
    parser.add_argument(
        "-p", "--password", required=False, help="MaRTS account password"
    )
    parser.add_argument(
        "--password-file",
        required=False,
        help="Path to a text file containing the password as-is",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="cookies.lwp",
        help="Output filename for standard LWP cookie jar (default: cookies.lwp)",
    )
    args = parser.parse_args()

    email = args.username
    password = None

    if args.password:
        password = args.password
    elif args.password_file:
        if not os.path.exists(args.password_file):
            error(f"Password file not found at: {args.password_file}")
        try:
            with open(
                args.password_file, "r", encoding="utf-8", errors="ignore"
            ) as f:
                password = f.read().rstrip("\r\n")
            log_msg(f"Loaded password from file: {args.password_file}")
        except Exception as e:
            error(f"Failed to read password file: {e}")
    else:
        env_pass = os.environ.get("MARTS_PASSWORD")
        if env_pass:
            password = env_pass
            log_msg("Loaded password from environment variable MARTS_PASSWORD")
        else:
            try:
                password = getpass_masked(
                    prompt="[*] Enter MaRTS Password (masked): "
                )
            except Exception as e:
                error(f"Failed to read password from secure prompt: {e}")

    if not password:
        error(
            "Password is required. Provide it via -p, "
            "--password-file, MARTS_PASSWORD env, or interactively."
        )

    # Initialize RTSAuth with the specified CLI output path as session_file
    rts_auth = auth.RTSAuth(session_file=args.output)
    
    # Temporarily override auth.log_msg to print to sys.stderr standalone
    auth.log_msg = log_msg

    try:
        rts_auth._login_with_credentials(email, password)
        log_msg(f"SUCCESS! Session cookies successfully written to '{args.output}'.")
        log_msg("To complete setup, copy this file into your Kodi device's userdata directory:")
        log_msg("  - Linux: ~/.kodi/userdata/addon_data/plugin.video.rtsplaytv/cookies.lwp")
        log_msg("  - CoreELEC/LibreELEC: /storage/.kodi/userdata/addon_data/plugin.video.rtsplaytv/cookies.lwp")
        log_msg("  - Android: /sdcard/Android/data/org.xbmc.kodi/files/.kodi/userdata/addon_data/plugin.video.rtsplaytv/cookies.lwp")
    except Exception as e:
        error(f"Authentication failed: {e}")


if __name__ == "__main__":
    main()
