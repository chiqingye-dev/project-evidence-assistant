"""One environment-based model configuration per development session."""

import os
import sys


def read_api_key():
    if "DEEPSEEK_API_KEY" in os.environ:
        return os.environ["DEEPSEEK_API_KEY"].strip()
    # Codex may have started before the user configured their Windows environment.
    # Read only this explicitly named environment variable, never another secret.
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
                value, _ = winreg.QueryValueEx(environment, "DEEPSEEK_API_KEY")
            return str(value).strip()
        except FileNotFoundError:
            return ""
    return ""
