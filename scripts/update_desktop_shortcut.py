"""
scripts/update_desktop_shortcut.py - Crée ou met à jour le raccourci NovaDAW sur le Bureau Windows
avec l'icône personnalisée Supernova haute résolution.
"""
import os
import sys

def update_shortcut():
    if sys.platform != "win32":
        print("La création de raccourci .lnk est réservée à Windows.")
        return

    try:
        import win32com.client
    except ImportError:
        try:
            import subprocess
            project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            main_py = os.path.join(project_dir, "main.py")
            ico_path = os.path.join(project_dir, "assets", "nova_icon.ico")
            pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw_exe):
                pythonw_exe = sys.executable

            def _ps_escape(val: str) -> str:
                return val.replace("'", "''")

            ps_cmd = (
                "$wsh = New-Object -ComObject WScript.Shell; "
                "$desktop = [System.Environment]::GetFolderPath('Desktop'); "
                "$shortcutPath = Join-Path $desktop 'NovaDAW.lnk'; "
                "$shortcut = $wsh.CreateShortcut($shortcutPath); "
                f"$shortcut.TargetPath = '{_ps_escape(pythonw_exe)}'; "
                f"$shortcut.Arguments = '\"{_ps_escape(main_py)}\"'; "
                f"$shortcut.WorkingDirectory = '{_ps_escape(project_dir)}'; "
                f"$shortcut.IconLocation = '{_ps_escape(ico_path)},0'; "
                "$shortcut.Description = 'NovaDAW - Station de Travail Audio Numérique'; "
                "$shortcut.Save(); "
                "Write-Output \"Raccourci Bureau mis à jour avec succès : $shortcutPath\""
            )
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
            print(res.stdout)
            return
        except Exception as e:
            print(f"Erreur PowerShell: {e}")
            return

    try:
        wsh = win32com.client.Dispatch("WScript.Shell")
        desktop = wsh.SpecialFolders("Desktop")
        shortcut_path = os.path.join(desktop, "NovaDAW.lnk")
        shortcut = wsh.CreateShortcut(shortcut_path)

        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        main_py = os.path.join(project_dir, "main.py")
        ico_path = os.path.join(project_dir, "assets", "nova_icon.ico")
        pythonw_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_exe):
            pythonw_exe = sys.executable

        shortcut.TargetPath = pythonw_exe
        shortcut.Arguments = f'"{main_py}"'
        shortcut.WorkingDirectory = project_dir
        shortcut.IconLocation = f"{ico_path},0"
        shortcut.Description = "NovaDAW - Station de Travail Audio Numérique"
        shortcut.Save()
        print(f"Raccourci Bureau mis à jour : {shortcut_path}")
    except Exception as e:
        print(f"Erreur création raccourci: {e}")

if __name__ == "__main__":
    update_shortcut()
