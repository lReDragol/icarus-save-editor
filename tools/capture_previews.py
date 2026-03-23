from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("ICARUS_EDITOR_SCREENSHOT_MODE", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import icarus_save_editor as editor


OUTPUT_DIR = ROOT / "screenshots"
CAPTURES = [
    (0, "01-main.png"),
    (1, "02-unlocks.png"),
    (2, "03-player.png"),
    (3, "04-inventory.png"),
    (4, "05-pets.png"),
    (5, "06-other.png"),
]


class PreviewCapture:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setStyleSheet(editor.DISCORD_QSS)
        editor.IcarusGameData.try_load_default = classmethod(lambda cls: None)
        print("Creating window...", flush=True)
        self.window = editor.MainWindow()
        print("Window ready.", flush=True)
        self.window.resize(1720, 980)
        self.window.show()
        self.deadline = time.monotonic() + 15.0
        self.index = 0

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        QTimer.singleShot(250, self._wait_for_content)

    def _wait_for_content(self) -> None:
        self.app.processEvents()

        if not self.window.model.root:
            folders = editor.guess_save_folders()
            best = editor.pick_best_folder(folders) if folders else None
            if best:
                try:
                    print(f"Loading save: {best}", flush=True)
                    self.window.load_folder(best)
                except Exception:
                    pass

        if self.window.model.root or time.monotonic() >= self.deadline:
            print(
                f"Capture start. Save loaded: {bool(self.window.model.root)}",
                flush=True,
            )
            QTimer.singleShot(300, self._capture_next)
            return

        QTimer.singleShot(250, self._wait_for_content)

    def _capture_next(self) -> None:
        if self.index >= len(CAPTURES):
            self.app.quit()
            return

        tab_index, filename = CAPTURES[self.index]
        self.window.tabs.setCurrentIndex(tab_index)
        self.window.repaint()
        self.app.processEvents()
        print(f"Capturing tab #{tab_index}: {filename}", flush=True)

        QTimer.singleShot(250, lambda: self._save_current(filename))

    def _save_current(self, filename: str) -> None:
        output_path = OUTPUT_DIR / filename
        self.app.processEvents()
        if not self.window.grab().save(str(output_path), "PNG"):
            raise RuntimeError(f"Failed to save screenshot: {output_path}")
        print(output_path)
        self.index += 1
        QTimer.singleShot(150, self._capture_next)

    def run(self) -> int:
        return self.app.exec()


def main() -> int:
    return PreviewCapture().run()


if __name__ == "__main__":
    raise SystemExit(main())
