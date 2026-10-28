# PyInstaller spec: one folder with two executables sharing the same runtime.
#   Chiku.exe      - the desktop app (no console window)
#   chiku-cli.exe  - diagnostics (--check, --benchmark, --mic-test, ...)
# Models are NOT bundled; the first-run setup downloads them with the user's consent.
# Build:  pyinstaller packaging/chiku.spec --noconfirm --distpath packaging/dist
#                     --workpath packaging/build
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
ICON = str(ROOT / "src" / "chiku" / "assets" / "chiku.ico")

datas = [(str(ROOT / "src" / "chiku" / "assets"), "chiku/assets")]
datas += collect_data_files("espeakng_loader")  # espeak-ng-data (phonemes for the voice)
datas += collect_data_files("kokoro_onnx")  # config.json
datas += collect_data_files("faster_whisper")  # bundled VAD asset
# phonemizer -> segments -> csvw -> language_tags read JSON/CSV data files at import time
# (caught by the --version-info smoke test in build.ps1).
for pkg in ("phonemizer", "segments", "csvw", "language_tags"):
    datas += collect_data_files(pkg)
datas += [(str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")]
binaries = collect_dynamic_libs("espeakng_loader") + collect_dynamic_libs("ctranslate2")

QT_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtXml", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
]
EXCLUDES = QT_EXCLUDES + ["tkinter", "pytest", "mypy", "ruff", "PIL", "PyInstaller",
                          "matplotlib", "IPython", "torch", "nvidia"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["chiku.ui.app", "chiku.ui.main_window", "chiku.ui.settings_dialog",
                   "chiku.ui.onboarding", "chiku.ui.memory_dialog", "chiku.ui.activity_dialog",
                   "chiku.ui.models_dialog", "keyring.backends.Windows",
                   "pydantic.deprecated.decorator"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

common = dict(exclude_binaries=True, icon=ICON, version=str(ROOT / "packaging" /
                                                             "version_info.txt"),
              upx=False)
gui = EXE(pyz, a.scripts, [], name="Chiku", console=False, **common)  # noqa: F821
cli = EXE(pyz, a.scripts, [], name="chiku-cli", console=True, **common)  # noqa: F821

coll = COLLECT(gui, cli, a.binaries, a.datas, name="Chiku", upx=False)  # noqa: F821
