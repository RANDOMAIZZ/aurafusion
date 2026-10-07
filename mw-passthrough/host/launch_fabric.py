"""launch_fabric.py - запуск Fabric 1.21.1 (TLauncher-версия) с gameDir C:\\dev\\passthrough\\mcgame.
Офлайн-аккаунт RANDOMAIZZER. Без GUI-лаунчера, напрямую через JDK21.
"""
import json, os, sys, subprocess, platform

MC = os.path.join(os.environ["APPDATA"], ".minecraft")
VER = os.path.join(MC, "versions", "Fabric 1.21.1", "Fabric 1.21.1.json")
VERJAR = os.path.join(MC, "versions", "Fabric 1.21.1", "Fabric 1.21.1.jar")
GAMEDIR = r"C:\dev\passthrough\mcgame"
JAVA = r"C:\dev\passthrough\jdk21\bin\java.exe"
UUID = "cf523a97ee0344a18227b8b38e86ff1f"
USER = "RANDOMAIZZER"

d = json.load(open(VER, encoding="utf-8"))
cp = [VERJAR]
for lib in d.get("libraries", []):
    # rules check
    ok = True
    for r in lib.get("rules", []):
        action = r.get("action")
        osr = r.get("os", {})
        name = osr.get("name", "")
        if name and name != "windows":
            if action == "allow":
                ok = False
        if action == "disallow" and (not name or name == "windows"):
            ok = False
    if not ok:
        continue
    art = lib.get("downloads", {}).get("artifact")
    if art:
        p = os.path.join(MC, "libraries", art["path"].replace("/", os.sep))
        if os.path.exists(p):
            cp.append(p)
    elif lib.get("name"):
        try:
            g, a, v = lib["name"].split(":")[:3]
            p = os.path.join(MC, "libraries", g.replace(".", os.sep), a, v, f"{a}-{v}.jar")
            if os.path.exists(p) and p not in cp:
                cp.append(p)
        except Exception:
            pass
    # natives for windows
    nat = (lib.get("downloads", {}).get("classifiers") or {})
    for key in ("natives-windows", "natives-windows-64", "natives-windows-32"):
        if key in nat:
            p = os.path.join(MC, "libraries", nat[key]["path"].replace("/", os.sep))
            if os.path.exists(p):
                cp.append(p)
asset_index = d.get("assets") or d.get("assetIndex", {}).get("id", "17")
cmd = [JAVA, "-Xmx2G",
       "-Djava.library.path=" + os.path.join(MC, "versions", "Fabric 1.21.1", "natives"),
       "-cp", ";".join(cp),
       d["mainClass"],
       "--username", USER, "--version", "Fabric 1.21.1",
       "--gameDir", GAMEDIR,
       "--assetsDir", os.path.join(MC, "assets"),
       "--assetIndex", str(asset_index),
       "--uuid", UUID, "--accessToken", "0",
       "--userType", "legacy",
       "--versionType", "release"]
print("запуск...", " ".join(cmd[:4]), "...")
os.makedirs(GAMEDIR, exist_ok=True)
log = open(os.path.join(GAMEDIR, "launch.log"), "w")
p = subprocess.Popen(cmd, cwd=GAMEDIR, stdout=log, stderr=subprocess.STDOUT)
print("PID:", p.pid)
