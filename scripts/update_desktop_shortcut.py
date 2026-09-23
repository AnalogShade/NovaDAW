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
            ps_cmd = """
            $wsh = New-Object -ComObject WScript.Shell
            $desktop = [System.Environment]::GetFolderPath('Desktop')
            $shortcutPath = Join-Path $desktop 'NovaDAW.lnk'
            $shortcut = $wsh.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = 'C:\\Users\\Utilisateur\\AppData\\Local\\Programs\\Python\\Python313\\pythonw.exe'
            $shortcut.Arguments = '\"C:\\Users\\Utilisateur\\Documents\\Dev\\music software\\main.py\"'
            $shortcut.WorkingDirectory = 'C:\\Users\\Utilisateur\\Documents\\Dev\\music software'
            $shortcut.IconLocation = 'C:\\Users\\Utilisateur\\Documents\\Dev\\music software\\assets\\nova_icon.ico,0'
            $shortcut.Description = 'NovaDAW - Digital Audio Workstation Open-Source'
            $shortcut.Save()
            Write-Output "Raccourci Bureau mis à jour avec succès : $shortcutPath"
            """
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
        shortcut.Description = "NovaDAW - Digital Audio Workstation Open-Source"
        shortcut.Save()
        print(f"Raccourci Bureau mis à jour : {shortcut_path}")
    except Exception as e:
        print(f"Erreur création raccourci: {e}")

if __name__ == "__main__":
    update_shortcut()
