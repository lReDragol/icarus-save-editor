from __future__ import annotations

import codecs
import copy
import base64
import hashlib
import json
import random
import math
import mmap
import os
import re
import shlex
import shutil
import struct
import sys
import traceback
import uuid
import zlib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _fatal_message(title: str, text: str) -> None:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10)
            return
    except Exception:
        pass
    try:
        sys.stderr.write(f"{title}\n{text}\n")
    except Exception:
        pass


try:
    from PySide6.QtCore import Qt, Signal, QTimer, QProcess, QPointF, QRectF
    from PySide6.QtGui import (
        QAction,
        QFont,
        QKeySequence,
        QTextCursor,
        QColor,
        QPainter,
        QPen,
        QPolygonF,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractSpinBox,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QCompleter,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QGroupBox,
        QGridLayout,
        QHeaderView,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
        QFileDialog,
        QScrollArea,
    )
except Exception:
    _fatal_message(
        "Ошибка запуска",
        "Не удалось импортировать PySide6.\n\n"
        "Установи зависимости:\n"
        "  pip install PySide6\n\n"
        "Трассировка:\n" + traceback.format_exc(),
    )
    raise


DEFAULT_TEST_MODE = False


def _truthy(v: Optional[str]) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "enable", "enabled")


def _parse_test_flag(argv: List[str]) -> bool:
    for a in argv[1:]:
        if not isinstance(a, str):
            continue
        s = a.strip().lower()
        if s in ("--test", "-t"):
            return True
        if s.startswith("--test="):
            return _truthy(s.split("=", 1)[1])
        if s.startswith("test="):
            return _truthy(s.split("=", 1)[1])
        if s == "test=true":
            return True
    return False


def _parse_flag(argv: List[str], *names: str) -> bool:
    wanted = {str(name).strip().lower() for name in names if str(name).strip()}
    if not wanted:
        return False
    for arg in argv[1:]:
        if not isinstance(arg, str):
            continue
        if arg.strip().lower() in wanted:
            return True
    return False


APP_TEST_MODE = (
    bool(DEFAULT_TEST_MODE)
    or _truthy(os.getenv("ICARUS_EDITOR_TEST"))
    or _parse_test_flag(sys.argv)
)
APP_SCREENSHOT_MODE = _truthy(
    os.getenv("ICARUS_EDITOR_SCREENSHOT_MODE")
) or _parse_flag(
    sys.argv,
    "--screenshot-mode",
)


def _mask_path_for_display(raw_path: str) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return ""
    if not APP_SCREENSHOT_MODE:
        return path

    normalized = path.replace("\\", "/")
    lowered = normalized.lower()

    if "/playerdata/" in lowered:
        prefix, _sep, _tail = normalized.rpartition("/")
        if prefix.lower().endswith("/playerdata"):
            return f"{prefix}/<SteamID>"
        base = prefix.rsplit("/playerdata/", 1)[0]
        return f"{base}/PlayerData/<SteamID>"

    if lowered.endswith("/engine.ini") or lowered.endswith("\\engine.ini"):
        return ".../Icarus/Saved/Config/WindowsNoEditor/Engine.ini"

    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return path
    if len(parts) == 1:
        return parts[0]
    return f".../{parts[-1]}"


@dataclass(frozen=True)
class GameItem:
    row_name: str
    display_name: str
    itemable_row: str


@dataclass(frozen=True)
class GameCreature:
    row_name: str
    display_name: str


@dataclass(frozen=True)
class GameCustomMobChoice:
    ai_setup: str
    actor_class: str
    display_name: str
    picker_label: str
    default_name: str
    tags: Tuple[str, ...]
    is_boss: bool
    is_great_hunt: bool
    is_friendly: bool


@dataclass(frozen=True)
class GameTalent:
    row_name: str
    display_name: str
    description: str
    talent_tree: str
    max_rank: int
    exact_rank_effects: Tuple[str, ...]


@dataclass(frozen=True)
class GamePhenotype:
    mount_type: str
    stored_value: int
    display_name: str
    rarity_label: str
    chance_percent: float
    weighting: int
    asset_name: str


@dataclass(frozen=True)
class GameCurrency:
    row_name: str
    display_name: str
    color_hex: str
    decorator_text: str
    display_on_main_screen: bool


@dataclass(frozen=True)
class GamePlayerTracker:
    row_name: str
    display_name: str
    tracker_category: str
    is_task_list: bool
    steam_stat_id: str


@dataclass(frozen=True)
class GameAccolade:
    row_name: str
    display_name: str
    description: str
    category: str
    impl_type: str
    tracker_row: str
    goal_count: int
    task_values: Tuple[str, ...]
    steam_achievement_id: str


@dataclass(frozen=True)
class GameBestiaryEntry:
    row_name: str
    display_name: str
    total_points_required: int
    maps: Tuple[str, ...]
    biomes: Tuple[str, ...]
    is_boss: bool


@dataclass(frozen=True)
class CustomMobPreset:
    title: str
    actor_class: str
    ai_setup: str
    mount_type: str
    level: int
    default_name: str
    warning: str


@dataclass(frozen=True)
class CustomSpawnProfile:
    key: str
    title: str
    preserve_progress: bool
    reset_talents: bool
    copy_icon: bool
    use_actor_class: bool
    use_ai_setup: bool


@dataclass(frozen=True)
class CurveKeyPoint:
    time: float
    value: float


class ExperienceCurve:
    def __init__(
        self, keys: List[CurveKeyPoint], post_infinity_extrap: str = "RCCE_Linear"
    ) -> None:
        self.keys = sorted(
            [k for k in keys if isinstance(k, CurveKeyPoint)], key=lambda k: k.time
        )
        self.post_infinity_extrap = (
            post_infinity_extrap or ""
        ).strip() or "RCCE_Linear"

    def value_at(self, t: float) -> float:
        if not self.keys:
            return 0.0

        x = float(t)
        if x <= self.keys[0].time:
            return float(self.keys[0].value)

        for i in range(len(self.keys) - 1):
            a = self.keys[i]
            b = self.keys[i + 1]
            if a.time <= x <= b.time:
                if b.time == a.time:
                    return float(b.value)
                f = (x - a.time) / (b.time - a.time)
                return float(a.value + (b.value - a.value) * f)

        if len(self.keys) >= 2 and self.post_infinity_extrap == "RCCE_Linear":
            a = self.keys[-2]
            b = self.keys[-1]
            if b.time != a.time:
                slope = (b.value - a.value) / (b.time - a.time)
                return float(b.value + (x - b.time) * slope)
        return float(self.keys[-1].value)

    def level_for_xp(self, xp: int, max_level: int = 1000) -> int:
        lvl = 0
        xpf = float(max(0, int(xp)))
        for L in range(int(max_level) + 1):
            if xpf >= self.value_at(float(L)):
                lvl = int(L)
            else:
                break
        return int(lvl)


DEFAULT_PLAYER_XP_CURVE = ExperienceCurve(
    keys=[
        CurveKeyPoint(0.0, 0.0),
        CurveKeyPoint(1.0, 2400.0),
        CurveKeyPoint(2.0, 8610.0),
        CurveKeyPoint(3.0, 18730.0),
        CurveKeyPoint(4.0, 32530.0),
        CurveKeyPoint(5.0, 48630.0),
        CurveKeyPoint(6.0, 67830.0),
        CurveKeyPoint(7.0, 89430.0),
        CurveKeyPoint(8.0, 111030.0),
        CurveKeyPoint(9.0, 135030.0),
        CurveKeyPoint(10.0, 161500.0),
        CurveKeyPoint(15.0, 326000.0),
        CurveKeyPoint(20.0, 600000.0),
        CurveKeyPoint(25.0, 975000.0),
        CurveKeyPoint(30.0, 1400000.0),
        CurveKeyPoint(35.0, 1942000.0),
        CurveKeyPoint(40.0, 2550000.0),
        CurveKeyPoint(45.0, 3200000.0),
        CurveKeyPoint(50.0, 3890000.0),
        CurveKeyPoint(51.0, 4034000.0),
    ],
    post_infinity_extrap="RCCE_Linear",
)


DEFAULT_MOUNT_XP_CURVE = ExperienceCurve(
    keys=[
        CurveKeyPoint(0.0, 0.0),
        CurveKeyPoint(5.0, 5000.0),
        CurveKeyPoint(10.0, 13500.0),
        CurveKeyPoint(15.0, 27500.0),
        CurveKeyPoint(20.0, 47000.0),
        CurveKeyPoint(25.0, 75000.0),
        CurveKeyPoint(30.0, 140000.0),
        CurveKeyPoint(35.0, 245000.0),
        CurveKeyPoint(40.0, 440000.0),
        CurveKeyPoint(45.0, 710000.0),
        CurveKeyPoint(50.0, 1150000.0),
    ],
    post_infinity_extrap="RCCE_Linear",
)


DEFAULT_PET_XP_CURVE = ExperienceCurve(
    keys=[
        CurveKeyPoint(0.0, 0.0),
        CurveKeyPoint(5.0, 10445.990234375),
        CurveKeyPoint(10.0, 26741.384765625),
        CurveKeyPoint(15.0, 60166.7421875),
        CurveKeyPoint(20.0, 126124.578125),
        CurveKeyPoint(25.0, 250144.9375),
    ],
    post_infinity_extrap="RCCE_Linear",
)


def _uasset_parse_header(buf: bytes) -> Optional[Dict[str, Any]]:
    try:
        if len(buf) < 64:
            return None
        (tag,) = struct.unpack_from("<I", buf, 0)
        if tag != 0x9E2A83C1:
            return None
        legacy_ver = struct.unpack_from("<i", buf, 4)[0]
        if legacy_ver >= 0:
            return None

        o = 0
        o += 4
        o += 4
        o += 4
        o += 4
        o += 4

        (num_custom,) = struct.unpack_from("<i", buf, o)
        o += 4
        if num_custom < 0 or num_custom > 1000:
            return None
        o += num_custom * 20

        (total_header_size,) = struct.unpack_from("<i", buf, o)
        o += 4
        if total_header_size <= 0 or total_header_size > len(buf):
            return None

        _folder, o = _read_fstring(buf, o)

        (package_flags,) = struct.unpack_from("<I", buf, o)
        o += 4
        (name_count,) = struct.unpack_from("<i", buf, o)
        o += 4
        (name_offset,) = struct.unpack_from("<i", buf, o)
        o += 4
        if name_count < 0 or name_count > 2_000_000:
            return None
        if name_offset < 0 or name_offset > total_header_size:
            return None

        (
            _gtd_count,
            _gtd_offset,
            export_count,
            export_offset,
            import_count,
            import_offset,
            dep_offset,
        ) = struct.unpack_from("<iiiiiii", buf, o)
        if export_count <= 0 or export_offset < 0:
            return None
        if export_offset > total_header_size:
            return None

        return {
            "total_header_size": int(total_header_size),
            "package_flags": int(package_flags),
            "name_count": int(name_count),
            "name_offset": int(name_offset),
            "export_count": int(export_count),
            "export_offset": int(export_offset),
            "import_count": int(import_count),
            "import_offset": int(import_offset),
            "dep_offset": int(dep_offset),
        }
    except Exception:
        return None


def _uasset_read_name_map(buf: bytes, header: Dict[str, Any]) -> Optional[List[str]]:
    try:
        name_count = int(header.get("name_count", 0))
        name_offset = int(header.get("name_offset", 0))
        if name_count <= 0 or name_offset < 0 or name_offset >= len(buf):
            return None

        names: List[str] = []
        o = name_offset
        for _ in range(name_count):
            s, o = _read_fstring(buf, o)
            o += 4
            names.append(s)
        return names
    except Exception:
        return None


def _uasset_read_fname(names: List[str], buf: bytes, off: int) -> Tuple[str, int]:
    idx, num = struct.unpack_from("<ii", buf, off)
    name = names[idx] if 0 <= idx < len(names) else f"<name#{idx}>"
    return name, off + 8


def _uasset_parse_prop(
    buf: bytes, off: int, names: List[str]
) -> Tuple[Optional[Dict[str, Any]], int]:
    try:
        name, off = _uasset_read_fname(names, buf, off)
        if name == "None":
            return None, off

        typ, off = _uasset_read_fname(names, buf, off)
        size, array_index = struct.unpack_from("<ii", buf, off)
        off += 8

        extra: Dict[str, Any] = {}
        if typ == "StructProperty":
            struct_name, off = _uasset_read_fname(names, buf, off)
            extra["struct_name"] = struct_name
            off += 16
        elif typ == "ArrayProperty":
            inner_type, off = _uasset_read_fname(names, buf, off)
            extra["inner_type"] = inner_type
        elif typ == "EnumProperty":
            enum_name, off = _uasset_read_fname(names, buf, off)
            extra["enum_name"] = enum_name
        elif typ == "ByteProperty":
            enum_name, off = _uasset_read_fname(names, buf, off)
            extra["enum_name"] = enum_name
        elif typ == "BoolProperty":
            val = buf[off]
            off += 1
            extra["bool"] = bool(val)

        has_guid = buf[off]
        off += 1
        if has_guid:
            extra["prop_guid"] = buf[off : off + 16]
            off += 16

        return (
            {
                "name": name,
                "type": typ,
                "size": int(size),
                "array_index": int(array_index),
                "value_off": int(off),
                "extra": extra,
            },
            int(off),
        )
    except Exception:
        return None, off


def _extract_curve_from_uexp(
    names: List[str], uexp: bytes
) -> Optional[ExperienceCurve]:
    try:
        name_to_idx = {n: i for i, n in enumerate(names)}
        floatcurve_idx = name_to_idx.get("FloatCurve")
        struct_idx = name_to_idx.get("StructProperty")
        if floatcurve_idx is None or struct_idx is None:
            return None

        pat = struct.pack("<ii", int(floatcurve_idx), 0) + struct.pack(
            "<ii", int(struct_idx), 0
        )
        start = uexp.find(pat)
        if start < 0:
            return None

        float_tag, _ = _uasset_parse_prop(uexp, start, names)
        if (
            not float_tag
            or float_tag.get("name") != "FloatCurve"
            or float_tag.get("type") != "StructProperty"
        ):
            return None

        rc_start = int(float_tag["value_off"])
        rc_end = rc_start + int(float_tag["size"])
        if rc_start < 0 or rc_end > len(uexp):
            return None

        keys: List[CurveKeyPoint] = []
        post_extrap = ""

        off = rc_start
        while off < rc_end:
            prop, _ = _uasset_parse_prop(uexp, off, names)
            if not prop:
                break

            if prop.get("name") == "Keys" and prop.get("type") == "ArrayProperty":
                val_off = int(prop["value_off"])
                count = struct.unpack_from("<i", uexp, val_off)[0]
                inner_tag, _ = _uasset_parse_prop(uexp, val_off + 4, names)
                if not inner_tag or inner_tag.get("type") != "StructProperty":
                    return None
                inner_val = int(inner_tag["value_off"])
                inner_size = int(inner_tag["size"])
                if count <= 0 or inner_val < 0 or inner_val + inner_size > len(uexp):
                    return None
                key_size = inner_size // count if count else 0
                if key_size <= 0 or key_size * count != inner_size or key_size < 11:
                    return None

                for i in range(int(count)):
                    chunk = uexp[
                        inner_val + i * key_size : inner_val + (i + 1) * key_size
                    ]
                    try:
                        t = struct.unpack_from("<f", chunk, 3)[0]
                        v = struct.unpack_from("<f", chunk, 7)[0]
                    except Exception:
                        continue
                    keys.append(CurveKeyPoint(time=float(t), value=float(v)))

            if (
                prop.get("name") == "PostInfinityExtrap"
                and prop.get("type") == "ByteProperty"
            ):
                val = uexp[
                    int(prop["value_off"]) : int(prop["value_off"]) + int(prop["size"])
                ]
                if len(val) >= 8:
                    idx, num = struct.unpack_from("<ii", val, 0)
                    if 0 <= idx < len(names):
                        post_extrap = names[idx]

            off = int(prop["value_off"]) + int(prop["size"])

        if not keys:
            return None
        return ExperienceCurve(
            keys=keys, post_infinity_extrap=post_extrap or "RCCE_Linear"
        )
    except Exception:
        return None


def load_experience_curve_from_game(
    paks_dir: str, curve_asset: str
) -> Optional[ExperienceCurve]:
    curve = (curve_asset or "").strip()
    if not curve:
        return None

    needle = f"/Game/Data/Character/{curve}".encode("ascii", errors="ignore")
    magic = b"\xc1\x83\x2a\x9e"

    pak_candidates: List[str] = []

    mods_dir = os.path.join(paks_dir, "mods")
    if os.path.isdir(mods_dir):
        try:
            mod_paks = [
                os.path.join(mods_dir, fn)
                for fn in os.listdir(mods_dir)
                if fn.lower().endswith(".pak")
            ]
            # Mods override base, so prefer them first.
            mod_paks.sort(
                key=lambda p: os.path.getmtime(p) if os.path.isfile(p) else 0.0,
                reverse=True,
            )
            pak_candidates += [p for p in mod_paks if os.path.isfile(p)]
        except Exception:
            pass

    prefer = os.path.join(paks_dir, "pakchunk0_s18-WindowsNoEditor.pak")
    if os.path.isfile(prefer):
        pak_candidates.append(prefer)
    try:
        for fn in os.listdir(paks_dir):
            if not fn.lower().startswith("pakchunk") or not fn.lower().endswith(
                "-windowsnoeditor.pak"
            ):
                continue
            p = os.path.join(paks_dir, fn)
            if p not in pak_candidates and os.path.isfile(p):
                pak_candidates.append(p)
    except Exception:
        pass

    for pak_path in pak_candidates:
        try:
            with open(pak_path, "rb") as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    pos = mm.find(needle)
                    while pos != -1:
                        start = mm.rfind(magic, max(0, pos - 1024 * 1024), pos)
                        if start == -1:
                            pos = mm.find(needle, pos + 1)
                            continue

                        head = mm[start : start + 2 * 1024 * 1024]
                        hdr = _uasset_parse_header(head)
                        if not hdr:
                            pos = mm.find(needle, pos + 1)
                            continue

                        names = _uasset_read_name_map(head, hdr)
                        if not names:
                            pos = mm.find(needle, pos + 1)
                            continue

                        exp_off = int(hdr["export_offset"])
                        if exp_off + 24 > len(head):
                            pos = mm.find(needle, pos + 1)
                            continue
                        name_idx = struct.unpack_from("<i", head, exp_off + 16)[0]
                        export_name = (
                            names[name_idx] if 0 <= name_idx < len(names) else ""
                        )
                        if export_name != curve:
                            pos = mm.find(needle, pos + 1)
                            continue

                        uexp_off = start + int(hdr["total_header_size"])
                        uexp = mm[uexp_off : uexp_off + 64 * 1024]
                        parsed = _extract_curve_from_uexp(names, uexp)
                        if parsed:
                            return parsed

                        pos = mm.find(needle, pos + 1)
                finally:
                    mm.close()
        except Exception:
            continue

    return None


def _parse_nsloctext(value: str) -> Optional[Tuple[str, str, str]]:
    value = value.strip()
    if not (value.startswith("NSLOCTEXT(") and value.endswith(")")):
        return None

    inner = value[len("NSLOCTEXT(") : -1]
    parts: List[str] = []
    i = 0
    while i < len(inner) and len(parts) < 3:
        while i < len(inner) and inner[i] != '"':
            i += 1
        if i >= len(inner):
            break
        i += 1  # skip quote
        out: List[str] = []
        while i < len(inner):
            ch = inner[i]
            if ch == "\\":
                i += 1
                if i < len(inner):
                    out.append(inner[i])
                    i += 1
                continue
            if ch == '"':
                i += 1
                parts.append("".join(out))
                break
            out.append(ch)
            i += 1

    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _localize_nsloctext(value: str, loc: Dict[Tuple[str, str], str]) -> str:
    parsed = _parse_nsloctext(value)
    if not parsed:
        return value
    ns, key, src = parsed
    return loc.get((ns, key), src)


def _parse_invtext(value: str) -> Optional[str]:
    s = (value or "").strip()
    m = re.match(r'^INVTEXT\("((?:[^"\\]|\\.)*)"\)$', s)
    if not m:
        return None
    inner = m.group(1)
    try:
        return bytes(inner, "utf-8").decode("unicode_escape")
    except Exception:
        return inner.replace('\\"', '"').replace("\\\\", "\\")


def _resolve_text(raw: Any, loc: Dict[Tuple[str, str], str], fallback: str = "") -> str:
    if not isinstance(raw, str):
        return fallback
    s = raw.strip()
    if not s:
        return fallback
    if s.startswith("NSLOCTEXT("):
        txt = _localize_nsloctext(s, loc).strip()
        return txt or fallback
    inv = _parse_invtext(s)
    if inv is not None:
        inv_s = inv.strip()
        return inv_s or fallback
    return s


def _rgba_to_hex(color_obj: Any, default: str = "#B5BAC1") -> str:
    if not isinstance(color_obj, dict):
        return default
    try:
        r = int(color_obj.get("R", 181))
        g = int(color_obj.get("G", 186))
        b = int(color_obj.get("B", 193))
    except Exception:
        return default
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"#{r:02X}{g:02X}{b:02X}"


GENETIC_VALUE_TITLES_RU = {
    "Vitality": "Живучесть",
    "Endurance": "Выносливость",
    "Muscle": "Сила",
    "Agility": "Ловкость",
    "Toughness": "Стойкость",
    "Hardiness": "Адаптация",
    "Utility": "Практичность",
}


GENETIC_VALUE_SHORT_RU = {
    "Vitality": "ЖИВ",
    "Endurance": "ВЫН",
    "Muscle": "СИЛ",
    "Agility": "ЛОВ",
    "Toughness": "СТОЙ",
    "Hardiness": "АДАП",
    "Utility": "ПОЛЬЗ",
}


GENETIC_LINEAGE_TITLES_RU = {
    "Wild": "Дикий",
    "Brave": "Храбрый",
    "Careful": "Осторожный",
    "Timid": "Пугливый",
    "Bold": "Смелый",
    "Hardy": "Выносливый",
    "Stout": "Крепкий",
    "Ambitious": "Амбициозный",
    "Resolute": "Решительный",
    "Fierce": "Свирепый",
    "Savage": "Жестокий",
    "Alpha": "Альфа",
}


MOUNT_TYPE_ALIASES = {
    "Arctic_Moa": "Arctic_moa",
    "BlueBack": "Blueback",
    "Horse_Standard_A1": "Horse_Standard",
    "RaptorDesert": "Raptor_Desert",
    "SwampBird": "Swamp_Bird",
}


MOUNT_PHENOTYPE_ALIASES = {
    "Snow_Wolf": "Wolf",
}

UNSUPPORTED_PHENOTYPE_TYPES = {
    "Snow_Wolf",
}


SEX_TITLES_RU = {
    0: "Не задано (0)",
    1: "Самка (1)",
    2: "Самец (2)",
}


PET_TALENT_DESC_RU = {
    "Increased Maximum Health": "Увеличивает максимальное здоровье",
    "Reduced Food Usage": "Снижает расход еды",
    "Increased Slow Resistance": "Повышает сопротивление замедлению",
    "Increased Weight Capacity": "Увеличивает грузоподъёмность",
    "Can be specialized into further speed, or to become more well-rounded.": "Можно развивать в ещё большую скорость или сделать более универсальным.",
    "Increased Maximum Stamina": "Увеличивает максимальную выносливость",
    "Increased Fall Damage Reduction": "Увеличивает защиту от урона при падении",
    "Increased Health Regeneration": "Увеличивает регенерацию здоровья",
    "Reduced Animal Threat": "Снижает агрессию животных",
    "Increases Food Buff Effectiveness and allows an additional Food Buff": "Усиливает эффекты еды и открывает дополнительный пищевой бафф",
    "Increased Stamina Regeneration": "Увеличивает регенерацию выносливости",
    "Reduced Water Consumption": "Снижает расход воды",
    "Increased Food Effects Duration": "Увеличивает длительность эффектов еды",
    "Increased Inventory Slots": "Увеличивает число слотов инвентаря",
    "Increased Physical Damage Reduction": "Повышает сопротивление физическому урону",
    "Reduced Jumping Stamina Cost": "Снижает расход выносливости на прыжок",
    "Increased Movement Speed": "Увеличивает скорость передвижения",
    "Enables Moa melee attack, increases Melee Attack Damage": "Открывает ближнюю атаку Moa и увеличивает её урон",
    "Increased Maximum Health, at max rank spawns a Juvenile on Death": "Увеличивает максимальное здоровье; на максимальном ранге при смерти появляется детёныш",
    "Increased Movement Speed in Shallow Water": "Увеличивает скорость передвижения по мелководью",
    "Reduces Sprinting Stamina cost, at max rank grants Movement Speed Aura": "Снижает расход выносливости на спринт; на максимальном ранге даёт ауру скорости передвижения",
    "Increased Movement Speed in Deserts, and Heat Resistance": "Увеличивает скорость передвижения в пустыне и сопротивление жаре",
    "Greatly increased Inventory Slots, reduced Movement Speed": "Сильно увеличивает число слотов инвентаря, но снижает скорость передвижения",
    "Increased Melee Damage": "Увеличивает урон в ближнем бою",
    "Reduced Water Consumption and Increased Heat Resistance": "Снижает расход воды и повышает сопротивление жаре",
    "Increased Food Effects Duration and Cold Resistance": "Увеличивает длительность эффектов еды и сопротивление холоду",
    "Can be specialized into attack bonuses, resistances and hauling bonuses.": "Можно развивать в урон, сопротивления или перевозку грузов.",
    "Reduced Animal Threat, Can Become Unaffected by Slime Trails": "Снижает агрессию животных; может дать иммунитет к слизистым следам",
    "Increases Food Buff Effectiveness and Allows an Additional Food Buff": "Усиливает эффекты еды и открывает дополнительный пищевой бафф",
    "Enables Ubis Melee Attack, Increases Melee Attack Damage, Adds Poison Effect": "Открывает ближнюю атаку Ubis, увеличивает её урон и добавляет яд",
    "Increased Movement Speed in Swamps and Shallow Water": "Увеличивает скорость передвижения в болотах и по мелководью",
    "Increases Attack Speed": "Увеличивает скорость атаки",
    "Poison Damage Resistance, Gives Aura at Max Rank": "Повышает сопротивление яду; на максимальном ранге даёт ауру",
    "Reduced Stamina Cost when Sprinting": "Снижает расход выносливости при спринте",
    "Increased Temperature Resistance at the Cost of Higher Food and Water Consumption": "Повышает температурную устойчивость ценой большего расхода еды и воды",
    "Increases Health Regeneration in the Arctic, at max rank grants Hypothermia Resistance Aura": "Увеличивает регенерацию здоровья в Арктике; на максимальном ранге даёт ауру сопротивления гипотермии",
    "Can be specialized into defensive or arctic bonuses.": "Можно развивать в защиту или арктические бонусы.",
    "Chance to apply Bleed on hit": "Даёт шанс наложить кровотечение при ударе",
    "Increased Frost Resistance": "Повышает сопротивление морозу",
    "Reduced Animal Threat while in Arctic": "Снижает агрессию животных в Арктике",
    "Can be specialized into defensive or hauling bonuses.": "Можно развивать в защиту или перевозку грузов.",
    "Returns damage to melee attackers": "Возвращает урон атакующим в ближнем бою",
    "Increases Health Regeneration, at max rank grants a Physical Resistance Aura": "Увеличивает регенерацию здоровья; на максимальном ранге даёт ауру физической защиты",
    "Increases Food Buff Effectiveness": "Усиливает эффекты еды",
    "Increases Movement Speed but Reduces Sprint Speed": "Увеличивает обычную скорость, но снижает скорость спринта",
    "Increased Inventory Slots, at max rank grants an additional Bulky Slot": "Увеличивает число слотов инвентаря; на максимальном ранге даёт дополнительный крупногабаритный слот",
    "Increases Melee Damage and grants chance to apply Bleed on hit": "Увеличивает урон в ближнем бою и даёт шанс наложить кровотечение",
    "Increased Arctic Movement Speed and Cold Resistance": "Увеличивает скорость передвижения в Арктике и сопротивление холоду",
    "Reduces Inventory Spoil Rate": "Снижает скорость порчи предметов в инвентаре",
    "Increased Sprint Speed": "Увеличивает скорость спринта",
    "Can be specialized into hauling, combat or arctic bonuses.": "Можно развивать в перевозку грузов, бой или арктические бонусы.",
    "Chance to apply Freeze on hit, and gains Cold Resistance": "Даёт шанс наложить заморозку при ударе и повышает сопротивление холоду",
    "Attacks Mark target on hit, causing extra damage the next time they are hit": "Атаки помечают цель; следующий удар по ней наносит дополнительный урон",
    "Heals a percentage of life when killing targets": "Восстанавливает процент здоровья при убийстве цели",
    "Highlights hit targets for a short duration": "Подсвечивает поражённые цели на короткое время",
    "Can be specialized into aggressive combat roles or specific afflictions.": "Можно развивать в агрессивный бой или особые статусные эффекты.",
    "Can be specialised into desert bonuses or general bonuses.": "Можно развивать в пустынные бонусы или универсальные бонусы.",
    "Reduces Desert Water Consumption, at max rank grants Hyperthermia Resist Aura": "Снижает расход воды в пустыне; на максимальном ранге даёт ауру сопротивления гипертермии",
    "Increased Movement Speed in Deserts": "Увеличивает скорость передвижения в пустыне",
    "Reduced Water Consumption in the Desert": "Снижает расход воды в пустыне",
    "Attacks Cause Blunt Trauma": "Атаки накладывают тупую травму",
    "Can be specialized into aggressive combat roles.": "Можно развивать в агрессивный боевой стиль.",
    "Increase Coziness Bonus": "Увеличивает бонус уюта",
    "Increased Wool Growth": "Увеличивает рост шерсти",
    "Can be specialized into further wool growth or defensive bonuses.": "Можно развивать в ускоренный рост шерсти или защитные бонусы.",
    "Reduces wool growth resources required": "Снижает расход ресурсов на рост шерсти",
    "A Heavy Hauler with a Thick Coat and Sharp Tusks": "Тяжёлый грузовой питомец с густой шерстью и острыми бивнями",
    "Increases Attack Damage and Range at the Cost of Carry Capacity": "Увеличивает урон и дальность атаки ценой грузоподъёмности",
    "Adds Ability to Perform Melee Attacks and Increases Damage": "Открывает ближнюю атаку и увеличивает урон",
    "Reduced Animal Threat While in Arctic": "Снижает агрессию животных в Арктике",
    "Chance to Apply Hemorrhage on Hit": "Даёт шанс наложить сильное кровотечение при ударе",
    "Increased Frost and Cold Resistance": "Повышает сопротивление морозу и холоду",
    "Adds Ability to Perform a Stomp Attack": "Открывает атаку топотом",
    "Increased Heavy Cargo Slots": "Увеличивает число тяжёлых грузовых слотов",
    "Sacrifice Defence for Damage": "Снижает защиту, но повышает урон",
    "Massive Physical Damage Increase at the cost of Reduced Physical Damage Resistance": "Сильно повышает физический урон, но снижает сопротивление физическому урону",
}


PET_TALENT_DESC_RU_PARTS = [
    (" at max rank grants ", " на максимальном ранге даёт "),
    (" at Max Rank ", " на максимальном ранге "),
    (" at max rank ", " на максимальном ранге "),
    ("Increased ", "Увеличивает "),
    ("Reduced ", "Снижает "),
    ("Increases ", "Увеличивает "),
    ("Reduces ", "Снижает "),
    ("Chance to ", "Даёт шанс "),
    ("Adds Ability to ", "Открывает возможность "),
    ("Allows an Additional Food Buff", "дополнительный пищевой бафф"),
    ("Allows for an Additional Food Buff", "дополнительный пищевой бафф"),
    ("Food Buff Effectiveness", "силу пищевых баффов"),
    ("Food Effects Duration", "длительность эффектов еды"),
    ("Health Regeneration", "регенерацию здоровья"),
    ("Maximum Health", "максимальное здоровье"),
    ("Maximum Stamina", "максимальную выносливость"),
    ("Movement Speed", "скорость передвижения"),
    ("Melee Damage", "урон в ближнем бою"),
    ("Water Consumption", "расход воды"),
    ("Food Usage", "расход еды"),
    ("Physical Damage Reduction", "сопротивление физическому урону"),
    ("Cold Resistance", "сопротивление холоду"),
    ("Heat Resistance", "сопротивление жаре"),
]


PET_TALENT_STAT_LABELS_RU = {
    "BaseAnimalThreatModifier_+%": "к уровню агрессии животных",
    "BaseArcticAnimalThreatModifier_+%": "к уровню агрессии животных в Арктике",
    "BaseArcticHealthRegen_+%": "к регенерации здоровья в Арктике",
    "BaseArcticMovementSpeed_+%": "к скорости передвижения в Арктике",
    "BaseAttackSpeed_+%": "к скорости атаки",
    "BaseAttacksCauseBleed_%": "к шансу вызвать кровотечение",
    "BaseAttacksCauseBluntTrauma_%": "к шансу вызвать тупую травму",
    "BaseAttacksCauseBurn_%": "к шансу поджечь цель",
    "BaseAttacksCauseFreeze_%": "к шансу заморозить цель",
    "BaseAttacksCauseHemorrhage_%": "к шансу вызвать сильное кровотечение",
    "BaseAttacksCauseHighlightedTarget_%": "к шансу подсветить цель",
    "BaseAttacksCauseMarkedTarget_%": "к шансу пометить цель",
    "BaseAttacksCauseMinorHemorrhage_%": "к шансу вызвать лёгкое кровотечение",
    "BaseAttacksCausePoison_%": "к шансу отравить цель",
    "BaseAttacksCauseSlow_%": "к шансу замедлить цель",
    "BaseAttacksLeechHealth_%": "к доле похищаемого здоровья",
    "BaseChanceToReturnDamage_%": "к шансу вернуть урон атакующему",
    "BaseColdResistance_%": "к сопротивлению холоду",
    "BaseComfortLevel_+": "к уюту",
    "BaseConsumedModifierEffectiveness_+%": "к эффективности расходуемых эффектов",
    "BaseDamageReturned_%": "к возвращаемому урону",
    "BaseDesertMovementSpeed_+%": "к скорости передвижения в пустыне",
    "BaseDesertWaterConsumption_+%": "к расходу воды в пустыне",
    "BaseFallDamageResistance_%": "к сопротивлению урону от падения",
    "BaseFireDamageResistanceWhileInLava_%": "к сопротивлению огню в лаве",
    "BaseFireDamageResistance_%": "к сопротивлению огню",
    "BaseFireDamageResistance_+%": "к сопротивлению огню",
    "BaseFireDamage_+": "к урону огнём",
    "BaseFoodConsumption_+%": "к расходу еды",
    "BaseFoodModifierDuration_+%": "к длительности эффектов еды",
    "BaseFrostDamageResistance_+%": "к сопротивлению морозу",
    "BaseFrostDamage_+": "к урону морозом",
    "BaseFruitAndVegeModifierEffectiveness_+%": "к эффективности фруктовых и овощных баффов",
    "BaseHealthRegen_+%": "к регенерации здоровья",
    "BaseHeatResistance_%": "к сопротивлению жаре",
    "BaseInventorySpoilRate_+%": "к скорости порчи инвентаря",
    "BaseJumpDistance_+%": "к дальности прыжка",
    "BaseJumpingStaminaActionCost_+%": "к расходу выносливости на прыжок",
    "BaseMaximumHealth_+%": "к максимальному здоровью",
    "BaseMaximumStamina_+%": "к максимальной выносливости",
    "BaseMaximumStomachFullness_+": "к сытости",
    "BaseMeleeDamage_+%": "к урону в ближнем бою",
    "BaseMountCargoSlots_+": "к грузовым слотам",
    "BaseMountHeavyCargoSlots_+": "к тяжёлым грузовым слотам",
    "BaseMovementSpeedInShallowWater_+%": "к скорости на мелководье",
    "BaseMovementSpeedWhilePullingCart_+%": "к скорости при тяге телеги",
    "BaseMovementSpeed_+%": "к скорости передвижения",
    "BaseNPCAttackRadius_+%": "к дальности атаки",
    "BaseNPCStompAttackRadius_+": "к радиусу топота",
    "BasePhysicalDamageResistance_%": "к сопротивлению физическому урону",
    "BasePoisonDamageResistance_%": "к сопротивлению яду",
    "BasePoisonDamageResistance_+%": "к сопротивлению яду",
    "BasePoisonDamage_+": "к урону ядом",
    "BasePounceCausesSlow_%": "к шансу замедлить укусом/прыжком",
    "BaseProjectileRicochetCount_+": "к числу рикошетов снаряда",
    "BaseSkinningRewards_%": "к бонусу при свежевании",
    "BaseSlowResistance_%": "к сопротивлению замедлению",
    "BaseSprintSpeed_+%": "к скорости спринта",
    "BaseSprintingStaminaActionCost_+%": "к расходу выносливости на спринт",
    "BaseStaminaActionCost_+%": "к общему расходу выносливости",
    "BaseStaminaRegen_+%": "к восстановлению выносливости",
    "BaseStunModifierDuration_+": "к длительности оглушения",
    "BaseSwampMovementSpeed_+%": "к скорости в болоте",
    "BaseTotalHealthRestoredOnKill_%": "к восстановлению здоровья за убийство",
    "BaseTundraAnimalThreatModifier_+%": "к уровню агрессии животных в тундре",
    "BaseWaterConsumption_+%": "к расходу воды",
    "BaseWeightCapacity_+%": "к переносимому весу",
}

PET_TALENT_BOOL_EFFECTS_RU = {
    "CanTameFetch_?": "включает перенос добычи",
    "GrantedAuraCamouflage_?": "открывает ауру маскировки",
    "GrantedAuraCropGrowthYield_?": "открывает ауру роста урожая",
    "GrantedAuraHealthRegen_?": "открывает ауру регенерации здоровья",
    "GrantedAuraHyperthermiaResistance_?": "открывает ауру сопротивления перегреву",
    "GrantedAuraHypothermiaResistance_?": "открывает ауру сопротивления переохлаждению",
    "GrantedAuraInhibitSlinkerHunting_?": "открывает ауру отпугивания слинкеров",
    "GrantedAuraInteractionSpeed_?": "открывает ауру скорости взаимодействия",
    "GrantedAuraMovementSpeed_?": "открывает ауру скорости передвижения",
    "GrantedAuraOxygenConsumptionRate_?": "открывает ауру экономии кислорода",
    "GrantedAuraPhysicalDamageResistance_?": "открывает ауру физической защиты",
    "GrantedAuraPoisonResistance_?": "открывает ауру защиты от яда",
    "GrantedAuraProtectorTalent_?": "открывает ауру защитника",
    "GrantedAuraTamingSpeed_?": "открывает ауру скорости приручения",
    "GrantedAuraWaterRetention_?": "открывает ауру сохранения воды",
    "IsFriendlyToWildHorses_?": "делает дружелюбным к диким лошадям",
    "IsMountCrouchEnabled_?": "включает приседание",
    "IsMountGrazingEnabled_?": "включает выпас",
    "IsMountPrimaryAttackEnabled_?": "включает основную атаку",
    "IsMountSecondaryAttackEnabled_?": "включает вторичную атаку",
    "IsUnaffectedBySlimeTrails_?": "даёт иммунитет к слизистым следам",
    "MountCanSprintJump_?": "разрешает прыжок в спринте",
    "MountReplaceWithJuvenileOnDeath_?": "при смерти заменяет питомца детёнышем",
    "ShouldHighlightNearbyCreatures_?": "подсвечивает ближайших существ",
    "TriggerStaminaRegenOnDamaged_?": "включает восстановление выносливости при получении урона",
    "TundraMonkeyCanThrowStick_?": "разрешает бросок палки",
    "TundraMonkeyCanUseStickInCombat_?": "разрешает использовать палку в бою",
    "WillDiscoverTruffles_?": "даёт поиск трюфелей",
}


def _talent_stat_key(raw_key: Any) -> str:
    text = str(raw_key or "").strip()
    m = re.search(r'Value="([^"]+)"', text)
    if m:
        return m.group(1).strip()
    return text


def _format_talent_effect_value_ru(stat_key: str, value: Any) -> str:
    try:
        num = float(value)
    except Exception:
        return str(value)
    if stat_key.endswith("_?"):
        return ""
    if num.is_integer():
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _format_talent_effect_ru(stat_key: str, value: Any) -> str:
    key = (stat_key or "").strip()
    if not key:
        return ""
    try:
        num = float(value)
    except Exception:
        num = 0.0

    bool_text = PET_TALENT_BOOL_EFFECTS_RU.get(key)
    if bool_text:
        return bool_text if num else ""

    label = PET_TALENT_STAT_LABELS_RU.get(key)
    if label:
        sign = "+" if num > 0 else ""
        value_text = _format_talent_effect_value_ru(key, num)
        if key.endswith("_+"):
            return f"{sign}{value_text} {label}"
        return f"{sign}{value_text}% {label}"

    pretty = _prettify_identifier(key).lower() or key
    sign = "+" if num > 0 else ""
    value_text = _format_talent_effect_value_ru(key, num)
    if key.endswith("_+"):
        return f"{sign}{value_text} к {pretty}"
    if key.endswith("_?"):
        return f"{pretty}: {'вкл' if num else 'выкл'}"
    return f"{sign}{value_text}% к {pretty}"


def _format_talent_reward_lines_ru(rewards: Any) -> Tuple[str, ...]:
    if not isinstance(rewards, list):
        return ()
    lines: List[str] = []
    for idx, reward in enumerate(rewards, start=1):
        if not isinstance(reward, dict):
            continue
        parts: List[str] = []
        stats = reward.get("GrantedStats") or {}
        if isinstance(stats, dict):
            for raw_key, value in stats.items():
                stat_key = _talent_stat_key(raw_key)
                effect = _format_talent_effect_ru(stat_key, value)
                if effect:
                    parts.append(effect)
        flags = reward.get("GrantedFlags") or []
        if isinstance(flags, list):
            for flag in flags:
                row_name = _table_row_name(flag)
                if row_name and row_name != "None":
                    parts.append(f"флаг: {row_name}")
        if parts:
            lines.append(f"Ранг {idx}: " + "; ".join(parts))
    return tuple(lines)


def _translate_pet_talent_description_ru(text: str) -> str:
    src = (text or "").strip()
    if not src:
        return ""
    exact = PET_TALENT_DESC_RU.get(src)
    if exact:
        return exact
    out = src
    for eng, ru in PET_TALENT_DESC_RU_PARTS:
        out = out.replace(eng, ru)
    return out


def _mount_variation_asset_name(asset_path: str) -> str:
    raw = (asset_path or "").strip()
    if not raw:
        return ""
    tail = raw.rsplit("/", 1)[-1]
    if "." in tail:
        tail = tail.split(".", 1)[0]
    return tail


def _mount_variation_display_name(asset_name: str, index: int) -> str:
    raw = (asset_name or "").strip()
    if not raw:
        return f"Вариант {int(index)}"
    rare_match = re.search(r"Rare[_-]?Var([A-Za-z0-9]+)", raw, flags=re.IGNORECASE)
    if rare_match:
        return f"Редкий вариант {rare_match.group(1).upper()}"
    var_match = re.search(r"Var(?:iant)?[_-]?(\d+)", raw, flags=re.IGNORECASE)
    if var_match:
        return f"Вариант {var_match.group(1)}"
    if int(index) == 0:
        return "Стандартный"
    return f"Вариант {int(index)}"


def _mount_variation_rarity_label(asset_name: str, chance_percent: float) -> str:
    chance = float(max(0.0, chance_percent))
    if chance >= 25.0:
        return "обычный"
    if chance >= 5.0:
        return "необычный"
    if chance >= 1.0:
        return "редкий"
    if chance >= 0.25:
        return "эпический"
    return "легендарный"


def _mount_variation_label(pheno: GamePhenotype) -> str:
    chance_text = f"{float(pheno.chance_percent):.2f}".rstrip("0").rstrip(".")
    return f"{pheno.display_name} [{pheno.rarity_label}] {chance_text}%"


def _first_mount_variation_asset(variation: Any) -> str:
    if not isinstance(variation, dict):
        return ""
    for key in (
        "MeshMaterials",
        "GFurMaterials",
        "CarcassMeshMaterials",
        "CarcassGFurMaterials",
    ):
        materials = variation.get(key)
        if not isinstance(materials, dict):
            continue
        for mat_key in sorted(materials.keys(), key=lambda x: str(x)):
            asset = materials.get(mat_key)
            if isinstance(asset, str) and asset and asset != "None":
                return asset
    return ""


def _pretty_identifier(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    text = raw.replace("_", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"(?<=\D)(\d+)$", r" \1", text)
    return re.sub(r"\s+", " ", text).strip()


def _tracker_ref_key(row_name: str) -> str:
    rn = (row_name or "").strip()
    return f'(RowName="{rn}",DataTableName="D_PlayerTrackers")'


def _tracker_ref_row_name(raw_key: str) -> str:
    key = (raw_key or "").strip()
    m = re.search(r'RowName="([^"]+)"', key)
    if not m:
        return ""
    return m.group(1).strip()


def _normalize_task_values(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _table_row_name(ref: Any) -> str:
    if isinstance(ref, dict):
        raw = ref.get("RowName")
        return raw.strip() if isinstance(raw, str) else ""
    return ""


def _asset_object_name(asset_ref: str) -> str:
    text = str(asset_ref or "").strip()
    if not text:
        return ""
    if "'" in text:
        parts = text.split("'")
        if len(parts) >= 2:
            text = parts[-2].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    elif "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip()


def _prettify_identifier(text: str) -> str:
    raw = str(text or "").strip().strip("_")
    if not raw:
        return ""
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    raw = raw.replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    words: List[str] = []
    for token in raw.split(" "):
        if not token:
            continue
        if token.isupper() and len(token) > 2:
            words.append(token)
        elif token.lower() in {"ai", "npc", "bp", "gh"}:
            words.append(token.upper())
        else:
            words.append(token.capitalize())
    return " ".join(words)


def _default_custom_mob_name(text: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "").strip()).strip("_")
    return raw.upper() or "CUSTOM_MOB"


def _pick_custom_mount_template(
    mounts_list: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    fallback: Optional[Dict[str, Any]] = None
    best_snow: Optional[Dict[str, Any]] = None
    for mount in mounts_list:
        if not isinstance(mount, dict):
            continue
        if fallback is None:
            fallback = mount
        name = str(mount.get("MountName", "") or "").strip().lower()
        mtype = str(mount.get("MountType", "") or "").strip().lower()
        if name == "saitama":
            return mount
        if best_snow is None and mtype == "snow_wolf":
            best_snow = mount
    return best_snow or fallback


CUSTOM_BOSS_COMPAT_ACTOR_CLASS = "BP_Tamed_Wolf_Snow_C"


def _custom_choice_actor_class(
    choice: GameCustomMobChoice, template_actor_class: str = ""
) -> str:
    if not isinstance(choice, GameCustomMobChoice):
        return (template_actor_class or "").strip()
    if choice.is_boss:
        return CUSTOM_BOSS_COMPAT_ACTOR_CLASS
    actor = (choice.actor_class or "").strip()
    return actor or (template_actor_class or "").strip()


CUSTOM_MOB_PRESETS: List[CustomMobPreset] = [
    CustomMobPreset(
        title="[Моб] Паук",
        actor_class="BP_NPC_Spider_Character_C",
        ai_setup="Spider",
        mount_type="Snow_Wolf",
        level=25,
        default_name="SPIDER",
        warning="Для паука теперь подставляется и AISetup=Spider. Это всё ещё NPC и может оказаться неуправляемым.",
    ),
    CustomMobPreset(
        title="[Моб] Лавовый выродок",
        actor_class="BP_NPC_LavaBroodling_C",
        ai_setup="",
        mount_type="Snow_Wolf",
        level=25,
        default_name="LAVA",
        warning="NPC-класс. Поведение и совместимость с маунтовой системой не гарантируются.",
    ),
    CustomMobPreset(
        title="[Босс] IceBreaker",
        actor_class="BP_NPC_IceBreaker_Character_C",
        ai_setup="IceBreaker",
        mount_type="Snow_Wolf",
        level=25,
        default_name="BOSS",
        warning="Боссовый AI. Высокий шанс, что существо не будет управляемым.",
    ),
    CustomMobPreset(
        title="[AI only] IceBreaker на шаблоне питомца",
        actor_class="",
        ai_setup="IceBreaker",
        mount_type="Snow_Wolf",
        level=25,
        default_name="BOSS_AI",
        warning="Меняется только AISetupRowName. Может заспавнить босса на базе шаблона питомца.",
    ),
]


CUSTOM_SPAWN_PROFILES: List[CustomSpawnProfile] = [
    CustomSpawnProfile(
        key="codex_clone",
        title="Как у Codex",
        preserve_progress=True,
        reset_talents=False,
        copy_icon=True,
        use_actor_class=True,
        use_ai_setup=True,
    ),
    CustomSpawnProfile(
        key="clean_spawn",
        title="Чистый спавн",
        preserve_progress=False,
        reset_talents=True,
        copy_icon=True,
        use_actor_class=True,
        use_ai_setup=True,
    ),
    CustomSpawnProfile(
        key="ai_only",
        title="Только AI",
        preserve_progress=True,
        reset_talents=False,
        copy_icon=True,
        use_actor_class=False,
        use_ai_setup=True,
    ),
]


class IcarusDataPak:
    def __init__(self, path: str) -> None:
        self.path = path
        self._raw: Optional[bytes] = None
        self._blob: Optional[bytes] = None
        self._segments: Optional[Dict[str, bytes]] = None

    def _read(self) -> bytes:
        if self._raw is None:
            with open(self.path, "rb") as f:
                self._raw = f.read()
        return self._raw

    @staticmethod
    def _find_directory_index_start(raw: bytes) -> Optional[int]:
        # Directory index in this file consistently starts with: len=2, "/\0", file_count=1
        needle = b"\x02\x00\x00\x00/\x00\x01\x00\x00\x00"
        pos = raw.find(needle)
        return pos if pos != -1 else None

    def _decompressed_blob(self) -> bytes:
        if self._blob is not None:
            return self._blob

        raw = self._read()
        end = self._find_directory_index_start(raw)
        if end is None:
            end = len(raw)
        headers = (b"\x78\x9c", b"\x78\xda", b"\x78\x01", b"\x78\x5e")
        pos = 0
        out_parts: List[bytes] = []
        while pos < end:
            starts = [raw.find(sig, pos, end) for sig in headers]
            starts = [st for st in starts if st != -1]
            if not starts:
                break
            start = min(starts)
            try:
                d = zlib.decompressobj()
                chunk = d.decompress(raw[start:end])
            except zlib.error:
                pos = start + 2
                continue
            if not d.eof:
                pos = start + 2
                continue
            used = (end - start) - len(d.unused_data)
            if used <= 0:
                pos = start + 2
                continue
            pos = start + used
            if chunk:
                out_parts.append(chunk)

        self._blob = b"".join(out_parts)
        return self._blob

    def segments_by_rowstruct(self) -> Dict[str, bytes]:
        if self._segments is not None:
            return self._segments

        blob = self._decompressed_blob()
        self._segments = {}
        if not blob:
            return self._segments

        boundary = b'}{\r\n    "RowStruct": '
        starts = [0]
        i = 0
        while True:
            j = blob.find(boundary, i)
            if j == -1:
                break
            starts.append(j + 1)  # next segment starts at '{'
            i = j + len(boundary)

        for si, st in enumerate(starts):
            end = (starts[si + 1]) if si + 1 < len(starts) else len(blob)
            seg = blob[st:end]
            # RowStruct is always near top; parse it without loading JSON.
            head = seg[:256].decode("utf-8", "ignore")
            marker = '"RowStruct": "'
            k = head.find(marker)
            if k == -1:
                continue
            k += len(marker)
            k2 = head.find('"', k)
            if k2 == -1:
                continue
            rowstruct = head[k:k2]
            if rowstruct and rowstruct not in self._segments:
                self._segments[rowstruct] = seg

        return self._segments

    def load_table(self, rowstruct: str) -> Optional[Dict[str, Any]]:
        seg = self.segments_by_rowstruct().get(rowstruct)
        if not seg:
            return None
        try:
            return json.loads(seg.decode("utf-8", "ignore"))
        except Exception:
            return None


ICARUS_STEAM_APP_ID = 1149460


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _dedupe_path_strings(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for p in paths:
        key = os.path.normcase(str(p or "").strip())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(p))
    return out


def _to_host_path(raw: str) -> Path:
    s = str(raw or "").strip().strip('"')
    if not s:
        return Path(".")
    s = s.replace("\\\\", "\\")
    if os.name != "nt":
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
        if m:
            drive = m.group(1).lower()
            rest = m.group(2).replace("\\", "/")
            return Path(f"/mnt/{drive}/{rest}")
        return Path(s.replace("\\", "/"))
    return Path(s.replace("/", "\\"))


def _guess_steam_roots() -> List[Path]:
    roots: List[Path] = []

    if os.name == "nt":
        try:
            import winreg  # type: ignore

            for hive, key_path, value_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\WOW6432Node\Valve\Steam",
                    "InstallPath",
                ),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, key_path) as k:
                        v, _ = winreg.QueryValueEx(k, value_name)
                        if v:
                            roots.append(_to_host_path(str(v)))
                except Exception:
                    continue
        except Exception:
            pass

        for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.getenv(env_name)
            if base:
                roots.append(Path(base) / "Steam")
    else:
        home = Path.home()
        roots += [
            home / ".steam" / "steam",
            home / ".steam" / "root",
            home / ".local" / "share" / "Steam",
        ]

        for drive in "abcdefghijklmnopqrstuvwxyz":
            drive_root = Path(f"/mnt/{drive}")
            if not drive_root.is_dir():
                continue
            roots += [
                drive_root / "Steam",
                drive_root / "SteamLibrary",
                drive_root / "Program Files (x86)" / "Steam",
            ]

    out: List[str] = []
    for p in roots:
        if (p / "steamapps").is_dir():
            out.append(str(p))
    return [Path(p) for p in _dedupe_path_strings(out)]


def _steam_libraries(steam_root: Path) -> List[Path]:
    libs: List[Path] = [steam_root]

    vdf = None
    for c in (
        steam_root / "steamapps" / "libraryfolders.vdf",
        steam_root / "config" / "libraryfolders.vdf",
    ):
        if c.is_file():
            vdf = c
            break
    if vdf is None:
        return libs

    txt = _read_text_safe(vdf)
    if not txt:
        return libs

    for m in re.finditer(r'"path"\s*"([^"]+)"', txt):
        libs.append(_to_host_path(m.group(1)))

    for m in re.finditer(r'^\s*"\d+"\s*"([^"]+)"\s*$', txt, flags=re.MULTILINE):
        libs.append(_to_host_path(m.group(1)))

    out: List[str] = []
    for p in libs:
        if (p / "steamapps").is_dir():
            out.append(str(p))
    return [Path(p) for p in _dedupe_path_strings(out)]


def _icarus_roots_from_steam_library(lib: Path) -> List[str]:
    roots: List[str] = []
    manifest = lib / "steamapps" / f"appmanifest_{ICARUS_STEAM_APP_ID}.acf"

    if manifest.is_file():
        txt = _read_text_safe(manifest)
        m = re.search(r'"installdir"\s*"([^"]+)"', txt)
        install_dir = (m.group(1).strip() if m else "Icarus") or "Icarus"
        root = lib / "steamapps" / "common" / install_dir
        if root.is_dir():
            roots.append(str(root))

    fallback = lib / "steamapps" / "common" / "Icarus"
    if fallback.is_dir():
        roots.append(str(fallback))

    return _dedupe_path_strings(roots)


def _guess_wsl_direct_icarus_paths() -> List[str]:
    if os.name == "nt":
        return []
    out: List[str] = []
    for drive in "abcdefghijklmnopqrstuvwxyz":
        base = Path(f"/mnt/{drive}")
        if not base.is_dir():
            continue
        for rel in (
            Path("SteamLibrary/steamapps/common/Icarus"),
            Path("Steam/steamapps/common/Icarus"),
            Path("Program Files (x86)/Steam/steamapps/common/Icarus"),
        ):
            p = base / rel
            if p.is_dir():
                out.append(str(p))
    return _dedupe_path_strings(out)


class IcarusGameData:
    def __init__(self, game_root: str) -> None:
        self.game_root = game_root
        self.data_pak_path = os.path.join(
            game_root, "Icarus", "Content", "Data", "data.pak"
        )
        self.paks_dir = os.path.join(game_root, "Icarus", "Content", "Paks")

        self.loc: Dict[Tuple[str, str], str] = {}
        self.items: Dict[str, GameItem] = {}
        self.creatures: Dict[str, GameCreature] = {}
        self.custom_mob_choices: List[GameCustomMobChoice] = []
        self.mount_types: List[str] = []
        self.mount_talent_archetype: Dict[str, str] = {}
        self.mount_ai_setup: Dict[str, str] = {}
        self.talents: Dict[str, GameTalent] = {}
        self.mount_talents: Dict[str, List[str]] = {}
        self.mount_phenotypes: Dict[str, List[GamePhenotype]] = {}
        self.meta_currencies: Dict[str, GameCurrency] = {}
        self.meta_currency_order: List[str] = []
        self.player_trackers: Dict[str, GamePlayerTracker] = {}
        self.player_tracker_order: List[str] = []
        self.accolades: Dict[str, GameAccolade] = {}
        self.accolade_order: List[str] = []
        self.bestiary_entries: Dict[str, GameBestiaryEntry] = {}
        self.bestiary_order: List[str] = []
        self.terrain_names: Dict[str, str] = {}
        self.terrain_order: List[str] = []
        self.genetic_value_titles: Dict[str, str] = {}
        self.genetic_value_short: Dict[str, str] = {}
        self.genetic_value_order: List[str] = []
        self.genetic_lineage_titles: Dict[str, str] = {}

        self._pak: Optional[IcarusDataPak] = None
        self._curve_cache: Dict[str, Optional[ExperienceCurve]] = {}

    @staticmethod
    def guess_game_roots() -> List[str]:
        env = os.getenv("ICARUS_GAME_PATH")
        candidates: List[str] = []
        if env:
            candidates.append(env)

        # Fast direct fallbacks for common installations.
        candidates += [
            r"C:\SteamLibrary\steamapps\common\Icarus",
            r"C:\Program Files (x86)\Steam\steamapps\common\Icarus",
            r"D:\SteamLibrary\steamapps\common\Icarus",
            r"E:\SteamLibrary\steamapps\common\Icarus",
            r"F:\SteamLibrary\steamapps\common\Icarus",
        ]

        # WSL convenience (harmless on Windows).
        candidates += [
            "/mnt/c/SteamLibrary/steamapps/common/Icarus",
            "/mnt/c/Program Files (x86)/Steam/steamapps/common/Icarus",
            "/mnt/d/SteamLibrary/steamapps/common/Icarus",
            "/mnt/e/SteamLibrary/steamapps/common/Icarus",
            "/mnt/f/SteamLibrary/steamapps/common/Icarus",
        ]

        # Proper Steam-library discovery from installed Steam roots.
        for steam_root in _guess_steam_roots():
            for lib in _steam_libraries(steam_root):
                candidates += _icarus_roots_from_steam_library(lib)

        candidates += _guess_wsl_direct_icarus_paths()

        out: List[str] = []
        seen: Set[str] = set()
        for raw in _dedupe_path_strings(candidates):
            p = _to_host_path(raw)
            if not p.is_dir():
                continue
            key = os.path.normcase(str(p))
            if key in seen:
                continue
            seen.add(key)
            out.append(str(p))
        return out

    @classmethod
    def try_load_default(cls) -> Optional["IcarusGameData"]:
        for root in cls.guess_game_roots():
            gd = cls(root)
            if gd.load():
                return gd
        return None

    def load(self) -> bool:
        if not os.path.isfile(self.data_pak_path):
            return False

        self._pak = IcarusDataPak(self.data_pak_path)
        self._build_from_datapak()
        return True

    def get_experience_curve(self, curve_asset: str) -> Optional[ExperienceCurve]:
        key = (curve_asset or "").strip()
        if not key:
            return None
        if key in self._curve_cache:
            return self._curve_cache[key]
        curve: Optional[ExperienceCurve] = None
        if os.path.isdir(self.paks_dir):
            curve = load_experience_curve_from_game(self.paks_dir, key)
        self._curve_cache[key] = curve
        return curve

    def _build_from_datapak(self) -> None:
        assert self._pak is not None

        items_static = self._pak.load_table("/Script/Icarus.ItemStaticData") or {}
        itemable = self._pak.load_table("/Script/Icarus.ItemableData") or {}
        bestiary = self._pak.load_table("/Script/Icarus.BestiaryData") or {}
        terrains = self._pak.load_table("/Script/Icarus.IcarusTerrain") or {}
        mounts = self._pak.load_table("/Script/Icarus.IcarusMount") or {}
        ai_setup = self._pak.load_table("/Script/Icarus.AISetup") or {}
        world_boss = self._pak.load_table("/Script/Icarus.WorldBossData") or {}
        great_hunts = self._pak.load_table("/Script/Icarus.GreatHuntCreatureInfo") or {}
        talents = self._pak.load_table("/Script/Icarus.Talent") or {}
        meta_currency = self._pak.load_table("/Script/Icarus.MetaCurrency") or {}
        player_trackers = self._pak.load_table("/Script/Icarus.PlayerTracker") or {}
        accolade_data = self._pak.load_table("/Script/Icarus.AccoladeData") or {}
        genetic_values = self._pak.load_table("/Script/Icarus.GeneticValue") or {}
        genetic_lineages = self._pak.load_table("/Script/Icarus.GeneticLineage") or {}

        itemable_rows = itemable.get("Rows", [])
        itemable_by_name: Dict[str, Dict[str, Any]] = {}
        if isinstance(itemable_rows, list):
            for r in itemable_rows:
                if isinstance(r, dict) and isinstance(r.get("Name"), str):
                    itemable_by_name[r["Name"]] = r

        self.items.clear()
        rows = items_static.get("Rows", [])
        if isinstance(rows, list):
            for r in rows:
                if not isinstance(r, dict):
                    continue
                row_name = r.get("Name")
                if not isinstance(row_name, str) or not row_name:
                    continue
                itemable_row = ""
                itemable_ref = r.get("Itemable")
                if isinstance(itemable_ref, dict):
                    rn = itemable_ref.get("RowName")
                    itemable_row = rn if isinstance(rn, str) else ""
                disp = row_name
                if itemable_row and itemable_row in itemable_by_name:
                    dn = itemable_by_name[itemable_row].get("DisplayName", "")
                    if isinstance(dn, str) and dn:
                        disp = _localize_nsloctext(dn, self.loc)
                self.items[row_name] = GameItem(
                    row_name=row_name, display_name=disp, itemable_row=itemable_row
                )

        self.terrain_names.clear()
        self.terrain_order = []
        terrain_rows = terrains.get("Rows", [])
        if isinstance(terrain_rows, list):
            for r in terrain_rows:
                if not isinstance(r, dict):
                    continue
                row_name = r.get("Name")
                if not isinstance(row_name, str) or not row_name:
                    continue
                self.terrain_names[row_name] = _resolve_text(
                    r.get("TerrainName"), self.loc, fallback=row_name
                )
                self.terrain_order.append(row_name)

        self.creatures.clear()
        self.bestiary_entries = {}
        self.bestiary_order = []
        b_rows = bestiary.get("Rows", [])
        bestiary_defaults = (
            bestiary.get("Defaults", {}) if isinstance(bestiary.get("Defaults"), dict) else {}
        )
        bestiary_default_points = int(
            bestiary_defaults.get("TotalPointsRequired", 0) or 0
        )
        if isinstance(b_rows, list):
            for r in b_rows:
                if not isinstance(r, dict):
                    continue
                row_name = r.get("Name")
                if not isinstance(row_name, str) or not row_name:
                    continue
                cn = r.get("CreatureName", "")
                display = row_name
                if isinstance(cn, str) and cn:
                    display = _localize_nsloctext(cn, self.loc)
                self.creatures[row_name] = GameCreature(
                    row_name=row_name, display_name=display
                )
                map_rows: List[str] = []
                seen_maps: Set[str] = set()
                for ref in r.get("Maps", []) if isinstance(r.get("Maps"), list) else []:
                    if not isinstance(ref, dict):
                        continue
                    map_row = ref.get("RowName")
                    if (
                        isinstance(map_row, str)
                        and map_row
                        and map_row not in seen_maps
                    ):
                        seen_maps.add(map_row)
                        map_rows.append(map_row)
                biome_rows: List[str] = []
                seen_biomes: Set[str] = set()
                for ref in (
                    r.get("Biomes", []) if isinstance(r.get("Biomes"), list) else []
                ):
                    if not isinstance(ref, dict):
                        continue
                    biome_row = ref.get("RowName")
                    if (
                        isinstance(biome_row, str)
                        and biome_row
                        and biome_row not in seen_biomes
                    ):
                        seen_biomes.add(biome_row)
                        biome_rows.append(biome_row)
                total_points = int(
                    r.get("TotalPointsRequired", bestiary_default_points)
                    or bestiary_default_points
                )
                self.bestiary_entries[row_name] = GameBestiaryEntry(
                    row_name=row_name,
                    display_name=display,
                    total_points_required=total_points,
                    maps=tuple(map_rows),
                    biomes=tuple(biome_rows),
                    is_boss=bool(r.get("bIsBoss", False)),
                )
                self.bestiary_order.append(row_name)

        self.custom_mob_choices = []
        world_boss_by_ai: Dict[str, str] = {}
        wb_rows = world_boss.get("Rows", [])
        if isinstance(wb_rows, list):
            for r in wb_rows:
                if not isinstance(r, dict):
                    continue
                ai_name = _table_row_name(r.get("AISetup"))
                boss_name = r.get("Name")
                if ai_name and isinstance(boss_name, str) and boss_name.strip():
                    world_boss_by_ai[ai_name] = boss_name.strip()

        great_hunt_by_ai: Dict[str, str] = {}
        gh_rows = great_hunts.get("Rows", [])
        if isinstance(gh_rows, list):
            for r in gh_rows:
                if not isinstance(r, dict):
                    continue
                ai_name = _table_row_name(r.get("AISetup"))
                boss_name = r.get("Name")
                if ai_name and isinstance(boss_name, str) and boss_name.strip():
                    great_hunt_by_ai[ai_name] = boss_name.strip()

        ai_rows = ai_setup.get("Rows", [])
        custom_choices: List[GameCustomMobChoice] = []
        seen_ai: Set[str] = set()
        if isinstance(ai_rows, list):
            for r in ai_rows:
                if not isinstance(r, dict):
                    continue
                ai_name = r.get("Name")
                if not isinstance(ai_name, str):
                    continue
                ai_name = ai_name.strip()
                if not ai_name or ai_name in seen_ai or ai_name == "Invalid":
                    continue
                seen_ai.add(ai_name)

                actor_path = r.get("ActorClass")
                actor_class = (
                    _asset_object_name(actor_path) if isinstance(actor_path, str) else ""
                )
                if not actor_class:
                    continue

                creature_type = _table_row_name(r.get("CreatureType"))
                relation = _table_row_name(r.get("Relationships")).lower()
                boss_alias = world_boss_by_ai.get(ai_name) or great_hunt_by_ai.get(ai_name)
                base_display = ""
                if boss_alias:
                    base_display = _prettify_identifier(boss_alias)
                elif creature_type and creature_type in self.creatures:
                    base_display = self.creatures[creature_type].display_name
                if not base_display:
                    base_display = _prettify_identifier(ai_name)

                variant_display = _prettify_identifier(ai_name)
                if (
                    variant_display
                    and variant_display.casefold() != base_display.casefold()
                ):
                    display_name = f"{base_display} - {variant_display}"
                else:
                    display_name = base_display

                tags: List[str] = []
                actor_lower = actor_class.lower()
                path_lower = str(actor_path or "").lower()
                is_boss = bool(
                    ai_name in world_boss_by_ai
                    or ai_name in great_hunt_by_ai
                    or "boss" in ai_name.lower()
                    or "boss" in actor_lower
                    or "/bosses/" in path_lower
                    or actor_lower.startswith("bp_factionboss_")
                )
                is_great_hunt = ai_name in great_hunt_by_ai
                is_friendly = relation == "player"

                if is_boss:
                    tags.append("босс")
                if is_great_hunt:
                    tags.append("great hunt")
                if is_friendly:
                    tags.append("дружелюбный")

                picker_label = display_name
                if tags:
                    picker_label += " [" + ", ".join(tags) + "]"
                picker_label += f" - {ai_name}"

                custom_choices.append(
                    GameCustomMobChoice(
                        ai_setup=ai_name,
                        actor_class=actor_class,
                        display_name=display_name,
                        picker_label=picker_label,
                        default_name=_default_custom_mob_name(boss_alias or ai_name),
                        tags=tuple(tags),
                        is_boss=is_boss,
                        is_great_hunt=is_great_hunt,
                        is_friendly=is_friendly,
                    )
                )

        self.custom_mob_choices = sorted(
            custom_choices,
            key=lambda entry: (
                0 if entry.is_boss else 1,
                0 if entry.is_great_hunt else 1,
                entry.display_name.casefold(),
                entry.ai_setup.casefold(),
            ),
        )

        self.mount_types = []
        self.mount_talent_archetype = {}
        self.mount_ai_setup = {}
        self.mount_phenotypes = {}
        m_rows = mounts.get("Rows", [])
        if isinstance(m_rows, list):
            for r in m_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                self.mount_types.append(name)
                arch = ""
                ref = r.get("MountTalentArchetype")
                if isinstance(ref, dict):
                    rn = ref.get("RowName")
                    arch = rn if isinstance(rn, str) else ""
                if arch:
                    self.mount_talent_archetype[name] = arch
                ai = r.get("AISetup")
                if isinstance(ai, dict):
                    arn = ai.get("RowName")
                    if isinstance(arn, str) and arn:
                        self.mount_ai_setup[name] = arn
                variations = r.get("Variations", [])
                if isinstance(variations, list) and variations:
                    weights = [
                        max(
                            0,
                            int(v.get("Weighting", 0) or 0),
                        )
                        for v in variations
                        if isinstance(v, dict)
                    ]
                    total_weight = sum(weights)
                    phenotypes: List[GamePhenotype] = []
                    for idx, variation in enumerate(variations):
                        if not isinstance(variation, dict):
                            continue
                        weight = max(0, int(variation.get("Weighting", 0) or 0))
                        chance = (
                            (float(weight) * 100.0 / float(total_weight))
                            if total_weight > 0
                            else 0.0
                        )
                        asset_name = _mount_variation_asset_name(
                            _first_mount_variation_asset(variation)
                        )
                        phenotypes.append(
                            GamePhenotype(
                                mount_type=name,
                                stored_value=int(idx),
                                display_name=_mount_variation_display_name(
                                    asset_name, idx
                                ),
                                rarity_label=_mount_variation_rarity_label(
                                    asset_name, chance
                                ),
                                chance_percent=float(chance),
                                weighting=int(weight),
                                asset_name=asset_name,
                            )
                        )
                    if phenotypes:
                        self.mount_phenotypes[name] = phenotypes
        self.mount_types = sorted(set(self.mount_types))

        self.talents.clear()
        t_rows = talents.get("Rows", [])
        if isinstance(t_rows, list):
            for r in t_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                tree = ""
                tref = r.get("TalentTree")
                if isinstance(tref, dict):
                    tr = tref.get("RowName")
                    tree = tr if isinstance(tr, str) else ""
                disp_raw = r.get("DisplayName", "")
                desc_raw = r.get("Description", "")
                disp = (
                    _localize_nsloctext(disp_raw, self.loc)
                    if isinstance(disp_raw, str)
                    else name
                )
                desc = (
                    _localize_nsloctext(desc_raw, self.loc)
                    if isinstance(desc_raw, str)
                    else ""
                )
                rewards = r.get("Rewards", [])
                max_rank = len(rewards) if isinstance(rewards, list) and rewards else 1
                exact_rank_effects = _format_talent_reward_lines_ru(rewards)
                self.talents[name] = GameTalent(
                    row_name=name,
                    display_name=disp,
                    description=desc,
                    talent_tree=tree,
                    max_rank=max_rank,
                    exact_rank_effects=exact_rank_effects,
                )

        # Build mount talent lists (include base + mount-specific tree if present)
        base_tree = "Creature_Mount_Base"
        self.mount_talents.clear()
        for mtype, arch in self.mount_talent_archetype.items():
            trees = {arch, base_tree}
            names = [
                t.row_name for t in self.talents.values() if t.talent_tree in trees
            ]
            self.mount_talents[mtype] = sorted(set(names))

        self.meta_currencies.clear()
        self.meta_currency_order = []
        mc_rows = meta_currency.get("Rows", [])
        if isinstance(mc_rows, list):
            for r in mc_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                disp = _resolve_text(r.get("DisplayName"), self.loc, fallback=name)
                color_hex = _rgba_to_hex(r.get("Color"), default="#B5BAC1")
                deco_raw = r.get("DecoratorText")
                deco = deco_raw.strip() if isinstance(deco_raw, str) else ""
                display_on_main = bool(r.get("bDisplayOnMainScreen", False))
                self.meta_currencies[name] = GameCurrency(
                    row_name=name,
                    display_name=disp or name,
                    color_hex=color_hex,
                    decorator_text=deco,
                    display_on_main_screen=display_on_main,
                )
                self.meta_currency_order.append(name)

        self.player_trackers = {}
        self.player_tracker_order = []
        pt_rows = player_trackers.get("Rows", [])
        if isinstance(pt_rows, list):
            for r in pt_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                display = _resolve_text(r.get("DisplayName"), self.loc, fallback=name)
                tracker_category = ""
                cat_ref = r.get("TrackerCategory")
                if isinstance(cat_ref, dict):
                    cat_raw = cat_ref.get("RowName")
                    if isinstance(cat_raw, str):
                        tracker_category = cat_raw.strip()
                steam_stat_id = r.get("SteamStatId", "")
                steam_stat_id_s = (
                    steam_stat_id.strip() if isinstance(steam_stat_id, str) else ""
                )
                self.player_trackers[name] = GamePlayerTracker(
                    row_name=name,
                    display_name=display or _pretty_identifier(name) or name,
                    tracker_category=tracker_category,
                    is_task_list=name.endswith("List"),
                    steam_stat_id=steam_stat_id_s,
                )
                self.player_tracker_order.append(name)

        self.accolades = {}
        self.accolade_order = []
        acc_rows = accolade_data.get("Rows", [])
        if isinstance(acc_rows, list):
            for r in acc_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                display = _resolve_text(r.get("DisplayName"), self.loc, fallback=name)
                description = _resolve_text(r.get("Description"), self.loc, fallback="")
                category = ""
                cat_ref = r.get("Category")
                if isinstance(cat_ref, dict):
                    cat_raw = cat_ref.get("Value")
                    if isinstance(cat_raw, str):
                        category = cat_raw.strip()
                tracker_row = _table_row_name(r.get("Tracker"))
                goal_count = int(r.get("GoalCount", 0) or 0)
                impl_raw = r.get("AccoladeImpl", "")
                impl_type = (
                    str(impl_raw).strip().rsplit(".", 1)[-1]
                    if isinstance(impl_raw, str) and impl_raw.strip()
                    else ""
                )
                task_values: List[str] = []
                extra_datas = r.get("ExtraDatas", [])
                if isinstance(extra_datas, list):
                    for ref in extra_datas:
                        ref_row = _table_row_name(ref)
                        if ref_row:
                            task_values.append(ref_row)
                steam_achievement_id = r.get("SteamAchievementId", "")
                steam_achievement_id_s = (
                    steam_achievement_id.strip()
                    if isinstance(steam_achievement_id, str)
                    else ""
                )
                self.accolades[name] = GameAccolade(
                    row_name=name,
                    display_name=display or _pretty_identifier(name) or name,
                    description=description or "",
                    category=category,
                    impl_type=impl_type,
                    tracker_row=tracker_row,
                    goal_count=goal_count,
                    task_values=tuple(task_values),
                    steam_achievement_id=steam_achievement_id_s,
                )
                self.accolade_order.append(name)

        self.genetic_value_titles = {}
        self.genetic_value_short = {}
        self.genetic_value_order = []
        gv_rows = genetic_values.get("Rows", [])
        if isinstance(gv_rows, list):
            for r in gv_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                title = _resolve_text(r.get("Title"), self.loc, fallback=name)
                short = _resolve_text(r.get("Short"), self.loc, fallback="")
                if not self.loc:
                    title = GENETIC_VALUE_TITLES_RU.get(name, title or name)
                    short = GENETIC_VALUE_SHORT_RU.get(name, short or "")
                self.genetic_value_titles[name] = title or name
                self.genetic_value_short[name] = short or name
                self.genetic_value_order.append(name)

        self.genetic_lineage_titles = {}
        gl_rows = genetic_lineages.get("Rows", [])
        if isinstance(gl_rows, list):
            for r in gl_rows:
                if not isinstance(r, dict):
                    continue
                name = r.get("Name")
                if not isinstance(name, str) or not name:
                    continue
                title = _resolve_text(r.get("Title"), self.loc, fallback=name)
                if not self.loc:
                    title = GENETIC_LINEAGE_TITLES_RU.get(name, title or name)
                self.genetic_lineage_titles[name] = title or name


GAME_DATA: Optional[IcarusGameData] = None


def detect_encoding(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    return "utf-8"


def read_json(path: str) -> Tuple[Any, str]:
    enc = detect_encoding(path)
    with open(path, "r", encoding=enc) as f:
        return json.load(f), enc


def write_json(path: str, data: Any, encoding: str) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write(text)


def _decompile_safe_filename(rowstruct: str) -> str:
    s = (rowstruct or "").strip()
    if not s:
        return "unknown.json"
    s = s.replace("\\", "/").strip("/")
    s = s.replace("/Script/", "Script_")
    s = s.replace("/", "_").replace(":", "_")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    return (s or "table") + ".json"


def decompile_data_pak_tables(data_pak_path: str, out_root: str) -> str:
    if not data_pak_path or not os.path.isfile(data_pak_path):
        raise FileNotFoundError(f"data.pak не найден: {data_pak_path}")
    if not out_root:
        raise ValueError("out_root пуст")

    os.makedirs(out_root, exist_ok=True)
    tables_dir = os.path.join(out_root, "data_pak_tables")
    os.makedirs(tables_dir, exist_ok=True)

    pak = IcarusDataPak(data_pak_path)
    segs = pak.segments_by_rowstruct()

    manifest: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": {"data_pak_path": os.path.abspath(data_pak_path)},
        "tables_dir": "data_pak_tables",
        "tables": [],
    }

    for rowstruct in sorted(segs.keys()):
        table = pak.load_table(rowstruct)
        if not isinstance(table, dict):
            continue
        fn = _decompile_safe_filename(rowstruct)
        out_path = os.path.join(tables_dir, fn)
        write_json(out_path, table, "utf-8")
        manifest["tables"].append(
            {"rowstruct": rowstruct, "file": f"data_pak_tables/{fn}"}
        )

    write_json(
        os.path.join(out_root, "MANIFEST_data_pak_tables.json"), manifest, "utf-8"
    )
    return out_root


def _safe_relpath(path: str, base: str) -> Optional[str]:
    try:
        rel = os.path.relpath(path, base)
    except Exception:
        return None
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel.replace("\\", "/")


def create_backup_zip(
    base_dir: str, files: List[str], backup_dir: str, prefix: str = "backup"
) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(backup_dir, f"{prefix}_{ts}.zip")

    info: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_dir": base_dir,
        "files": [],
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            if not p or not os.path.isfile(p):
                continue
            rel = _safe_relpath(p, base_dir)
            arcname = rel if rel else f"__external__/{os.path.basename(p)}"
            zf.write(p, arcname)
            info["files"].append({"path": p, "arcname": arcname})
        zf.writestr(
            "_icarus_editor_backup.json", json.dumps(info, ensure_ascii=False, indent=2)
        )

    return zip_path


def restore_backup_zip(base_dir: str, zip_path: str) -> List[str]:
    restored: List[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for zi in zf.infolist():
            name = zi.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if name.startswith("_icarus_editor_backup"):
                continue
            if name.startswith("__external__/"):
                continue

            dst = os.path.normpath(os.path.join(base_dir, name))
            base_norm = os.path.normpath(base_dir)
            if not (dst == base_norm or dst.startswith(base_norm + os.sep)):
                continue

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with zf.open(zi, "r") as src, open(dst, "wb") as out:
                out.write(src.read())
            restored.append(dst)

    return restored


def read_backup_zip_info(zip_path: str) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            try:
                raw = zf.read("_icarus_editor_backup.json")
            except KeyError:
                return None
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return None
    except Exception:
        return None


def read_text_with_fallback(path: str) -> Tuple[str, str]:
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1251"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace"), "utf-8"


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def ini_get_section_values(
    path: str, section: str, keys: List[str]
) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {k: None for k in keys}
    try:
        text, _enc = read_text_with_fallback(path)
    except Exception:
        return out

    section_header_re = re.compile(r"^\s*\[.*\]\s*$")
    in_section = False
    key_map = {k.lower(): k for k in keys}

    for ln in text.splitlines():
        stripped = ln.strip()
        if section_header_re.match(stripped):
            in_section = stripped == section
            continue
        if not in_section:
            continue
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        kk = k.strip().lower()
        if kk in key_map:
            out[key_map[kk]] = v.strip()
    return out


def ini_ensure_section_keys(
    path: str, section: str, key_values: Dict[str, int]
) -> bool:
    text, enc = read_text_with_fallback(path)
    nl = detect_newline(text)
    lines = text.splitlines(keepends=True)

    section_header_re = re.compile(r"^\s*\[.*\]\s*$")
    key_re = re.compile(
        r"^(\s*)("
        + "|".join(re.escape(k) for k in key_values.keys())
        + r")(\s*=\s*)(.*?)(\s*)$",
        flags=re.IGNORECASE,
    )

    section_starts = [i for i, ln in enumerate(lines) if ln.strip() == section]
    changed = False

    if not section_starts:
        if lines and not (lines[-1].endswith("\n") or lines[-1].endswith("\r\n")):
            lines[-1] = lines[-1] + nl
            changed = True
        if lines and lines[-1].strip() != "":
            lines.append(nl)
            changed = True

        lines.append(section + nl)
        for k, v in key_values.items():
            lines.append(f"{k}={int(v)}{nl}")
        changed = True
    else:
        in_target = False
        for idx, ln in enumerate(lines):
            stripped = ln.strip()
            if section_header_re.match(stripped):
                in_target = stripped == section
                continue

            if not in_target:
                continue

            line_ending = ""
            if ln.endswith("\r\n"):
                line_ending = "\r\n"
            elif ln.endswith("\n"):
                line_ending = "\n"

            core = ln[: -len(line_ending)] if line_ending else ln
            m = key_re.match(core)
            if not m:
                continue

            indent, key, eq, _old_val, trailing = m.groups()
            target_key = next(
                (kk for kk in key_values.keys() if kk.lower() == key.lower()), key
            )
            new_line = (
                f"{indent}{key}{eq}{int(key_values[target_key])}{trailing}{line_ending}"
            )
            if new_line != ln:
                lines[idx] = new_line
                changed = True

        last_start = section_starts[-1]
        last_end = len(lines)
        for j in range(last_start + 1, len(lines)):
            if section_header_re.match(lines[j].strip()):
                last_end = j
                break

        present_lower: set[str] = set()
        for j in range(last_start + 1, last_end):
            core = lines[j].rstrip("\r\n")
            m = key_re.match(core)
            if m:
                present_lower.add(m.group(2).lower())

        to_add = [k for k in key_values.keys() if k.lower() not in present_lower]
        if to_add:
            insert = [f"{k}={int(key_values[k])}{nl}" for k in to_add]
            lines[last_end:last_end] = insert
            changed = True

    if changed:
        new_text = "".join(lines)
        with open(path, "w", encoding=enc, newline="") as f:
            f.write(new_text)
    return changed


def find_files(root: str) -> Dict[str, str]:
    wanted = {
        "profile.json": "Profile.json",
        "metainventory.json": "MetaInventory.json",
        "loadouts.json": "Loadouts.json",
        "mounts.json": "Mounts.json",
        "characters.json": "Characters.json",
        "accolades.json": "Accolades.json",
        "bestiarydata.json": "BestiaryData.json",
    }
    found: Dict[str, str] = {}
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            key = fn.lower()
            if key in wanted:
                canon = wanted[key]
                if canon not in found:
                    found[canon] = os.path.join(dirpath, fn)
    return found


def _guess_localappdata_dir() -> Optional[str]:
    env = os.getenv("LOCALAPPDATA")
    if env and os.path.isdir(env):
        return env

    # WSL convenience: try to locate Windows user profile on /mnt/c
    wsl_users = "/mnt/c/Users"
    candidates: List[str] = []

    for base in (os.getcwd(), os.path.abspath(__file__)):
        base_norm = base.replace("\\", "/")
        m = re.match(r"^/mnt/c/Users/([^/]+)/", base_norm)
        if m:
            candidates.append(os.path.join(wsl_users, m.group(1), "AppData", "Local"))

    try:
        if os.path.isdir(wsl_users):
            for name in os.listdir(wsl_users):
                candidates.append(os.path.join(wsl_users, name, "AppData", "Local"))
    except Exception:
        pass

    for cand in candidates:
        if os.path.isdir(os.path.join(cand, "Icarus", "Saved")):
            return cand

    home_guess = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    if os.path.isdir(os.path.join(home_guess, "Icarus", "Saved")):
        return home_guess
    return None


def _default_playerdata_base() -> Optional[str]:
    localapp = _guess_localappdata_dir()
    if not localapp:
        return None
    base = os.path.join(localapp, "Icarus", "Saved", "PlayerData")
    return base if os.path.isdir(base) else None


def _default_drago_icarus_cache_dir() -> Optional[str]:
    localapp = _guess_localappdata_dir()
    if localapp:
        return os.path.join(localapp, "Drago", "icarus")
    # Cross-platform fallback (harmless on Windows if LOCALAPPDATA exists)
    return os.path.join(os.path.expanduser("~"), ".local", "share", "Drago", "icarus")


def guess_save_folders() -> List[str]:
    candidates: List[str] = []
    base = _default_playerdata_base()
    if not base:
        return candidates

    try:
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if not os.path.isdir(p):
                continue
            ff = find_files(p)
            if all(
                k in ff for k in ("Profile.json", "MetaInventory.json", "Loadouts.json")
            ):
                candidates.append(p)
    except Exception:
        return candidates

    return candidates


def pick_best_folder(folders: List[str]) -> Optional[str]:
    best: Optional[str] = None
    best_mtime = -1.0
    for folder in folders:
        ff = find_files(folder)
        prof = ff.get("Profile.json")
        try:
            mtime = os.path.getmtime(prof) if prof else os.path.getmtime(folder)
        except Exception:
            mtime = 0.0
        if mtime > best_mtime:
            best_mtime = mtime
            best = folder
    return best


@dataclass(frozen=True)
class UnlockRow:
    flag: int
    unlock_name: str
    unlocked_by: str


UNLOCK_ROWS: list[UnlockRow] = [
    UnlockRow(flag=1, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(flag=2, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(flag=3, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(flag=4, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(flag=5, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(
        flag=6,
        unlock_name="3 Extra Character Talent Points",
        unlocked_by="Prometheus: Null Sector",
    ),
    UnlockRow(flag=7, unlock_name="HEAL Device", unlocked_by="Prometheus: Crisis"),
    UnlockRow(
        flag=8,
        unlock_name="2 Extra Character Talent Points",
        unlocked_by="Olympus: Nightfall",
    ),
    UnlockRow(
        flag=9,
        unlock_name="2 Extra Character Talent Points",
        unlocked_by="Styx: Ironclad",
    ),
    UnlockRow(flag=10, unlock_name="Animal Baits", unlocked_by="Olympus: Wet Work"),
    UnlockRow(
        flag=11,
        unlock_name="Caveworm Knife & Caveworm Spear",
        unlocked_by="Olympus: Unearthed",
    ),
    UnlockRow(
        flag=12,
        unlock_name="Caveworm Bow & Caveworm Arrow",
        unlocked_by="Styx: Augmentation",
    ),
    UnlockRow(
        flag=13,
        unlock_name="Scorpion Hedgehog & Scorpion Pincer Trap",
        unlocked_by="Olympus: Carapace",
    ),
    UnlockRow(
        flag=14,
        unlock_name="Sandworm Knife & Sandworm Spear",
        unlocked_by="Styx: Crescendo",
    ),
    UnlockRow(
        flag=15,
        unlock_name="Sandworm Bow & Sandworm Arrow",
        unlocked_by="Olympus: Dust Up",
    ),
    UnlockRow(
        flag=16,
        unlock_name="Sandworm Armor Set",
        unlocked_by="Olympus: Migrating Sands",
    ),
    UnlockRow(flag=17, unlock_name="Scorpion Armor Set", unlocked_by="Styx: Husk"),
    UnlockRow(
        flag=18,
        unlock_name="Black Wolf Knife & Black Wolf Arrow",
        unlocked_by="Styx: Lupine",
    ),
    UnlockRow(
        flag=19, unlock_name="Alien Decorations", unlocked_by="Prometheus: Fracture"
    ),
    UnlockRow(flag=20, unlock_name="Target Dummy Set", unlocked_by="Oylmpus: PotShot"),
    UnlockRow(
        flag=21, unlock_name="Prototype Drill Arrow", unlocked_by="Quarrite: Prototype"
    ),
    UnlockRow(
        flag=22,
        unlock_name="Enzymatic Mutation Ammo",
        unlocked_by="Rimetusk: Hypothesis",
    ),
    UnlockRow(
        flag=23,
        unlock_name="Laboratory Themed Decorations",
        unlocked_by="Garganutan: Breakout",
    ),
    UnlockRow(flag=24, unlock_name="ECHO Device", unlocked_by="Rimetusk: Survivers"),
    UnlockRow(
        flag=25, unlock_name="Luriform Serum", unlocked_by="Rimetusk: Prevention"
    ),
    UnlockRow(flag=26, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(
        flag=27,
        unlock_name="Banana Farming Packet",
        unlocked_by="Garganutan: Ape Escape",
    ),
    UnlockRow(
        flag=28,
        unlock_name="2 Extra Character Talent Points",
        unlocked_by="Quarrite: Earthquake",
    ),
    UnlockRow(
        flag=29,
        unlock_name="2 Extra Character Talent Points",
        unlocked_by="Rimetusk: Glaciation",
    ),
    UnlockRow(
        flag=30,
        unlock_name="2 Extra Character Talent Points",
        unlocked_by="Garganutan: Extinction",
    ),
    UnlockRow(
        flag=31, unlock_name="Carbonweave Armor", unlocked_by="Quarrite: Research"
    ),
    UnlockRow(
        flag=32, unlock_name="Carbonweave Backpack", unlocked_by="Quarrite: Weakness"
    ),
    UnlockRow(
        flag=33,
        unlock_name="Arctic Survival Armor",
        unlocked_by="Rimetusk: Destruction",
    ),
    UnlockRow(flag=34, unlock_name="Неизвестно", unlocked_by=""),
    UnlockRow(
        flag=35,
        unlock_name="Prototype Mini Thumper",
        unlocked_by="Quarrite: Repercussion",
    ),
    UnlockRow(
        flag=36,
        unlock_name="Garganutan Fishing Trap",
        unlocked_by="Garganutan: Sellout",
    ),
    UnlockRow(
        flag=37,
        unlock_name="Garganutan Grenade",
        unlocked_by="Garganutan: Strange Troop",
    ),
    UnlockRow(
        flag=38,
        unlock_name="Garganutan Frenzy Tonic",
        unlocked_by="Garganutan: Compliance",
    ),
    UnlockRow(
        flag=39,
        unlock_name="Garganutan Sonic Attractor",
        unlocked_by="Garganutan: Curiosity",
    ),
    UnlockRow(
        flag=40,
        unlock_name="Garganutan Damage Module",
        unlocked_by="Garganutan: Ethics",
    ),
    UnlockRow(
        flag=41,
        unlock_name="Garganutan Force Attachment",
        unlocked_by="Garganutan: Blood Thirst",
    ),
    UnlockRow(
        flag=42,
        unlock_name="Garganutan Armor Set",
        unlocked_by="Garganutan: Obliteration",
    ),
    UnlockRow(
        flag=43, unlock_name="Garganutan Trophies", unlocked_by="Garganutan: Extinction"
    ),
    UnlockRow(
        flag=44, unlock_name="Rimetusk Arctic Module", unlocked_by="Rimetusk: Research"
    ),
    UnlockRow(
        flag=45,
        unlock_name="Rimetusk Frost Attachment",
        unlocked_by="Rimetusk: Experiments",
    ),
    UnlockRow(
        flag=46, unlock_name="Rimetusk Snap Trap", unlocked_by="Rimetusk: Mutation"
    ),
    UnlockRow(
        flag=47, unlock_name="Rimetusk Spear", unlocked_by="Rimetusk: Supplementary"
    ),
    UnlockRow(
        flag=48, unlock_name="Rimetusk Shield", unlocked_by="Rimetusk: Propagation"
    ),
    UnlockRow(flag=49, unlock_name="Rimetusk Armor", unlocked_by="Rimetusk: Fieldwork"),
    UnlockRow(
        flag=50, unlock_name="Rimetusk Trophies", unlocked_by="Rimetusk: Glaciation"
    ),
    UnlockRow(
        flag=51, unlock_name="Quarrite Grenade", unlocked_by="Quarrite: Liberation"
    ),
    UnlockRow(
        flag=52,
        unlock_name="Quarrite Armor Attachment",
        unlocked_by="Quarrite: Elimination",
    ),
    UnlockRow(
        flag=53,
        unlock_name="Quarrite Mining Module",
        unlocked_by="Quarrite: Rebuilding",
    ),
    UnlockRow(flag=54, unlock_name="Quarrite Gun", unlocked_by="Quarrite: Liberation"),
    UnlockRow(
        flag=55, unlock_name="Quarrite Sledgehammer", unlocked_by="Quarrite: Habitat"
    ),
    UnlockRow(
        flag=56, unlock_name="Quarrite Trophies", unlocked_by="Quarrite: Earthquake"
    ),
    UnlockRow(
        flag=57, unlock_name="Lava Hunter Trophies", unlocked_by="Prometheus: Ashlands"
    ),
    UnlockRow(
        flag=58,
        unlock_name="Lava Hunter Bomber Mine",
        unlocked_by="Prometheus: Ice Sheet",
    ),
    UnlockRow(
        flag=59,
        unlock_name="Lava Hunter Heated Backpack",
        unlocked_by="Prometheus: Rescue",
    ),
    UnlockRow(
        flag=60, unlock_name="Lava Hunter Sickle", unlocked_by="Prometheus: Tempest"
    ),
    UnlockRow(
        flag=61,
        unlock_name="Lava Hunter Volcanic Module",
        unlocked_by="Prometheus: Composition",
    ),
    UnlockRow(
        flag=62,
        unlock_name="Lava Hunter Scorch Attachment",
        unlocked_by="Prometheus: Stranded",
    ),
    UnlockRow(
        flag=63, unlock_name="Hammerhead Axe", unlocked_by="Prometheus: Shadowed"
    ),
    UnlockRow(
        flag=64, unlock_name="Hammerhead Swamp Module", unlocked_by="Prometheus: Drover"
    ),
    UnlockRow(
        flag=65,
        unlock_name="Hammerhead Slime Attachment",
        unlocked_by="Prometheus: Miasmic",
    ),
    UnlockRow(
        flag=66, unlock_name="Hammerhead Grenade", unlocked_by="Prometheus: Treehut"
    ),
    UnlockRow(
        flag=67, unlock_name="Hammerhead Trophies", unlocked_by="Prometheus: Magmatic"
    ),
    UnlockRow(flag=68, unlock_name="Epoxy", unlocked_by="Prometheus: Celebrity Chef"),
    UnlockRow(
        flag=69, unlock_name="Giant Scorpion Thorn Module", unlocked_by="Styx: Genesis"
    ),
    UnlockRow(
        flag=70,
        unlock_name="Giant Scorpion Thorns Attachment",
        unlocked_by="Styx: Flatline",
    ),
    UnlockRow(flag=71, unlock_name="Scorpion Fishing Rod", unlocked_by="Styx: Oasis"),
    UnlockRow(flag=72, unlock_name="Scorpion Crossbow", unlocked_by="Styx: Gossamer"),
    UnlockRow(
        flag=73, unlock_name="Giant Scorpion Trophies", unlocked_by="Styx: Alcazar"
    ),
    UnlockRow(
        flag=74,
        unlock_name="Black Wolf Wounding Attachment",
        unlocked_by="Styx: Encroachment",
    ),
    UnlockRow(flag=75, unlock_name="Black Wolf Shield", unlocked_by="Styx: Highrise"),
    UnlockRow(
        flag=76, unlock_name="Black Wolf Armor Set", unlocked_by="Olympus: Big Shot"
    ),
    UnlockRow(
        flag=77, unlock_name="Black Wolf Claw Trap", unlocked_by="Olympus: Waterfall"
    ),
    UnlockRow(
        flag=78,
        unlock_name="Black Wolf Forest Module",
        unlocked_by="Olympus: Avalanche",
    ),
    UnlockRow(
        flag=79, unlock_name="Black Wolf Trophies", unlocked_by="Olympus: IceStorm"
    ),
    UnlockRow(flag=80, unlock_name="Sandworm Trophies", unlocked_by="Olympus: Dry Run"),
    UnlockRow(
        flag=81, unlock_name="Sandworm Building Set", unlocked_by="Olympus: Pyramid"
    ),
    UnlockRow(
        flag=82,
        unlock_name="Sandworm Building Advanced Set",
        unlocked_by="Olympus: Spirit Level",
    ),
    UnlockRow(
        flag=83, unlock_name="Sandworm Desert Module", unlocked_by="Olympus: El Camino"
    ),
    UnlockRow(
        flag=84,
        unlock_name="Sandworm Plating Attachment",
        unlocked_by="Olympus: Nightfall",
    ),
    UnlockRow(flag=85, unlock_name="Caveworm Armor", unlocked_by="Quarrite: Brillance"),
]


class SaveModel:
    def __init__(self) -> None:
        self.root: Optional[str] = None

        self.profile_path: Optional[str] = None
        self.meta_path: Optional[str] = None
        self.loadouts_path: Optional[str] = None
        self.mounts_path: Optional[str] = None
        self.characters_path: Optional[str] = None
        self.accolades_path: Optional[str] = None
        self.bestiary_path: Optional[str] = None

        self.profile_enc: str = "utf-8"
        self.meta_enc: str = "utf-8"
        self.loadouts_enc: str = "utf-8"
        self.mounts_enc: str = "utf-8"
        self.characters_enc: str = "utf-8"
        self.accolades_enc: str = "utf-8"
        self.bestiary_enc: str = "utf-8"

        self.profile: Dict[str, Any] = {}
        self.meta: Dict[str, Any] = {}
        self.loadouts: Dict[str, Any] = {}
        self.mounts: Dict[str, Any] = {}
        self.characters_container: Dict[str, Any] = {}
        self.characters_key: str = "Characters.json"
        self.characters: List[Dict[str, Any]] = []
        self.accolades: Dict[str, Any] = {}
        self.bestiary: Dict[str, Any] = {}

        # Prospects/*.json (world saves)
        self.prospect_paths: List[str] = []
        self._prospects: Dict[str, Dict[str, Any]] = {}
        self._prospects_enc: Dict[str, str] = {}

        self.unlock_rows: List[UnlockRow] = list(UNLOCK_ROWS)

        self.last_backup_path: Optional[str] = None

        self.dirty_profile = False
        self.dirty_meta = False
        self.dirty_loadouts = False
        self.dirty_mounts = False
        self.dirty_characters = False
        self.dirty_accolades = False
        self.dirty_bestiary = False
        self.dirty_prospects = False
        self.dirty_prospect_paths: Set[str] = set()

    def has_any_dirty(self) -> bool:
        return (
            self.dirty_profile
            or self.dirty_meta
            or self.dirty_loadouts
            or self.dirty_mounts
            or self.dirty_characters
            or self.dirty_accolades
            or self.dirty_bestiary
            or self.dirty_prospects
        )

    @staticmethod
    def is_main_prospect_file(fn: str) -> bool:
        f = (fn or "").lower()
        if not f.endswith(".json"):
            return False
        if ".backup" in f or ".bak" in f:
            return False
        return True

    def _scan_prospects(self) -> None:
        self.prospect_paths = []
        if not self.root:
            return
        prospects_dir = os.path.join(self.root, "Prospects")
        if not os.path.isdir(prospects_dir):
            return
        try:
            files = [
                os.path.join(prospects_dir, fn)
                for fn in os.listdir(prospects_dir)
                if self.is_main_prospect_file(fn)
            ]
        except Exception:
            files = []
        self.prospect_paths = sorted(p for p in files if os.path.isfile(p))

    def load_prospect(self, path: str) -> Tuple[Dict[str, Any], str]:
        if path in self._prospects:
            return self._prospects[path], self._prospects_enc.get(path, "utf-8")
        raw, enc = read_json(path)
        obj = raw if isinstance(raw, dict) else {}
        self._prospects[path] = obj
        self._prospects_enc[path] = enc
        return obj, enc

    def prospect_info(self, prospect_path: str) -> Dict[str, Any]:
        raw, _enc = self.load_prospect(prospect_path)
        pinfo = raw.get("ProspectInfo")
        if not isinstance(pinfo, dict):
            raw["ProspectInfo"] = pinfo = {}
        return pinfo

    def prospect_difficulty(self, prospect_path: str) -> str:
        pinfo = self.prospect_info(prospect_path)
        value = pinfo.get("Difficulty", "")
        return str(value).strip() if isinstance(value, str) else ""

    def set_prospect_difficulty(self, prospect_path: str, difficulty: str) -> bool:
        if not prospect_path:
            return False
        target = str(difficulty or "").strip()
        if not target:
            return False
        pinfo = self.prospect_info(prospect_path)
        if pinfo.get("Difficulty") == target:
            return False
        pinfo["Difficulty"] = target
        self.dirty_prospects = True
        self.dirty_prospect_paths.add(prospect_path)
        return True

    def prospect_blob_ai_counts(
        self, prospect_path: str, ai_tokens: Iterable[str]
    ) -> Dict[str, int]:
        if not prospect_path:
            return {}
        try:
            raw, _enc = self.load_prospect(prospect_path)
        except Exception:
            return {}
        return prospect_blob_ai_setup_counts(raw, ai_tokens)

    def prospect_backup_candidates(self, prospect_path: str) -> List[str]:
        if not prospect_path:
            return []
        out: List[str] = []
        seen: set[str] = set()
        p = os.path.abspath(prospect_path)
        base_dir = os.path.dirname(p)
        base_name = os.path.basename(p)
        stem = os.path.splitext(base_name)[0].lower()

        try:
            for fn in os.listdir(base_dir):
                if not fn.lower().startswith(base_name.lower() + ".backup"):
                    continue
                cand = os.path.join(base_dir, fn)
                if os.path.isfile(cand):
                    out.append(cand)
        except Exception:
            pass

        backups_dir = self.backups_dir()
        if backups_dir and os.path.isdir(backups_dir) and len(stem) >= 3:
            try:
                for fn in os.listdir(backups_dir):
                    low = fn.lower()
                    if not low.endswith(".json"):
                        continue
                    if f"_{stem}_" not in low and not low.startswith(stem + "."):
                        continue
                    cand = os.path.join(backups_dir, fn)
                    if os.path.isfile(cand):
                        out.append(cand)
            except Exception:
                pass

        ordered: List[str] = []
        for cand in sorted(out, key=lambda x: os.path.getmtime(x), reverse=True):
            key = os.path.normcase(os.path.abspath(cand))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(cand)
        return ordered

    def find_latest_clean_prospect_backup(
        self, prospect_path: str, ai_tokens: Iterable[str]
    ) -> Optional[str]:
        tokens = [str(t).strip() for t in ai_tokens if str(t).strip()]
        if not prospect_path or not tokens:
            return None
        for cand in self.prospect_backup_candidates(prospect_path):
            try:
                raw, _enc = read_json(cand)
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            counts = prospect_blob_ai_setup_counts(raw, tokens)
            if not any(int(v) > 0 for v in counts.values()):
                return cand
        return None

    def replace_prospect_from_file(self, prospect_path: str, source_path: str) -> bool:
        if not prospect_path or not source_path:
            return False
        raw, enc = read_json(source_path)
        obj = raw if isinstance(raw, dict) else {}
        self._prospects[prospect_path] = obj
        self._prospects_enc[prospect_path] = enc
        self.dirty_prospects = True
        self.dirty_prospect_paths.add(prospect_path)
        return True

    def list_world_items(self, prospect_path: str) -> List[Dict[str, Any]]:
        if not prospect_path:
            return []
        try:
            raw, _enc = self.load_prospect(prospect_path)
        except Exception:
            return []
        pinfo = raw.get("ProspectInfo") or {}
        pid = ""
        if isinstance(pinfo, dict):
            v = pinfo.get("ProspectID", "")
            if isinstance(v, (str, int)) and str(v).strip():
                pid = str(v).strip()
        if not pid:
            pid = os.path.splitext(os.path.basename(prospect_path))[0]
        try:
            uncompressed = prospect_blob_decompress(raw)
            _tag, data_start, data_end = prospect_container_manager_binarydata(
                uncompressed
            )
            binary_data = uncompressed[data_start:data_end]
            return container_manager_list_world_items(binary_data, prospect_path, pid)
        except Exception:
            return []

    def list_world_containers(self, prospect_path: str) -> List[Dict[str, Any]]:
        if not prospect_path:
            return []
        try:
            raw, _enc = self.load_prospect(prospect_path)
        except Exception:
            return []
        pinfo = raw.get("ProspectInfo") or {}
        pid = ""
        if isinstance(pinfo, dict):
            v = pinfo.get("ProspectID", "")
            if isinstance(v, (str, int)) and str(v).strip():
                pid = str(v).strip()
        if not pid:
            pid = os.path.splitext(os.path.basename(prospect_path))[0]
        try:
            uncompressed = prospect_blob_decompress(raw)
            _tag, data_start, data_end = prospect_container_manager_binarydata(
                uncompressed
            )
            binary_data = uncompressed[data_start:data_end]
            return container_manager_list_world_containers(
                binary_data, prospect_path, pid
            )
        except Exception:
            return []

    def update_prospect_container_manager_binary(
        self, prospect_path: str, new_binary_data: bytes
    ) -> None:
        if not prospect_path:
            raise ValueError("missing prospect_path")
        raw, _enc = self.load_prospect(prospect_path)
        uncompressed = prospect_blob_decompress(raw)
        tag, data_start, data_end = prospect_container_manager_binarydata(uncompressed)

        uc = bytearray(uncompressed)
        uc[data_start:data_end] = bytes(new_binary_data)

        new_count = int(len(new_binary_data))
        struct.pack_into("<i", uc, int(tag.value_offset), new_count)
        struct.pack_into("<i", uc, int(tag.size_offset), new_count + 4)

        prospect_blob_update(raw, bytes(uc))
        self.dirty_prospects = True
        self.dirty_prospect_paths.add(prospect_path)

    def export_world_item_to_stash(self, world_item: Dict[str, Any]) -> Dict[str, Any]:
        w = world_item.get("_world")
        if not isinstance(w, dict):
            raise ValueError("not a world item")
        prospect_path = w.get("prospect_path")
        if not isinstance(prospect_path, str) or not prospect_path:
            raise ValueError("missing prospect_path")

        container_index = w.get("container_index")
        slot_order = w.get("slot_order")
        slot_location = w.get("slot_location")
        if (
            not isinstance(container_index, int)
            or not isinstance(slot_order, int)
            or not isinstance(slot_location, int)
        ):
            raise ValueError("missing container/slot identifiers")

        row_name = SaveModel.item_rowname(world_item)
        if not row_name or row_name == "(неизвестно)":
            raise ValueError("missing row_name")

        raw, _enc = self.load_prospect(prospect_path)
        uncompressed = prospect_blob_decompress(raw)
        tag, data_start, data_end = prospect_container_manager_binarydata(uncompressed)
        binary_data = uncompressed[data_start:data_end]

        new_binary, extracted = container_manager_pop_world_item(
            binary_data=binary_data,
            container_index=int(container_index),
            slot_order=int(slot_order),
            slot_location=int(slot_location),
            row_name=row_name,
        )

        uc = bytearray(uncompressed)
        uc[data_start:data_end] = new_binary

        new_count = int(len(new_binary))
        struct.pack_into("<i", uc, int(tag.value_offset), new_count)
        struct.pack_into("<i", uc, int(tag.size_offset), new_count + 4)

        prospect_blob_update(raw, bytes(uc))
        self.dirty_prospects = True
        self.dirty_prospect_paths.add(prospect_path)

        meta_items = self.meta.get("Items")
        if not isinstance(meta_items, list):
            self.meta["Items"] = meta_items = []

        new_item = SaveModel.new_meta_item(str(extracted.get("row_name", row_name)))
        SaveModel.set_dyn(new_item, "ItemableStack", int(extracted.get("stack", 1)))
        dur = extracted.get("durability", 0)
        if isinstance(dur, int) and dur > 0:
            SaveModel.set_dyn(new_item, "Durability", int(dur))

        meta_items.append(new_item)
        self.dirty_meta = True
        return new_item

    def load_from_folder(self, folder: str) -> None:
        found = find_files(folder)
        missing = [
            fn
            for fn in ("Profile.json", "MetaInventory.json", "Loadouts.json")
            if fn not in found
        ]
        if missing:
            raise FileNotFoundError("Не найдены файлы: " + ", ".join(missing))

        self.root = folder
        self.profile_path = found["Profile.json"]
        self.meta_path = found["MetaInventory.json"]
        self.loadouts_path = found["Loadouts.json"]
        self.mounts_path = found.get("Mounts.json")
        self.characters_path = found.get("Characters.json")
        self.accolades_path = found.get("Accolades.json")
        self.bestiary_path = found.get("BestiaryData.json")

        self.profile, self.profile_enc = read_json(self.profile_path)
        self.meta, self.meta_enc = read_json(self.meta_path)
        self.loadouts, self.loadouts_enc = read_json(self.loadouts_path)
        if self.mounts_path:
            self.mounts, self.mounts_enc = read_json(self.mounts_path)
        else:
            self.mounts = {}
            self.mounts_enc = "utf-8"
        if self.characters_path:
            raw, enc = read_json(self.characters_path)
            self.characters_enc = enc
            self.characters_container = raw if isinstance(raw, dict) else {}
            self.characters_key = "Characters.json"
            for k in self.characters_container.keys():
                if isinstance(k, str) and k.lower() == "characters.json":
                    self.characters_key = k
                    break
            arr = self.characters_container.get(self.characters_key, [])
            parsed: List[Dict[str, Any]] = []
            if isinstance(arr, list):
                for s in arr:
                    if not isinstance(s, str):
                        continue
                    try:
                        inner = json.loads(s)
                    except Exception:
                        continue
                    if isinstance(inner, dict):
                        parsed.append(inner)
            self.characters = parsed
        else:
            self.characters_container = {}
            self.characters_key = "Characters.json"
            self.characters = []
            self.characters_enc = "utf-8"
        if self.accolades_path:
            raw, enc = read_json(self.accolades_path)
            self.accolades = raw if isinstance(raw, dict) else {}
            self.accolades_enc = enc
        else:
            self.accolades = {}
            self.accolades_enc = "utf-8"
        if self.bestiary_path:
            raw, enc = read_json(self.bestiary_path)
            self.bestiary = raw if isinstance(raw, dict) else {}
            self.bestiary_enc = enc
        else:
            self.bestiary = {}
            self.bestiary_enc = "utf-8"

        self.dirty_profile = False
        self.dirty_meta = False
        self.dirty_loadouts = False
        self.dirty_mounts = False
        self.dirty_characters = False
        self.dirty_accolades = False
        self.dirty_bestiary = False
        self.dirty_prospects = False
        self.dirty_prospect_paths = set()
        self.last_backup_path = None
        self._prospects = {}
        self._prospects_enc = {}
        self._scan_prospects()

    @staticmethod
    def new_meta_item(row_name: str) -> Dict[str, Any]:
        return {
            "ItemStaticData": {"RowName": row_name, "DataTableName": "D_ItemsStatic"},
            "ItemDynamicData": [],
            "ItemCustomStats": [],
            "CustomProperties": {
                "StaticWorldStats": [],
                "StaticWorldHeldStats": [],
                "Stats": [],
                "Alterations": [],
                "LivingItemSlots": [],
            },
            "DatabaseGUID": uuid.uuid4().hex.upper(),
            "ItemOwnerLookupId": -1,
            "RuntimeTags": {"GameplayTags": []},
        }

    def backups_dir(self) -> Optional[str]:
        if not self.root:
            return None
        return os.path.join(self.root, "IcarusEditorBackups")

    def save_all(self) -> List[str]:
        self.last_backup_path = None

        if not self.root:
            return []

        files_to_backup: List[str] = []
        if self.dirty_profile and self.profile_path:
            files_to_backup.append(self.profile_path)
        if self.dirty_meta and self.meta_path:
            files_to_backup.append(self.meta_path)
        if self.dirty_loadouts and self.loadouts_path:
            files_to_backup.append(self.loadouts_path)
        if self.dirty_mounts and self.mounts_path:
            files_to_backup.append(self.mounts_path)
        if self.dirty_characters and self.characters_path:
            files_to_backup.append(self.characters_path)
        if self.dirty_accolades and self.accolades_path:
            files_to_backup.append(self.accolades_path)
        if self.dirty_bestiary and self.bestiary_path:
            files_to_backup.append(self.bestiary_path)
        if self.dirty_prospects:
            for p in sorted(self.dirty_prospect_paths):
                if p and os.path.isfile(p):
                    files_to_backup.append(p)

        if files_to_backup:
            self.last_backup_path = create_backup_zip(
                base_dir=self.root,
                files=files_to_backup,
                backup_dir=os.path.join(self.root, "IcarusEditorBackups"),
                prefix="save",
            )

        saved: List[str] = []
        if self.dirty_profile and self.profile_path:
            write_json(self.profile_path, self.profile, self.profile_enc)
            self.dirty_profile = False
            saved.append("Profile.json")
        if self.dirty_meta and self.meta_path:
            write_json(self.meta_path, self.meta, self.meta_enc)
            self.dirty_meta = False
            saved.append("MetaInventory.json")
        if self.dirty_loadouts and self.loadouts_path:
            write_json(self.loadouts_path, self.loadouts, self.loadouts_enc)
            self.dirty_loadouts = False
            saved.append("Loadouts.json")
        if self.dirty_mounts and self.mounts_path:
            write_json(self.mounts_path, self.mounts, self.mounts_enc)
            self.dirty_mounts = False
            saved.append("Mounts.json")
        if self.dirty_characters and self.characters_path:
            if not isinstance(self.characters_container, dict):
                self.characters_container = {}
            key = self.characters_key or "Characters.json"
            self.characters_container[key] = [
                json.dumps(ch, ensure_ascii=False, indent=2)
                for ch in self.characters
                if isinstance(ch, dict)
            ]
            write_json(
                self.characters_path, self.characters_container, self.characters_enc
            )
            self.dirty_characters = False
            saved.append("Characters.json")
        if self.dirty_accolades and self.accolades_path:
            write_json(self.accolades_path, self.accolades, self.accolades_enc)
            self.dirty_accolades = False
            saved.append("Accolades.json")
        if self.dirty_bestiary and self.bestiary_path:
            write_json(self.bestiary_path, self.bestiary, self.bestiary_enc)
            self.dirty_bestiary = False
            saved.append("BestiaryData.json")
        if self.dirty_prospects:
            saved_any = False
            for p in sorted(self.dirty_prospect_paths):
                obj = self._prospects.get(p)
                enc = self._prospects_enc.get(p, "utf-8")
                if isinstance(obj, dict) and p and os.path.isfile(p):
                    write_json(p, obj, enc)
                    saved_any = True
            self.dirty_prospect_paths = set()
            self.dirty_prospects = False
            if saved_any:
                saved.append("Prospects")
        return saved

    def restore_from_backup(self, zip_path: str) -> List[str]:
        if not self.root:
            raise RuntimeError("Сейв не загружен.")
        restored = restore_backup_zip(self.root, zip_path)
        return restored

    def get_currency(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for row in self.profile.get("MetaResources", []):
            mr = row.get("MetaRow")
            cnt = row.get("Count")
            if isinstance(mr, str) and isinstance(cnt, int):
                out[mr] = cnt
        return out

    def set_currency(self, meta_row: str, value: int) -> None:
        for row in self.profile.get("MetaResources", []):
            if row.get("MetaRow") == meta_row:
                row["Count"] = int(value)
                self.dirty_profile = True
                return
        self.profile.setdefault("MetaResources", []).append(
            {"MetaRow": meta_row, "Count": int(value)}
        )
        self.dirty_profile = True

    def flags_set(self) -> set[int]:
        return set(
            int(x) for x in self.profile.get("UnlockedFlags", []) if isinstance(x, int)
        )

    def set_flag(self, flag: int, enabled: bool) -> None:
        flags = self.flags_set()
        if enabled:
            flags.add(int(flag))
        else:
            flags.discard(int(flag))
        self.profile["UnlockedFlags"] = sorted(flags)
        self.dirty_profile = True

    def _completed_accolades_list(self) -> List[Dict[str, Any]]:
        lst = self.accolades.get("CompletedAccolades")
        if not isinstance(lst, list):
            self.accolades["CompletedAccolades"] = lst = []
        return lst

    def completed_accolade_map(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for rec in self._completed_accolades_list():
            if not isinstance(rec, dict):
                continue
            acc = rec.get("Accolade")
            if not isinstance(acc, dict):
                continue
            row_name = acc.get("RowName")
            if isinstance(row_name, str) and row_name and row_name not in out:
                out[row_name] = rec
        return out

    def known_accolade_rows(self) -> List[str]:
        names: set[str] = set(self.completed_accolade_map().keys())
        if self.root:
            try:
                files = sorted(os.listdir(self.root))
            except Exception:
                files = []
            for fn in files:
                low = fn.lower()
                if not (
                    low == "accolades.json" or low.startswith("accolades.json.backup")
                ):
                    continue
                p = os.path.join(self.root, fn)
                if not os.path.isfile(p):
                    continue
                try:
                    raw, _enc = read_json(p)
                except Exception:
                    continue
                rows = raw.get("CompletedAccolades", []) if isinstance(raw, dict) else []
                if not isinstance(rows, list):
                    continue
                for rec in rows:
                    if not isinstance(rec, dict):
                        continue
                    acc = rec.get("Accolade")
                    if not isinstance(acc, dict):
                        continue
                    row_name = acc.get("RowName")
                    if isinstance(row_name, str) and row_name:
                        names.add(row_name)
        return sorted(names)

    def set_accolade_completed(
        self, row_name: str, enabled: bool, prospect_id: str = ""
    ) -> bool:
        rn = (row_name or "").strip()
        if not rn:
            return False
        lst = self._completed_accolades_list()
        changed = False
        if enabled:
            if any(
                isinstance(rec, dict)
                and isinstance(rec.get("Accolade"), dict)
                and rec["Accolade"].get("RowName") == rn
                for rec in lst
            ):
                return False
            lst.append(
                {
                    "Accolade": {"RowName": rn, "DataTableName": "D_Accolades"},
                    "TimeCompleted": datetime.now().strftime("%Y.%m.%d-%H.%M.%S"),
                    "ProspectID": str(prospect_id or ""),
                }
            )
            changed = True
        else:
            kept: List[Dict[str, Any]] = []
            for rec in lst:
                if (
                    isinstance(rec, dict)
                    and isinstance(rec.get("Accolade"), dict)
                    and rec["Accolade"].get("RowName") == rn
                ):
                    changed = True
                    continue
                kept.append(rec)
            if changed:
                self.accolades["CompletedAccolades"] = kept
        if changed:
            self.dirty_accolades = True
        return changed

    def player_trackers_map(self) -> Dict[str, int]:
        src = self.accolades.get("PlayerTrackers")
        if not isinstance(src, dict):
            return {}
        out: Dict[str, int] = {}
        for raw_key, value in src.items():
            row_name = _tracker_ref_row_name(str(raw_key))
            if not row_name or not isinstance(value, int):
                continue
            out[row_name] = int(value)
        return out

    def player_task_list_map(self) -> Dict[str, List[str]]:
        src = self.accolades.get("PlayerTaskListTrackers")
        if not isinstance(src, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for raw_key, value in src.items():
            row_name = _tracker_ref_row_name(str(raw_key))
            if not row_name or not isinstance(value, dict):
                continue
            tasks = value.get("CompletedTasks", [])
            if isinstance(tasks, list):
                out[row_name] = _normalize_task_values(tasks)
        return out

    def set_player_tracker_value(self, row_name: str, value: int) -> bool:
        rn = (row_name or "").strip()
        if not rn:
            return False
        trackers = self.accolades.setdefault("PlayerTrackers", {})
        if not isinstance(trackers, dict):
            self.accolades["PlayerTrackers"] = trackers = {}
        key = _tracker_ref_key(rn)
        new_value = int(value)
        if trackers.get(key) == new_value:
            return False
        trackers[key] = new_value
        self.dirty_accolades = True
        return True

    def set_player_task_list(self, row_name: str, values: Iterable[Any]) -> bool:
        rn = (row_name or "").strip()
        if not rn:
            return False
        trackers = self.accolades.setdefault("PlayerTaskListTrackers", {})
        if not isinstance(trackers, dict):
            self.accolades["PlayerTaskListTrackers"] = trackers = {}
        key = _tracker_ref_key(rn)
        normalized = _normalize_task_values(values)
        current = trackers.get(key)
        current_tasks: List[str] = []
        if isinstance(current, dict):
            current_tasks = _normalize_task_values(current.get("CompletedTasks", []))
        if current_tasks == normalized:
            return False
        trackers[key] = {"CompletedTasks": normalized}
        self.dirty_accolades = True
        return True

    def bestiary_points_map(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        rows = self.bestiary.get("BestiaryTracking", [])
        if not isinstance(rows, list):
            return out
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            group = rec.get("BestiaryGroup")
            if not isinstance(group, dict):
                continue
            row_name = group.get("RowName")
            points = rec.get("NumPoints")
            if isinstance(row_name, str) and row_name and isinstance(points, int):
                out[row_name] = int(points)
        return out

    def set_bestiary_points(self, row_name: str, points: int) -> bool:
        rn = (row_name or "").strip()
        if not rn:
            return False
        rows = self.bestiary.setdefault("BestiaryTracking", [])
        if not isinstance(rows, list):
            self.bestiary["BestiaryTracking"] = rows = []
        new_points = max(0, int(points))
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            group = rec.get("BestiaryGroup")
            if not isinstance(group, dict):
                continue
            if group.get("RowName") != rn:
                continue
            if int(rec.get("NumPoints", 0) or 0) == new_points:
                return False
            rec["NumPoints"] = new_points
            self.dirty_bestiary = True
            return True
        rows.append(
            {
                "BestiaryGroup": {
                    "RowName": rn,
                    "DataTableName": "D_BestiaryData",
                },
                "NumPoints": new_points,
            }
        )
        self.dirty_bestiary = True
        return True

    @staticmethod
    def item_rowname(item: Dict[str, Any]) -> str:
        rn = (item.get("ItemStaticData") or {}).get("RowName")
        return rn if isinstance(rn, str) and rn else "(неизвестно)"

    @staticmethod
    def item_pretty_name(row_name: str) -> str:
        if GAME_DATA and row_name in GAME_DATA.items:
            dn = GAME_DATA.items[row_name].display_name
            if dn:
                return dn
        pretty = (
            row_name.replace("LegendaryWeapon_", "")
            .replace("Meta_", "")
            .replace("_", " ")
        )
        return pretty

    @staticmethod
    def item_title(item: Dict[str, Any]) -> str:
        rn = SaveModel.item_rowname(item)
        return f"{SaveModel.item_pretty_name(rn)}  ({rn})"

    @staticmethod
    def _runtime_tags_list(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        rt = item.setdefault("RuntimeTags", {})
        if not isinstance(rt, dict):
            item["RuntimeTags"] = rt = {}
        tags = rt.setdefault("GameplayTags", [])
        if not isinstance(tags, list):
            rt["GameplayTags"] = tags = []
        return tags

    @staticmethod
    def has_runtime_tag(item: Dict[str, Any], tag_name: str) -> bool:
        tags = SaveModel._runtime_tags_list(item)
        for t in tags:
            if isinstance(t, dict) and t.get("TagName") == tag_name:
                return True
        return False

    @staticmethod
    def set_runtime_tag(item: Dict[str, Any], tag_name: str, enabled: bool) -> None:
        tags = SaveModel._runtime_tags_list(item)
        if enabled:
            if not SaveModel.has_runtime_tag(item, tag_name):
                tags.append({"TagName": tag_name})
            return
        rt = item.get("RuntimeTags")
        if not isinstance(rt, dict):
            return
        gt = rt.get("GameplayTags")
        if not isinstance(gt, list):
            return
        rt["GameplayTags"] = [
            t for t in gt if not (isinstance(t, dict) and t.get("TagName") == tag_name)
        ]

    @staticmethod
    def _dyn_index(item: Dict[str, Any]) -> Dict[str, int]:
        idx: Dict[str, int] = {}
        dyn = item.get("ItemDynamicData", [])
        if isinstance(dyn, list):
            for i, p in enumerate(dyn):
                if isinstance(p, dict):
                    pt = p.get("PropertyType")
                    if isinstance(pt, str) and pt not in idx:
                        idx[pt] = i
        return idx

    @staticmethod
    def get_dyn(item: Dict[str, Any], prop: str, default: int = 0) -> int:
        idx = SaveModel._dyn_index(item)
        dyn = item.get("ItemDynamicData", [])
        if prop in idx and isinstance(dyn, list):
            v = dyn[idx[prop]].get("Value")
            if isinstance(v, int):
                return v
        return default

    @staticmethod
    def set_dyn(item: Dict[str, Any], prop: str, value: int) -> None:
        dyn = item.setdefault("ItemDynamicData", [])
        if not isinstance(dyn, list):
            item["ItemDynamicData"] = dyn = []
        idx = SaveModel._dyn_index(item)
        if prop in idx:
            dyn[idx[prop]]["Value"] = int(value)
        else:
            dyn.append({"PropertyType": prop, "Value": int(value)})

    @staticmethod
    def remove_dyn(item: Dict[str, Any], prop: str) -> None:
        dyn = item.get("ItemDynamicData", [])
        if not isinstance(dyn, list):
            return
        item["ItemDynamicData"] = [
            p
            for p in dyn
            if not (isinstance(p, dict) and p.get("PropertyType") == prop)
        ]


@dataclass
class _MountBlobTag:
    name: str
    type_name: str
    size: int
    value_offset: int


@dataclass
class _MountBlobTagEx:
    name: str
    type_name: str
    size: int
    value_offset: int
    tag_offset: int
    size_offset: int


def _read_fstring(buf: bytes, offset: int) -> Tuple[str, int]:
    (ln,) = struct.unpack_from("<i", buf, offset)
    offset += 4
    if ln == 0:
        return "", offset
    if ln > 0:
        raw = buf[offset : offset + ln]
        offset += ln
        return raw[:-1].decode("utf-8", errors="replace"), offset
    ln = -ln
    raw = buf[offset : offset + ln * 2]
    offset += ln * 2
    return raw[:-2].decode("utf-16-le", errors="replace"), offset


def _ascii_fstring_bytes(text: str) -> bytes:
    b = text.encode("ascii", errors="strict") + b"\x00"
    return struct.pack("<i", len(b)) + b


_UE_NONE_FSTRING = _ascii_fstring_bytes("None")


def _fstring_bytes(text: str) -> bytes:
    b = text.encode("utf-8", errors="replace") + b"\x00"
    return struct.pack("<i", len(b)) + b


def _ue_fstring_bytes(text: str) -> bytes:
    try:
        b = text.encode("ascii", errors="strict") + b"\x00"
        return struct.pack("<i", len(b)) + b
    except Exception:
        raw = text.encode("utf-16-le", errors="replace") + b"\x00\x00"
        return struct.pack("<i", -(len(raw) // 2)) + raw


def _parse_mount_blob_tag(buf: bytes, offset: int) -> _MountBlobTag:
    name, offset = _read_fstring(buf, offset)
    if name == "None" or not name:
        raise ValueError("not a property tag")

    type_name, offset = _read_fstring(buf, offset)
    size = struct.unpack_from("<i", buf, offset)[0]
    offset += 4
    _array_index = struct.unpack_from("<i", buf, offset)[0]
    offset += 4

    if type_name == "StructProperty":
        _struct_name, offset = _read_fstring(buf, offset)
        offset += 16  # Struct GUID
    elif type_name == "EnumProperty":
        _enum_name, offset = _read_fstring(buf, offset)
    elif type_name == "ArrayProperty":
        _inner_type, offset = _read_fstring(buf, offset)
    elif type_name == "ByteProperty":
        _enum_name, offset = _read_fstring(buf, offset)
    elif type_name == "BoolProperty":
        offset += 1  # bool value stored in tag header

    has_guid = struct.unpack_from("<?", buf, offset)[0]
    offset += 1
    if has_guid:
        offset += 16

    return _MountBlobTag(name=name, type_name=type_name, size=size, value_offset=offset)


def _parse_mount_blob_tag_ex(buf: bytes, offset: int) -> _MountBlobTagEx:
    tag_offset = offset
    name, offset = _read_fstring(buf, offset)
    if name == "None" or not name:
        raise ValueError("not a property tag")

    type_name, offset = _read_fstring(buf, offset)
    size_offset = offset
    size = struct.unpack_from("<i", buf, offset)[0]
    offset += 4
    _array_index = struct.unpack_from("<i", buf, offset)[0]
    offset += 4

    if type_name == "StructProperty":
        _struct_name, offset = _read_fstring(buf, offset)
        offset += 16  # Struct GUID
    elif type_name == "EnumProperty":
        _enum_name, offset = _read_fstring(buf, offset)
    elif type_name == "ArrayProperty":
        _inner_type, offset = _read_fstring(buf, offset)
    elif type_name == "ByteProperty":
        _enum_name, offset = _read_fstring(buf, offset)
    elif type_name == "BoolProperty":
        offset += 1  # bool value stored in tag header

    has_guid = struct.unpack_from("<?", buf, offset)[0]
    offset += 1
    if has_guid:
        offset += 16

    return _MountBlobTagEx(
        name=name,
        type_name=type_name,
        size=size,
        value_offset=offset,
        tag_offset=tag_offset,
        size_offset=size_offset,
    )


def _find_mount_blob_tag(buf: bytes, prop_name: str) -> Optional[_MountBlobTag]:
    try:
        pat = _ascii_fstring_bytes(prop_name)
    except Exception:
        return None

    start = 0
    while True:
        idx = buf.find(pat, start)
        if idx < 0:
            return None
        try:
            tag = _parse_mount_blob_tag(buf, idx)
        except Exception:
            start = idx + 1
            continue
        if tag.name == prop_name:
            return tag
        start = idx + 1


def _find_mount_blob_tag_ex(
    buf: bytes, prop_name: str, type_name: Optional[str] = None
) -> Optional[_MountBlobTagEx]:
    try:
        pat = _ascii_fstring_bytes(prop_name)
    except Exception:
        return None

    start = 0
    while True:
        idx = buf.find(pat, start)
        if idx < 0:
            return None
        try:
            tag = _parse_mount_blob_tag_ex(buf, idx)
        except Exception:
            start = idx + 1
            continue
        if tag.name == prop_name and (type_name is None or tag.type_name == type_name):
            return tag
        start = idx + 1


def mount_blob_get_int(data: List[int], prop_name: str) -> Optional[int]:
    buf = bytes(data)
    tag = _find_mount_blob_tag(buf, prop_name)
    if not tag or tag.type_name != "IntProperty" or tag.size != 4:
        return None
    return struct.unpack_from("<i", buf, tag.value_offset)[0]


def mount_blob_set_int(data: List[int], prop_name: str, value: int) -> bool:
    buf = bytearray(data)
    tag = _find_mount_blob_tag(bytes(buf), prop_name)
    if not tag or tag.type_name != "IntProperty" or tag.size != 4:
        return False
    struct.pack_into("<i", buf, tag.value_offset, int(value))
    data[:] = list(buf)
    return True


def mount_blob_set_int_at_offset(
    data: List[int], value_offset: int, value: int
) -> bool:
    if value_offset < 0 or value_offset + 4 > len(data):
        return False
    buf = bytearray(data)
    struct.pack_into("<i", buf, value_offset, int(value))
    data[:] = list(buf)
    return True


def mount_blob_get_fstring(
    data: List[int], prop_name: str, type_name: Optional[str] = None
) -> Optional[str]:
    buf = bytes(data)
    tag = _find_mount_blob_tag(buf, prop_name)
    if not tag:
        return None
    if type_name is not None:
        if tag.type_name != type_name:
            return None
    else:
        if tag.type_name not in ("NameProperty", "StrProperty"):
            return None
    try:
        s, _ = _read_fstring(buf, tag.value_offset)
        return s
    except Exception:
        return None


def mount_blob_set_fstring(
    data: List[int], prop_name: str, type_name: Optional[str], text: str
) -> bool:
    buf = bytearray(data)
    tag = _find_mount_blob_tag_ex(bytes(buf), prop_name, type_name)
    if not tag:
        return False
    if type_name is None and tag.type_name not in ("NameProperty", "StrProperty"):
        return False
    old_size = int(tag.size)
    start = int(tag.value_offset)
    end = start + old_size
    if start < 0 or end < start or end > len(buf):
        return False
    new_val = _ue_fstring_bytes(text)
    buf[start:end] = new_val
    struct.pack_into("<i", buf, int(tag.size_offset), len(new_val))
    data[:] = list(buf)
    return True


def _find_mount_blob_bool_value_offset(buf: bytes, prop_name: str) -> Optional[int]:
    try:
        pat = _ascii_fstring_bytes(prop_name)
    except Exception:
        return None

    start = 0
    while True:
        idx = buf.find(pat, start)
        if idx < 0:
            return None
        try:
            off = idx
            name, off = _read_fstring(buf, off)
            if name != prop_name:
                start = idx + 1
                continue

            type_name, off = _read_fstring(buf, off)
            _size = struct.unpack_from("<i", buf, off)[0]
            off += 4
            _array_index = struct.unpack_from("<i", buf, off)[0]
            off += 4

            if type_name == "StructProperty":
                _struct_name, off = _read_fstring(buf, off)
                off += 16
            elif type_name == "EnumProperty":
                _enum_name, off = _read_fstring(buf, off)
            elif type_name == "ArrayProperty":
                _inner_type, off = _read_fstring(buf, off)
            elif type_name == "ByteProperty":
                _enum_name, off = _read_fstring(buf, off)

            bool_value_offset: Optional[int] = None
            if type_name == "BoolProperty":
                bool_value_offset = off
                off += 1

            has_guid = struct.unpack_from("<?", buf, off)[0]
            off += 1
            if has_guid:
                off += 16

            if type_name == "BoolProperty":
                return bool_value_offset
        except Exception:
            pass

        start = idx + 1


def mount_blob_get_bool(data: List[int], prop_name: str) -> Optional[bool]:
    buf = bytes(data)
    value_offset = _find_mount_blob_bool_value_offset(buf, prop_name)
    if value_offset is None or value_offset < 0 or value_offset >= len(buf):
        return None
    return bool(buf[value_offset])


def mount_blob_set_bool(data: List[int], prop_name: str, value: bool) -> bool:
    buf = bytearray(data)
    value_offset = _find_mount_blob_bool_value_offset(bytes(buf), prop_name)
    if value_offset is None or value_offset < 0 or value_offset >= len(buf):
        return False
    buf[value_offset] = 1 if bool(value) else 0
    data[:] = list(buf)
    return True


@dataclass(frozen=True)
class MountTalent:
    row_name: str
    rank: int
    rank_value_offset: int


@dataclass(frozen=True)
class MountGeneticValue:
    value_name: str
    value: int
    value_offset: int


@dataclass(frozen=True)
class MountActorIntVariable:
    variable_name: str
    value: int
    value_offset: int


def _mount_blob_parse_struct_array_entries(
    buf: bytes, array_tag: _MountBlobTagEx
) -> List[Tuple[List[_MountBlobTagEx], int]]:
    if array_tag.type_name != "ArrayProperty":
        return []
    try:
        count = struct.unpack_from("<i", buf, array_tag.value_offset)[0]
        inner = _parse_mount_blob_tag_ex(buf, array_tag.value_offset + 4)
    except Exception:
        return []
    if count <= 0 or inner.type_name != "StructProperty":
        return []
    cur = inner.value_offset
    limit = inner.value_offset + inner.size
    out: List[Tuple[List[_MountBlobTagEx], int]] = []
    for _ in range(int(count)):
        if cur >= limit:
            break
        try:
            fields, next_cur = _ue_parse_struct_fields(buf, cur, limit)
        except Exception:
            break
        out.append((fields, cur))
        if next_cur <= cur:
            break
        cur = next_cur
    return out


def mount_blob_list_genetics(data: List[int]) -> List[MountGeneticValue]:
    buf = bytes(data)
    genetics = _find_mount_blob_tag_ex(buf, "Genetics", "ArrayProperty")
    if not genetics:
        return []

    out: List[MountGeneticValue] = []
    for fields, _ in _mount_blob_parse_struct_array_entries(buf, genetics):
        name_tag = _ue_find_tag(fields, "GeneticValueName")
        value_tag = _ue_find_tag(fields, "Value")
        if not name_tag or not value_tag:
            continue
        if name_tag.type_name not in ("NameProperty", "StrProperty"):
            continue
        if value_tag.type_name != "IntProperty" or int(value_tag.size) != 4:
            continue
        try:
            value_name = _ue_read_tag_fstring(buf, name_tag)
            value = _ue_read_i32(buf, value_tag.value_offset)
        except Exception:
            continue
        if not value_name:
            continue
        out.append(
            MountGeneticValue(
                value_name=value_name,
                value=int(value),
                value_offset=int(value_tag.value_offset),
            )
        )
    return out


def mount_blob_set_genetic_value(data: List[int], value_name: str, value: int) -> bool:
    target = (value_name or "").strip()
    if not target:
        return False
    for g in mount_blob_list_genetics(data):
        if g.value_name == target:
            return mount_blob_set_int_at_offset(data, g.value_offset, int(value))
    return False


def mount_blob_list_int_variables(data: List[int]) -> List[MountActorIntVariable]:
    buf = bytes(data)
    variables = _find_mount_blob_tag_ex(buf, "IntVariables", "ArrayProperty")
    if not variables:
        return []

    out: List[MountActorIntVariable] = []
    for fields, _ in _mount_blob_parse_struct_array_entries(buf, variables):
        name_tag = _ue_find_tag(fields, "VariableName")
        value_tag = _ue_find_tag(fields, "iVariable")
        if not name_tag or not value_tag:
            continue
        if name_tag.type_name not in ("NameProperty", "StrProperty"):
            continue
        if value_tag.type_name != "IntProperty" or int(value_tag.size) != 4:
            continue
        try:
            variable_name = _ue_read_tag_fstring(buf, name_tag)
            value = _ue_read_i32(buf, value_tag.value_offset)
        except Exception:
            continue
        if not variable_name:
            continue
        out.append(
            MountActorIntVariable(
                variable_name=variable_name,
                value=int(value),
                value_offset=int(value_tag.value_offset),
            )
        )
    return out


def mount_blob_get_int_variable(data: List[int], variable_name: str) -> Optional[int]:
    target = (variable_name or "").strip()
    if not target:
        return None
    for item in mount_blob_list_int_variables(data):
        if item.variable_name == target:
            return int(item.value)
    return None


def mount_blob_set_int_variable(
    data: List[int], variable_name: str, value: int
) -> bool:
    target = (variable_name or "").strip()
    if not target:
        return False
    for item in mount_blob_list_int_variables(data):
        if item.variable_name == target:
            return mount_blob_set_int_at_offset(data, item.value_offset, int(value))
    return False


def _build_mount_talent_element(row_name: str, rank: int) -> bytes:
    row_val = _fstring_bytes(row_name)

    out = bytearray()

    out += _ascii_fstring_bytes("TalentRowName")
    out += _ascii_fstring_bytes("StrProperty")
    out += struct.pack("<i", len(row_val))
    out += struct.pack("<i", 0)  # array index
    out += struct.pack("<?", False)  # has guid
    out += row_val

    out += _ascii_fstring_bytes("TalentRank")
    out += _ascii_fstring_bytes("IntProperty")
    out += struct.pack("<i", 4)
    out += struct.pack("<i", 0)  # array index
    out += struct.pack("<?", False)  # has guid
    out += struct.pack("<i", int(rank))

    out += _ascii_fstring_bytes("None")

    return bytes(out)


def mount_blob_add_talent(data: List[int], row_name: str, rank: int) -> bool:
    row_name = row_name.strip()
    if not row_name:
        return False

    for t in mount_blob_list_talents(data):
        if t.row_name == row_name:
            return mount_blob_set_int_at_offset(data, t.rank_value_offset, int(rank))

    buf = bytearray(data)

    outer = _find_mount_blob_tag_ex(bytes(buf), "Talents", "ArrayProperty")
    if not outer:
        return False

    count_offset = outer.value_offset
    if count_offset < 0 or count_offset + 4 > len(buf):
        return False
    (count,) = struct.unpack_from("<i", buf, count_offset)

    inner_tag_off = count_offset + 4
    if inner_tag_off < 0 or inner_tag_off >= len(buf):
        return False
    try:
        inner = _parse_mount_blob_tag_ex(bytes(buf), inner_tag_off)
    except Exception:
        return False
    if inner.type_name != "StructProperty":
        return False

    insert_at = inner.value_offset + inner.size
    if insert_at < 0 or insert_at > len(buf):
        return False

    element = _build_mount_talent_element(row_name, int(rank))
    buf[insert_at:insert_at] = element
    delta = len(element)

    struct.pack_into("<i", buf, count_offset, int(count) + 1)
    struct.pack_into("<i", buf, inner.size_offset, int(inner.size) + delta)
    struct.pack_into("<i", buf, outer.size_offset, int(outer.size) + delta)

    data[:] = list(buf)
    return True


def mount_blob_add_missing_talents(data: List[int], row_names: List[str]) -> int:
    existing = {t.row_name for t in mount_blob_list_talents(data)}
    added = 0
    for rn in row_names:
        rn_s = rn.strip()
        if not rn_s or rn_s in existing:
            continue
        if mount_blob_add_talent(data, rn_s, 0):
            existing.add(rn_s)
            added += 1
    return added


def mount_blob_list_talents(data: List[int]) -> List[MountTalent]:
    buf = bytes(data)
    try:
        pat_row = _ascii_fstring_bytes("TalentRowName")
        pat_rank = _ascii_fstring_bytes("TalentRank")
    except Exception:
        return []

    out: List[MountTalent] = []
    start = 0
    while True:
        pos = buf.find(pat_row, start)
        if pos < 0:
            return out

        try:
            tag_row = _parse_mount_blob_tag(buf, pos)
        except Exception:
            start = pos + 1
            continue

        if tag_row.name != "TalentRowName" or tag_row.type_name != "StrProperty":
            start = pos + 1
            continue

        try:
            row_name, _ = _read_fstring(buf, tag_row.value_offset)
        except Exception:
            start = pos + 1
            continue

        rank_guess = tag_row.value_offset + tag_row.size
        if 0 <= rank_guess < len(buf) and buf.startswith(pat_rank, rank_guess):
            pos_rank = rank_guess
        else:
            pos_rank = buf.find(pat_rank, rank_guess, min(len(buf), rank_guess + 256))

        if pos_rank < 0:
            start = rank_guess
            continue

        try:
            tag_rank = _parse_mount_blob_tag(buf, pos_rank)
        except Exception:
            start = pos_rank + 1
            continue

        if (
            tag_rank.name != "TalentRank"
            or tag_rank.type_name != "IntProperty"
            or tag_rank.size != 4
        ):
            start = pos_rank + 1
            continue

        rank = struct.unpack_from("<i", buf, tag_rank.value_offset)[0]
        out.append(
            MountTalent(
                row_name=row_name, rank=rank, rank_value_offset=tag_rank.value_offset
            )
        )
        start = tag_rank.value_offset + tag_rank.size


def _ue_read_i32(buf: bytes, offset: int) -> int:
    return struct.unpack_from("<i", buf, offset)[0]


def _ue_read_tag_fstring(buf: bytes, tag: _MountBlobTagEx) -> str:
    s, _ = _read_fstring(buf, tag.value_offset)
    return s


def _ue_find_tag(fields: List[_MountBlobTagEx], name: str) -> Optional[_MountBlobTagEx]:
    for t in fields:
        if t.name == name:
            return t
    return None


def _ue_parse_struct_fields(
    buf: bytes, offset: int, end: int
) -> Tuple[List[_MountBlobTagEx], int]:
    fields: List[_MountBlobTagEx] = []
    while offset < end:
        if buf.startswith(_UE_NONE_FSTRING, offset):
            return fields, offset + len(_UE_NONE_FSTRING)
        t = _parse_mount_blob_tag_ex(buf, offset)
        fields.append(t)
        offset = t.value_offset + t.size
    raise ValueError("struct terminator not found")


def _ue_fstring_bytes_allow_empty(text: str) -> bytes:
    if text == "":
        return struct.pack("<i", 0)
    return _ue_fstring_bytes(text)


def _ue_build_int_property(name: str, value: int) -> bytes:
    out = bytearray()
    out += _ascii_fstring_bytes(name)
    out += _ascii_fstring_bytes("IntProperty")
    out += struct.pack("<i", 4)
    out += struct.pack("<i", 0)  # array index
    out += struct.pack("<?", False)  # has guid
    out += struct.pack("<i", int(value))
    return bytes(out)


def _ue_build_name_property(name: str, value: str) -> bytes:
    payload = _ue_fstring_bytes_allow_empty(value)
    out = bytearray()
    out += _ascii_fstring_bytes(name)
    out += _ascii_fstring_bytes("NameProperty")
    out += struct.pack("<i", len(payload))
    out += struct.pack("<i", 0)  # array index
    out += struct.pack("<?", False)  # has guid
    out += payload
    return bytes(out)


def _ue_build_str_property(name: str, value: str) -> bytes:
    payload = _ue_fstring_bytes_allow_empty(value)
    out = bytearray()
    out += _ascii_fstring_bytes(name)
    out += _ascii_fstring_bytes("StrProperty")
    out += struct.pack("<i", len(payload))
    out += struct.pack("<i", 0)  # array index
    out += struct.pack("<?", False)  # has guid
    out += payload
    return bytes(out)


def _ue_build_struct_tag(name: str, struct_name: str, value_bytes: bytes) -> bytes:
    out = bytearray()
    out += _ascii_fstring_bytes(name)
    out += _ascii_fstring_bytes("StructProperty")
    out += struct.pack("<i", len(value_bytes))
    out += struct.pack("<i", 0)  # array index
    out += _ascii_fstring_bytes(struct_name)
    out += b"\x00" * 16  # struct guid
    out += struct.pack("<?", False)  # has guid
    out += value_bytes
    return bytes(out)


def _ue_build_array_tag(name: str, inner_type: str, value_bytes: bytes) -> bytes:
    out = bytearray()
    out += _ascii_fstring_bytes(name)
    out += _ascii_fstring_bytes("ArrayProperty")
    out += struct.pack("<i", len(value_bytes))
    out += struct.pack("<i", 0)  # array index
    out += _ascii_fstring_bytes(inner_type)
    out += struct.pack("<?", False)  # has guid
    out += value_bytes
    return bytes(out)


def _ue_build_array_of_structs(
    name: str, struct_name: str, elements: List[bytes]
) -> bytes:
    inner_value = b"".join(elements)
    inner_tag = _ue_build_struct_tag(name, struct_name, inner_value)
    arr_value = struct.pack("<i", int(len(elements))) + inner_tag
    return _ue_build_array_tag(name, "StructProperty", arr_value)


def _ue_build_dynamic_element(index: int, value: int) -> bytes:
    return (
        _ue_build_int_property("Index", int(index))
        + _ue_build_int_property("Value", int(value))
        + _UE_NONE_FSTRING
    )


def _ue_build_world_slot_bytes(
    row_name: str, slot_location: int, stack: int, durability: int
) -> bytes:
    dyn_elems: List[bytes] = []
    if int(stack) <= 0:
        stack = 1
    dyn_elems.append(_ue_build_dynamic_element(7, int(stack)))
    if int(durability) > 0:
        dyn_elems.append(_ue_build_dynamic_element(6, int(durability)))

    out = bytearray()
    out += _ue_build_int_property("Location", int(slot_location))
    out += _ue_build_name_property("ItemStaticData", row_name)
    out += _ue_build_str_property("ItemGuid", "")
    out += _ue_build_int_property("ItemOwnerLookupId", -1)
    out += _ue_build_array_of_structs(
        "DynamicData", "InventorySlotDynamicData", dyn_elems
    )
    out += _ue_build_array_of_structs("AdditionalStats", "InventorySlotStatData", [])
    out += _ue_build_array_of_structs("Alterations", "InventorySlotAlterationData", [])
    out += _ue_build_array_of_structs("LivingItemSlots", "LivingItemSlotSaveData", [])
    out += _UE_NONE_FSTRING
    return bytes(out)


def _ue_set_bytes_int_property(blob: bytes, prop_name: str, value: int) -> bytes:
    tag = _find_mount_blob_tag_ex(blob, prop_name, "IntProperty")
    if not tag or tag.size != 4:
        raise KeyError(f"{prop_name} IntProperty not found")
    buf = bytearray(blob)
    struct.pack_into("<i", buf, int(tag.value_offset), int(value))
    return bytes(buf)


def _ue_set_bytes_fstring_property(
    blob: bytes, prop_name: str, type_name: str, text: str
) -> bytes:
    tag = _find_mount_blob_tag_ex(blob, prop_name, type_name)
    if not tag:
        raise KeyError(f"{prop_name} {type_name} not found")
    start = int(tag.value_offset)
    end = start + int(tag.size)
    if start < 0 or end < start or end > len(blob):
        raise ValueError("invalid string tag range")
    payload = _ue_fstring_bytes_allow_empty(text)
    buf = bytearray(blob)
    buf[start:end] = payload
    struct.pack_into("<i", buf, int(tag.size_offset), len(payload))
    return bytes(buf)


def _ue_parse_slot_fields(slot_bytes: bytes) -> List[_MountBlobTagEx]:
    fields, _pos = _ue_parse_struct_fields(slot_bytes, 0, len(slot_bytes))
    return fields


def _ue_clone_world_slot_bytes(
    template_slot_bytes: bytes,
    slot_location: int,
    *,
    row_name: Optional[str] = None,
    stack: Optional[int] = None,
    durability: Optional[int] = None,
) -> bytes:
    if (
        not isinstance(template_slot_bytes, (bytes, bytearray))
        or not template_slot_bytes
    ):
        raise ValueError("template slot bytes missing")

    blob = bytes(template_slot_bytes)
    blob = _ue_set_bytes_int_property(blob, "Location", int(slot_location))
    if isinstance(row_name, str) and row_name.strip():
        blob = _ue_set_bytes_fstring_property(
            blob, "ItemStaticData", "NameProperty", row_name.strip()
        )

    if stack is not None:
        if int(stack) <= 0:
            stack = 1
        fields = _ue_parse_slot_fields(blob)
        dyn = _container_manager_dynamic_entries(blob, fields)
        stack_entry = next(
            (d for d in dyn if int(d.get("index", -999)) == 7 and "value_offset" in d),
            None,
        )
        if not isinstance(stack_entry, dict):
            raise KeyError("stack dynamic entry not found")
        buf = bytearray(blob)
        struct.pack_into("<i", buf, int(stack_entry["value_offset"]), int(stack))
        blob = bytes(buf)

    if durability is not None:
        if int(durability) < 0:
            durability = 0
        fields = _ue_parse_slot_fields(blob)
        dyn = _container_manager_dynamic_entries(blob, fields)
        dur_entry = next(
            (d for d in dyn if int(d.get("index", -999)) == 6 and "value_offset" in d),
            None,
        )
        if isinstance(dur_entry, dict):
            buf = bytearray(blob)
            struct.pack_into("<i", buf, int(dur_entry["value_offset"]), int(durability))
            blob = bytes(buf)
        elif int(durability) > 0:
            raise KeyError("durability dynamic entry not found")

    return blob


def _container_manager_dynamic_pairs(
    buf: bytes, slot_fields: List[_MountBlobTagEx]
) -> List[Tuple[int, int]]:
    dyn = _ue_find_tag(slot_fields, "DynamicData")
    if not dyn or dyn.type_name != "ArrayProperty":
        return []
    count = _ue_read_i32(buf, dyn.value_offset)
    if count <= 0:
        return []
    inner = _parse_mount_blob_tag_ex(buf, dyn.value_offset + 4)
    if inner.type_name != "StructProperty":
        return []
    pos = inner.value_offset
    end = inner.value_offset + inner.size
    out: List[Tuple[int, int]] = []
    for _ in range(int(count)):
        flds, pos = _ue_parse_struct_fields(buf, pos, end)
        it_idx = _ue_find_tag(flds, "Index")
        it_val = _ue_find_tag(flds, "Value")
        if not it_idx or not it_val:
            continue
        out.append(
            (
                _ue_read_i32(buf, it_idx.value_offset),
                _ue_read_i32(buf, it_val.value_offset),
            )
        )
    return out


def _container_manager_dyn_get(
    pairs: List[Tuple[int, int]], idx: int, default: int = 0
) -> int:
    for i, v in pairs:
        if int(i) == int(idx):
            return int(v)
    return int(default)


def _container_manager_dynamic_entries(
    buf: bytes, slot_fields: List[_MountBlobTagEx]
) -> List[Dict[str, int]]:
    dyn = _ue_find_tag(slot_fields, "DynamicData")
    if not dyn or dyn.type_name != "ArrayProperty":
        return []
    count = _ue_read_i32(buf, dyn.value_offset)
    if count <= 0:
        return []
    inner = _parse_mount_blob_tag_ex(buf, dyn.value_offset + 4)
    if inner.type_name != "StructProperty":
        return []
    pos = inner.value_offset
    end = inner.value_offset + inner.size

    out: List[Dict[str, int]] = []
    for _ in range(int(count)):
        flds, pos = _ue_parse_struct_fields(buf, pos, end)
        it_idx = _ue_find_tag(flds, "Index")
        it_val = _ue_find_tag(flds, "Value")
        if not it_idx or not it_val:
            continue
        out.append(
            {
                "index": int(_ue_read_i32(buf, it_idx.value_offset)),
                "value": int(_ue_read_i32(buf, it_val.value_offset)),
                "value_offset": int(it_val.value_offset),
            }
        )
    return out


def container_manager_list_world_containers(
    binary_data: bytes, prospect_path: str, prospect_id: str
) -> List[Dict[str, Any]]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventoryContainers", "ArrayProperty")
    if not outer:
        return []

    container_count = _ue_read_i32(buf, outer.value_offset)
    if container_count <= 0:
        return []

    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        return []

    pos = inner.value_offset
    end = inner.value_offset + inner.size

    containers: List[Dict[str, Any]] = []
    for _ci in range(int(container_count)):
        try:
            cont_fields, pos = _ue_parse_struct_fields(buf, pos, end)
        except Exception:
            break

        t_idx = _ue_find_tag(cont_fields, "InventoryIndex")
        t_info = _ue_find_tag(cont_fields, "InventoryInfo")
        t_save = _ue_find_tag(cont_fields, "InventorySaveData")
        if not t_idx or not t_info or not t_save:
            continue

        inv_index = _ue_read_i32(buf, t_idx.value_offset)
        try:
            inv_info = _ue_read_tag_fstring(buf, t_info)
        except Exception:
            inv_info = ""

        slots_out: List[Dict[str, Any]] = []
        if t_save.type_name == "StructProperty":
            inv_save_start = t_save.value_offset
            inv_save_end = inv_save_start + int(t_save.size)
            try:
                save_fields, _ = _ue_parse_struct_fields(
                    buf, inv_save_start, inv_save_end
                )
            except Exception:
                save_fields = []

            slots = _ue_find_tag(save_fields, "Slots")
            if slots and slots.type_name == "ArrayProperty":
                slot_count = _ue_read_i32(buf, slots.value_offset)
                if slot_count > 0:
                    try:
                        slots_inner = _parse_mount_blob_tag_ex(
                            buf, slots.value_offset + 4
                        )
                    except Exception:
                        slots_inner = None
                    if slots_inner and slots_inner.type_name == "StructProperty":
                        sp = slots_inner.value_offset
                        se = slots_inner.value_offset + int(slots_inner.size)
                        for slot_order in range(int(slot_count)):
                            try:
                                slot_fields, sp_next = _ue_parse_struct_fields(
                                    buf, sp, se
                                )
                            except Exception:
                                break
                            loc_tag = _ue_find_tag(slot_fields, "Location")
                            row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
                            if not loc_tag or not row_tag:
                                sp = sp_next
                                continue
                            if (
                                loc_tag.type_name != "IntProperty"
                                or row_tag.type_name != "NameProperty"
                            ):
                                sp = sp_next
                                continue

                            slot_location = _ue_read_i32(buf, loc_tag.value_offset)
                            try:
                                row_name = _ue_read_tag_fstring(buf, row_tag)
                            except Exception:
                                row_name = ""

                            dyn = _container_manager_dynamic_entries(buf, slot_fields)
                            dyn_offsets = {
                                int(d["index"]): int(d["value_offset"])
                                for d in dyn
                                if isinstance(d, dict)
                            }
                            stack = 1
                            durability = 0
                            for d in dyn:
                                if int(d.get("index", -999)) == 7:
                                    stack = int(d.get("value", 1))
                                if int(d.get("index", -999)) == 6:
                                    durability = int(d.get("value", 0))
                            if stack <= 0:
                                stack = 1
                            if durability < 0:
                                durability = 0

                            slots_out.append(
                                {
                                    "row_name": row_name,
                                    "slot_location": int(slot_location),
                                    "slot_order": int(slot_order),
                                    "stack": int(stack),
                                    "durability": int(durability),
                                    "dyn_offsets": dyn_offsets,
                                    "_world": {
                                        "prospect_path": prospect_path,
                                        "prospect_id": prospect_id,
                                        "container_index": int(inv_index),
                                        "inventory_info": inv_info,
                                        "slot_order": int(slot_order),
                                        "slot_location": int(slot_location),
                                    },
                                }
                            )

                            sp = sp_next

        containers.append(
            {
                "container_index": int(inv_index),
                "inventory_info": inv_info,
                "slots": slots_out,
                "prospect_path": prospect_path,
                "prospect_id": prospect_id,
            }
        )

    return containers


def _container_manager_find_slot_context(
    binary_data: bytes,
    container_index: int,
    slot_location: int,
    row_name: str,
) -> Dict[str, Any]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventoryContainers", "ArrayProperty")
    if not outer:
        raise KeyError("SavedInventoryContainers not found")
    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        raise KeyError("SavedInventoryContainers inner header not found")

    container_count = _ue_read_i32(buf, outer.value_offset)
    pos = inner.value_offset
    end = inner.value_offset + int(inner.size)

    for _ci in range(int(container_count)):
        cont_fields, pos = _ue_parse_struct_fields(buf, pos, end)
        t_idx = _ue_find_tag(cont_fields, "InventoryIndex")
        t_save = _ue_find_tag(cont_fields, "InventorySaveData")
        if not t_idx or not t_save:
            continue
        inv_index = _ue_read_i32(buf, t_idx.value_offset)
        if int(inv_index) != int(container_index):
            continue

        if t_save.type_name != "StructProperty":
            raise KeyError("InventorySaveData not a struct")

        inv_save_size_offset = int(t_save.size_offset)
        inv_save_size = int(t_save.size)
        inv_save_start = int(t_save.value_offset)
        inv_save_end = inv_save_start + inv_save_size

        save_fields, _ = _ue_parse_struct_fields(buf, inv_save_start, inv_save_end)
        slots = _ue_find_tag(save_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            raise KeyError("Slots not found")

        slots_count_offset = int(slots.value_offset)
        slots_count = _ue_read_i32(buf, slots_count_offset)
        slots_outer_size_offset = int(slots.size_offset)
        slots_outer_size = int(slots.size)

        slots_inner = _parse_mount_blob_tag_ex(buf, slots.value_offset + 4)
        if slots_inner.type_name != "StructProperty":
            raise KeyError("Slots inner header not found")
        slots_inner_size_offset = int(slots_inner.size_offset)
        slots_inner_size = int(slots_inner.size)

        sp = int(slots_inner.value_offset)
        se = sp + slots_inner_size

        for _si in range(int(slots_count)):
            slot_start = sp
            slot_fields, sp_next = _ue_parse_struct_fields(buf, sp, se)
            loc_tag = _ue_find_tag(slot_fields, "Location")
            row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
            if not loc_tag or not row_tag:
                sp = sp_next
                continue
            if (
                loc_tag.type_name != "IntProperty"
                or row_tag.type_name != "NameProperty"
            ):
                sp = sp_next
                continue

            loc = _ue_read_i32(buf, loc_tag.value_offset)
            try:
                rn = _ue_read_tag_fstring(buf, row_tag)
            except Exception:
                rn = ""

            if int(loc) == int(slot_location) and rn == row_name:
                return {
                    "outer": outer,
                    "inner": inner,
                    "inv_save_size_offset": inv_save_size_offset,
                    "inv_save_size": inv_save_size,
                    "slots_count_offset": slots_count_offset,
                    "slots_count": int(slots_count),
                    "slots_outer_size_offset": slots_outer_size_offset,
                    "slots_outer_size": slots_outer_size,
                    "slots_inner_size_offset": slots_inner_size_offset,
                    "slots_inner_size": slots_inner_size,
                    "slot_start": int(slot_start),
                    "slot_end": int(sp_next),
                    "slot_fields": slot_fields,
                }

            sp = sp_next

        raise KeyError("slot not found")

    raise KeyError("container not found")


def container_manager_remove_world_slot(
    binary_data: bytes, container_index: int, slot_location: int, row_name: str
) -> bytes:
    ctx = _container_manager_find_slot_context(
        binary_data, container_index, slot_location, row_name
    )
    buf = bytearray(binary_data)
    remove_start = int(ctx["slot_start"])
    remove_end = int(ctx["slot_end"])
    delta = int(remove_end - remove_start)
    if delta <= 0:
        raise ValueError("invalid slot range")
    del buf[remove_start:remove_end]

    struct.pack_into(
        "<i", buf, int(ctx["slots_count_offset"]), int(ctx["slots_count"]) - 1
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_inner_size_offset"]),
        int(ctx["slots_inner_size"]) - delta,
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_outer_size_offset"]),
        int(ctx["slots_outer_size"]) - delta,
    )
    struct.pack_into(
        "<i", buf, int(ctx["inv_save_size_offset"]), int(ctx["inv_save_size"]) - delta
    )
    struct.pack_into(
        "<i", buf, int(ctx["inner"].size_offset), int(ctx["inner"].size) - delta
    )
    struct.pack_into(
        "<i", buf, int(ctx["outer"].size_offset), int(ctx["outer"].size) - delta
    )

    return bytes(buf)


def container_manager_add_world_slot(
    binary_data: bytes,
    container_index: int,
    row_name: str,
    slot_location: int,
    stack: int,
    durability: int,
) -> bytes:
    buf = bytearray(binary_data)

    outer = _find_mount_blob_tag_ex(
        bytes(buf), "SavedInventoryContainers", "ArrayProperty"
    )
    if not outer:
        raise KeyError("SavedInventoryContainers not found")
    inner = _parse_mount_blob_tag_ex(bytes(buf), outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        raise KeyError("SavedInventoryContainers inner header not found")

    container_count = _ue_read_i32(bytes(buf), outer.value_offset)
    pos = inner.value_offset
    end = inner.value_offset + int(inner.size)

    for _ci in range(int(container_count)):
        cont_fields, pos = _ue_parse_struct_fields(bytes(buf), pos, end)
        t_idx = _ue_find_tag(cont_fields, "InventoryIndex")
        t_save = _ue_find_tag(cont_fields, "InventorySaveData")
        if not t_idx or not t_save:
            continue
        inv_index = _ue_read_i32(bytes(buf), t_idx.value_offset)
        if int(inv_index) != int(container_index):
            continue
        if t_save.type_name != "StructProperty":
            raise KeyError("InventorySaveData not a struct")

        inv_save_size_offset = int(t_save.size_offset)
        inv_save_size = int(t_save.size)
        inv_save_start = int(t_save.value_offset)
        inv_save_end = inv_save_start + inv_save_size
        save_fields, _ = _ue_parse_struct_fields(
            bytes(buf), inv_save_start, inv_save_end
        )

        slots = _ue_find_tag(save_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            raise KeyError("Slots not found")

        slots_count_offset = int(slots.value_offset)
        slots_count = _ue_read_i32(bytes(buf), slots_count_offset)
        slots_outer_size_offset = int(slots.size_offset)
        slots_outer_size = int(slots.size)

        slots_inner = _parse_mount_blob_tag_ex(bytes(buf), slots.value_offset + 4)
        if slots_inner.type_name != "StructProperty":
            raise KeyError("Slots inner header not found")
        slots_inner_size_offset = int(slots_inner.size_offset)
        slots_inner_size = int(slots_inner.size)

        insert_at = int(slots_inner.value_offset + slots_inner.size)
        element = _ue_build_world_slot_bytes(
            row_name=row_name,
            slot_location=int(slot_location),
            stack=int(stack),
            durability=int(durability),
        )
        buf[insert_at:insert_at] = element
        delta = len(element)

        struct.pack_into("<i", buf, slots_count_offset, int(slots_count) + 1)
        struct.pack_into(
            "<i", buf, slots_inner_size_offset, int(slots_inner_size) + delta
        )
        struct.pack_into(
            "<i", buf, slots_outer_size_offset, int(slots_outer_size) + delta
        )
        struct.pack_into("<i", buf, inv_save_size_offset, int(inv_save_size) + delta)
        struct.pack_into("<i", buf, int(inner.size_offset), int(inner.size) + delta)
        struct.pack_into("<i", buf, int(outer.size_offset), int(outer.size) + delta)

        return bytes(buf)

    raise KeyError("container not found")


def container_manager_replace_world_slot(
    binary_data: bytes,
    container_index: int,
    slot_location: int,
    old_row_name: str,
    new_row_name: str,
    stack: int,
    durability: int,
) -> bytes:
    # delete old slot, then add new slot at same location (appends; order may change)
    buf = container_manager_remove_world_slot(
        binary_data, container_index, slot_location, old_row_name
    )
    return container_manager_add_world_slot(
        buf, container_index, new_row_name, slot_location, stack, durability
    )


def container_manager_set_world_slot_stack(
    binary_data: bytes,
    container_index: int,
    slot_location: int,
    row_name: str,
    stack: int,
) -> bytes:
    if int(stack) <= 0:
        stack = 1
    ctx = _container_manager_find_slot_context(
        binary_data, container_index, slot_location, row_name
    )
    slot_fields: List[_MountBlobTagEx] = ctx["slot_fields"]
    dyn = _container_manager_dynamic_entries(binary_data, slot_fields)
    for d in dyn:
        if int(d.get("index", -999)) == 7 and "value_offset" in d:
            buf = bytearray(binary_data)
            struct.pack_into("<i", buf, int(d["value_offset"]), int(stack))
            return bytes(buf)

    # No stack entry: replace slot with a minimal fresh representation.
    return container_manager_replace_world_slot(
        binary_data=binary_data,
        container_index=container_index,
        slot_location=slot_location,
        old_row_name=row_name,
        new_row_name=row_name,
        stack=int(stack),
        durability=0,
    )


def container_manager_set_world_slot_durability(
    binary_data: bytes,
    container_index: int,
    slot_location: int,
    row_name: str,
    durability: int,
) -> bytes:
    if int(durability) < 0:
        durability = 0
    ctx = _container_manager_find_slot_context(
        binary_data, container_index, slot_location, row_name
    )
    slot_fields: List[_MountBlobTagEx] = ctx["slot_fields"]
    dyn = _container_manager_dynamic_entries(binary_data, slot_fields)
    for d in dyn:
        if int(d.get("index", -999)) == 6 and "value_offset" in d:
            buf = bytearray(binary_data)
            struct.pack_into("<i", buf, int(d["value_offset"]), int(durability))
            return bytes(buf)

    # No durability entry: replace slot with a minimal fresh representation.
    # Preserve stack if present.
    stack = 1
    for d in dyn:
        if int(d.get("index", -999)) == 7:
            stack = int(d.get("value", 1))
    return container_manager_replace_world_slot(
        binary_data=binary_data,
        container_index=container_index,
        slot_location=slot_location,
        old_row_name=row_name,
        new_row_name=row_name,
        stack=int(stack),
        durability=int(durability),
    )


def saved_inventories_list(binary_data: bytes) -> List[Dict[str, Any]]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventories", "ArrayProperty")
    if not outer:
        return []

    inv_count = _ue_read_i32(buf, outer.value_offset)
    if inv_count <= 0:
        return []

    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        return []

    pos = inner.value_offset
    end = inner.value_offset + inner.size

    inventories: List[Dict[str, Any]] = []
    for _ii in range(int(inv_count)):
        try:
            inv_fields, pos_next = _ue_parse_struct_fields(buf, pos, end)
        except Exception:
            break

        id_tag = _ue_find_tag(inv_fields, "InventoryID")
        inv_id = (
            _ue_read_i32(buf, id_tag.value_offset)
            if id_tag and id_tag.type_name == "IntProperty"
            else None
        )

        slots_out: List[Dict[str, Any]] = []
        slots = _ue_find_tag(inv_fields, "Slots")
        if slots and slots.type_name == "ArrayProperty":
            slot_count = _ue_read_i32(buf, slots.value_offset)
            if slot_count > 0:
                try:
                    slots_inner = _parse_mount_blob_tag_ex(buf, slots.value_offset + 4)
                except Exception:
                    slots_inner = None
                if slots_inner and slots_inner.type_name == "StructProperty":
                    sp = slots_inner.value_offset
                    se = slots_inner.value_offset + int(slots_inner.size)
                    for slot_order in range(int(slot_count)):
                        try:
                            slot_fields, sp_next = _ue_parse_struct_fields(buf, sp, se)
                        except Exception:
                            break
                        loc_tag = _ue_find_tag(slot_fields, "Location")
                        row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
                        if not loc_tag or not row_tag:
                            sp = sp_next
                            continue
                        if (
                            loc_tag.type_name != "IntProperty"
                            or row_tag.type_name != "NameProperty"
                        ):
                            sp = sp_next
                            continue

                        slot_location = _ue_read_i32(buf, loc_tag.value_offset)
                        try:
                            row_name = _ue_read_tag_fstring(buf, row_tag)
                        except Exception:
                            row_name = ""

                        dyn = _container_manager_dynamic_entries(buf, slot_fields)
                        dyn_offsets = {
                            int(d["index"]): int(d["value_offset"])
                            for d in dyn
                            if isinstance(d, dict)
                        }
                        stack = 1
                        durability = 0
                        for d in dyn:
                            if int(d.get("index", -999)) == 7:
                                stack = int(d.get("value", 1))
                            if int(d.get("index", -999)) == 6:
                                durability = int(d.get("value", 0))
                        if stack <= 0:
                            stack = 1
                        if durability < 0:
                            durability = 0

                        slots_out.append(
                            {
                                "row_name": row_name,
                                "slot_location": int(slot_location),
                                "slot_order": int(slot_order),
                                "stack": int(stack),
                                "durability": int(durability),
                                "dyn_offsets": dyn_offsets,
                            }
                        )
                        sp = sp_next

        if isinstance(inv_id, int):
            inventories.append({"inventory_id": int(inv_id), "slots": slots_out})

        pos = pos_next

    return inventories


def _saved_inventories_find_slot_context(
    binary_data: bytes, inventory_id: int, slot_location: int, row_name: str
) -> Dict[str, Any]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventories", "ArrayProperty")
    if not outer:
        raise KeyError("SavedInventories not found")
    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        raise KeyError("SavedInventories inner header not found")

    inv_count = _ue_read_i32(buf, outer.value_offset)
    pos = inner.value_offset
    end = inner.value_offset + int(inner.size)

    for _ii in range(int(inv_count)):
        inv_fields, pos_next = _ue_parse_struct_fields(buf, pos, end)
        pos = pos_next
        id_tag = _ue_find_tag(inv_fields, "InventoryID")
        if not id_tag or id_tag.type_name != "IntProperty":
            continue
        inv_id = _ue_read_i32(buf, id_tag.value_offset)
        if int(inv_id) != int(inventory_id):
            continue

        slots = _ue_find_tag(inv_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            raise KeyError("Slots not found")

        slots_count_offset = int(slots.value_offset)
        slots_count = _ue_read_i32(buf, slots_count_offset)
        slots_outer_size_offset = int(slots.size_offset)
        slots_outer_size = int(slots.size)

        slots_inner = _parse_mount_blob_tag_ex(buf, slots.value_offset + 4)
        if slots_inner.type_name != "StructProperty":
            raise KeyError("Slots inner header not found")
        slots_inner_size_offset = int(slots_inner.size_offset)
        slots_inner_size = int(slots_inner.size)

        sp = int(slots_inner.value_offset)
        se = sp + slots_inner_size

        for _si in range(int(slots_count)):
            slot_start = sp
            slot_fields, sp_next = _ue_parse_struct_fields(buf, sp, se)
            loc_tag = _ue_find_tag(slot_fields, "Location")
            row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
            if not loc_tag or not row_tag:
                sp = sp_next
                continue
            if (
                loc_tag.type_name != "IntProperty"
                or row_tag.type_name != "NameProperty"
            ):
                sp = sp_next
                continue

            loc = _ue_read_i32(buf, loc_tag.value_offset)
            try:
                rn = _ue_read_tag_fstring(buf, row_tag)
            except Exception:
                rn = ""

            if int(loc) == int(slot_location) and rn == row_name:
                return {
                    "outer": outer,
                    "inner": inner,
                    "slots_count_offset": slots_count_offset,
                    "slots_count": int(slots_count),
                    "slots_outer_size_offset": slots_outer_size_offset,
                    "slots_outer_size": slots_outer_size,
                    "slots_inner_size_offset": slots_inner_size_offset,
                    "slots_inner_size": slots_inner_size,
                    "slot_start": int(slot_start),
                    "slot_end": int(sp_next),
                    "slot_fields": slot_fields,
                }

            sp = sp_next

        raise KeyError("slot not found")

    raise KeyError("inventory not found")


def _saved_inventories_find_inventory_context(
    binary_data: bytes, inventory_id: int
) -> Dict[str, Any]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventories", "ArrayProperty")
    if not outer:
        raise KeyError("SavedInventories not found")
    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        raise KeyError("SavedInventories inner header not found")

    inv_count = _ue_read_i32(buf, outer.value_offset)
    pos = inner.value_offset
    end = inner.value_offset + int(inner.size)

    for _ii in range(int(inv_count)):
        inv_fields, pos_next = _ue_parse_struct_fields(buf, pos, end)
        pos = pos_next
        id_tag = _ue_find_tag(inv_fields, "InventoryID")
        if not id_tag or id_tag.type_name != "IntProperty":
            continue
        inv_id = _ue_read_i32(buf, id_tag.value_offset)
        if int(inv_id) != int(inventory_id):
            continue

        slots = _ue_find_tag(inv_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            raise KeyError("Slots not found")

        slots_count_offset = int(slots.value_offset)
        slots_count = _ue_read_i32(buf, slots_count_offset)
        slots_outer_size_offset = int(slots.size_offset)
        slots_outer_size = int(slots.size)

        slots_inner = _parse_mount_blob_tag_ex(buf, slots.value_offset + 4)
        if slots_inner.type_name != "StructProperty":
            raise KeyError("Slots inner header not found")
        slots_inner_size_offset = int(slots_inner.size_offset)
        slots_inner_size = int(slots_inner.size)

        insert_at = int(slots_inner.value_offset + slots_inner.size)
        return {
            "outer": outer,
            "inner": inner,
            "slots_count_offset": slots_count_offset,
            "slots_count": int(slots_count),
            "slots_outer_size_offset": slots_outer_size_offset,
            "slots_outer_size": slots_outer_size,
            "slots_inner_size_offset": slots_inner_size_offset,
            "slots_inner_size": slots_inner_size,
            "insert_at": insert_at,
        }

    raise KeyError("inventory not found")


def saved_inventories_remove_slot(
    binary_data: bytes, inventory_id: int, slot_location: int, row_name: str
) -> bytes:
    ctx = _saved_inventories_find_slot_context(
        binary_data, inventory_id, slot_location, row_name
    )
    buf = bytearray(binary_data)
    remove_start = int(ctx["slot_start"])
    remove_end = int(ctx["slot_end"])
    delta = int(remove_end - remove_start)
    if delta <= 0:
        raise ValueError("invalid slot range")
    del buf[remove_start:remove_end]

    struct.pack_into(
        "<i", buf, int(ctx["slots_count_offset"]), int(ctx["slots_count"]) - 1
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_inner_size_offset"]),
        int(ctx["slots_inner_size"]) - delta,
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_outer_size_offset"]),
        int(ctx["slots_outer_size"]) - delta,
    )
    struct.pack_into(
        "<i", buf, int(ctx["inner"].size_offset), int(ctx["inner"].size) - delta
    )
    struct.pack_into(
        "<i", buf, int(ctx["outer"].size_offset), int(ctx["outer"].size) - delta
    )

    return bytes(buf)


def saved_inventories_extract_slot_bytes(
    binary_data: bytes, inventory_id: int, slot_location: int, row_name: str
) -> bytes:
    ctx = _saved_inventories_find_slot_context(
        binary_data, inventory_id, slot_location, row_name
    )
    start = int(ctx["slot_start"])
    end = int(ctx["slot_end"])
    if start < 0 or end <= start or end > len(binary_data):
        raise ValueError("invalid slot range")
    return bytes(binary_data[start:end])


def saved_inventories_insert_slot_bytes(
    binary_data: bytes, inventory_id: int, slot_bytes: bytes
) -> bytes:
    if not isinstance(slot_bytes, (bytes, bytearray)) or not slot_bytes:
        raise ValueError("slot_bytes missing")

    ctx = _saved_inventories_find_inventory_context(binary_data, inventory_id)
    buf = bytearray(binary_data)

    insert_at = int(ctx["insert_at"])
    element = bytes(slot_bytes)
    buf[insert_at:insert_at] = element
    delta = len(element)

    struct.pack_into(
        "<i", buf, int(ctx["slots_count_offset"]), int(ctx["slots_count"]) + 1
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_inner_size_offset"]),
        int(ctx["slots_inner_size"]) + delta,
    )
    struct.pack_into(
        "<i",
        buf,
        int(ctx["slots_outer_size_offset"]),
        int(ctx["slots_outer_size"]) + delta,
    )
    struct.pack_into(
        "<i", buf, int(ctx["inner"].size_offset), int(ctx["inner"].size) + delta
    )
    struct.pack_into(
        "<i", buf, int(ctx["outer"].size_offset), int(ctx["outer"].size) + delta
    )

    return bytes(buf)


def saved_inventories_add_slot(
    binary_data: bytes,
    inventory_id: int,
    row_name: str,
    slot_location: int,
    stack: int,
    durability: int,
) -> bytes:
    if int(stack) <= 0:
        stack = 1
    if int(durability) < 0:
        durability = 0

    element = _ue_build_world_slot_bytes(
        row_name=row_name,
        slot_location=int(slot_location),
        stack=int(stack),
        durability=int(durability),
    )
    return saved_inventories_insert_slot_bytes(binary_data, inventory_id, element)


def saved_inventories_replace_slot(
    binary_data: bytes,
    inventory_id: int,
    slot_location: int,
    old_row_name: str,
    new_row_name: str,
    stack: int,
    durability: int,
) -> bytes:
    buf = saved_inventories_remove_slot(
        binary_data, inventory_id, slot_location, old_row_name
    )
    return saved_inventories_add_slot(
        buf, inventory_id, new_row_name, slot_location, stack, durability
    )


def saved_inventories_set_slot_stack(
    binary_data: bytes,
    inventory_id: int,
    slot_location: int,
    row_name: str,
    stack: int,
) -> bytes:
    if int(stack) <= 0:
        stack = 1
    ctx = _saved_inventories_find_slot_context(
        binary_data, inventory_id, slot_location, row_name
    )
    slot_fields: List[_MountBlobTagEx] = ctx["slot_fields"]
    dyn = _container_manager_dynamic_entries(binary_data, slot_fields)
    for d in dyn:
        if int(d.get("index", -999)) == 7 and "value_offset" in d:
            buf = bytearray(binary_data)
            struct.pack_into("<i", buf, int(d["value_offset"]), int(stack))
            return bytes(buf)
    raise KeyError("stack dynamic entry not found")


def saved_inventories_set_slot_durability(
    binary_data: bytes,
    inventory_id: int,
    slot_location: int,
    row_name: str,
    durability: int,
) -> bytes:
    if int(durability) < 0:
        durability = 0
    ctx = _saved_inventories_find_slot_context(
        binary_data, inventory_id, slot_location, row_name
    )
    slot_fields: List[_MountBlobTagEx] = ctx["slot_fields"]
    dyn = _container_manager_dynamic_entries(binary_data, slot_fields)
    for d in dyn:
        if int(d.get("index", -999)) == 6 and "value_offset" in d:
            buf = bytearray(binary_data)
            struct.pack_into("<i", buf, int(d["value_offset"]), int(durability))
            return bytes(buf)
    if int(durability) <= 0:
        return binary_data
    raise KeyError("durability dynamic entry not found")


def container_manager_list_world_items(
    binary_data: bytes, prospect_path: str, prospect_id: str
) -> List[Dict[str, Any]]:
    buf = binary_data
    outer = _find_mount_blob_tag_ex(buf, "SavedInventoryContainers", "ArrayProperty")
    if not outer:
        return []

    container_count = _ue_read_i32(buf, outer.value_offset)
    if container_count <= 0:
        return []

    inner = _parse_mount_blob_tag_ex(buf, outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        return []

    pos = inner.value_offset
    end = inner.value_offset + inner.size

    items: List[Dict[str, Any]] = []
    for _ci in range(int(container_count)):
        try:
            cont_fields, pos = _ue_parse_struct_fields(buf, pos, end)
        except Exception:
            break

        t_idx = _ue_find_tag(cont_fields, "InventoryIndex")
        t_info = _ue_find_tag(cont_fields, "InventoryInfo")
        t_save = _ue_find_tag(cont_fields, "InventorySaveData")
        if not t_idx or not t_info or not t_save:
            continue

        inv_index = _ue_read_i32(buf, t_idx.value_offset)
        try:
            inv_info = _ue_read_tag_fstring(buf, t_info)
        except Exception:
            inv_info = ""

        if t_save.type_name != "StructProperty":
            continue
        inv_save_start = t_save.value_offset
        inv_save_end = inv_save_start + int(t_save.size)
        try:
            save_fields, _ = _ue_parse_struct_fields(buf, inv_save_start, inv_save_end)
        except Exception:
            continue

        slots = _ue_find_tag(save_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            continue

        slot_count = _ue_read_i32(buf, slots.value_offset)
        if slot_count <= 0:
            continue

        try:
            slots_inner = _parse_mount_blob_tag_ex(buf, slots.value_offset + 4)
        except Exception:
            continue
        if slots_inner.type_name != "StructProperty":
            continue

        sp = slots_inner.value_offset
        se = slots_inner.value_offset + int(slots_inner.size)

        for slot_order in range(int(slot_count)):
            try:
                slot_fields, sp_next = _ue_parse_struct_fields(buf, sp, se)
            except Exception:
                break

            loc_tag = _ue_find_tag(slot_fields, "Location")
            row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
            if not loc_tag or not row_tag:
                sp = sp_next
                continue
            if (
                loc_tag.type_name != "IntProperty"
                or row_tag.type_name != "NameProperty"
            ):
                sp = sp_next
                continue

            slot_location = _ue_read_i32(buf, loc_tag.value_offset)
            try:
                row_name = _ue_read_tag_fstring(buf, row_tag)
            except Exception:
                row_name = ""

            pairs = _container_manager_dynamic_pairs(buf, slot_fields)
            stack = _container_manager_dyn_get(pairs, 7, 1)
            if stack <= 0:
                stack = 1
            durability = _container_manager_dyn_get(pairs, 6, 0)
            if durability < 0:
                durability = 0

            it: Dict[str, Any] = {
                "ItemStaticData": {
                    "RowName": row_name,
                    "DataTableName": "D_ItemsStatic",
                },
                "ItemDynamicData": [],
                "_world": {
                    "prospect_path": prospect_path,
                    "prospect_id": prospect_id,
                    "container_index": int(inv_index),
                    "inventory_info": inv_info,
                    "slot_order": int(slot_order),
                    "slot_location": int(slot_location),
                },
            }
            SaveModel.set_dyn(it, "ItemableStack", int(stack))
            if int(durability) > 0:
                SaveModel.set_dyn(it, "Durability", int(durability))

            items.append(it)
            sp = sp_next

    return items


def container_manager_pop_world_item(
    binary_data: bytes,
    container_index: int,
    slot_order: int,
    slot_location: int,
    row_name: str,
) -> Tuple[bytes, Dict[str, Any]]:
    buf = bytearray(binary_data)

    outer = _find_mount_blob_tag_ex(
        bytes(buf), "SavedInventoryContainers", "ArrayProperty"
    )
    if not outer:
        raise KeyError("SavedInventoryContainers not found")

    container_count = _ue_read_i32(bytes(buf), outer.value_offset)
    inner = _parse_mount_blob_tag_ex(bytes(buf), outer.value_offset + 4)
    if inner.type_name != "StructProperty":
        raise KeyError("SavedInventoryContainers inner header not found")

    pos = inner.value_offset
    end = inner.value_offset + int(inner.size)

    for _ci in range(int(container_count)):
        cont_fields, pos = _ue_parse_struct_fields(bytes(buf), pos, end)

        t_idx = _ue_find_tag(cont_fields, "InventoryIndex")
        t_info = _ue_find_tag(cont_fields, "InventoryInfo")
        t_save = _ue_find_tag(cont_fields, "InventorySaveData")
        if not t_idx or not t_save:
            continue
        inv_index = _ue_read_i32(bytes(buf), t_idx.value_offset)
        if int(inv_index) != int(container_index):
            continue

        inv_info = ""
        if t_info:
            try:
                inv_info = _ue_read_tag_fstring(bytes(buf), t_info)
            except Exception:
                inv_info = ""

        if t_save.type_name != "StructProperty":
            continue
        inv_save_size_offset = int(t_save.size_offset)
        inv_save_size = int(t_save.size)

        inv_save_start = t_save.value_offset
        inv_save_end = inv_save_start + inv_save_size
        save_fields, _ = _ue_parse_struct_fields(
            bytes(buf), inv_save_start, inv_save_end
        )

        slots = _ue_find_tag(save_fields, "Slots")
        if not slots or slots.type_name != "ArrayProperty":
            continue

        slots_count_offset = int(slots.value_offset)
        slots_outer_size_offset = int(slots.size_offset)
        slots_outer_size = int(slots.size)
        slots_count = _ue_read_i32(bytes(buf), slots_count_offset)

        slots_inner = _parse_mount_blob_tag_ex(bytes(buf), slots.value_offset + 4)
        if slots_inner.type_name != "StructProperty":
            continue
        slots_inner_size_offset = int(slots_inner.size_offset)
        slots_inner_size = int(slots_inner.size)

        sp = slots_inner.value_offset
        se = slots_inner.value_offset + slots_inner_size

        found: Optional[Dict[str, Any]] = None
        remove_start = -1
        remove_end = -1
        actual_slot_count = int(slots_count)

        for si in range(actual_slot_count):
            slot_start = sp
            slot_fields, sp_next = _ue_parse_struct_fields(bytes(buf), sp, se)

            loc_tag = _ue_find_tag(slot_fields, "Location")
            row_tag = _ue_find_tag(slot_fields, "ItemStaticData")
            if (
                loc_tag
                and row_tag
                and loc_tag.type_name == "IntProperty"
                and row_tag.type_name == "NameProperty"
            ):
                loc = _ue_read_i32(bytes(buf), loc_tag.value_offset)
                try:
                    rn = _ue_read_tag_fstring(bytes(buf), row_tag)
                except Exception:
                    rn = ""
                if (
                    int(si) == int(slot_order)
                    and int(loc) == int(slot_location)
                    and rn == row_name
                ):
                    pairs = _container_manager_dynamic_pairs(bytes(buf), slot_fields)
                    stack = _container_manager_dyn_get(pairs, 7, 1)
                    if stack <= 0:
                        stack = 1
                    durability = _container_manager_dyn_get(pairs, 6, 0)
                    if durability < 0:
                        durability = 0
                    found = {
                        "row_name": rn,
                        "stack": int(stack),
                        "durability": int(durability),
                        "container_index": int(container_index),
                        "inventory_info": inv_info,
                        "slot_location": int(loc),
                        "slot_order": int(si),
                    }
                    remove_start = slot_start
                    remove_end = sp_next
                    break

            sp = sp_next

        if not found or remove_start < 0 or remove_end <= remove_start:
            raise KeyError("slot not found in container")

        delta = int(remove_end - remove_start)
        del buf[remove_start:remove_end]

        # Update sizes upwards (all of these offsets are before `remove_start`)
        struct.pack_into("<i", buf, slots_count_offset, int(slots_count) - 1)
        struct.pack_into(
            "<i", buf, slots_inner_size_offset, int(slots_inner_size) - delta
        )
        struct.pack_into(
            "<i", buf, slots_outer_size_offset, int(slots_outer_size) - delta
        )
        struct.pack_into("<i", buf, inv_save_size_offset, int(inv_save_size) - delta)
        struct.pack_into("<i", buf, int(inner.size_offset), int(inner.size) - delta)
        struct.pack_into("<i", buf, int(outer.size_offset), int(outer.size) - delta)

        return bytes(buf), found

    raise KeyError("container not found")


def prospect_blob_decompress(prospect_raw: Dict[str, Any]) -> bytes:
    pb = prospect_raw.get("ProspectBlob") or {}
    if not isinstance(pb, dict):
        raise ValueError("ProspectBlob missing")
    b64 = pb.get("BinaryBlob")
    if not isinstance(b64, str) or not b64:
        raise ValueError("ProspectBlob.BinaryBlob missing")
    return zlib.decompress(base64.b64decode(b64))


def prospect_blob_update(prospect_raw: Dict[str, Any], uncompressed: bytes) -> None:
    pb = prospect_raw.get("ProspectBlob")
    if not isinstance(pb, dict):
        prospect_raw["ProspectBlob"] = pb = {}
    comp = zlib.compress(uncompressed)
    pb["BinaryBlob"] = base64.b64encode(comp).decode("ascii")
    pb["Hash"] = hashlib.sha1(uncompressed).hexdigest()
    pb["TotalLength"] = int(len(comp))
    pb["DataLength"] = int(len(comp))
    pb["UncompressedLength"] = int(len(uncompressed))


def prospect_blob_ai_setup_counts(
    prospect_raw: Dict[str, Any], ai_tokens: Iterable[str]
) -> Dict[str, int]:
    try:
        uncompressed = prospect_blob_decompress(prospect_raw)
    except Exception:
        return {}
    text = uncompressed.decode("utf-8", errors="ignore")
    counts: Dict[str, int] = {}
    for raw_token in ai_tokens:
        token = str(raw_token or "").strip()
        if not token:
            continue
        pattern = r"AISetupRowName.{0,256}?" + re.escape(token)
        hits = len(re.findall(pattern, text, flags=re.S))
        counts[token] = int(hits)
    return counts


def _find_marked_ranges(buf: bytes, marker: bytes) -> List[Tuple[int, int]]:
    starts: List[int] = []
    pos = 0
    while True:
        idx = buf.find(marker, pos)
        if idx < 0:
            break
        starts.append(idx)
        pos = idx + 1
    return [
        (st, starts[i + 1] if i + 1 < len(starts) else len(buf))
        for i, st in enumerate(starts)
    ]


def _find_tag_in_range(
    buf: bytes, start: int, end: int, name: str, type_name: str
) -> Optional[_MountBlobTagEx]:
    try:
        pat = _ascii_fstring_bytes(name)
    except Exception:
        return None
    pos = start
    while True:
        idx = buf.find(pat, pos, end)
        if idx < 0:
            return None
        try:
            tag = _parse_mount_blob_tag_ex(buf, idx)
        except Exception:
            pos = idx + 1
            continue
        if tag.name == name and tag.type_name == type_name:
            return tag
        pos = idx + 1


def prospect_container_manager_binarydata(
    uncompressed: bytes,
) -> Tuple[_MountBlobTagEx, int, int]:
    marker = b"/Script/Icarus.IcarusContainerManagerRecorderComponent"
    for st, en in _find_marked_ranges(uncompressed, marker):
        tag = _find_tag_in_range(uncompressed, st, en, "BinaryData", "ArrayProperty")
        if not tag:
            continue
        count = _ue_read_i32(uncompressed, tag.value_offset)
        if count < 0:
            continue
        data_start = int(tag.value_offset + 4)
        data_end = int(data_start + count)
        if data_end <= en:
            return tag, data_start, data_end
    raise KeyError("ContainerManager BinaryData not found in ProspectBlob")


DISCORD_QSS = """
QMainWindow, QWidget { background: #2B2D31; color: #DBDEE1; font-size: 13px; }
QTabWidget::pane { border: 1px solid #1E1F22; }
QTabBar::tab {
  background: #1E1F22; padding: 8px 12px; border: 1px solid #111214; margin-right: 2px;
}
QTabBar::tab:selected { background: #313338; }
QGroupBox { border: 1px solid #1E1F22; margin-top: 10px; border-radius: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #B5BAC1; }

QLineEdit, QComboBox {
  background: #1E1F22; border: 1px solid #111214; border-radius: 6px; padding: 6px;
}
QPushButton {
  background: #5865F2; border: none; border-radius: 6px; padding: 8px 10px; color: white;
}
QPushButton:hover { background: #4752C4; }
QPushButton:disabled { background: #3b3f46; color: #8e9297; }
QSpinBox {
  background: #1E1F22; border: 1px solid #111214; border-radius: 6px; padding: 6px;
}
QTextEdit, QTextBrowser {
  background: #1E1F22; border: 1px solid #111214; border-radius: 6px; padding: 8px; color: #DBDEE1;
}


QTableWidget, QTableView {
  background: #1E1F22;
  border: 1px solid #111214;
  gridline-color: #313338;
  alternate-background-color: #232428; 
  selection-background-color: #3A3D45;
  selection-color: #DBDEE1;
}
QTableWidget::item, QTableView::item {
  background-color: #1E1F22;
  padding: 4px;
}
QTableWidget::item:alternate, QTableView::item:alternate {
  background-color: #232428;
}
QTableWidget::item:selected, QTableView::item:selected {
  background-color: #3A3D45;
}

QTreeWidget, QTreeView {
  background: #1E1F22;
  border: 1px solid #111214;
  alternate-background-color: #232428;
  selection-background-color: #3A3D45;
  selection-color: #DBDEE1;
}
QTreeWidget::item, QTreeView::item {
  background-color: #1E1F22;
  padding: 4px;
}
QTreeWidget::item:alternate, QTreeView::item:alternate {
  background-color: #232428;
}
QTreeWidget::item:selected, QTreeView::item:selected {
  background-color: #3A3D45;
}

QHeaderView::section {
  background: #111214; color: #B5BAC1; border: none; padding: 6px;
}
QSplitter::handle { background: #1E1F22; }


QScrollBar:vertical {
  border: none;
  background: #1E1F22;
  width: 10px;
  margin: 0px;
}
QScrollBar::handle:vertical {
  background: #3B3F46;
  min-height: 28px;
  border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #4B5160; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
  border: none;
  background: #1E1F22;
  height: 10px;
  margin: 0px;
}
QScrollBar::handle:horizontal {
  background: #3B3F46;
  min-width: 28px;
  border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #4B5160; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
"""


class CurrencyRowWidget(QWidget):
    valueChanged = Signal(str, int)

    def __init__(self, meta_row: str, label: str, color: str, icon_text: str) -> None:
        super().__init__()
        self.meta_row = meta_row

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        icon = QLabel(icon_text)
        icon.setFixedWidth(24)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {color}; font-weight: 900;")

        name = QLabel(label)
        name.setStyleSheet("color: #B5BAC1;")

        self.spin = QSpinBox()
        self.spin.setRange(0, 10**9)
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        f = QFont()
        f.setPointSize(16)
        f.setBold(True)
        self.spin.setFont(f)
        self.spin.valueChanged.connect(self._on_change)

        lay.addWidget(icon)
        lay.addWidget(name, 1)
        lay.addWidget(self.spin)

        self.setStyleSheet(
            "background:#1E1F22; border:1px solid #111214; border-radius:10px;"
        )

    def set_value(self, v: int) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(int(v))
        self.spin.blockSignals(False)

    def _on_change(self, v: int) -> None:
        self.valueChanged.emit(self.meta_row, int(v))


class UnlocksTable(QTableWidget):
    flagToggled = Signal(int, bool)

    def __init__(self) -> None:
        super().__init__(0, 3)
        self._populating = False
        self.setHorizontalHeaderLabels(["Флаг", "Разблокировка", "За что"])
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setTextElideMode(Qt.ElideNone)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.itemChanged.connect(self._on_item_changed)

    def set_rows(self, rows: List[UnlockRow], enabled_flags: set[int]) -> None:
        self._populating = True
        self.blockSignals(True)
        self.setSortingEnabled(False)
        self.setRowCount(0)
        for r in rows:
            row = self.rowCount()
            self.insertRow(row)

            it_flag = QTableWidgetItem(str(r.flag))
            it_flag.setData(Qt.UserRole, r.flag)
            it_flag.setFlags(it_flag.flags() | Qt.ItemIsUserCheckable)
            it_flag.setCheckState(
                Qt.Checked if r.flag in enabled_flags else Qt.Unchecked
            )

            it_unlock = QTableWidgetItem(r.unlock_name)
            it_by = QTableWidgetItem(r.unlocked_by)

            self.setItem(row, 0, it_flag)
            self.setItem(row, 1, it_unlock)
            self.setItem(row, 2, it_by)

        self.setSortingEnabled(True)
        self.blockSignals(False)
        self._populating = False
        self.resizeColumnsToContents()
        self.resizeRowsToContents()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating:
            return
        if not item or item.column() != 0:
            return
        flag_raw = item.data(Qt.UserRole)
        if not isinstance(flag_raw, int):
            return
        enabled = item.checkState() == Qt.Checked
        self.flagToggled.emit(int(flag_raw), enabled)


class MainTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        self.info = QLabel("Сейв не загружен.")
        self.info.setStyleSheet("color:#B5BAC1;")
        root.addWidget(self.info)

        self.cards = QVBoxLayout()
        self.cards.setSpacing(10)
        root.addLayout(self.cards)
        root.addStretch(1)

        self._fallback_currency_cfg = [
            ("Credits", "Ren", "#FEE75C", "⦿"),
            ("Exotic1", "Exotics", "#B197FC", "⬢"),
            ("Exotic_Red", "Red Exotics", "#ED4245", "⬢"),
            ("Biomass", "Biomass", "#57F287", "⬢"),
            ("Licence", "Licence", "#00A8FC", "🎟"),
        ]
        self.currency_cfg: List[Tuple[str, str, str, str]] = (
            self._fallback_currency_cfg[:]
        )
        self.rows: Dict[str, CurrencyRowWidget] = {}

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data
        self._rebuild_currency_cfg(self.model.get_currency())

    def _currency_icon(self, row_name: str, decorator_text: str) -> str:
        preset = {
            "Credits": "⦿",
            "Exotic1": "⬢",
            "Exotic_Red": "⬢",
            "Biomass": "⬢",
            "Licence": "🎟",
        }
        if row_name in preset:
            return preset[row_name]
        d = (decorator_text or "").strip()
        if d:
            return d[:1].upper()
        return "¤"

    def _rebuild_currency_cfg(
        self, current_currency: Optional[Dict[str, int]] = None
    ) -> None:
        cfg: List[Tuple[str, str, str, str]] = []

        if self._game_data and self._game_data.meta_currency_order:
            for row_name in self._game_data.meta_currency_order:
                meta = self._game_data.meta_currencies.get(row_name)
                if not meta:
                    continue
                cfg.append(
                    (
                        meta.row_name,
                        meta.display_name or meta.row_name,
                        meta.color_hex or "#B5BAC1",
                        self._currency_icon(meta.row_name, meta.decorator_text),
                    )
                )
        else:
            cfg = self._fallback_currency_cfg[:]

        seen = {row_name for row_name, _, _, _ in cfg}
        cur = current_currency or {}
        for row_name in sorted(cur.keys()):
            if row_name in seen:
                continue
            cfg.append((row_name, row_name, "#B5BAC1", "¤"))
            seen.add(row_name)

        self.currency_cfg = cfg if cfg else self._fallback_currency_cfg[:]

    def load(self) -> None:
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()

        cur = self.model.get_currency()
        self._rebuild_currency_cfg(cur)
        self.rows.clear()

        for meta_row, label, color, icon_text in self.currency_cfg:
            w = CurrencyRowWidget(meta_row, label, color, icon_text)
            w.set_value(cur.get(meta_row, 0))
            w.valueChanged.connect(self._changed)
            self.cards.addWidget(w)
            self.rows[meta_row] = w

        self.info.setText("")

    def _changed(self, meta_row: str, value: int) -> None:
        self.model.set_currency(meta_row, value)
        self.mark_dirty()


class UnlocksTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        top = QHBoxLayout()
        self.cb_unlock_all = QCheckBox("unlock all")
        self.cb_unlock_all.stateChanged.connect(self._unlock_all_changed)
        top.addWidget(self.cb_unlock_all)
        top.addStretch(1)
        root.addLayout(top)

        g_leg = QGroupBox("Легендарные предметы (быстрая разблокировка)")
        leg_lay = QVBoxLayout(g_leg)

        self.legendary = [
            ("Легендарный лук (Tactical Bow / Rimetusk финал)", [29, 50]),
            ("Крупнокалиберка (Anti-Materiel Rifle / Garganutan финал)", [30, 43]),
            ("Перчатка (Mining Armature / Quarrite финал)", [28, 56]),
        ]
        self.leg_checks: List[QCheckBox] = []
        for title, flags in self.legendary:
            cb = QCheckBox(f"{title} — флаги {flags}")
            cb.stateChanged.connect(
                lambda st, fl=flags: self._toggle_many(
                    fl, st in (Qt.Checked, Qt.Checked.value)
                )
            )
            leg_lay.addWidget(cb)
            self.leg_checks.append(cb)

        root.addWidget(g_leg)

        filter_row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(
            "Фильтр (например: Sandworm, Scorpion, Rimetusk...)"
        )
        self.filter.textChanged.connect(self._apply_filter)
        filter_row.addWidget(QLabel("Список разблокировок:"))
        filter_row.addWidget(self.filter, 1)
        root.addLayout(filter_row)

        self.table = UnlocksTable()
        self.table.flagToggled.connect(self._toggle_flag)
        root.addWidget(self.table, 1)

        self._all_rows: List[UnlockRow] = []

    def load(self) -> None:
        self._all_rows = self.model.unlock_rows
        self._sync_legendary_checks()
        self._sync_unlock_all()
        self._apply_filter()

    def _sync_unlock_all(self) -> None:
        flags = self.model.flags_set()
        all_enabled = all(r.flag in flags for r in self.model.unlock_rows)
        self.cb_unlock_all.blockSignals(True)
        self.cb_unlock_all.setChecked(all_enabled)
        self.cb_unlock_all.blockSignals(False)

    def _unlock_all_changed(self, st: int) -> None:
        if st not in (Qt.Checked, Qt.Checked.value):
            return
        for r in self.model.unlock_rows:
            self.model.set_flag(r.flag, True)
        self._sync_legendary_checks()
        self._apply_filter()
        self.mark_dirty()

    def _sync_legendary_checks(self) -> None:
        flags = self.model.flags_set()
        for cb, (_, fl) in zip(self.leg_checks, self.legendary):
            enabled = all(f in flags for f in fl)
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def _toggle_many(self, flags: List[int], enabled: bool) -> None:
        for f in flags:
            self.model.set_flag(f, enabled)
        self._sync_legendary_checks()
        self._sync_unlock_all()
        self._apply_filter()
        self.mark_dirty()

    def _apply_filter(self) -> None:
        if not self._all_rows:
            self.table.setRowCount(0)
            return
        q = self.filter.text().strip().lower()
        rows = self._all_rows
        if q:
            rows = [
                r
                for r in rows
                if q in r.unlock_name.lower()
                or q in r.unlocked_by.lower()
                or q in str(r.flag)
            ]
        self.table.set_rows(rows, self.model.flags_set())
        self._sync_legendary_checks()
        self._sync_unlock_all()

    def _toggle_flag(self, flag: int, enabled: bool) -> None:
        self.model.set_flag(flag, enabled)
        self._sync_legendary_checks()
        self._sync_unlock_all()
        self.mark_dirty()


class AchievementsTab(QWidget):
    TRACKER_TABS: List[Tuple[str, str]] = [
        ("survival", "Выживание"),
        ("hunting", "Охота"),
        ("building", "Строительство"),
        ("general", "Общее"),
    ]
    BESTIARY_WORLD_ORDER: Tuple[str, ...] = (
        "Terrain_016",
        "Terrain_017",
        "Terrain_019",
        "Terrain_021",
    )
    BESTIARY_WORLD_FALLBACKS: Dict[str, str] = {
        "Terrain_016": "Olympus",
        "Terrain_017": "Styx",
        "Terrain_019": "Prometheus",
        "Terrain_021": "Elysium",
        "Space": "Орбита",
    }
    BESTIARY_SCOPE_BOSSES = "__world_bosses__"

    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None
        self._populating_accolades = False
        self._populating_tracker_tabs: Set[str] = set()
        self._populating_bestiary_tabs: Set[str] = set()
        self._populating_medals = False
        self._visible_accolade_rows: List[str] = []
        self._visible_tracker_rows: Dict[str, List[str]] = {}
        self._visible_bestiary_rows: Dict[str, List[str]] = {}
        self._visible_medal_rows: List[str] = []
        self._tracker_pages: Dict[str, Dict[str, Any]] = {}
        self._bestiary_pages: Dict[str, Dict[str, Any]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.info = QLabel("")
        self.info.setStyleSheet("color:#B5BAC1;")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.subtabs = QTabWidget()
        root.addWidget(self.subtabs, 1)

        # --- Achievements / accolades ---
        achievements_page = QWidget()
        achievements_root = QVBoxLayout(achievements_page)
        achievements_root.setContentsMargins(0, 0, 0, 0)
        achievements_root.setSpacing(6)

        acc_top = QHBoxLayout()
        acc_top.addWidget(QLabel("Поиск:"))
        self.search_accolades = QLineEdit()
        self.search_accolades.setPlaceholderText(
            "Поиск по достижению / RowName / числу / ProspectID…"
        )
        self.search_accolades.textChanged.connect(self._apply_accolade_filter)
        acc_top.addWidget(self.search_accolades, 1)
        self.btn_acc_unlock = QPushButton("Открыть всё найденное")
        self.btn_acc_unlock.clicked.connect(self._unlock_visible_accolades)
        acc_top.addWidget(self.btn_acc_unlock)
        self.btn_acc_lock = QPushButton("Снять всё найденное")
        self.btn_acc_lock.clicked.connect(self._lock_visible_accolades)
        acc_top.addWidget(self.btn_acc_lock)
        self.btn_acc_add = QPushButton("Добавить RowName…")
        self.btn_acc_add.clicked.connect(self._add_manual_accolade)
        acc_top.addWidget(self.btn_acc_add)
        achievements_root.addLayout(acc_top)

        self.tbl_accolades = QTableWidget(0, 5)
        self.tbl_accolades.setHorizontalHeaderLabels(
            ["Открыто", "Достижение", "RowName", "Когда", "Prospect"]
        )
        self.tbl_accolades.verticalHeader().setVisible(False)
        self.tbl_accolades.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_accolades.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_accolades.setTextElideMode(Qt.ElideNone)
        self.tbl_accolades.setAlternatingRowColors(True)
        acc_hdr = self.tbl_accolades.horizontalHeader()
        acc_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        acc_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        acc_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        acc_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        acc_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_accolades.itemChanged.connect(self._accolade_item_changed)
        achievements_root.addWidget(self.tbl_accolades, 1)

        self.subtabs.addTab(achievements_page, "Достижения")

        # --- Tracker pages ---
        for key, title in self.TRACKER_TABS:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)

            note = QLabel(
                "Чекбокс сразу открывает или снимает ачивку. Прогресс и цель берутся из игровых таблиц."
            )
            note.setStyleSheet("color:#B5BAC1;")
            note.setWordWrap(True)
            lay.addWidget(note)

            top = QHBoxLayout()
            top.addWidget(QLabel("Поиск:"))
            search = QLineEdit()
            search.setPlaceholderText("Поиск по ачивке / описанию / RowName / числу…")
            search.textChanged.connect(
                lambda _text="", category=key: self._apply_tracker_filter(category)
            )
            top.addWidget(search, 1)
            btn_unlock = QPushButton("Открыть всё найденное")
            btn_unlock.clicked.connect(
                lambda _checked=False, category=key: self._unlock_visible_tracker_rows(
                    category
                )
            )
            top.addWidget(btn_unlock)
            btn_lock = QPushButton("Снять всё найденное")
            btn_lock.clicked.connect(
                lambda _checked=False, category=key: self._lock_visible_tracker_rows(
                    category
                )
            )
            top.addWidget(btn_lock)
            lay.addLayout(top)

            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(
                ["Открыто", "Ачивка", "Прогресс", "Нужно", "Трекер", "RowName"]
            )
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setTextElideMode(Qt.ElideNone)
            table.setAlternatingRowColors(True)
            hdr = table.horizontalHeader()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
            table.itemChanged.connect(
                lambda item, category=key: self._tracker_item_changed(category, item)
            )
            lay.addWidget(table, 1)

            self._tracker_pages[key] = {"page": page, "search": search, "table": table}
            self._visible_tracker_rows[key] = []
            self.subtabs.addTab(page, title)

        # --- Bestiary by world ---
        bestiary_page = QWidget()
        bestiary_root = QVBoxLayout(bestiary_page)
        bestiary_root.setContentsMargins(0, 0, 0, 0)
        bestiary_root.setSpacing(6)

        bestiary_note = QLabel(
            "Бестиарий по мирам: очки можно править вручную, чекбокс добивает запись до порога медали."
        )
        bestiary_note.setStyleSheet("color:#B5BAC1;")
        bestiary_note.setWordWrap(True)
        bestiary_root.addWidget(bestiary_note)

        self.bestiary_subtabs = QTabWidget()
        bestiary_root.addWidget(self.bestiary_subtabs, 1)
        self.subtabs.addTab(bestiary_page, "Бестиарий")

        # --- Medals / bestiary ---
        medals_page = QWidget()
        medals_root = QVBoxLayout(medals_page)
        medals_root.setContentsMargins(0, 0, 0, 0)
        medals_root.setSpacing(6)

        medals_note = QLabel(
            "Медали бестиария: можно выставить очки вручную или переключить unlock/lock."
        )
        medals_note.setStyleSheet("color:#B5BAC1;")
        medals_note.setWordWrap(True)
        medals_root.addWidget(medals_note)

        medals_top = QHBoxLayout()
        medals_top.addWidget(QLabel("Поиск:"))
        self.search_medals = QLineEdit()
        self.search_medals.setPlaceholderText("Поиск по существу / RowName / числу…")
        self.search_medals.textChanged.connect(self._apply_medal_filter)
        medals_top.addWidget(self.search_medals, 1)
        self.btn_medals_unlock = QPushButton("Открыть всё найденное")
        self.btn_medals_unlock.clicked.connect(self._unlock_visible_medals)
        medals_top.addWidget(self.btn_medals_unlock)
        self.btn_medals_lock = QPushButton("Снять всё найденное")
        self.btn_medals_lock.clicked.connect(self._lock_visible_medals)
        medals_top.addWidget(self.btn_medals_lock)
        medals_root.addLayout(medals_top)

        self.tbl_medals = QTableWidget(0, 5)
        self.tbl_medals.setHorizontalHeaderLabels(
            ["Открыто", "Медаль", "Очки", "Нужно", "RowName"]
        )
        self.tbl_medals.verticalHeader().setVisible(False)
        self.tbl_medals.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_medals.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.tbl_medals.setTextElideMode(Qt.ElideNone)
        self.tbl_medals.setAlternatingRowColors(True)
        medals_hdr = self.tbl_medals.horizontalHeader()
        medals_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        medals_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        medals_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        medals_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        medals_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_medals.itemChanged.connect(self._medal_item_changed)
        medals_root.addWidget(self.tbl_medals, 1)

        self.subtabs.addTab(medals_page, "Медали")
        self._rebuild_bestiary_pages()

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data
        self._rebuild_bestiary_pages()
        self._refresh_bestiary_views()

    def load(self) -> None:
        if not self.model.root:
            self.info.setText("Сейв не загружен.")
        elif not self.model.accolades_path and not self.model.bestiary_path:
            self.info.setText(
                "Accolades.json / BestiaryData.json не найдены. Вкладка работает только с ними."
            )
        else:
            self.info.setText(
                "Accolades.json хранит достижения и трекеры. BestiaryData.json хранит медали и бестиарий существ."
            )
        self._apply_accolade_filter()
        for key, _title in self.TRACKER_TABS:
            self._apply_tracker_filter(key)
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    @staticmethod
    def _tracker_group_key(
        row_name: str, tracker_category: str, is_task_list: bool
    ) -> str:
        low = (row_name or "").strip().lower()
        cat = (tracker_category or "").strip().lower()

        hunting_cats = {
            "creatures",
            "meleeweapons",
            "rangedweapons",
            "projectilesfired",
            "skinning",
        }
        survival_cats = {"mining", "gathering", "logging", "farming", "consume"}

        if cat in hunting_cats or any(
            token in low
            for token in (
                "kill",
                "boss",
                "wolf",
                "bear",
                "boar",
                "deer",
                "mammoth",
                "jaguar",
                "lion",
                "zebra",
                "rabbit",
                "fish",
                "sandworm",
                "caveworm",
                "skin",
                "bestiary",
                "bow",
                "firearm",
                "spear",
                "axe",
                "knife",
                "stealth",
            )
        ):
            return "hunting"

        if cat == "crafting" or any(
            token in low
            for token in (
                "building",
                "buildings",
                "blueprint",
                "deployable",
                "crafted",
                "altered",
            )
        ):
            return "building"

        if cat in survival_cats or any(
            token in low
            for token in (
                "survived",
                "traveled",
                "distance",
                "time",
                "collected",
                "mined",
                "voxel",
                "ore",
                "resource",
                "wood",
                "stone",
                "fiber",
                "fibre",
                "food",
                "plant",
                "tree",
                "water",
                "night",
                "sleep",
                "thumper",
            )
        ):
            return "survival"

        if is_task_list and any(
            token in low for token in ("biome", "plant", "oretypes", "food")
        ):
            return "survival"

        return "general"

    def _accolade_display_name(self, row_name: str) -> str:
        rn = (row_name or "").strip()
        if not rn:
            return ""
        if self._game_data:
            meta = self._game_data.accolades.get(rn)
            if isinstance(meta, GameAccolade) and meta.display_name:
                return meta.display_name
        return _pretty_identifier(rn) or rn

    def _accolade_description(self, row_name: str) -> str:
        rn = (row_name or "").strip()
        if not rn or not self._game_data:
            return ""
        meta = self._game_data.accolades.get(rn)
        if isinstance(meta, GameAccolade):
            return meta.description or ""
        return ""

    def _accolade_tooltip(
        self,
        row_name: str,
        tracker_row: str = "",
        goal_count: int = 0,
        category: str = "",
    ) -> str:
        lines = [str(row_name or "").strip()]
        desc = self._accolade_description(row_name)
        if desc:
            lines.append(desc)
        if tracker_row:
            lines.append(f"Трекер: {tracker_row}")
        if goal_count > 0:
            lines.append(f"Цель: {int(goal_count)}")
        if category:
            lines.append(f"Категория: {category}")
        return "\n".join(line for line in lines if line)

    def _accolade_meta(self, row_name: str) -> Optional[GameAccolade]:
        rn = (row_name or "").strip()
        if not rn or not self._game_data:
            return None
        meta = self._game_data.accolades.get(rn)
        return meta if isinstance(meta, GameAccolade) else None

    def _refresh_achievement_views(self) -> None:
        self._apply_accolade_filter()
        for key, _title in self.TRACKER_TABS:
            self._apply_tracker_filter(key)

    def _refresh_bestiary_views(self) -> None:
        for scope_key in list(self._bestiary_pages.keys()):
            self._apply_bestiary_filter(scope_key)

    def _terrain_display_name(self, terrain_row: str) -> str:
        rn = (terrain_row or "").strip()
        if not rn:
            return ""
        if rn == "Space":
            return "Орбита"
        if self._game_data:
            title = self._game_data.terrain_names.get(rn, "")
            if isinstance(title, str) and title.strip():
                return title.strip()
        fallback = self.BESTIARY_WORLD_FALLBACKS.get(rn, "")
        if fallback:
            return fallback
        return _pretty_identifier(rn) or rn

    def _bestiary_scope_specs(self) -> List[Tuple[str, str]]:
        terrain_rows: List[str] = []
        seen: Set[str] = set()
        for row_name in self.BESTIARY_WORLD_ORDER:
            if row_name not in seen:
                terrain_rows.append(row_name)
                seen.add(row_name)

        if self._game_data:
            extra_rows = [
                row_name
                for row_name in self._game_data.terrain_order
                if isinstance(row_name, str)
                and row_name.startswith("Terrain_")
                and row_name not in seen
            ]
            for row_name in extra_rows:
                terrain_rows.append(row_name)
                seen.add(row_name)

        specs = [(row_name, self._terrain_display_name(row_name)) for row_name in terrain_rows]
        specs.append(("Space", self._terrain_display_name("Space")))
        specs.append((self.BESTIARY_SCOPE_BOSSES, "Мировые боссы"))
        return specs

    def _rebuild_bestiary_pages(self) -> None:
        while self.bestiary_subtabs.count() > 0:
            page = self.bestiary_subtabs.widget(0)
            self.bestiary_subtabs.removeTab(0)
            if page is not None:
                page.deleteLater()
        self._bestiary_pages = {}
        self._visible_bestiary_rows = {}
        self._populating_bestiary_tabs.clear()

        for scope_key, title in self._bestiary_scope_specs():
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)

            note = QLabel(
                "Чекбокс открывает запись, поле очков можно править вручную. Список строится из игрового бестиария."
            )
            note.setStyleSheet("color:#B5BAC1;")
            note.setWordWrap(True)
            lay.addWidget(note)

            top = QHBoxLayout()
            top.addWidget(QLabel("Поиск:"))
            search = QLineEdit()
            search.setPlaceholderText("Поиск по существу / миру / биому / RowName / числу…")
            search.textChanged.connect(
                lambda _text="", scope=scope_key: self._apply_bestiary_filter(scope)
            )
            top.addWidget(search, 1)
            btn_unlock = QPushButton("Открыть всё найденное")
            btn_unlock.clicked.connect(
                lambda _checked=False, scope=scope_key: self._unlock_visible_bestiary_rows(
                    scope
                )
            )
            top.addWidget(btn_unlock)
            btn_lock = QPushButton("Снять всё найденное")
            btn_lock.clicked.connect(
                lambda _checked=False, scope=scope_key: self._lock_visible_bestiary_rows(
                    scope
                )
            )
            top.addWidget(btn_lock)
            lay.addLayout(top)

            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(
                ["Открыто", "Существо", "Очки", "Нужно", "Биомы", "RowName"]
            )
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setEditTriggers(QAbstractItemView.AllEditTriggers)
            table.setTextElideMode(Qt.ElideNone)
            table.setAlternatingRowColors(True)
            hdr = table.horizontalHeader()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
            table.itemChanged.connect(
                lambda item, scope=scope_key: self._bestiary_item_changed(scope, item)
            )
            lay.addWidget(table, 1)

            self._bestiary_pages[scope_key] = {
                "page": page,
                "search": search,
                "table": table,
            }
            self._visible_bestiary_rows[scope_key] = []
            self.bestiary_subtabs.addTab(page, title)

    def _set_accolade_state(self, row_name: str, enabled: bool) -> bool:
        rn = (row_name or "").strip()
        if not rn:
            return False
        changed = self.model.set_accolade_completed(rn, enabled)
        meta = self._accolade_meta(rn)
        if not isinstance(meta, GameAccolade):
            return changed

        if meta.impl_type == "TrackerAccolade" and meta.tracker_row:
            current_value = int(self.model.player_trackers_map().get(meta.tracker_row, 0))
            goal = int(meta.goal_count if meta.goal_count > 0 else 1)
            if enabled:
                target_value = max(current_value, goal)
            else:
                target_value = min(current_value, max(0, goal - 1))
            changed = (
                self.model.set_player_tracker_value(meta.tracker_row, target_value)
                or changed
            )
        elif meta.impl_type == "TaskListAccolade" and meta.tracker_row:
            current_values = self.model.player_task_list_map().get(meta.tracker_row, [])
            current_norm = _normalize_task_values(current_values)
            wanted = _normalize_task_values(meta.task_values)
            if enabled:
                merged = list(current_norm)
                seen = {v.lower() for v in merged}
                for value in wanted:
                    low = value.lower()
                    if low in seen:
                        continue
                    merged.append(value)
                    seen.add(low)
                changed = self.model.set_player_task_list(meta.tracker_row, merged) or changed
            else:
                wanted_low = {v.lower() for v in wanted}
                kept = [v for v in current_norm if v.lower() not in wanted_low]
                changed = self.model.set_player_task_list(meta.tracker_row, kept) or changed
        return changed

    @staticmethod
    def _category_group_key(category_value: str) -> str:
        low = (category_value or "").strip().lower()
        if low == "hunting":
            return "hunting"
        if low == "survival":
            return "survival"
        if low in ("construction", "building", "crafting"):
            return "building"
        return "general"

    @staticmethod
    def _search_matches(query: str, *parts: Any) -> bool:
        q = (query or "").strip().lower()
        if not q:
            return True
        for part in parts:
            text = str(part if part is not None else "").strip().lower()
            if q and q in text:
                return True
        return False

    def _accolade_rows(self) -> List[Dict[str, Any]]:
        completed = self.model.completed_accolade_map()
        rows: List[Dict[str, Any]] = []
        known_rows: set[str] = set(self.model.known_accolade_rows()) | set(completed.keys())
        if self._game_data:
            known_rows |= set(self._game_data.accolades.keys())
        for row_name in known_rows:
            rec = completed.get(row_name, {})
            meta = self._accolade_meta(row_name)
            rows.append(
                {
                    "row_name": row_name,
                    "display_name": (
                        meta.display_name
                        if isinstance(meta, GameAccolade) and meta.display_name
                        else self._accolade_display_name(row_name)
                    ),
                    "description": meta.description if isinstance(meta, GameAccolade) else "",
                    "category": meta.category if isinstance(meta, GameAccolade) else "",
                    "completed": row_name in completed,
                    "time_completed": str(rec.get("TimeCompleted", "")) if rec else "",
                    "prospect_id": str(rec.get("ProspectID", "")) if rec else "",
                }
            )
        rows.sort(key=lambda x: (x["display_name"].lower(), x["row_name"].lower()))
        return rows

    def _apply_accolade_filter(self) -> None:
        rows = self._accolade_rows()
        q = self.search_accolades.text().strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if self._search_matches(
                    q,
                    r["display_name"],
                    r["description"],
                    r["category"],
                    r["row_name"],
                    r["prospect_id"],
                    r["time_completed"],
                )
            ]

        self._visible_accolade_rows = [r["row_name"] for r in rows]
        self._populating_accolades = True
        try:
            self.tbl_accolades.setRowCount(0)
            for rec in rows:
                row = self.tbl_accolades.rowCount()
                self.tbl_accolades.insertRow(row)
                meta = self._accolade_meta(rec["row_name"])

                it_done = QTableWidgetItem("")
                it_done.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                it_done.setCheckState(Qt.Checked if rec["completed"] else Qt.Unchecked)
                it_done.setData(Qt.UserRole, rec["row_name"])
                self.tbl_accolades.setItem(row, 0, it_done)

                it_name = QTableWidgetItem(rec["display_name"])
                it_name.setToolTip(
                    self._accolade_tooltip(
                        rec["row_name"],
                        tracker_row=(
                            meta.tracker_row if isinstance(meta, GameAccolade) else ""
                        ),
                        goal_count=(
                            int(meta.goal_count)
                            if isinstance(meta, GameAccolade) and meta.goal_count > 0
                            else 0
                        ),
                        category=rec["category"],
                    )
                )
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                self.tbl_accolades.setItem(row, 1, it_name)
                it_row = QTableWidgetItem(rec["row_name"])
                it_row.setFlags(it_row.flags() & ~Qt.ItemIsEditable)
                self.tbl_accolades.setItem(row, 2, it_row)
                it_time = QTableWidgetItem(rec["time_completed"])
                it_time.setFlags(it_time.flags() & ~Qt.ItemIsEditable)
                self.tbl_accolades.setItem(row, 3, it_time)
                it_prospect = QTableWidgetItem(rec["prospect_id"])
                it_prospect.setFlags(it_prospect.flags() & ~Qt.ItemIsEditable)
                self.tbl_accolades.setItem(row, 4, it_prospect)
        finally:
            self._populating_accolades = False

    def _accolade_item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating_accolades or not item or item.column() != 0:
            return
        row_name = item.data(Qt.UserRole)
        if not isinstance(row_name, str) or not row_name:
            return
        enabled = item.checkState() in (Qt.Checked, Qt.Checked.value)
        if self._set_accolade_state(row_name, enabled):
            self.mark_dirty()
        self._refresh_achievement_views()

    def _unlock_visible_accolades(self) -> None:
        changed = False
        for row_name in self._visible_accolade_rows:
            changed = self._set_accolade_state(row_name, True) or changed
        if changed:
            self.mark_dirty()
        self._refresh_achievement_views()

    def _lock_visible_accolades(self) -> None:
        changed = False
        for row_name in self._visible_accolade_rows:
            changed = self._set_accolade_state(row_name, False) or changed
        if changed:
            self.mark_dirty()
        self._refresh_achievement_views()

    def _add_manual_accolade(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Добавить достижение",
            "RowName ачивки / accolade:",
        )
        if not ok:
            return
        row_name = str(text).strip()
        if not row_name:
            return
        if self._set_accolade_state(row_name, True):
            self.mark_dirty()
        self.search_accolades.setText(row_name)
        self._refresh_achievement_views()

    def _tracker_rows_for_category(self, category_key: str) -> List[Dict[str, Any]]:
        int_values = self.model.player_trackers_map()
        list_values = self.model.player_task_list_map()
        completed = self.model.completed_accolade_map()
        rows: List[Dict[str, Any]] = []
        accolade_order = (
            list(self._game_data.accolade_order)
            if self._game_data and self._game_data.accolade_order
            else sorted(self.model.known_accolade_rows())
        )
        for row_name in accolade_order:
            meta = self._accolade_meta(row_name)
            if not isinstance(meta, GameAccolade):
                continue
            if meta.impl_type not in ("TrackerAccolade", "TaskListAccolade"):
                continue
            if self._category_group_key(meta.category) != category_key:
                continue

            tracker_row = meta.tracker_row
            if meta.impl_type == "TaskListAccolade":
                current_values = _normalize_task_values(list_values.get(tracker_row, []))
                required_values = _normalize_task_values(meta.task_values)
                done_count = sum(
                    1 for value in required_values if value.lower() in {v.lower() for v in current_values}
                )
                progress_text = f"{done_count}/{len(required_values)}"
                goal_text = ", ".join(required_values)
            else:
                current_value = int(int_values.get(tracker_row, 0))
                goal_value = int(meta.goal_count if meta.goal_count > 0 else 1)
                progress_text = str(current_value)
                goal_text = str(goal_value)

            rows.append(
                {
                    "row_name": row_name,
                    "display_name": meta.display_name or self._accolade_display_name(row_name),
                    "description": meta.description or "",
                    "category": meta.category or "",
                    "tracker_row": tracker_row,
                    "progress_text": progress_text,
                    "goal_text": goal_text,
                    "goal_count": int(meta.goal_count if meta.goal_count > 0 else 0),
                    "completed": row_name in completed,
                }
            )
        rows.sort(
            key=lambda x: (
                x["display_name"].lower(),
                x["row_name"].lower(),
            )
        )
        return rows

    def _apply_tracker_filter(self, category_key: str) -> None:
        page = self._tracker_pages.get(category_key) or {}
        table = page.get("table")
        search = page.get("search")
        if not isinstance(table, QTableWidget) or not isinstance(search, QLineEdit):
            return

        rows = self._tracker_rows_for_category(category_key)
        q = search.text().strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if self._search_matches(
                    q,
                    r["display_name"],
                    r["description"],
                    r["row_name"],
                    r["tracker_row"],
                    r["category"],
                    r["progress_text"],
                    r["goal_text"],
                )
            ]

        self._visible_tracker_rows[category_key] = [r["row_name"] for r in rows]
        self._populating_tracker_tabs.add(category_key)
        try:
            table.setRowCount(0)
            for rec in rows:
                row = table.rowCount()
                table.insertRow(row)

                it_done = QTableWidgetItem("")
                it_done.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                it_done.setCheckState(Qt.Checked if rec["completed"] else Qt.Unchecked)
                it_done.setData(Qt.UserRole, rec["row_name"])
                table.setItem(row, 0, it_done)

                it_name = QTableWidgetItem(rec["display_name"])
                tip = self._accolade_tooltip(
                    rec["row_name"],
                    tracker_row=rec["tracker_row"],
                    goal_count=rec["goal_count"],
                    category=rec["category"],
                )
                it_name.setToolTip(tip)
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 1, it_name)

                it_progress = QTableWidgetItem(rec["progress_text"])
                it_progress.setFlags(it_progress.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 2, it_progress)

                it_goal = QTableWidgetItem(rec["goal_text"])
                it_goal.setFlags(it_goal.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 3, it_goal)

                it_tracker = QTableWidgetItem(rec["tracker_row"])
                it_tracker.setFlags(it_tracker.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 4, it_tracker)

                it_row = QTableWidgetItem(rec["row_name"])
                it_row.setFlags(it_row.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 5, it_row)
        finally:
            self._populating_tracker_tabs.discard(category_key)

    def _tracker_item_changed(
        self, category_key: str, item: Optional[QTableWidgetItem]
    ) -> None:
        if category_key in self._populating_tracker_tabs or not item or item.column() != 0:
            return
        row_name = item.data(Qt.UserRole)
        if not isinstance(row_name, str) or not row_name:
            return
        enabled = item.checkState() in (Qt.Checked, Qt.Checked.value)
        if self._set_accolade_state(row_name, enabled):
            self.mark_dirty()
        self._refresh_achievement_views()

    def _unlock_visible_tracker_rows(self, category_key: str) -> None:
        changed = False
        for row_name in self._visible_tracker_rows.get(category_key, []):
            changed = self._set_accolade_state(row_name, True) or changed
        if changed:
            self.mark_dirty()
        self._refresh_achievement_views()

    def _lock_visible_tracker_rows(self, category_key: str) -> None:
        changed = False
        for row_name in self._visible_tracker_rows.get(category_key, []):
            changed = self._set_accolade_state(row_name, False) or changed
        if changed:
            self.mark_dirty()
        self._refresh_achievement_views()

    def _bestiary_tooltip(self, rec: Dict[str, Any]) -> str:
        lines = [str(rec.get("row_name", "")).strip()]
        maps_text = str(rec.get("maps_text", "")).strip()
        biomes_text = str(rec.get("biomes_text", "")).strip()
        if maps_text:
            lines.append(f"Миры: {maps_text}")
        if biomes_text:
            lines.append(f"Биомы: {biomes_text}")
        if rec.get("is_boss"):
            lines.append("Мировой босс")
        required = int(rec.get("required", 0) or 0)
        if required > 0:
            lines.append(f"Нужно очков: {required}")
        return "\n".join(line for line in lines if line)

    def _bestiary_rows_for_scope(self, scope_key: str) -> List[Dict[str, Any]]:
        rows = self._medal_rows()
        if scope_key == self.BESTIARY_SCOPE_BOSSES:
            return [r for r in rows if r.get("is_boss")]
        return [r for r in rows if scope_key in tuple(r.get("maps", ()) or ())]

    def _apply_bestiary_filter(self, scope_key: str) -> None:
        page = self._bestiary_pages.get(scope_key) or {}
        table = page.get("table")
        search = page.get("search")
        if not isinstance(table, QTableWidget) or not isinstance(search, QLineEdit):
            return

        rows = self._bestiary_rows_for_scope(scope_key)
        q = search.text().strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if self._search_matches(
                    q,
                    r["display_name"],
                    r["row_name"],
                    r["points"],
                    r["required"],
                    r["biomes_text"],
                    r["maps_text"],
                    "1" if r["unlocked"] else "0",
                    "boss" if r["is_boss"] else "",
                )
            ]

        self._visible_bestiary_rows[scope_key] = [r["row_name"] for r in rows]
        self._populating_bestiary_tabs.add(scope_key)
        try:
            table.setRowCount(0)
            for rec in rows:
                row = table.rowCount()
                table.insertRow(row)

                it_done = QTableWidgetItem("")
                it_done.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                it_done.setCheckState(Qt.Checked if rec["unlocked"] else Qt.Unchecked)
                it_done.setData(Qt.UserRole, rec["row_name"])
                it_done.setData(Qt.UserRole + 1, int(rec["required"]))
                table.setItem(row, 0, it_done)

                it_name = QTableWidgetItem(rec["display_name"])
                it_name.setToolTip(self._bestiary_tooltip(rec))
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 1, it_name)

                it_points = QTableWidgetItem(str(int(rec["points"])))
                it_points.setData(Qt.UserRole, rec["row_name"])
                table.setItem(row, 2, it_points)

                it_required = QTableWidgetItem(str(int(rec["required"])))
                it_required.setFlags(it_required.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 3, it_required)

                it_biomes = QTableWidgetItem(rec["biomes_text"])
                it_biomes.setToolTip(self._bestiary_tooltip(rec))
                it_biomes.setFlags(it_biomes.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 4, it_biomes)

                it_row = QTableWidgetItem(rec["row_name"])
                it_row.setFlags(it_row.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 5, it_row)
        finally:
            self._populating_bestiary_tabs.discard(scope_key)

    def _bestiary_item_changed(
        self, scope_key: str, item: Optional[QTableWidgetItem]
    ) -> None:
        if scope_key in self._populating_bestiary_tabs or not item:
            return

        changed = False
        if item.column() == 0:
            row_name = item.data(Qt.UserRole)
            required = item.data(Qt.UserRole + 1)
            if not isinstance(row_name, str) or not row_name:
                return
            enabled = item.checkState() in (Qt.Checked, Qt.Checked.value)
            target = int(required) if isinstance(required, int) and required > 0 else 1
            changed = self.model.set_bestiary_points(row_name, target if enabled else 0)
        elif item.column() == 2:
            row_name = item.data(Qt.UserRole)
            if not isinstance(row_name, str) or not row_name:
                return
            try:
                points = int(item.text().strip() or "0")
            except Exception:
                QMessageBox.warning(
                    self,
                    "Бестиарий",
                    f"Очки для `{row_name}` должны быть целым числом.",
                )
                self._apply_bestiary_filter(scope_key)
                return
            changed = self.model.set_bestiary_points(row_name, points)
        else:
            return

        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    def _unlock_visible_bestiary_rows(self, scope_key: str) -> None:
        rows = {r["row_name"]: r for r in self._bestiary_rows_for_scope(scope_key)}
        changed = False
        for row_name in self._visible_bestiary_rows.get(scope_key, []):
            rec = rows.get(row_name)
            if not rec:
                continue
            target = int(rec["required"]) if int(rec["required"]) > 0 else 1
            changed = self.model.set_bestiary_points(row_name, target) or changed
        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    def _lock_visible_bestiary_rows(self, scope_key: str) -> None:
        changed = False
        for row_name in self._visible_bestiary_rows.get(scope_key, []):
            changed = self.model.set_bestiary_points(row_name, 0) or changed
        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    def _medal_rows(self) -> List[Dict[str, Any]]:
        current = self.model.bestiary_points_map()
        meta_map = self._game_data.bestiary_entries if self._game_data else {}
        known_rows = set(current.keys()) | set(meta_map.keys())

        rows: List[Dict[str, Any]] = []
        for row_name in known_rows:
            meta = meta_map.get(row_name)
            display_name = (
                meta.display_name
                if isinstance(meta, GameBestiaryEntry) and meta.display_name
                else _pretty_identifier(row_name) or row_name
            )
            total_required = (
                int(meta.total_points_required)
                if isinstance(meta, GameBestiaryEntry)
                else 0
            )
            maps = tuple(meta.maps) if isinstance(meta, GameBestiaryEntry) else ()
            biomes = tuple(meta.biomes) if isinstance(meta, GameBestiaryEntry) else ()
            maps_text = ", ".join(self._terrain_display_name(v) for v in maps if v)
            biomes_text = ", ".join(_pretty_identifier(v) or v for v in biomes if v)
            points = int(current.get(row_name, 0))
            unlocked = points >= (total_required if total_required > 0 else 1)
            rows.append(
                {
                    "row_name": row_name,
                    "display_name": display_name,
                    "points": points,
                    "required": total_required,
                    "unlocked": unlocked,
                    "maps": maps,
                    "biomes": biomes,
                    "maps_text": maps_text,
                    "biomes_text": biomes_text,
                    "is_boss": bool(meta.is_boss) if isinstance(meta, GameBestiaryEntry) else False,
                }
            )
        rows.sort(key=lambda x: (x["display_name"].lower(), x["row_name"].lower()))
        return rows

    def _apply_medal_filter(self) -> None:
        rows = self._medal_rows()
        q = self.search_medals.text().strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if self._search_matches(
                    q,
                    r["display_name"],
                    r["row_name"],
                    r["points"],
                    r["required"],
                    r["maps_text"],
                    r["biomes_text"],
                    "boss" if r["is_boss"] else "",
                    "1" if r["unlocked"] else "0",
                )
            ]

        self._visible_medal_rows = [r["row_name"] for r in rows]
        self._populating_medals = True
        try:
            self.tbl_medals.setRowCount(0)
            for rec in rows:
                row = self.tbl_medals.rowCount()
                self.tbl_medals.insertRow(row)

                it_done = QTableWidgetItem("")
                it_done.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                it_done.setCheckState(Qt.Checked if rec["unlocked"] else Qt.Unchecked)
                it_done.setData(Qt.UserRole, rec["row_name"])
                it_done.setData(Qt.UserRole + 1, int(rec["required"]))
                self.tbl_medals.setItem(row, 0, it_done)

                it_name = QTableWidgetItem(rec["display_name"])
                it_name.setToolTip(self._bestiary_tooltip(rec))
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
                self.tbl_medals.setItem(row, 1, it_name)

                it_points = QTableWidgetItem(str(int(rec["points"])))
                it_points.setData(Qt.UserRole, rec["row_name"])
                self.tbl_medals.setItem(row, 2, it_points)
                it_required = QTableWidgetItem(str(int(rec["required"])))
                it_required.setFlags(it_required.flags() & ~Qt.ItemIsEditable)
                self.tbl_medals.setItem(row, 3, it_required)
                it_row = QTableWidgetItem(rec["row_name"])
                it_row.setFlags(it_row.flags() & ~Qt.ItemIsEditable)
                self.tbl_medals.setItem(row, 4, it_row)
        finally:
            self._populating_medals = False

    def _medal_item_changed(self, item: Optional[QTableWidgetItem]) -> None:
        if self._populating_medals or not item:
            return

        changed = False
        if item.column() == 0:
            row_name = item.data(Qt.UserRole)
            required = item.data(Qt.UserRole + 1)
            if not isinstance(row_name, str) or not row_name:
                return
            enabled = item.checkState() in (Qt.Checked, Qt.Checked.value)
            target = int(required) if isinstance(required, int) and required > 0 else 1
            changed = self.model.set_bestiary_points(row_name, target if enabled else 0)
        elif item.column() == 2:
            row_name = item.data(Qt.UserRole)
            if not isinstance(row_name, str) or not row_name:
                return
            try:
                points = int(item.text().strip() or "0")
            except Exception:
                QMessageBox.warning(
                    self,
                    "Медаль",
                    f"Очки для `{row_name}` должны быть целым числом.",
                )
                self._apply_medal_filter()
                return
            changed = self.model.set_bestiary_points(row_name, points)
        else:
            return

        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    def _unlock_visible_medals(self) -> None:
        rows = {r["row_name"]: r for r in self._medal_rows()}
        changed = False
        for row_name in self._visible_medal_rows:
            rec = rows.get(row_name)
            if not rec:
                continue
            target = int(rec["required"]) if int(rec["required"]) > 0 else 1
            changed = self.model.set_bestiary_points(row_name, target) or changed
        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()

    def _lock_visible_medals(self) -> None:
        changed = False
        for row_name in self._visible_medal_rows:
            changed = self.model.set_bestiary_points(row_name, 0) or changed
        if changed:
            self.mark_dirty()
        self._refresh_bestiary_views()
        self._apply_medal_filter()


class PlayerTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None
        self._char: Optional[Dict[str, Any]] = None
        self._populating_char_form = False
        self._skills_catalog: List[Tuple[str, str, str, str, int]] = (
            []
        )  # (tree, title, row_name, desc, max_rank)
        self._blueprints_catalog: List[Tuple[str, str, str, str, int]] = (
            []
        )  # (tree, title, row_name, desc, max_rank)
        self._populating_blueprints = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#B5BAC1;")
        top.addWidget(self.info, 1)

        top.addWidget(QLabel("Персонаж:"))
        self.cmb_char = QComboBox()
        self.cmb_char.currentIndexChanged.connect(self._char_changed)
        top.addWidget(self.cmb_char)

        root.addLayout(top)

        self.split = QSplitter(Qt.Horizontal)
        root.addWidget(self.split, 1)

        # Left: character settings
        char_box = QGroupBox("Персонаж")
        char_form = QFormLayout(char_box)
        char_form.setContentsMargins(8, 10, 8, 8)
        char_form.setHorizontalSpacing(10)
        char_form.setVerticalSpacing(6)

        self.ed_char_name = QLineEdit()
        self.ed_char_name.setPlaceholderText("Имя персонажа…")
        self.ed_char_name.textEdited.connect(self._char_name_changed)

        self.sb_char_xp = QSpinBox()
        self.sb_char_xp.setRange(0, 2_000_000_000)
        self.sb_char_xp.valueChanged.connect(self._char_xp_changed)
        self.sb_char_xp.setToolTip(
            "В сейве хранится XP. Уровень в игре считается из XP (после загрузки сохранения)."
        )

        self.lbl_char_xp_level = QLabel("")
        self.lbl_char_xp_level.setStyleSheet("color:#B5BAC1;")

        self.sb_char_level = QSpinBox()
        self.sb_char_level.setRange(0, 1000)
        self.sb_char_level.valueChanged.connect(self._char_level_changed)
        self.sb_char_level.setToolTip("Уровень будет пересчитан в XP и записан в сейв.")

        self.sb_char_xp_debt = QSpinBox()
        self.sb_char_xp_debt.setRange(0, 2_000_000_000)
        self.sb_char_xp_debt.valueChanged.connect(self._char_xp_debt_changed)

        self.lbl_skill_points = QLabel("")
        self.lbl_skill_points.setStyleSheet("color:#B5BAC1;")
        self.lbl_skill_points.setToolTip(
            "Оценка на основе уровня и потраченных талантов (Characters.json + Profile.json)."
        )

        self.lbl_blueprint_points = QLabel("")
        self.lbl_blueprint_points.setStyleSheet("color:#B5BAC1;")
        self.lbl_blueprint_points.setToolTip(
            "Оценка на основе уровня и потраченных чертежей (Characters.json)."
        )

        xp_row = QWidget()
        xp_lay = QHBoxLayout(xp_row)
        xp_lay.setContentsMargins(0, 0, 0, 0)
        xp_lay.setSpacing(8)
        xp_lay.addWidget(self.sb_char_xp, 1)
        xp_lay.addWidget(self.lbl_char_xp_level, 0)

        char_form.addRow("Имя", self.ed_char_name)
        char_form.addRow("Уровень", self.sb_char_level)
        char_form.addRow("XP", xp_row)
        char_form.addRow("XP_Debt", self.sb_char_xp_debt)
        char_form.addRow("Очки навыков (доступно)", self.lbl_skill_points)
        char_form.addRow("Очки чертежей (доступно)", self.lbl_blueprint_points)

        self.split.addWidget(char_box)

        # Skills (talents)
        skills_box = QWidget()
        skills_root = QVBoxLayout(skills_box)
        skills_root.setContentsMargins(0, 0, 0, 0)
        skills_root.setSpacing(6)

        skills_top = QHBoxLayout()
        skills_top.addWidget(QLabel("Дерево:"))
        self.cmb_skill_tree = QComboBox()
        self.cmb_skill_tree.currentIndexChanged.connect(self._apply_skill_filter)
        skills_top.addWidget(self.cmb_skill_tree)
        skills_top.addWidget(QLabel("Поиск:"))
        self.search_skills = QLineEdit()
        self.search_skills.setPlaceholderText("Поиск по навыкам/RowName…")
        self.search_skills.setFixedWidth(240)
        self.search_skills.textChanged.connect(self._apply_skill_filter)
        skills_top.addWidget(self.search_skills)
        self.cb_all_skills = QCheckBox("Все навыки MAX")
        self.cb_all_skills.setToolTip("Прокачать все навыки персонажа до максимума")
        self.cb_all_skills.stateChanged.connect(self._all_skills_max)
        skills_top.addWidget(self.cb_all_skills)
        skills_top.addStretch(1)
        skills_root.addLayout(skills_top)

        self.tbl_skills = QTableWidget(0, 3)
        self.tbl_skills.setHorizontalHeaderLabels(["Дерево", "Навык", "Ранг"])
        self.tbl_skills.verticalHeader().setVisible(False)
        self.tbl_skills.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_skills.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_skills.setTextElideMode(Qt.ElideNone)
        self.tbl_skills.setAlternatingRowColors(True)
        hdr = self.tbl_skills.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        skills_root.addWidget(self.tbl_skills, 1)

        # Blueprints
        bp_box = QWidget()
        bp_root = QVBoxLayout(bp_box)
        bp_root.setContentsMargins(0, 0, 0, 0)
        bp_root.setSpacing(6)

        bp_top = QHBoxLayout()
        bp_top.addWidget(QLabel("Дерево:"))
        self.cmb_bp_tree = QComboBox()
        self.cmb_bp_tree.currentIndexChanged.connect(self._apply_blueprint_filter)
        bp_top.addWidget(self.cmb_bp_tree)
        bp_top.addWidget(QLabel("Поиск:"))
        self.search_bp = QLineEdit()
        self.search_bp.setPlaceholderText("Поиск по чертежам/RowName…")
        self.search_bp.setFixedWidth(240)
        self.search_bp.textChanged.connect(self._apply_blueprint_filter)
        bp_top.addWidget(self.search_bp)
        self.cb_all_blueprints = QCheckBox("Открыть все чертежи")
        self.cb_all_blueprints.setToolTip("Открыть все предметы из веток Blueprint_*")
        self.cb_all_blueprints.stateChanged.connect(self._all_blueprints_unlock)
        bp_top.addWidget(self.cb_all_blueprints)
        bp_top.addStretch(1)
        bp_root.addLayout(bp_top)

        self.tbl_blueprints = QTableWidget(0, 3)
        self.tbl_blueprints.setHorizontalHeaderLabels(["Дерево", "Чертёж", "Открыто"])
        self.tbl_blueprints.verticalHeader().setVisible(False)
        self.tbl_blueprints.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_blueprints.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_blueprints.setTextElideMode(Qt.ElideNone)
        self.tbl_blueprints.setAlternatingRowColors(True)
        hdr2 = self.tbl_blueprints.horizontalHeader()
        hdr2.setStretchLastSection(False)
        hdr2.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr2.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_blueprints.itemChanged.connect(self._on_blueprint_item_changed)
        bp_root.addWidget(self.tbl_blueprints, 1)

        right_tabs = QTabWidget()
        right_tabs.addTab(skills_box, "Навыки")
        right_tabs.addTab(bp_box, "Чертежи")
        self.split.addWidget(right_tabs)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 3)

        self.setEnabled(False)

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data
        self._rebuild_catalog()
        if self._char:
            self._rebuild_tables()
        self._update_xp_level_label()

    def load(self) -> None:
        if not self.model.characters_path:
            self.info.setText("Characters.json не найден в выбранном сейве.")
            self.cmb_char.clear()
            self.tbl_skills.setRowCount(0)
            self.tbl_blueprints.setRowCount(0)
            self._char = None
            self._load_char_form()
            self.setEnabled(False)
            return

        if not self.model.characters:
            self.info.setText("Characters.json найден, но персонажи не распознаны.")
            self.cmb_char.clear()
            self.tbl_skills.setRowCount(0)
            self.tbl_blueprints.setRowCount(0)
            self._char = None
            self._load_char_form()
            self.setEnabled(False)
            return

        self.setEnabled(True)
        self.info.setText("")

        self.cmb_char.blockSignals(True)
        self.cmb_char.clear()
        for idx, ch in enumerate(self.model.characters):
            if not isinstance(ch, dict):
                continue
            nm = ch.get("CharacterName", "")
            nm_s = nm if isinstance(nm, str) and nm else f"Character #{idx+1}"
            slot = ch.get("ChrSlot", None)
            slot_s = f"slot {slot}" if isinstance(slot, int) else f"#{idx+1}"
            self.cmb_char.addItem(f"{nm_s} ({slot_s})", idx)
        self.cmb_char.blockSignals(False)

        self.search_skills.blockSignals(True)
        self.search_skills.clear()
        self.search_skills.blockSignals(False)
        self.search_bp.blockSignals(True)
        self.search_bp.clear()
        self.search_bp.blockSignals(False)

        self._char_changed()

    def _rebuild_catalog(self) -> None:
        def is_player_skill_tree(tree: str) -> bool:
            t = (tree or "").strip()
            return t.startswith(
                ("Survival_", "Combat_", "Construction_", "Solo_", "Adventure_")
            )

        def is_blueprint_tree(tree: str) -> bool:
            return (tree or "").strip().startswith("Blueprint_")

        skills: List[Tuple[str, str, str, str, int]] = []
        blueprints: List[Tuple[str, str, str, str, int]] = []
        seen: set[str] = set()

        if self._game_data:
            for rn, meta in self._game_data.talents.items():
                tree = (meta.talent_tree or "").strip()
                if not tree:
                    continue
                title = (meta.display_name or "").strip() or rn
                desc = (meta.description or "").strip()
                max_rank = int(meta.max_rank or 1)

                if is_blueprint_tree(tree):
                    blueprints.append((tree, title, rn, desc, max_rank))
                    seen.add(rn)
                    continue
                if is_player_skill_tree(tree):
                    skills.append((tree, title, rn, desc, max_rank))
                    seen.add(rn)
                    continue

        if self._char:
            for rec in (
                self._char.get("Talents", [])
                if isinstance(self._char.get("Talents"), list)
                else []
            ):
                if not isinstance(rec, dict):
                    continue
                rn = rec.get("RowName")
                if not isinstance(rn, str) or not rn or rn in seen:
                    continue
                # Unknown entries in save: keep editable anyway.
                if rn.startswith("Blueprint_"):
                    blueprints.append(("", rn, rn, "", 1))
                else:
                    skills.append(("", rn, rn, "", 100))
                seen.add(rn)

        skills.sort(key=lambda x: ((x[0] or "zz").lower(), x[1].lower(), x[2].lower()))
        blueprints.sort(
            key=lambda x: ((x[0] or "zz").lower(), x[1].lower(), x[2].lower())
        )
        self._skills_catalog = skills
        self._blueprints_catalog = blueprints

        # Rebuild tree filters (keep selection when possible).
        cur_skill = self.cmb_skill_tree.currentData()
        cur_bp = self.cmb_bp_tree.currentData()

        self.cmb_skill_tree.blockSignals(True)
        self.cmb_skill_tree.clear()
        self.cmb_skill_tree.addItem("Все деревья", None)
        if any(not t for t, *_ in self._skills_catalog):
            self.cmb_skill_tree.addItem("Неизвестно", "")
        for t in sorted({t for t, *_ in self._skills_catalog if t}):
            self.cmb_skill_tree.addItem(self._ru_tree_label(t), t)
        idx = self.cmb_skill_tree.findData(cur_skill)
        if idx >= 0:
            self.cmb_skill_tree.setCurrentIndex(idx)
        self.cmb_skill_tree.blockSignals(False)

        self.cmb_bp_tree.blockSignals(True)
        self.cmb_bp_tree.clear()
        self.cmb_bp_tree.addItem("Все деревья", None)
        if any(not t for t, *_ in self._blueprints_catalog):
            self.cmb_bp_tree.addItem("Неизвестно", "")
        for t in sorted({t for t, *_ in self._blueprints_catalog if t}):
            self.cmb_bp_tree.addItem(self._ru_tree_label(t), t)
        idx2 = self.cmb_bp_tree.findData(cur_bp)
        if idx2 >= 0:
            self.cmb_bp_tree.setCurrentIndex(idx2)
        self.cmb_bp_tree.blockSignals(False)

    def _rank_map(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if not self._char:
            return out
        lst = self._char.get("Talents", [])
        if not isinstance(lst, list):
            return out
        for rec in lst:
            if not isinstance(rec, dict):
                continue
            rn = rec.get("RowName")
            rk = rec.get("Rank")
            if isinstance(rn, str) and rn and isinstance(rk, int):
                out[rn] = int(rk)
        return out

    def _set_rank(self, row_name: str, rank: int) -> bool:
        if not self._char:
            return False
        lst = self._char.get("Talents")
        if not isinstance(lst, list):
            self._char["Talents"] = lst = []
        # update/remove existing
        for i, rec in enumerate(list(lst)):
            if not isinstance(rec, dict):
                continue
            if rec.get("RowName") != row_name:
                continue
            old = rec.get("Rank")
            if rank <= 0:
                del lst[i]
                return True
            if old != int(rank):
                rec["Rank"] = int(rank)
                return True
            return False
        # add new
        if rank > 0:
            lst.append({"RowName": row_name, "Rank": int(rank)})
            return True
        return False

    def _char_changed(self) -> None:
        idx = self.cmb_char.currentData()
        if not isinstance(idx, int) or not (0 <= idx < len(self.model.characters)):
            self._char = None
            self.tbl_skills.setRowCount(0)
            self.tbl_blueprints.setRowCount(0)
            self._load_char_form()
            return
        ch = self.model.characters[idx]
        self._char = ch if isinstance(ch, dict) else None
        self._load_char_form()
        self._rebuild_catalog()
        self._rebuild_tables()

    def _load_char_form(self) -> None:
        self._populating_char_form = True
        try:
            enabled = bool(self._char)
            for w in (
                self.ed_char_name,
                self.sb_char_level,
                self.sb_char_xp,
                self.lbl_char_xp_level,
                self.sb_char_xp_debt,
                self.lbl_skill_points,
                self.lbl_blueprint_points,
            ):
                w.setEnabled(enabled)

            if not self._char:
                self.ed_char_name.setText("")
                self.sb_char_level.setValue(0)
                self.sb_char_xp.setValue(0)
                self.lbl_char_xp_level.setText("")
                self.sb_char_xp_debt.setValue(0)
                self.lbl_skill_points.setText("")
                self.lbl_blueprint_points.setText("")
                return

            ch = self._char
            nm = ch.get("CharacterName", "")
            self.ed_char_name.setText(nm if isinstance(nm, str) else "")

            xp = ch.get("XP", 0)
            self.sb_char_xp.setValue(int(xp) if isinstance(xp, int) else 0)
            self._update_xp_level_label()

            debt = ch.get("XP_Debt", 0)
            self.sb_char_xp_debt.setValue(int(debt) if isinstance(debt, int) else 0)
        finally:
            self._populating_char_form = False

    def _refresh_char_combo_title(self) -> None:
        if not self._char:
            return
        row = self.cmb_char.currentIndex()
        if row < 0:
            return
        nm = self._char.get("CharacterName", "")
        nm_s = nm if isinstance(nm, str) and nm else f"Character #{row + 1}"
        slot = self._char.get("ChrSlot", None)
        slot_s = f"slot {slot}" if isinstance(slot, int) else f"#{row + 1}"
        self.cmb_char.setItemText(row, f"{nm_s} ({slot_s})")

    def _char_name_changed(self, text: str) -> None:
        if self._populating_char_form or not self._char:
            return
        self._char["CharacterName"] = str(text)
        self.model.dirty_characters = True
        self._refresh_char_combo_title()
        self.mark_dirty()

    def _char_xp_changed(self, v: int) -> None:
        if self._populating_char_form or not self._char:
            return
        self._char["XP"] = int(v)
        self.model.dirty_characters = True
        self._update_xp_level_label()
        self.mark_dirty()

    def _char_xp_debt_changed(self, v: int) -> None:
        if self._populating_char_form or not self._char:
            return
        self._char["XP_Debt"] = int(v)
        self.model.dirty_characters = True
        self.mark_dirty()

    def _update_xp_level_label(self) -> None:
        if not self._char:
            self.lbl_char_xp_level.setText("")
            self._update_points_labels()
            return

        xp = int(self.sb_char_xp.value())
        curve = DEFAULT_PLAYER_XP_CURVE
        if self._game_data:
            cached = self._game_data._curve_cache.get("C_PlayerExperienceGrowth")
            if isinstance(cached, ExperienceCurve):
                curve = cached

        lvl = curve.level_for_xp(xp, max_level=1000)
        self.lbl_char_xp_level.setText(f"ур. {lvl}")
        self.sb_char_level.blockSignals(True)
        self.sb_char_level.setValue(int(lvl))
        self.sb_char_level.blockSignals(False)
        self._update_points_labels()

    def _char_level_changed(self, lvl: int) -> None:
        if self._populating_char_form or not self._char:
            return

        curve = DEFAULT_PLAYER_XP_CURVE
        if self._game_data:
            cached = self._game_data._curve_cache.get("C_PlayerExperienceGrowth")
            if isinstance(cached, ExperienceCurve):
                curve = cached

        try:
            xp = int(curve.value_at(float(int(lvl))))
        except Exception:
            xp = 0

        self.sb_char_xp.setValue(int(xp))

    @staticmethod
    def _ru_tree_label(tree: str) -> str:
        t = (tree or "").strip()
        if not t:
            return "Неизвестно"

        direct = {
            "Survival_Resources": "Выживание: Ресурсы",
            "Survival_Hunting": "Выживание: Охота",
            "Survival_Exploration": "Выживание: Исследование",
            "Survival_Produce": "Выживание: Производство",
            "Combat_Blades": "Бой: Клинки",
            "Combat_Bows": "Бой: Луки",
            "Combat_Firearms": "Бой: Огнестрел",
            "Combat_Spears": "Бой: Копья",
            "Construction_Building": "Строительство: Постройки",
            "Construction_Tools": "Строительство: Инструменты",
            "Construction_Repairing": "Строительство: Ремонт",
            "Construction_Husbandry": "Строительство: Животноводство",
            "Adventure_Fishing": "Приключения: Рыбалка",
            "Solo_Player": "Одиночка: Игрок",
            "Blueprint_Bosses": "Чертежи: Боссы",
            "Blueprint_T1_Player": "Чертежи: T1 (Игрок)",
            "Blueprint_T2_Crafting": "Чертежи: T2 (Ремесло)",
            "Blueprint_T3_Machine": "Чертежи: T3 (Машины)",
            "Blueprint_T4_Fabricator": "Чертежи: T4 (Фабрикатор)",
            "Blueprint_T5_Manufacturer": "Чертежи: T5 (Производство)",
        }
        if t in direct:
            return direct[t]

        prefix_map = {
            "Survival_": "Выживание",
            "Combat_": "Бой",
            "Construction_": "Строительство",
            "Adventure_": "Приключения",
            "Solo_": "Одиночка",
            "Blueprint_": "Чертежи",
        }
        word_map = {
            "Resources": "Ресурсы",
            "Hunting": "Охота",
            "Exploration": "Исследование",
            "Produce": "Производство",
            "Firearms": "Огнестрел",
            "Blades": "Клинки",
            "Bows": "Луки",
            "Spears": "Копья",
            "Building": "Постройки",
            "Tools": "Инструменты",
            "Repairing": "Ремонт",
            "Husbandry": "Животноводство",
            "Fishing": "Рыбалка",
            "Bosses": "Боссы",
            "Player": "Игрок",
            "Crafting": "Ремесло",
            "Machine": "Машины",
            "Fabricator": "Фабрикатор",
            "Manufacturer": "Производство",
        }
        for pref, pref_ru in prefix_map.items():
            if t.startswith(pref):
                rest = t[len(pref) :].replace("_", " ").strip()
                if rest:
                    rest_ru = " ".join(word_map.get(w, w) for w in rest.split())
                    return f"{pref_ru}: {rest_ru}"
                return pref_ru
        return t.replace("_", " ")

    def _rebuild_tables(self) -> None:
        self._rebuild_skills_table()
        self._rebuild_blueprints_table()
        self._apply_skill_filter()
        self._apply_blueprint_filter()
        self._sync_bulk_checks()

    def _rebuild_skills_table(self) -> None:
        self.tbl_skills.blockSignals(True)
        self.tbl_skills.setRowCount(0)

        ranks = self._rank_map()
        for tree, title, rn, desc, max_rank in self._skills_catalog:
            row = self.tbl_skills.rowCount()
            self.tbl_skills.insertRow(row)

            tree_key = tree if isinstance(tree, str) else ""
            it_tree = QTableWidgetItem(self._ru_tree_label(tree_key))
            it_tree.setData(Qt.UserRole, tree_key)
            it_tree.setFlags(it_tree.flags() & ~Qt.ItemIsEditable)

            it_name = QTableWidgetItem(title)
            it_name.setData(Qt.UserRole, rn)
            tip = rn
            if desc:
                tip += "\n" + desc
            it_name.setToolTip(tip)
            it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)

            sb = QSpinBox()
            sb.setRange(0, max(0, int(max_rank)))
            sb.setAlignment(Qt.AlignCenter)
            sb.setButtonSymbols(QAbstractSpinBox.NoButtons)
            sb.setMinimumWidth(86)
            sb.setValue(min(int(ranks.get(rn, 0)), int(max_rank)))
            sb.setToolTip(f"{rn}\nMax: {int(max_rank)}")
            sb.valueChanged.connect(lambda v, r=rn: self._on_skill_rank_changed(r, v))

            self.tbl_skills.setItem(row, 0, it_tree)
            self.tbl_skills.setItem(row, 1, it_name)
            self.tbl_skills.setCellWidget(row, 2, sb)

        self.tbl_skills.blockSignals(False)
        self.tbl_skills.resizeColumnToContents(0)
        self.tbl_skills.resizeColumnToContents(2)
        self.tbl_skills.resizeRowsToContents()

    def _rebuild_blueprints_table(self) -> None:
        self._populating_blueprints = True
        self.tbl_blueprints.blockSignals(True)
        self.tbl_blueprints.setRowCount(0)

        ranks = self._rank_map()
        for tree, title, rn, desc, _max_rank in self._blueprints_catalog:
            row = self.tbl_blueprints.rowCount()
            self.tbl_blueprints.insertRow(row)

            tree_key = tree if isinstance(tree, str) else ""
            it_tree = QTableWidgetItem(self._ru_tree_label(tree_key))
            it_tree.setData(Qt.UserRole, tree_key)
            it_tree.setFlags(it_tree.flags() & ~Qt.ItemIsEditable)

            it_name = QTableWidgetItem(title)
            it_name.setData(Qt.UserRole, rn)
            tip = rn
            if desc:
                tip += "\n" + desc
            it_name.setToolTip(tip)
            it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)

            it_on = QTableWidgetItem("")
            it_on.setTextAlignment(Qt.AlignCenter)
            it_on.setFlags(it_on.flags() | Qt.ItemIsUserCheckable)
            it_on.setCheckState(
                Qt.Checked if int(ranks.get(rn, 0)) > 0 else Qt.Unchecked
            )

            self.tbl_blueprints.setItem(row, 0, it_tree)
            self.tbl_blueprints.setItem(row, 1, it_name)
            self.tbl_blueprints.setItem(row, 2, it_on)

        self.tbl_blueprints.blockSignals(False)
        self._populating_blueprints = False
        self.tbl_blueprints.resizeColumnToContents(0)
        self.tbl_blueprints.resizeColumnToContents(2)
        self.tbl_blueprints.resizeRowsToContents()

    def _apply_skill_filter(self) -> None:
        q = self.search_skills.text().strip().lower()
        sel = self.cmb_skill_tree.currentData()
        for r in range(self.tbl_skills.rowCount()):
            it_tree = self.tbl_skills.item(r, 0)
            it_name = self.tbl_skills.item(r, 1)
            if not it_tree or not it_name:
                continue
            tree_key = it_tree.data(Qt.UserRole)
            if sel is not None and tree_key != sel:
                self.tbl_skills.setRowHidden(r, True)
                continue
            rn = it_name.data(Qt.UserRole)
            rn_s = rn if isinstance(rn, str) else ""
            hay = f"{it_tree.text()} {it_name.text()} {rn_s}".lower()
            self.tbl_skills.setRowHidden(r, bool(q and q not in hay))

    def _apply_blueprint_filter(self) -> None:
        q = self.search_bp.text().strip().lower()
        sel = self.cmb_bp_tree.currentData()
        for r in range(self.tbl_blueprints.rowCount()):
            it_tree = self.tbl_blueprints.item(r, 0)
            it_name = self.tbl_blueprints.item(r, 1)
            if not it_tree or not it_name:
                continue
            tree_key = it_tree.data(Qt.UserRole)
            if sel is not None and tree_key != sel:
                self.tbl_blueprints.setRowHidden(r, True)
                continue
            rn = it_name.data(Qt.UserRole)
            rn_s = rn if isinstance(rn, str) else ""
            hay = f"{it_tree.text()} {it_name.text()} {rn_s}".lower()
            self.tbl_blueprints.setRowHidden(r, bool(q and q not in hay))

    def _on_blueprint_item_changed(self, item: QTableWidgetItem) -> None:
        if self._populating_blueprints:
            return
        if not item or item.column() != 2 or not self._char:
            return
        it_name = self.tbl_blueprints.item(item.row(), 1)
        if not it_name:
            return
        rn = it_name.data(Qt.UserRole)
        if not isinstance(rn, str) or not rn:
            return
        enabled = item.checkState() == Qt.Checked
        if self._set_rank(rn, 1 if enabled else 0):
            self.model.dirty_characters = True
            self.mark_dirty()
        self._sync_bulk_checks()
        self._update_points_labels()

    def _on_skill_rank_changed(self, row_name: str, v: int) -> None:
        if not self._char:
            return
        if self._set_rank(row_name, int(v)):
            self.model.dirty_characters = True
            self.mark_dirty()
        self._sync_bulk_checks()
        self._update_points_labels()

    def _bonus_talent_points_from_profile(self) -> int:
        bonus = 0
        flags = self.model.flags_set() if self.model else set()
        rx = re.compile(r"^\s*(\d+)\s+Extra Character Talent Points\b", re.IGNORECASE)
        for row in getattr(self.model, "unlock_rows", []) if self.model else []:
            if not isinstance(row, UnlockRow):
                continue
            if int(row.flag) not in flags:
                continue
            nm = (row.unlock_name or "").strip()
            m = rx.match(nm)
            if not m:
                continue
            try:
                bonus += int(m.group(1))
            except Exception:
                continue
        return int(bonus)

    def _update_points_labels(self) -> None:
        if not self._char:
            for w in (self.lbl_skill_points, self.lbl_blueprint_points):
                w.setText("")
            return

        lvl = int(self.sb_char_level.value())
        total_skill = int(max(0, lvl)) + int(self._bonus_talent_points_from_profile())
        total_blue = int(max(0, lvl))

        spent_skill = 0
        spent_blue = 0
        lst = self._char.get("Talents", [])
        if isinstance(lst, list):
            for rec in lst:
                if not isinstance(rec, dict):
                    continue
                rn = rec.get("RowName")
                rk = rec.get("Rank", 0)
                if (
                    not isinstance(rn, str)
                    or not rn
                    or not isinstance(rk, int)
                    or rk <= 0
                ):
                    continue

                tree = ""
                if self._game_data and rn in self._game_data.talents:
                    tree = (self._game_data.talents[rn].talent_tree or "").strip()
                if tree.startswith("Blueprint_") or rn.startswith("Blueprint_"):
                    spent_blue += int(rk)
                else:
                    spent_skill += int(rk)

        avail_skill = int(total_skill - spent_skill)
        avail_blue = int(total_blue - spent_blue)

        self.lbl_skill_points.setText(str(avail_skill))
        self.lbl_skill_points.setToolTip(
            f"Доступно: {avail_skill}  (потрачено: {spent_skill} / всего: {total_skill})"
        )

        self.lbl_blueprint_points.setText(str(avail_blue))
        self.lbl_blueprint_points.setToolTip(
            f"Доступно: {avail_blue}  (потрачено: {spent_blue} / всего: {total_blue})"
        )

    def _sync_bulk_checks(self) -> None:
        if not self._char:
            for cb in (self.cb_all_skills, self.cb_all_blueprints):
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
                cb.setEnabled(False)
            return

        self.cb_all_skills.setEnabled(True)
        self.cb_all_blueprints.setEnabled(True)

        ranks = self._rank_map()

        skills_all = True
        for tree, _title, rn, _desc, max_rank in self._skills_catalog:
            if not tree:
                continue
            if int(ranks.get(rn, 0)) != int(max_rank):
                skills_all = False
                break

        blue_all = True
        for tree, _title, rn, _desc, _max_rank in self._blueprints_catalog:
            if not tree:
                continue
            if int(ranks.get(rn, 0)) <= 0:
                blue_all = False
                break

        self.cb_all_skills.blockSignals(True)
        self.cb_all_skills.setChecked(skills_all)
        self.cb_all_skills.blockSignals(False)

        self.cb_all_blueprints.blockSignals(True)
        self.cb_all_blueprints.setChecked(blue_all)
        self.cb_all_blueprints.blockSignals(False)

    def _all_skills_max(self, st: int) -> None:
        if st not in (Qt.Checked, Qt.Checked.value):
            return
        if not self._char or not self._game_data:
            self._sync_bulk_checks()
            return
        self.cb_all_skills.setEnabled(False)
        QTimer.singleShot(0, self._all_skills_max_impl)

    def _all_skills_max_impl(self) -> None:
        try:
            if not self._char or not self._game_data:
                return
            changed = False
            for tree, _title, rn, _desc, max_rank in self._skills_catalog:
                if not tree:
                    continue
                if self._set_rank(rn, int(max_rank)):
                    changed = True
            if changed:
                self.model.dirty_characters = True
                self.mark_dirty()
            self._rebuild_tables()
        finally:
            self.cb_all_skills.setEnabled(True)

    def _all_blueprints_unlock(self, st: int) -> None:
        if st not in (Qt.Checked, Qt.Checked.value):
            return
        if not self._char or not self._game_data:
            self._sync_bulk_checks()
            return
        self.cb_all_blueprints.setEnabled(False)
        QTimer.singleShot(0, self._all_blueprints_unlock_impl)

    def _all_blueprints_unlock_impl(self) -> None:
        try:
            if not self._char or not self._game_data:
                return
            changed = False
            for tree, _title, rn, _desc, _max_rank in self._blueprints_catalog:
                if not tree:
                    continue
                if self._set_rank(rn, 1):
                    changed = True
            if changed:
                self.model.dirty_characters = True
                self.mark_dirty()
            self._rebuild_tables()
        finally:
            self.cb_all_blueprints.setEnabled(True)


class ItemDetails(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.item: Optional[Dict[str, Any]] = None
        self._read_only = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.title = QLabel("Выбери предмет слева.")
        self.title.setStyleSheet("color:#B5BAC1;")
        root.addWidget(self.title)

        self.form_box = QGroupBox("Основные параметры")
        form_lay = QFormLayout(self.form_box)
        form_lay.setLabelAlignment(Qt.AlignLeft)
        form_lay.setFormAlignment(Qt.AlignTop)

        def mk_spin() -> QSpinBox:
            sb = QSpinBox()
            sb.setRange(0, 10**9)
            return sb

        self.sb_stack = mk_spin()
        self.sb_dur = mk_spin()
        self.sb_mag = mk_spin()
        self.sb_fuel = mk_spin()

        self.sb_stack.setToolTip("ItemableStack")
        self.sb_dur.setToolTip("Durability")
        self.sb_mag.setToolTip("GunCurrentMagSize")
        self.sb_fuel.setToolTip("Fillable_StoredUnits")

        self.sb_stack.valueChanged.connect(lambda v: self._set_prop("ItemableStack", v))
        self.sb_dur.valueChanged.connect(lambda v: self._set_prop("Durability", v))
        self.sb_mag.valueChanged.connect(
            lambda v: self._set_prop("GunCurrentMagSize", v)
        )
        self.sb_fuel.valueChanged.connect(
            lambda v: self._set_prop("Fillable_StoredUnits", v)
        )

        form_lay.addRow("Количество", self.sb_stack)
        form_lay.addRow("Прочность", self.sb_dur)
        form_lay.addRow("Патроны", self.sb_mag)
        form_lay.addRow("Топливо/заряд", self.sb_fuel)

        root.addWidget(self.form_box)

        self.dyn_box = QGroupBox("Динамические свойства (ItemDynamicData)")
        dyn_root = QVBoxLayout(self.dyn_box)

        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels(["Свойство (PropertyType)", "Значение"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setTextElideMode(Qt.ElideNone)
        hdr = self.tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(
            QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed
        )
        self.tbl.cellChanged.connect(self._cell_changed)
        dyn_root.addWidget(self.tbl)

        add_row = QHBoxLayout()
        self.new_prop = QLineEdit()
        self.new_prop.setPlaceholderText(
            "Новое свойство (PropertyType, напр. Durability)"
        )
        self.new_val = QSpinBox()
        self.new_val.setRange(0, 10**9)
        self.btn_add_prop = QPushButton("Добавить/обновить")
        self.btn_add_prop.clicked.connect(self._add_prop)
        self.btn_del_prop = QPushButton("Удалить выбранный")
        self.btn_del_prop.clicked.connect(self._del_selected)
        add_row.addWidget(self.new_prop, 2)
        add_row.addWidget(self.new_val, 1)
        add_row.addWidget(self.btn_add_prop)
        add_row.addWidget(self.btn_del_prop)
        dyn_root.addLayout(add_row)

        root.addWidget(self.dyn_box, 1)

        self.setEnabled(False)

    def set_item(self, item: Optional[Dict[str, Any]], read_only: bool = False) -> None:
        self.item = item
        self._read_only = bool(read_only)
        if not item:
            self.setEnabled(False)
            self.title.setText("Выбери предмет слева.")
            return

        rn = SaveModel.item_rowname(item)
        guid = item.get("DatabaseGUID", "")
        guid_s = guid if isinstance(guid, str) and guid else "-"
        world = item.get("_world")
        extra = ""
        if isinstance(world, dict):
            extra = (
                f"<br><span style='color:#B5BAC1'>Мир:</span> {world.get('prospect_id', '-')}"
                f"<br><span style='color:#B5BAC1'>Контейнер:</span> {world.get('inventory_info', '-')}"
                f" ({world.get('container_index', '-')})"
                f"<br><span style='color:#B5BAC1'>Слот:</span> {world.get('slot_location', '-')}"
            )
        ro = (
            "<br><span style='color:#B5BAC1'>Режим:</span> только чтение"
            if self._read_only
            else ""
        )
        self.title.setText(
            f"<b>{SaveModel.item_pretty_name(rn)}</b>"
            f"<br><span style='color:#B5BAC1'>ID (RowName):</span> {rn}"
            f"<br><span style='color:#B5BAC1'>GUID:</span> {guid_s}"
            f"{extra}{ro}"
        )
        self.setEnabled(True)

        editable = not self._read_only
        try:
            for sb in (self.sb_stack, self.sb_dur, self.sb_mag, self.sb_fuel):
                sb.setReadOnly(not editable)
        except Exception:
            for sb in (self.sb_stack, self.sb_dur, self.sb_mag, self.sb_fuel):
                sb.setEnabled(editable)

        self.tbl.setEditTriggers(
            (QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
            if editable
            else QAbstractItemView.NoEditTriggers
        )
        self.new_prop.setEnabled(editable)
        self.new_val.setEnabled(editable)
        self.btn_add_prop.setEnabled(editable)
        self.btn_del_prop.setEnabled(editable)
        self._refresh_all()

    def _refresh_all(self) -> None:
        if not self.item:
            return

        self.sb_stack.blockSignals(True)
        self.sb_dur.blockSignals(True)
        self.sb_mag.blockSignals(True)
        self.sb_fuel.blockSignals(True)
        self.sb_stack.setValue(SaveModel.get_dyn(self.item, "ItemableStack", 1))
        self.sb_dur.setValue(SaveModel.get_dyn(self.item, "Durability", 0))
        self.sb_mag.setValue(SaveModel.get_dyn(self.item, "GunCurrentMagSize", 0))
        self.sb_fuel.setValue(SaveModel.get_dyn(self.item, "Fillable_StoredUnits", 0))
        self.sb_stack.blockSignals(False)
        self.sb_dur.blockSignals(False)
        self.sb_mag.blockSignals(False)
        self.sb_fuel.blockSignals(False)

        dyn = self.item.get("ItemDynamicData", [])
        if not isinstance(dyn, list):
            dyn = []
        self.tbl.blockSignals(True)
        self.tbl.setRowCount(0)
        for p in dyn:
            if not isinstance(p, dict):
                continue
            pt = p.get("PropertyType")
            v = p.get("Value")
            if not isinstance(pt, str):
                continue
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            it_pt = QTableWidgetItem(pt)
            it_pt.setFlags(it_pt.flags() & ~Qt.ItemIsEditable)
            it_val = QTableWidgetItem(str(v if v is not None else 0))
            self.tbl.setItem(row, 0, it_pt)
            self.tbl.setItem(row, 1, it_val)
        self.tbl.blockSignals(False)
        self.tbl.resizeColumnToContents(0)

    def refresh(self) -> None:
        self._refresh_all()

    def _set_prop(self, prop: str, v: int) -> None:
        if self._read_only:
            return
        if not self.item:
            return
        SaveModel.set_dyn(self.item, prop, int(v))
        self._refresh_all()
        self.changed.emit()

    def _cell_changed(self, row: int, col: int) -> None:
        if self._read_only:
            return
        if col != 1 or not self.item:
            return
        pt_item = self.tbl.item(row, 0)
        v_item = self.tbl.item(row, 1)
        if not pt_item or not v_item:
            return
        prop = pt_item.text()
        txt = v_item.text().strip()
        try:
            val = int(txt)
        except Exception:
            return
        SaveModel.set_dyn(self.item, prop, val)
        self.changed.emit()

    def _add_prop(self) -> None:
        if self._read_only:
            return
        if not self.item:
            return
        prop = self.new_prop.text().strip()
        if not prop:
            return
        SaveModel.set_dyn(self.item, prop, int(self.new_val.value()))
        self.new_prop.clear()
        self._refresh_all()
        self.changed.emit()

    def _del_selected(self) -> None:
        if self._read_only:
            return
        if not self.item:
            return
        r = self.tbl.currentRow()
        if r < 0:
            return
        pt_item = self.tbl.item(r, 0)
        if not pt_item:
            return
        prop = pt_item.text()
        SaveModel.remove_dyn(self.item, prop)
        self._refresh_all()
        self.changed.emit()


class InventoryTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None

        self._items: List[Dict[str, Any]] = []
        self._kind: str = "meta"

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._rebuild)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию/RowName…")
        self.search.textChanged.connect(self._apply_filter)
        self.btn_add = QPushButton("Добавить предмет")
        self.btn_add.clicked.connect(self._add_item)

        top.addWidget(QLabel("Источник:"))
        top.addWidget(self.source, 2)
        top.addWidget(QLabel("Поиск:"))
        top.addWidget(self.search, 3)
        top.addWidget(self.btn_add)

        root.addLayout(top)

        self.split = QSplitter(Qt.Horizontal)
        root.addWidget(self.split, 1)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        self.items_tbl = QTableWidget(0, 5)
        self.items_tbl.setHorizontalHeaderLabels(
            ["Предмет", "Кол-во", "Прочн.", "Пат.", "Топл."]
        )
        self.items_tbl.verticalHeader().setVisible(False)
        self.items_tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.items_tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.items_tbl.setTextElideMode(Qt.ElideNone)
        self.items_tbl.setSortingEnabled(True)
        header = self.items_tbl.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.items_tbl.currentCellChanged.connect(self._sel_changed)
        self.items_tbl.cellChanged.connect(self._cell_changed)
        self.items_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.items_tbl.customContextMenuRequested.connect(self._items_context_menu)
        left_l.addWidget(self.items_tbl, 1)

        self.split.addWidget(left)

        self.details = ItemDetails()
        self.details.changed.connect(self._details_changed)
        self.split.addWidget(self.details)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data

    def load(self) -> None:
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("Орбитальный сташ (MetaInventory.json)", ("meta", None))

        loads = self.model.loadouts.get("Loadouts", [])
        if isinstance(loads, list):
            for i, lo in enumerate(loads):
                label = f"Снаряжение / Loadout #{i+1}"
                pid = (lo.get("AssociatedProspect") or {}).get("ProspectID", "")
                if isinstance(pid, str) and pid:
                    label += f"  [{pid}]"
                self.source.addItem(label, ("loadout", i))

        if self.model.prospect_paths:
            for p in self.model.prospect_paths:
                base = os.path.splitext(os.path.basename(p))[0]
                self.source.addItem(
                    f"Мир / Заказанные предметы (Prospects): {base}", ("prospect", p)
                )

        self.source.blockSignals(False)
        self._rebuild()

    def _items_for_source(self) -> Tuple[List[Dict[str, Any]], str]:
        kind, idx = self.source.currentData()
        if kind == "meta":
            items = self.model.meta.get("Items", [])
            return (items if isinstance(items, list) else []), "meta"
        if kind == "loadout" and isinstance(idx, int):
            loads = self.model.loadouts.get("Loadouts", [])
            if isinstance(loads, list) and 0 <= idx < len(loads):
                items = loads[idx].get("MetaItems", [])
                return (items if isinstance(items, list) else []), "loadouts"
        if kind == "prospect" and isinstance(idx, str) and idx:
            return self.model.list_world_items(idx), "prospect"
        return [], "unknown"

    def _rebuild(self) -> None:
        self._refresh_current_source(preserve_search=False)

    def _refresh_current_source(self, preserve_search: bool) -> None:
        prev_q = self.search.text() if preserve_search else ""
        self._items, self._kind = self._items_for_source()

        self.search.blockSignals(True)
        if preserve_search:
            self.search.setText(prev_q)
        else:
            self.search.clear()
        self.search.blockSignals(False)

        self.btn_add.setEnabled(self._kind in ("meta", "loadouts"))
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.search.text().strip().lower()
        items = []
        for it in self._items:
            if not isinstance(it, dict):
                continue
            rn = SaveModel.item_rowname(it)
            title = SaveModel.item_pretty_name(rn)
            if not q or q in rn.lower() or q in title.lower():
                items.append(it)

        self.items_tbl.blockSignals(True)
        self.items_tbl.setSortingEnabled(False)
        self.items_tbl.setRowCount(0)

        for it in items:
            rn = SaveModel.item_rowname(it)
            title = SaveModel.item_pretty_name(rn)

            row = self.items_tbl.rowCount()
            self.items_tbl.insertRow(row)

            it_title = QTableWidgetItem(title)
            it_title.setData(Qt.UserRole, it)
            tip = rn
            w = it.get("_world")
            if isinstance(w, dict):
                tip = (
                    f"{rn}"
                    f"\nМир: {w.get('prospect_id', '')}"
                    f"\nКонтейнер: {w.get('inventory_info', '')} ({w.get('container_index', '')})"
                    f"\nСлот: {w.get('slot_location', '')}  (#{w.get('slot_order', '')})"
                )
            it_title.setToolTip(tip)
            it_title.setFlags(it_title.flags() & ~Qt.ItemIsEditable)

            def mk_num(v: int) -> QTableWidgetItem:
                x = QTableWidgetItem(str(int(v)))
                x.setTextAlignment(Qt.AlignCenter)
                return x

            self.items_tbl.setItem(row, 0, it_title)
            self.items_tbl.setItem(
                row, 1, mk_num(SaveModel.get_dyn(it, "ItemableStack", 1))
            )
            self.items_tbl.setItem(
                row, 2, mk_num(SaveModel.get_dyn(it, "Durability", 0))
            )
            self.items_tbl.setItem(
                row, 3, mk_num(SaveModel.get_dyn(it, "GunCurrentMagSize", 0))
            )
            self.items_tbl.setItem(
                row, 4, mk_num(SaveModel.get_dyn(it, "Fillable_StoredUnits", 0))
            )

        self.items_tbl.setSortingEnabled(True)
        self.items_tbl.blockSignals(False)
        for c in range(1, 5):
            self.items_tbl.resizeColumnToContents(c)

        self.details.set_item(None)
        if self.items_tbl.rowCount() > 0:
            self.items_tbl.selectRow(0)

    def _sel_changed(
        self, currentRow: int, currentColumn: int, prevRow: int, prevCol: int
    ) -> None:
        if currentRow < 0:
            self.details.set_item(None)
            return

        name_item = self.items_tbl.item(currentRow, 0)
        if not name_item:
            self.details.set_item(None)
            return

        it = name_item.data(Qt.UserRole)
        if isinstance(it, dict):
            self.details.set_item(it, read_only=(self._kind == "prospect"))
        else:
            self.details.set_item(None)

    def _cell_changed(self, row: int, col: int) -> None:
        if self._kind == "prospect":
            return
        col_to_prop = {
            1: "ItemableStack",
            2: "Durability",
            3: "GunCurrentMagSize",
            4: "Fillable_StoredUnits",
        }
        if col not in col_to_prop:
            return

        name_item = self.items_tbl.item(row, 0)
        val_item = self.items_tbl.item(row, col)
        if not name_item or not val_item:
            return
        it = name_item.data(Qt.UserRole)
        if not isinstance(it, dict):
            return

        try:
            val = int(val_item.text().strip())
        except Exception:
            return

        SaveModel.set_dyn(it, col_to_prop[col], val)

        self.items_tbl.blockSignals(True)
        val_item.setText(str(val))
        val_item.setTextAlignment(Qt.AlignCenter)
        self.items_tbl.blockSignals(False)
        self.items_tbl.resizeColumnToContents(col)

        if self.details.item is it:
            self.details.refresh()

        if self._kind == "meta":
            self.model.dirty_meta = True
        elif self._kind == "loadouts":
            self.model.dirty_loadouts = True
        self.mark_dirty()

    def _details_changed(self) -> None:
        if self._kind == "prospect":
            return

        r = self.items_tbl.currentRow()
        if r >= 0 and self.details.item:
            it = self.details.item
            self.items_tbl.blockSignals(True)

            def set_col(col: int, v: int):
                self.items_tbl.setItem(r, col, QTableWidgetItem(str(int(v))))
                self.items_tbl.item(r, col).setTextAlignment(Qt.AlignCenter)

            set_col(1, SaveModel.get_dyn(it, "ItemableStack", 1))
            set_col(2, SaveModel.get_dyn(it, "Durability", 0))
            set_col(3, SaveModel.get_dyn(it, "GunCurrentMagSize", 0))
            set_col(4, SaveModel.get_dyn(it, "Fillable_StoredUnits", 0))
            self.items_tbl.blockSignals(False)
            for c in range(1, 5):
                self.items_tbl.resizeColumnToContents(c)

        if self._kind == "meta":
            self.model.dirty_meta = True
        elif self._kind == "loadouts":
            self.model.dirty_loadouts = True
        self.mark_dirty()

    def _ensure_target_items(self) -> Tuple[List[Dict[str, Any]], str]:
        kind, idx = self.source.currentData()
        if kind == "meta":
            items = self.model.meta.get("Items")
            if not isinstance(items, list):
                self.model.meta["Items"] = items = []
            return items, "meta"

        if kind == "loadout" and isinstance(idx, int):
            loads = self.model.loadouts.get("Loadouts")
            if not isinstance(loads, list) or not (0 <= idx < len(loads)):
                return [], "unknown"
            lo = loads[idx]
            if not isinstance(lo, dict):
                return [], "unknown"
            items = lo.get("MetaItems")
            if not isinstance(items, list):
                lo["MetaItems"] = items = []
            return items, "loadouts"

        return [], "unknown"

    def _blank_item(self, row_name: str) -> Dict[str, Any]:
        return SaveModel.new_meta_item(row_name)

    def _known_item_row_names(self) -> List[str]:
        out: set[str] = set()
        items = self.model.meta.get("Items", [])
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    out.add(SaveModel.item_rowname(it))
        loads = self.model.loadouts.get("Loadouts", [])
        if isinstance(loads, list):
            for lo in loads:
                if not isinstance(lo, dict):
                    continue
                its = lo.get("MetaItems", [])
                if not isinstance(its, list):
                    continue
                for it in its:
                    if isinstance(it, dict):
                        out.add(SaveModel.item_rowname(it))
        return sorted(rn for rn in out if rn and rn != "(неизвестно)")

    def _add_item(self) -> None:
        items, kind = self._ensure_target_items()
        if kind == "unknown":
            QMessageBox.warning(
                self, "Ошибка", "Не удалось определить, куда добавить предмет."
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить предмет")
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        cmb_row = QComboBox()
        cmb_row.setEditable(True)
        if self._game_data and self._game_data.items:
            for it in sorted(
                self._game_data.items.values(),
                key=lambda x: (x.display_name.lower(), x.row_name.lower()),
            ):
                title = it.display_name or it.row_name
                cmb_row.addItem(f"{title} ({it.row_name})", it.row_name)
        else:
            cmb_row.addItems(self._known_item_row_names())
        cmb_row.setCurrentText("")
        cmb_row.lineEdit().setPlaceholderText("Поиск (имя или RowName)…")
        try:
            comp = QCompleter(cmb_row.model(), cmb_row)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            cmb_row.setCompleter(comp)
        except Exception:
            pass

        sb_stack = QSpinBox()
        sb_stack.setRange(1, 10**9)
        sb_stack.setValue(1)
        sb_dur = QSpinBox()
        sb_dur.setRange(0, 10**9)
        sb_mag = QSpinBox()
        sb_mag.setRange(0, 10**9)
        sb_fuel = QSpinBox()
        sb_fuel.setRange(0, 10**9)

        form.addRow("RowName", cmb_row)
        form.addRow("Количество (ItemableStack)", sb_stack)
        form.addRow("Прочность (Durability)", sb_dur)
        form.addRow("Патроны (GunCurrentMagSize)", sb_mag)
        form.addRow("Топливо/заряд (Fillable_StoredUnits)", sb_fuel)
        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        row_name = cmb_row.currentData()
        if not isinstance(row_name, str) or not row_name.strip():
            raw = cmb_row.currentText().strip()
            m = re.search(r"\(([^)]+)\)\s*$", raw)
            row_name = m.group(1).strip() if m else raw
        row_name = row_name.strip()
        if not row_name:
            QMessageBox.warning(self, "Ошибка", "RowName обязателен.")
            return

        new_item = self._blank_item(row_name)
        SaveModel.set_dyn(new_item, "ItemableStack", int(sb_stack.value()))
        if sb_dur.value():
            SaveModel.set_dyn(new_item, "Durability", int(sb_dur.value()))
        if sb_mag.value():
            SaveModel.set_dyn(new_item, "GunCurrentMagSize", int(sb_mag.value()))
        if sb_fuel.value():
            SaveModel.set_dyn(new_item, "Fillable_StoredUnits", int(sb_fuel.value()))

        items.append(new_item)
        self._items = items
        self._kind = kind

        if kind == "meta":
            self.model.dirty_meta = True
        elif kind == "loadouts":
            self.model.dirty_loadouts = True
        self.mark_dirty()

        self.search.setText("")
        self._apply_filter()
        for r in range(self.items_tbl.rowCount()):
            it = self.items_tbl.item(r, 0)
            if it and it.data(Qt.UserRole) is new_item:
                self.items_tbl.selectRow(r)
                break

    def _clone_item(self) -> None:
        try:
            count, ok = QInputDialog.getInt(
                self, "Клонировать предмет", "Сколько копий создать?", 1, 1, 10**6, 1
            )
        except Exception:
            count, ok = (1, True)
        if not ok or count <= 0:
            return
        self._clone_selected_item(int(count))

    def _items_context_menu(self, pos) -> None:
        row = self.items_tbl.rowAt(pos.y())
        if row < 0:
            return

        # чтобы ПКМ работал по строке под курсором
        self.items_tbl.selectRow(row)

        # Совместимость PyQt5/PyQt6/PySide
        USER_ROLE = getattr(Qt, "UserRole", None)
        if USER_ROLE is None:
            USER_ROLE = Qt.ItemDataRole.UserRole

        it0 = self.items_tbl.item(row, 0)
        src = it0.data(USER_ROLE) if it0 else None
        if not isinstance(src, dict):
            return

        menu = QMenu(self)

        # Подсветка пунктов меню при наведении (фиксится даже при глобальном QSS)
        menu.setStyleSheet(
            """
            QMenu { background: #1E1F22; color: #DBDEE1; border: 1px solid #111214; }
            QMenu::item { padding: 6px 12px; background: transparent; }
            QMenu::item:selected { background: #4752C4; color: #ffffff; }
            QMenu::separator { height: 1px; background: #111214; margin: 4px 8px; }
            QMenu::item:disabled { color: #8e9297; }
        """
        )

        act_clone = menu.addAction("Клонировать…")
        act_del = menu.addAction("Удалить")

        kind, _idx = self.source.currentData()
        act_to_stash = None
        if kind in ("loadout", "prospect"):
            act_to_stash = menu.addAction("Забрать в орбитальный сташ")

        # ВАЖНО: не сравниваем chosen is act_*, а работаем через triggered
        if kind != "prospect":
            act_clone.triggered.connect(
                lambda _=False, s=src: self._clone_item_dialog(src_override=s)
            )
            act_del.triggered.connect(
                lambda _=False, s=src: self._delete_selected_item(src_override=s)
            )
        else:
            act_clone.setEnabled(False)
            act_del.setEnabled(False)

        if act_to_stash is not None:
            if kind == "loadout":
                act_to_stash.triggered.connect(
                    lambda _=False, s=src: self._move_item_to_stash(src_override=s)
                )
            elif kind == "prospect":
                act_to_stash.triggered.connect(
                    lambda _=False, s=src: self._move_world_item_to_stash(
                        src_override=s
                    )
                )

        exec_fn = getattr(menu, "exec", None) or getattr(menu, "exec_", None)
        exec_fn(self.items_tbl.viewport().mapToGlobal(pos))

    def _clone_item_dialog(self, src_override: Optional[Dict[str, Any]] = None) -> None:
        try:
            count, ok = QInputDialog.getInt(
                self, "Клонировать предмет", "Сколько копий создать?", 1, 1, 10**6, 1
            )
        except Exception:
            count, ok = (1, True)
        if not ok or count <= 0:
            return
        self._clone_selected_item(int(count), src_override=src_override)

    def _move_item_to_stash(
        self, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        kind, _idx = self.source.currentData()
        if kind != "loadout":
            QMessageBox.information(
                self, "Сташ", "Эта операция доступна только для миров (Loadout)."
            )
            return

        loads = self.model.loadouts.get("Loadouts")
        if not isinstance(loads, list):
            QMessageBox.warning(self, "Сташ", "Loadouts.json не распознан.")
            return

        lo_idx = self.source.currentData()[1]
        if (
            not isinstance(lo_idx, int)
            or not (0 <= lo_idx < len(loads))
            or not isinstance(loads[lo_idx], dict)
        ):
            QMessageBox.warning(
                self, "Сташ", "Не удалось определить выбранный мир (Loadout)."
            )
            return

        src = src_override
        if src is None:
            r = self.items_tbl.currentRow()
            if r < 0:
                return
            it0 = self.items_tbl.item(r, 0)
            src = it0.data(Qt.UserRole) if it0 else None
        if not isinstance(src, dict):
            return

        meta_items = self.model.meta.get("Items")
        if not isinstance(meta_items, list):
            self.model.meta["Items"] = meta_items = []

        moved = copy.deepcopy(src)
        guid_key = None
        for k in moved.keys():
            if str(k).lower() == "databaseguid":
                guid_key = k
                break
        if not guid_key:
            guid_key = "DatabaseGUID"
        moved[guid_key] = uuid.uuid4().hex.upper()
        meta_items.append(moved)
        self.model.dirty_meta = True

        # удаляем из мира
        self._delete_selected_item(src_override=src)

    def _move_world_item_to_stash(
        self, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        kind, _idx = self.source.currentData()
        if kind != "prospect":
            QMessageBox.information(
                self, "Сташ", "Эта операция доступна только для мира (Prospects)."
            )
            return

        src = src_override
        if src is None:
            r = self.items_tbl.currentRow()
            if r < 0:
                return
            it0 = self.items_tbl.item(r, 0)
            src = it0.data(Qt.UserRole) if it0 else None
        if not isinstance(src, dict):
            return

        try:
            self.model.export_world_item_to_stash(src)
        except Exception as e:
            QMessageBox.warning(
                self, "Сташ", f"Не удалось забрать предмет из мира.\n\n{e}"
            )
            return

        self.mark_dirty()
        self._refresh_current_source(preserve_search=True)

    def _clone_selected_item(
        self, count: int, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        items, kind = self._ensure_target_items()
        if kind == "unknown":
            QMessageBox.warning(
                self, "Ошибка", "Не удалось определить, откуда клонировать предмет."
            )
            return

        USER_ROLE = getattr(Qt, "UserRole", None)
        if USER_ROLE is None:
            USER_ROLE = Qt.ItemDataRole.UserRole

        def _guid_key(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            for k in x.keys():
                if str(k).lower() == "databaseguid":
                    return k
            return None

        def _guid(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            k = _guid_key(x)
            if not k:
                return None
            g = x.get(k)
            if isinstance(g, str) and g and g.lower() != "noguid":
                return g.upper()
            return None

        def _same(a: Any, b: Any) -> bool:
            if a is b:
                return True
            ga, gb = _guid(a), _guid(b)
            if ga and gb and ga == gb:
                return True
            if isinstance(a, dict) and isinstance(b, dict) and a == b:
                return True
            return False

        src = src_override
        if src is None:
            r = self.items_tbl.currentRow()
            if r < 0:
                QMessageBox.information(
                    self, "Копирование", "Выбери предмет в списке слева."
                )
                return
            name_item = self.items_tbl.item(r, 0)
            src = name_item.data(USER_ROLE) if name_item else None

        if not isinstance(src, dict):
            QMessageBox.information(
                self, "Копирование", "Выбери предмет в списке слева."
            )
            return

        # ВАЖНО: GUID пишем в тот же ключ, который реально есть в исходнике (иначе появляются “две версии” GUID)
        guid_key = _guid_key(src) or "DatabaseGUID"

        insert_at = None
        for i, it in enumerate(items):
            if _same(it, src):
                insert_at = i + 1
                break

        clones: List[Dict[str, Any]] = []
        for _ in range(int(count)):
            c = copy.deepcopy(src)
            c[guid_key] = uuid.uuid4().hex.upper()
            clones.append(c)

        if insert_at is None:
            items.extend(clones)
        else:
            items[insert_at:insert_at] = clones

        self._items = items
        self._kind = kind

        if kind == "meta":
            self.model.dirty_meta = True
        elif kind == "loadouts":
            self.model.dirty_loadouts = True
        self.mark_dirty()

        self._apply_filter()

        # Попытаться выделить последний клон (по GUID / по ==), не полагаясь на `is`
        target = clones[-1] if clones else None
        if target:
            target_guid = _guid(target)
            for rr in range(self.items_tbl.rowCount()):
                itw = self.items_tbl.item(rr, 0)
                if not itw:
                    continue
                data = itw.data(USER_ROLE)
                if data is target:
                    self.items_tbl.selectRow(rr)
                    break
                if target_guid and _guid(data) == target_guid:
                    self.items_tbl.selectRow(rr)
                    break
                if isinstance(data, dict) and data == target:
                    self.items_tbl.selectRow(rr)
                    break

    def _delete_selected_item(
        self, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        items, kind = self._ensure_target_items()
        if kind == "unknown":
            QMessageBox.warning(
                self, "Ошибка", "Не удалось определить, откуда удалить предмет."
            )
            return

        USER_ROLE = getattr(Qt, "UserRole", None)
        if USER_ROLE is None:
            USER_ROLE = Qt.ItemDataRole.UserRole

        def _guid_key(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            for k in x.keys():
                if str(k).lower() == "databaseguid":
                    return k
            return None

        def _guid(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            k = _guid_key(x)
            if not k:
                return None
            g = x.get(k)
            if isinstance(g, str) and g and g.lower() != "noguid":
                return g.upper()
            return None

        def _same(a: Any, b: Any) -> bool:
            if a is b:
                return True
            ga, gb = _guid(a), _guid(b)
            if ga and gb and ga == gb:
                return True
            if isinstance(a, dict) and isinstance(b, dict) and a == b:
                return True
            return False

        src = src_override
        if src is None:
            r = self.items_tbl.currentRow()
            if r < 0:
                return
            name_item = self.items_tbl.item(r, 0)
            src = name_item.data(USER_ROLE) if name_item else None

        if not isinstance(src, dict):
            return

        removed = False
        removed_obj = None

        # Не полагаемся на `is` — ищем по GUID (любая капитализация ключа) или по равенству словарей
        for i, it in enumerate(items):
            if _same(it, src):
                removed_obj = it
                del items[i]
                removed = True
                break

        if not removed:
            return

        # чистим правую панель, если она показывала удалённый предмет
        cur = getattr(self.details, "item", None)
        if _same(cur, removed_obj) or _same(cur, src):
            self.details.set_item(None)

        self._items = items
        self._kind = kind

        if kind == "meta":
            self.model.dirty_meta = True
        elif kind == "loadouts":
            self.model.dirty_loadouts = True
        self.mark_dirty()

        self._apply_filter()


class GeneticsRadarEditor(QWidget):
    valueChanged = Signal(str, int)  # (row_name, value)

    def __init__(self) -> None:
        super().__init__()
        self._entries: List[Dict[str, Any]] = []
        self._nodes: List[QWidget] = []
        self.setMinimumHeight(390)

    def clear(self) -> None:
        self.set_entries([])

    def set_entries(self, entries: List[Tuple[str, str, int, int, str, bool]]) -> None:
        for w in self._nodes:
            w.hide()
            w.deleteLater()
        self._nodes = []
        self._entries = []

        for row_name, short_label, value, value_offset, full_title, editable in entries:
            rec: Dict[str, Any] = {
                "row_name": str(row_name),
                "short_label": str(short_label),
                "full_title": str(full_title),
                "value": int(value),
                "value_offset": int(value_offset),
                "editable": bool(editable),
                "spin": None,
            }
            self._entries.append(rec)

            holder = QWidget(self)
            holder.setFixedSize(128, 70)
            holder.setStyleSheet(
                "background:#1E1F22; border:1px solid #111214; border-radius:8px;"
            )
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(6, 4, 6, 4)
            lay.setSpacing(2)

            lbl = QLabel(rec["short_label"] or rec["row_name"])
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#B5BAC1; border:none;")
            tip = rec["full_title"] or rec["row_name"]
            if tip and tip != rec["row_name"]:
                tip = f"{tip}\n{rec['row_name']}"
            lbl.setToolTip(tip or rec["row_name"])

            sb = QSpinBox()
            sb.setRange(-(10**9), 10**9)
            sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            sb.setValue(int(rec["value"]))
            sb.setReadOnly(not bool(rec["editable"]))
            if not bool(rec["editable"]):
                sb.setStyleSheet("color:#8B9098;")
            sb.setToolTip(tip or rec["row_name"])
            sb.valueChanged.connect(lambda v, ref=rec: self._spin_changed(ref, v))

            lay.addWidget(lbl)
            lay.addWidget(sb)
            rec["spin"] = sb
            self._nodes.append(holder)
            holder.show()
            holder.raise_()

        self._reposition_nodes()
        self.update()

    def _spin_changed(self, rec: Dict[str, Any], v: int) -> None:
        if not bool(rec.get("editable", False)):
            return
        rec["value"] = int(v)
        self.update()
        self.valueChanged.emit(str(rec["row_name"]), int(v))

    def _outer_geometry(self) -> Tuple[QRectF, QPointF, float, float]:
        r = QRectF(self.rect().adjusted(8, 8, -8, -8))
        cx = r.center().x()
        cy = r.center().y()
        base = min(r.width(), r.height())
        chart = max(36.0, base * 0.36)  # ~30% larger than previous chart
        outer = max(chart + 58.0, base * 0.49)
        return r, QPointF(cx, cy), chart, outer

    def _reposition_nodes(self) -> None:
        if not self._nodes:
            return
        r, center, _chart_r, outer_r = self._outer_geometry()
        cx = center.x()
        cy = center.y()
        n = len(self._nodes)
        for i, w in enumerate(self._nodes):
            angle = -math.pi / 2.0 + (2.0 * math.pi * i) / max(1, n)
            x = cx + math.cos(angle) * outer_r - (w.width() / 2.0)
            y = cy + math.sin(angle) * outer_r - (w.height() / 2.0)

            # Keep editors inside the widget.
            min_x = r.left()
            max_x = r.right() - w.width()
            min_y = r.top()
            max_y = r.bottom() - w.height()
            x = max(min_x, min(max_x, x))
            y = max(min_y, min(max_y, y))
            w.setGeometry(int(x), int(y), w.width(), w.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_nodes()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if len(self._entries) < 3:
            p.setPen(QColor("#6B7280"))
            p.drawText(self.rect(), Qt.AlignCenter, "Нет генетических данных")
            return

        r, center, radius, _outer_r = self._outer_geometry()
        cx = center.x()
        cy = center.y()
        n = len(self._entries)
        values = [int(e.get("value", 0)) for e in self._entries]
        min_v = min(0, min(values))
        max_v = max(10, max(values))
        span = max(1, max_v - min_v)

        def axis_point(i: int, k: float = 1.0) -> QPointF:
            a = -math.pi / 2.0 + (2.0 * math.pi * i) / max(1, n)
            return QPointF(cx + math.cos(a) * radius * k, cy + math.sin(a) * radius * k)

        # Radar grid.
        p.setPen(QPen(QColor("#3A3F46"), 1))
        for ring in range(1, 5):
            k = ring / 4.0
            poly = QPolygonF([axis_point(i, k) for i in range(n)])
            p.drawPolygon(poly)

        # Axis lines.
        p.setPen(QPen(QColor("#4A505A"), 1))
        for i in range(n):
            p.drawLine(center, axis_point(i, 1.0))

        # Data polygon.
        pts: List[QPointF] = []
        for i, v in enumerate(values):
            k = (float(v) - float(min_v)) / float(span)
            k = max(0.0, min(1.0, k))
            pts.append(axis_point(i, k))
        poly = QPolygonF(pts)
        p.setPen(QPen(QColor("#F59E0B"), 2))
        fill = QColor("#F59E0B")
        fill.setAlpha(90)
        p.setBrush(fill)
        p.drawPolygon(poly)

        p.setPen(QPen(QColor("#FCD34D"), 2))
        p.setBrush(QColor("#FCD34D"))
        for pt in pts:
            p.drawEllipse(pt, 3.0, 3.0)

        p.setPen(QColor("#9CA3AF"))
        p.drawText(int(r.left()) + 4, int(r.top()) + 16, f"Scale: {min_v} .. {max_v}")


class MountDetails(QWidget):
    changed = Signal()
    DEFAULT_GENETIC_ORDER = [
        "Vitality",
        "Endurance",
        "Muscle",
        "Agility",
        "Toughness",
        "Hardiness",
        "Utility",
    ]
    DEFAULT_GENETIC_SHORT = {
        "Vitality": "VIR",
        "Endurance": "FIT",
        "Muscle": "PHY",
        "Agility": "REF",
        "Toughness": "TGH",
        "Hardiness": "ADP",
        "Utility": "INS",
    }
    TALENT_OVERCAP_UI_MAX = 999
    SEX_CHOICES = [
        (0, SEX_TITLES_RU[0]),
        (1, SEX_TITLES_RU[1]),
        (2, SEX_TITLES_RU[2]),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.mount: Optional[Dict[str, Any]] = None
        self._talent_catalog: Dict[str, List[str]] = {}
        self._talent_info: Dict[str, GameTalent] = {}
        self._mount_phenotypes: Dict[str, List[GamePhenotype]] = {}
        self._mount_ai_setup: Dict[str, str] = {}
        self._genetic_value_titles: Dict[str, str] = {}
        self._genetic_value_short: Dict[str, str] = {}
        self._genetic_value_order: List[str] = []
        self._genetic_lineage_titles: Dict[str, str] = {}
        self._xp_curve_mount: ExperienceCurve = DEFAULT_MOUNT_XP_CURVE
        self._xp_curve_pet: ExperienceCurve = DEFAULT_PET_XP_CURVE

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.title = QLabel("Выбери питомца слева.")
        self.title.setStyleSheet("color:#B5BAC1;")
        root.addWidget(self.title)

        self.basic_box = QGroupBox("Основное")
        basic_lay = QFormLayout(self.basic_box)
        basic_lay.setContentsMargins(8, 10, 8, 8)
        basic_lay.setHorizontalSpacing(10)
        basic_lay.setVerticalSpacing(6)

        self.ed_name = QLineEdit()
        self.ed_name.textChanged.connect(self._name_changed)

        self.cmb_type = QComboBox()
        self.cmb_type.setEditable(True)
        self.cmb_type.addItems(["Horse", "Horse_Standard", "Cat", "Moa"])
        self.cmb_type.currentTextChanged.connect(self._type_changed)

        self.sb_level = QSpinBox()
        self.sb_level.setRange(0, 10**9)
        self.sb_level.valueChanged.connect(self._level_changed)
        self.btn_level_max = QPushButton("MAX")
        self.btn_level_max.setFixedWidth(52)
        self.btn_level_max.setToolTip("Установить максимальный уровень (50)")
        self.btn_level_max.clicked.connect(self._set_max_level)

        basic_lay.addRow("Имя", self.ed_name)
        basic_lay.addRow("Тип", self.cmb_type)
        lvl_row = QWidget()
        lvl_lay = QHBoxLayout(lvl_row)
        lvl_lay.setContentsMargins(0, 0, 0, 0)
        lvl_lay.setSpacing(6)
        lvl_lay.addWidget(self.sb_level, 1)
        lvl_lay.addWidget(self.btn_level_max)
        basic_lay.addRow("Уровень", lvl_row)

        self.stats_box = QGroupBox("Статы (RecorderBlob)")
        stats_root = QVBoxLayout(self.stats_box)
        stats_root.setContentsMargins(8, 10, 8, 8)
        stats_root.setSpacing(4)

        stats_lay = QGridLayout()
        stats_lay.setContentsMargins(0, 0, 0, 0)
        stats_lay.setHorizontalSpacing(10)
        stats_lay.setVerticalSpacing(4)
        stats_root.addLayout(stats_lay)

        def mk_spin(prop: str) -> QSpinBox:
            sb = QSpinBox()
            sb.setRange(0, 10**9)
            sb.setToolTip(prop)
            sb.valueChanged.connect(lambda v, p=prop: self._stat_changed(p, v))
            return sb

        self.sb_health = mk_spin("CurrentHealth")
        self.sb_stamina = mk_spin("Stamina")
        self.sb_food = mk_spin("FoodLevel")
        self.sb_water = mk_spin("WaterLevel")
        self.sb_oxygen = mk_spin("OxygenLevel")
        self.sb_xp = mk_spin("Experience")
        self.lbl_xp_level = QLabel("")
        self.lbl_xp_level.setStyleSheet("color:#B5BAC1;")
        xp_row = QWidget()
        xp_lay = QHBoxLayout(xp_row)
        xp_lay.setContentsMargins(0, 0, 0, 0)
        xp_lay.setSpacing(6)
        xp_lay.addWidget(self.sb_xp, 1)
        xp_lay.addWidget(self.lbl_xp_level, 0)

        stats_lay.addWidget(QLabel("ХП"), 0, 0)
        stats_lay.addWidget(self.sb_health, 0, 1)
        stats_lay.addWidget(QLabel("Стамина"), 0, 2)
        stats_lay.addWidget(self.sb_stamina, 0, 3)

        stats_lay.addWidget(QLabel("Еда"), 1, 0)
        stats_lay.addWidget(self.sb_food, 1, 1)
        stats_lay.addWidget(QLabel("Вода"), 1, 2)
        stats_lay.addWidget(self.sb_water, 1, 3)

        stats_lay.addWidget(QLabel("Кислород"), 2, 0)
        stats_lay.addWidget(self.sb_oxygen, 2, 1)
        stats_lay.addWidget(QLabel("XP"), 2, 2)
        stats_lay.addWidget(xp_row, 2, 3)

        self.genetics_box = QGroupBox("Селекция / генетика")
        genetics_lay = QVBoxLayout(self.genetics_box)
        genetics_lay.setContentsMargins(8, 10, 8, 8)
        genetics_lay.setSpacing(6)

        g_form = QFormLayout()
        g_form.setContentsMargins(0, 0, 0, 0)
        g_form.setHorizontalSpacing(10)
        g_form.setVerticalSpacing(6)

        self.cmb_sex = QComboBox()
        self.cmb_sex.currentIndexChanged.connect(self._sex_changed)
        g_form.addRow("Пол", self.cmb_sex)

        self.cmb_phenotype = QComboBox()
        self.cmb_phenotype.setEditable(False)
        self.cmb_phenotype.currentIndexChanged.connect(self._phenotype_changed)
        self.cmb_phenotype.setToolTip(
            "Хранится как CosmeticSkinIndex и Variation.\n"
            "Если для вида известны визуальные вариации, они будут показаны списком."
        )
        self.sb_phenotype_raw = QSpinBox()
        self.sb_phenotype_raw.setRange(-1, 10**6)
        self.sb_phenotype_raw.setSpecialValueText("Авто (-1)")
        self.sb_phenotype_raw.setToolTip(
            "Сырое значение фенотипа.\n"
            "Для ручного выбора редактор синхронизирует CosmeticSkinIndex и Variation.\n"
            "Можно задать вручную, даже если точное имя варианта неизвестно."
        )
        self.sb_phenotype_raw.valueChanged.connect(self._phenotype_raw_changed)
        phenotype_row = QWidget()
        phenotype_lay = QHBoxLayout(phenotype_row)
        phenotype_lay.setContentsMargins(0, 0, 0, 0)
        phenotype_lay.setSpacing(6)
        phenotype_lay.addWidget(self.cmb_phenotype, 1)
        phenotype_lay.addWidget(self.sb_phenotype_raw, 0)
        g_form.addRow("Фенотип", phenotype_row)

        self.cmb_lineage = QComboBox()
        self.cmb_lineage.setEditable(True)
        self.cmb_lineage.currentTextChanged.connect(self._lineage_changed)
        g_form.addRow("Родословная", self.cmb_lineage)

        self.cb_has_genetics = QCheckBox(
            "Генетика сгенерирована (bHasGeneratedGenetics)"
        )
        self.cb_has_genetics.setToolTip(
            "Служебный флаг игры: у сущности уже создана генетическая запись.\n"
            "Обычно должен быть включён у животных с генетикой."
        )
        self.cb_has_genetics.stateChanged.connect(self._has_generated_genetics_changed)
        g_form.addRow("", self.cb_has_genetics)
        genetics_lay.addLayout(g_form)

        self.radar_genetics = GeneticsRadarEditor()
        self.radar_genetics.valueChanged.connect(self._genetic_value_changed)
        genetics_lay.addWidget(self.radar_genetics, 1)

        self.talents_box = QGroupBox("Навыки")
        talents_root = QVBoxLayout(self.talents_box)
        talents_root.setContentsMargins(8, 10, 8, 8)
        talents_root.setSpacing(4)

        talent_btns = QHBoxLayout()
        self.btn_add_missing = QPushButton("Добавить отсутствующие (0)")
        self.btn_add_missing.clicked.connect(self._add_missing_talents)
        self.btn_add_talent = QPushButton("Добавить навык…")
        self.btn_add_talent.clicked.connect(self._add_talent_dialog)
        talent_btns.addWidget(self.btn_add_missing)
        talent_btns.addWidget(self.btn_add_talent)
        self.cb_all_talents_max = QCheckBox("Все MAX")
        self.cb_all_talents_max.setToolTip(
            "Прокачать все навыки питомца до максимума (включая неоткрытые)"
        )
        self.cb_all_talents_max.stateChanged.connect(self._set_all_talents_max)
        talent_btns.addWidget(self.cb_all_talents_max)
        talent_btns.addStretch(1)
        talents_root.addLayout(talent_btns)

        self.talent_tree = QTreeWidget()
        self.talent_tree.setHeaderLabels(["Навык", "Ранг"])
        self.talent_tree.setAlternatingRowColors(True)
        self.talent_tree.setTextElideMode(Qt.ElideNone)
        hdr = self.talent_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        talents_root.addWidget(self.talent_tree, 1)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)
        top_row.addWidget(self.basic_box, 1)
        top_row.addWidget(self.stats_box, 1)
        root.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(8)
        bottom_row.addWidget(self.genetics_box, 3)
        bottom_row.addWidget(self.talents_box, 2)
        root.addLayout(bottom_row, 1)

        self.setEnabled(False)

    def set_talent_catalog(self, catalog: Dict[str, List[str]]) -> None:
        self._talent_catalog = catalog or {}
        self._refresh_talents()

    def set_talent_info(self, info: Dict[str, GameTalent]) -> None:
        self._talent_info = info or {}
        self._refresh_talents()

    def set_mount_ai_setup(self, mapping: Dict[str, str]) -> None:
        self._mount_ai_setup = mapping or {}

    def set_mount_phenotypes(
        self, phenotypes: Dict[str, List[GamePhenotype]]
    ) -> None:
        self._mount_phenotypes = phenotypes or {}
        self._refresh_genetics()

    def set_genetics_info(
        self,
        value_titles: Dict[str, str],
        lineage_titles: Dict[str, str],
        value_short: Optional[Dict[str, str]] = None,
        value_order: Optional[List[str]] = None,
    ) -> None:
        self._genetic_value_titles = value_titles or {}
        self._genetic_value_short = value_short or {}
        self._genetic_value_order = (
            value_order[:] if isinstance(value_order, list) else []
        )
        self._genetic_lineage_titles = lineage_titles or {}
        self._refresh_genetics()

    def set_experience_curves(
        self,
        mount_curve: Optional[ExperienceCurve],
        pet_curve: Optional[ExperienceCurve],
    ) -> None:
        if isinstance(mount_curve, ExperienceCurve):
            self._xp_curve_mount = mount_curve
        else:
            self._xp_curve_mount = DEFAULT_MOUNT_XP_CURVE
        if isinstance(pet_curve, ExperienceCurve):
            self._xp_curve_pet = pet_curve
        else:
            self._xp_curve_pet = DEFAULT_PET_XP_CURVE
        self._update_xp_level_label()

    def set_mount(self, mount: Optional[Dict[str, Any]]) -> None:
        self.mount = mount
        if not mount:
            self.setEnabled(False)
            self.title.setText("Выбери питомца слева.")
            self.talent_tree.clear()
            self.radar_genetics.clear()
            self.cmb_sex.blockSignals(True)
            self.cmb_sex.clear()
            self.cmb_sex.setEnabled(False)
            self.cmb_sex.blockSignals(False)
            self.cmb_phenotype.blockSignals(True)
            self.cmb_phenotype.clear()
            self.cmb_phenotype.setCurrentIndex(-1)
            self.cmb_phenotype.setEnabled(False)
            self.cmb_phenotype.blockSignals(False)
            self.sb_phenotype_raw.blockSignals(True)
            self.sb_phenotype_raw.setValue(-1)
            self.sb_phenotype_raw.setEnabled(False)
            self.sb_phenotype_raw.blockSignals(False)
            self.cmb_lineage.blockSignals(True)
            self.cmb_lineage.clear()
            self.cmb_lineage.setCurrentIndex(-1)
            self.cmb_lineage.setEditText("")
            self.cmb_lineage.blockSignals(False)
            self.cb_has_genetics.blockSignals(True)
            self.cb_has_genetics.setChecked(False)
            self.cb_has_genetics.setEnabled(False)
            self.cb_has_genetics.blockSignals(False)
            return

        self.setEnabled(True)
        name = mount.get("MountName", "")
        name_s = name if isinstance(name, str) else ""
        mtype = mount.get("MountType", "")
        mtype_s = mtype if isinstance(mtype, str) else ""
        level = mount.get("MountLevel", 0)
        level_i = int(level) if isinstance(level, int) else 0

        self.title.setText("")
        self.ed_name.blockSignals(True)
        self.sb_level.blockSignals(True)
        self.cmb_type.blockSignals(True)
        self.ed_name.setText(name_s)
        effective_type = self._mount_type_from_blob(self._blob_data()) or mtype_s
        if effective_type and self.cmb_type.findText(effective_type) < 0:
            self.cmb_type.addItem(effective_type)
        elif mtype_s and self.cmb_type.findText(mtype_s) < 0:
            self.cmb_type.addItem(mtype_s)
        self.cmb_type.setCurrentText(effective_type or mtype_s)
        self.sb_level.setValue(level_i)
        self.ed_name.blockSignals(False)
        self.sb_level.blockSignals(False)
        self.cmb_type.blockSignals(False)
        if effective_type and mtype_s and effective_type != mtype_s:
            self.cmb_type.setToolTip(
                "Тип сверху и тип внутри RecorderBlob не совпадают.\n"
                f"MountType: {mtype_s}\n"
                f"По Actor/AI используется: {effective_type}"
            )
        else:
            self.cmb_type.setToolTip("")

        self._update_title()
        self._refresh_stats()
        self._refresh_genetics()
        self._update_xp_level_label()
        self._refresh_talents()

    def _update_title(self) -> None:
        if not self.mount:
            self.title.setText("Выбери питомца слева.")
            return
        self.title.setText("")

    def _blob_data(self) -> Optional[List[int]]:
        if not self.mount:
            return None
        rec = self.mount.get("RecorderBlob")
        if not isinstance(rec, dict):
            return None
        data = rec.get("BinaryData")
        return (
            data
            if isinstance(data, list) and all(isinstance(x, int) for x in data)
            else None
        )

    def commit_pending_edits(self) -> None:
        if not self.mount:
            return
        focus = QApplication.focusWidget()
        if focus is not None and self.isAncestorOf(focus):
            try:
                focus.clearFocus()
            except Exception:
                pass
        current_name = self.ed_name.text()
        stored_name = (
            self.mount.get("MountName", "") if isinstance(self.mount, dict) else ""
        )
        if isinstance(stored_name, str) and current_name != stored_name:
            self._name_changed(current_name)
        for sb in self.findChildren(QAbstractSpinBox):
            try:
                sb.interpretText()
            except Exception:
                pass
            if isinstance(sb, QSpinBox):
                try:
                    le = sb.lineEdit()
                    txt = le.text().strip() if le else ""
                    if txt:
                        sb.setValue(int(txt))
                except Exception:
                    pass
        self._name_changed(self.ed_name.text())
        self._type_changed(self.cmb_type.currentText())
        self._level_changed(int(self.sb_level.value()))
        self._stat_changed("CurrentHealth", int(self.sb_health.value()))
        self._stat_changed("Stamina", int(self.sb_stamina.value()))
        self._stat_changed("FoodLevel", int(self.sb_food.value()))
        self._stat_changed("WaterLevel", int(self.sb_water.value()))
        self._stat_changed("OxygenLevel", int(self.sb_oxygen.value()))
        self._stat_changed("Experience", int(self.sb_xp.value()))
        self._sex_changed(self.cmb_sex.currentIndex())
        self._phenotype_changed(self.cmb_phenotype.currentText())
        self._lineage_changed(self.cmb_lineage.currentText())
        state = self.cb_has_genetics.checkState()
        try:
            state_i = int(getattr(state, "value", state))
        except Exception:
            state_i = 0
        self._has_generated_genetics_changed(state_i)
        for rec in self.radar_genetics._entries:
            try:
                if not bool(rec.get("editable")):
                    continue
                spin = rec.get("spin")
                if not isinstance(spin, QSpinBox):
                    continue
                txt = ""
                try:
                    le = spin.lineEdit()
                    txt = le.text().strip() if le else ""
                except Exception:
                    txt = ""
                value = int(txt) if txt else int(spin.value())
                rec["value"] = int(value)
                self._genetic_value_changed(str(rec.get("row_name", "")), int(value))
            except Exception:
                pass

    def _level_meta(self) -> Tuple[int, bool, Optional[ExperienceCurve]]:
        data = self._blob_data()
        actor_class = ""
        if data:
            actor_class = mount_blob_get_fstring(data, "ActorClassName") or ""
        use_pet = actor_class.lower().startswith("bp_tame_")
        if not use_pet and self.mount:
            mt = self.mount.get("MountType", "")
            use_pet = isinstance(mt, str) and mt.strip().lower() == "cat"
        if use_pet:
            return 25, True, self._xp_curve_pet
        return 50, False, self._xp_curve_mount

    def _sync_level_spin_from_xp(self) -> int:
        data = self._blob_data()
        raw_level = 0
        if self.mount:
            level = self.mount.get("MountLevel", 0)
            raw_level = int(level) if isinstance(level, int) else 0

        if not data:
            self.sb_level.blockSignals(True)
            self.sb_level.setValue(raw_level)
            self.sb_level.blockSignals(False)
            return raw_level

        xp = mount_blob_get_int(data, "Experience") or 0
        cap, _use_pet, curve = self._level_meta()
        lvl = (
            curve.level_for_xp(int(xp), max_level=cap)
            if isinstance(curve, ExperienceCurve)
            else raw_level
        )
        self.sb_level.blockSignals(True)
        self.sb_level.setValue(int(lvl))
        self.sb_level.blockSignals(False)
        return int(lvl)

    def _refresh_stats(self) -> None:
        data = self._blob_data()
        sbs = (
            self.sb_health,
            self.sb_stamina,
            self.sb_food,
            self.sb_water,
            self.sb_oxygen,
            self.sb_xp,
        )
        for sb in sbs:
            sb.blockSignals(True)

        if not data:
            for sb in sbs:
                sb.setValue(0)
        else:
            self.sb_health.setValue(mount_blob_get_int(data, "CurrentHealth") or 0)
            self.sb_stamina.setValue(mount_blob_get_int(data, "Stamina") or 0)
            self.sb_food.setValue(mount_blob_get_int(data, "FoodLevel") or 0)
            self.sb_water.setValue(mount_blob_get_int(data, "WaterLevel") or 0)
            self.sb_oxygen.setValue(mount_blob_get_int(data, "OxygenLevel") or 0)
            self.sb_xp.setValue(mount_blob_get_int(data, "Experience") or 0)

        for sb in sbs:
            sb.blockSignals(False)

        self._sync_level_spin_from_xp()
        self._update_xp_level_label()

    def _update_xp_level_label(self) -> None:
        data = self._blob_data()
        if not data:
            self.lbl_xp_level.setText("")
            return

        xp = mount_blob_get_int(data, "Experience") or 0
        cap, _use_pet, curve = self._level_meta()
        lvl = (
            curve.level_for_xp(int(xp), max_level=cap)
            if isinstance(curve, ExperienceCurve)
            else 0
        )
        self.lbl_xp_level.setText(f"ур. {lvl}")
        self._sync_level_spin_from_xp()

    def _genetic_value_label(self, row_name: str) -> str:
        title = self._genetic_value_titles.get(row_name, "")
        if title and title != row_name:
            return f"{title} ({row_name})"
        return row_name

    def _set_combo_item_tooltip(self, combo: QComboBox, index: int, text: str) -> None:
        if index < 0:
            return
        try:
            combo.setItemData(index, text, Qt.ToolTipRole)
        except Exception:
            pass

    @staticmethod
    def _mount_type_from_blob(data: Optional[List[int]]) -> str:
        if not data:
            return ""
        actor = (mount_blob_get_fstring(data, "ActorClassName", None) or "").strip()
        if actor == "BP_Tame_Cat_C":
            return "Cat"
        if actor == "BP_Tamed_Wolf_Snow_C":
            return "Snow_Wolf"
        m = re.match(r"BP_Mount_(.+?)_C$", actor)
        if m:
            raw = (m.group(1) or "").strip()
            return MOUNT_TYPE_ALIASES.get(raw, raw)

        ai = (mount_blob_get_fstring(data, "AISetupRowName", None) or "").strip()
        if ai.startswith("Mount_") and len(ai) > len("Mount_"):
            raw = ai[len("Mount_") :].strip()
            return MOUNT_TYPE_ALIASES.get(raw, raw)
        return ""

    def _current_phenotypes_for_mount(self) -> List[GamePhenotype]:
        mtype = self._current_mount_type()
        if not mtype:
            return []
        if mtype in UNSUPPORTED_PHENOTYPE_TYPES:
            return []
        direct = self._mount_phenotypes.get(mtype, [])
        if direct:
            return direct
        alias = MOUNT_PHENOTYPE_ALIASES.get(mtype, "")
        return self._mount_phenotypes.get(alias, []) if alias else []

    def _refresh_sex_selector(self, data: List[int]) -> None:
        current = mount_blob_get_int(data, "Sex")
        self.cmb_sex.blockSignals(True)
        self.cmb_sex.clear()
        self.cmb_sex.setEnabled(False)
        if current is None:
            self.cmb_sex.addItem("Нет данных")
            self.cmb_sex.setToolTip("У этого питомца в RecorderBlob нет тега Sex.")
            self.cmb_sex.blockSignals(False)
            return

        for value, label in self.SEX_CHOICES:
            idx = self.cmb_sex.count()
            self.cmb_sex.addItem(label, int(value))
            self._set_combo_item_tooltip(
                self.cmb_sex,
                idx,
                "Пол хранится как IntProperty Sex.\n"
                f"Текущее значение списка: {int(value)}",
            )

        idx = self.cmb_sex.findData(int(current))
        if idx < 0:
            idx = self.cmb_sex.count()
            self.cmb_sex.addItem(f"Пользовательское ({int(current)})", int(current))
            self._set_combo_item_tooltip(
                self.cmb_sex,
                idx,
                f"В сейве записано нестандартное значение Sex = {int(current)}",
            )
        self.cmb_sex.setCurrentIndex(idx)
        self.cmb_sex.setEnabled(True)
        self.cmb_sex.setToolTip(
            "Пол питомца. Значения 1/2 показаны по реальным данным сейва."
        )
        self.cmb_sex.blockSignals(False)

    def _refresh_phenotype_selector(self, data: List[int]) -> None:
        mtype = self._current_mount_type()
        unsupported_type = mtype in UNSUPPORTED_PHENOTYPE_TYPES
        skin_value = mount_blob_get_int_variable(data, "CosmeticSkinIndex")
        applied_variation = mount_blob_get_int(data, "Variation")
        current = skin_value
        if (
            current is None
            and isinstance(applied_variation, int)
            and int(applied_variation) >= 0
        ):
            current = int(applied_variation)
        elif (
            isinstance(current, int)
            and int(current) >= 0
            and isinstance(applied_variation, int)
            and int(applied_variation) >= 0
            and int(applied_variation) != int(current)
        ):
            # If the save is desynced, show the actually applied visual variant.
            current = int(applied_variation)
        phenotypes = self._current_phenotypes_for_mount()

        self.cmb_phenotype.blockSignals(True)
        self.cmb_phenotype.clear()
        self.cmb_phenotype.setCurrentIndex(-1)
        self.cmb_phenotype.setEnabled(False)
        self.sb_phenotype_raw.blockSignals(True)
        self.sb_phenotype_raw.setValue(-1)
        self.sb_phenotype_raw.setEnabled(False)
        self.sb_phenotype_raw.blockSignals(False)

        if current is None:
            self.cmb_phenotype.setToolTip(
                "У этого питомца в IntVariables нет CosmeticSkinIndex."
            )
            self.cmb_phenotype.blockSignals(False)
            return

        if unsupported_type:
            self.cmb_phenotype.addItem(
                f"Не поддерживается для {mtype} (raw={int(current)})", int(current)
            )
            self.cmb_phenotype.setCurrentIndex(0)
            self.cmb_phenotype.setToolTip(
                "Для этого вида в локальных данных игры не найдено подтверждённой таблицы Variations.\n"
                "Игра может игнорировать CosmeticSkinIndex/Variation и оставлять дефолтный внешний вид."
            )
            self.cmb_phenotype.blockSignals(False)
            self.sb_phenotype_raw.setToolTip(
                "Для этого вида phenotype-правка отключена: по текущим данным игры внешний вид не применяется."
            )
            return

        auto_idx = self.cmb_phenotype.count()
        self.cmb_phenotype.addItem("Авто / как в игре (-1)", -1)
        self._set_combo_item_tooltip(
            self.cmb_phenotype,
            auto_idx,
            "Raw value: -1\nИгра сама выберет визуальный вариант.",
        )

        for pheno in phenotypes:
            idx = self.cmb_phenotype.count()
            self.cmb_phenotype.addItem(_mount_variation_label(pheno), pheno.stored_value)
            self._set_combo_item_tooltip(
                self.cmb_phenotype,
                idx,
                f"Raw value: {int(pheno.stored_value)}\n"
                f"Редкость: {pheno.rarity_label}\n"
                f"Шанс: {float(pheno.chance_percent):.2f}%\n"
                f"Ассет: {pheno.asset_name or 'n/a'}",
            )

        idx = self.cmb_phenotype.findData(int(current))
        if idx < 0:
            label = f"Текущее значение ({int(current)})"
            idx = self.cmb_phenotype.count()
            self.cmb_phenotype.addItem(label, int(current))
            self._set_combo_item_tooltip(
                self.cmb_phenotype,
                idx,
                f"Raw value: {int(current)}\n"
                "Для этого вида нет подтверждённой таблицы визуальных вариаций.",
            )
        self.cmb_phenotype.setCurrentIndex(idx)
        self.cmb_phenotype.setEnabled(True)
        self.cmb_phenotype.setToolTip(
            "Фенотип / визуальный вариант. Для известных видов справа в названии показан шанс появления.\n"
            f"CosmeticSkinIndex: {int(skin_value) if isinstance(skin_value, int) else 'нет'}\n"
            f"Variation: {int(applied_variation) if isinstance(applied_variation, int) else 'нет'}"
        )
        self.cmb_phenotype.blockSignals(False)
        self.sb_phenotype_raw.blockSignals(True)
        self.sb_phenotype_raw.setValue(int(current))
        self.sb_phenotype_raw.setEnabled(True)
        self.sb_phenotype_raw.blockSignals(False)

    def _refresh_genetics(self) -> None:
        self.radar_genetics.clear()

        data = self._blob_data()
        self.cmb_sex.blockSignals(True)
        self.cmb_sex.clear()
        self.cmb_sex.setEnabled(False)
        self.cmb_sex.blockSignals(False)
        self.cmb_phenotype.blockSignals(True)
        self.cmb_phenotype.clear()
        self.cmb_phenotype.setCurrentIndex(-1)
        self.cmb_phenotype.setEnabled(False)
        self.cmb_phenotype.blockSignals(False)
        self.sb_phenotype_raw.blockSignals(True)
        self.sb_phenotype_raw.setValue(-1)
        self.sb_phenotype_raw.setEnabled(False)
        self.sb_phenotype_raw.blockSignals(False)
        self.cmb_lineage.blockSignals(True)
        self.cmb_lineage.clear()
        self.cmb_lineage.setCurrentIndex(-1)
        self.cmb_lineage.setEditText("")
        self.cmb_lineage.blockSignals(False)
        self.cb_has_genetics.blockSignals(True)
        self.cb_has_genetics.setChecked(False)
        self.cb_has_genetics.setEnabled(False)
        self.cb_has_genetics.blockSignals(False)

        if not data:
            return

        self._refresh_sex_selector(data)
        self._refresh_phenotype_selector(data)

        lineage_pairs = sorted(
            self._genetic_lineage_titles.items(),
            key=lambda kv: (kv[1] or kv[0]).lower(),
        )
        self.cmb_lineage.blockSignals(True)
        for row_name, title in lineage_pairs:
            text = f"{title} ({row_name})" if title and title != row_name else row_name
            self.cmb_lineage.addItem(text, row_name)

        current_lineage = mount_blob_get_fstring(data, "Lineage", "NameProperty") or ""
        if current_lineage:
            idx = self.cmb_lineage.findData(current_lineage)
            if idx < 0:
                self.cmb_lineage.addItem(current_lineage, current_lineage)
                idx = self.cmb_lineage.findData(current_lineage)
            if idx >= 0:
                self.cmb_lineage.setCurrentIndex(idx)
            else:
                self.cmb_lineage.setCurrentText(current_lineage)
        else:
            self.cmb_lineage.setCurrentIndex(-1)
            self.cmb_lineage.setEditText("")
        self.cmb_lineage.blockSignals(False)

        has_generated = mount_blob_get_bool(data, "bHasGeneratedGenetics")
        self.cb_has_genetics.blockSignals(True)
        if has_generated is not None:
            self.cb_has_genetics.setChecked(bool(has_generated))
            self.cb_has_genetics.setEnabled(True)
            self.cb_has_genetics.setToolTip(
                "Служебный флаг игры: у сущности уже создана генетическая запись.\n"
                "Обычно должен быть включён у животных с генетикой."
            )
        else:
            self.cb_has_genetics.setToolTip(
                "У этого питомца в RecorderBlob нет тега bHasGeneratedGenetics."
            )
        self.cb_has_genetics.blockSignals(False)

        genetics = mount_blob_list_genetics(data)
        by_name = {g.value_name: g for g in genetics}
        ordered_names: List[str] = []
        if self._genetic_value_order:
            ordered_names = [
                n for n in self._genetic_value_order if isinstance(n, str) and n
            ]
            for g in genetics:
                if g.value_name not in ordered_names:
                    ordered_names.append(g.value_name)
        else:
            ordered_names = self.DEFAULT_GENETIC_ORDER[:]
            for g in genetics:
                if g.value_name not in ordered_names:
                    ordered_names.append(g.value_name)

        entries: List[Tuple[str, str, int, int, str, bool]] = []
        for name in ordered_names:
            g = by_name.get(name)
            value = int(g.value) if g else 0
            value_offset = int(g.value_offset) if g else -1
            editable = g is not None
            short = (self._genetic_value_short.get(name) or "").strip()
            if not short:
                short = self.DEFAULT_GENETIC_SHORT.get(name, "") or name[:6].upper()
            title = self._genetic_value_titles.get(name) or name
            entries.append((name, short, value, value_offset, title, editable))
        self.radar_genetics.set_entries(entries)

    def _sex_changed(self, _index: int) -> None:
        data = self._blob_data()
        if not data:
            return
        raw_value = self.cmb_sex.currentData()
        if not isinstance(raw_value, int):
            return
        if mount_blob_set_int(data, "Sex", int(raw_value)):
            self.changed.emit()

    def _apply_phenotype_value(self, raw_value: int) -> None:
        data = self._blob_data()
        if not data:
            return
        synced_type = self._sync_mount_type_from_blob()
        current_skin = mount_blob_get_int_variable(data, "CosmeticSkinIndex")
        current_variation = mount_blob_get_int(data, "Variation")

        changed = False
        if current_skin is None or int(current_skin) != int(raw_value):
            if mount_blob_set_int_variable(data, "CosmeticSkinIndex", int(raw_value)):
                changed = True

        # Explicit phenotype selection must also update the applied visual variation,
        # otherwise the game can keep showing the stale/default look.
        if int(raw_value) >= 0:
            if current_variation is None or int(current_variation) != int(raw_value):
                if mount_blob_set_int(data, "Variation", int(raw_value)):
                    changed = True

        if changed or synced_type:
            self.changed.emit()
            self._refresh_phenotype_selector(data)

    def _phenotype_changed(self, _index: int) -> None:
        raw_value = self.cmb_phenotype.currentData()
        if not isinstance(raw_value, int):
            return
        self._apply_phenotype_value(int(raw_value))

    def _phenotype_raw_changed(self, value: int) -> None:
        self._apply_phenotype_value(int(value))

    def _lineage_changed(self, _text: str) -> None:
        data = self._blob_data()
        if not data:
            return
        row_name = ""
        current_text = self.cmb_lineage.currentText().strip()
        current_index = int(self.cmb_lineage.currentIndex())
        if (
            current_index >= 0
            and current_text
            and current_text == self.cmb_lineage.itemText(current_index)
        ):
            current_data = self.cmb_lineage.currentData()
            if isinstance(current_data, str) and current_data.strip():
                row_name = current_data.strip()
        if not row_name:
            row_name = current_text
        if not row_name:
            return
        current_lineage_name = (
            mount_blob_get_fstring(data, "LineageName", "NameProperty") or ""
        ).strip()
        changed = mount_blob_set_fstring(data, "Lineage", "NameProperty", row_name)
        # Some working pets keep LineageName as the FName sentinel "None".
        # Preserve that shape instead of forcing a mirrored lineage name.
        lineage_name_target = (
            "None" if current_lineage_name.casefold() == "none" else row_name
        )
        line_name_changed = mount_blob_set_fstring(
            data, "LineageName", "NameProperty", lineage_name_target
        )
        if changed or line_name_changed:
            self.changed.emit()

    def _has_generated_genetics_changed(self, state: int) -> None:
        data = self._blob_data()
        if not data:
            return
        enabled = state in (Qt.Checked, Qt.Checked.value)
        if mount_blob_set_bool(data, "bHasGeneratedGenetics", enabled):
            self.changed.emit()

    def _genetic_value_changed(self, row_name: str, v: int) -> None:
        data = self._blob_data()
        if not data:
            return
        if mount_blob_set_genetic_value(data, str(row_name), int(v)):
            self.radar_genetics.update()
            self.changed.emit()

    def _talent_parts(self, row_name: str) -> Tuple[str, str]:
        parts = row_name.split("_")
        if len(parts) >= 3:
            category = parts[1]
            leaf = " ".join(parts[2:])
        else:
            category = "Other"
            leaf = row_name
        return category or "Other", leaf or row_name

    @staticmethod
    def _normalize_talent_lookup(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

    def _current_mount_type(self) -> str:
        if not self.mount:
            return ""
        raw = self.mount.get("MountType", "")
        raw_s = raw.strip() if isinstance(raw, str) else ""
        raw_s = MOUNT_TYPE_ALIASES.get(raw_s, raw_s)
        blob_s = self._mount_type_from_blob(self._blob_data())
        return blob_s or raw_s

    def _sync_mount_type_from_blob(self) -> bool:
        if not self.mount:
            return False
        blob_s = self._mount_type_from_blob(self._blob_data())
        if not blob_s:
            return False
        current = self.mount.get("MountType", "")
        current_s = current.strip() if isinstance(current, str) else ""
        current_s = MOUNT_TYPE_ALIASES.get(current_s, current_s)
        if current_s == blob_s:
            return False
        self.mount["MountType"] = blob_s
        return True

    def _resolve_talent_row_name(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""

        candidates: List[str] = []
        seen: Set[str] = set()

        def add_candidate(row_name: str) -> None:
            rn = row_name.strip() if isinstance(row_name, str) else ""
            if not rn or rn in seen:
                return
            seen.add(rn)
            candidates.append(rn)

        mtype = self._current_mount_type()
        for rn in self._talent_catalog.get(mtype, []) if mtype else []:
            add_candidate(rn)

        data = self._blob_data()
        if data:
            for t in mount_blob_list_talents(data):
                add_candidate(t.row_name)

        if not candidates:
            return ""

        exact = {rn.casefold(): rn for rn in candidates}
        found = exact.get(text.casefold())
        if found:
            return found

        normalized = self._normalize_talent_lookup(text)
        if not normalized:
            return text

        for rn in candidates:
            meta = self._talent_info.get(rn)
            display = meta.display_name if meta and meta.display_name else ""
            _cat, leaf = self._talent_parts(rn)
            aliases = (rn, leaf, display)
            for alias in aliases:
                if self._normalize_talent_lookup(alias) == normalized:
                    return rn

        # When we know the pet's talent catalog, refusing unknown names is safer
        # than writing an invalid row and producing a broken pet record.
        return ""

    def _talent_tooltip_text(
        self, row_name: str, display_name: str, description: str, max_rank: int
    ) -> str:
        lines = [display_name or row_name]
        desc_ru = _translate_pet_talent_description_ru(description)
        if desc_ru:
            lines.append(desc_ru)
        elif description:
            lines.append(description)
        meta = self._talent_info.get(row_name)
        if meta and meta.exact_rank_effects:
            lines.append("")
            lines.append("Точные эффекты по рангам:")
            lines.extend(meta.exact_rank_effects)
        lines.append(f"Штатный максимум: {int(max_rank)}")
        lines.append(
            f"Редактор позволяет завысить ранг вручную до {int(self.TALENT_OVERCAP_UI_MAX)}"
        )
        lines.append(f"RowName: {row_name}")
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _is_pet_talent_meta(meta: Optional[GameTalent]) -> bool:
        if not isinstance(meta, GameTalent):
            return False
        tree = (meta.talent_tree or "").strip()
        return tree == "Creature_Mount_Base" or tree.startswith("Creature_")

    def _talent_picker_rows(
        self, existing: Set[str]
    ) -> List[Tuple[str, str, str, int]]:
        rows: List[Tuple[str, str, str, int]] = []
        seen: Set[str] = set()
        mtype = self._current_mount_type()

        def append_row(rn: str) -> None:
            row_name = (rn or "").strip()
            if not row_name or row_name in seen:
                return
            meta = self._talent_info.get(row_name)
            if (
                meta
                and not self._is_pet_talent_meta(meta)
                and row_name not in existing
                and row_name not in (self._talent_catalog.get(mtype, []) if mtype else [])
            ):
                return
            seen.add(row_name)
            title = meta.display_name if meta and meta.display_name else row_name
            desc_src = meta.description if meta and meta.description else ""
            desc = (
                _translate_pet_talent_description_ru(desc_src)
                or desc_src
                or "Описание отсутствует."
            )
            exact = "\n".join(meta.exact_rank_effects) if meta else ""
            if exact:
                desc = desc + "\n\n" + exact
            max_rank = int(meta.max_rank if meta else 1)
            rows.append((title, desc, row_name, max_rank))

        if mtype:
            for rn in self._talent_catalog.get(mtype, []):
                append_row(rn)
        for rn in sorted(existing):
            append_row(rn)

        return rows

    def _refresh_talents(self) -> None:
        self.talent_tree.clear()
        data = self._blob_data()
        if not data:
            return

        talents = mount_blob_list_talents(data)
        by_name = {t.row_name: t for t in talents}

        mtype = ""
        if self.mount:
            t = self.mount.get("MountType", "")
            mtype = t.strip() if isinstance(t, str) else ""
        known = self._talent_catalog.get(mtype, []) if mtype else []
        desired = known[:] if known else list(by_name.keys())
        if not desired:
            return
        desired = sorted(set(desired))

        groups: Dict[str, QTreeWidgetItem] = {}
        for row_name in desired:
            t = by_name.get(row_name)
            cat, label = self._talent_parts(row_name)
            meta = self._talent_info.get(row_name)
            name_text = meta.display_name if meta and meta.display_name else label
            max_rank = int(meta.max_rank) if meta and meta.max_rank else 4
            desc = meta.description if meta and meta.description else ""
            if cat not in groups:
                groups[cat] = QTreeWidgetItem(self.talent_tree, [cat])
                groups[cat].setFirstColumnSpanned(True)
            leaf = QTreeWidgetItem(groups[cat], [name_text, ""])
            tooltip = self._talent_tooltip_text(row_name, name_text, desc, max_rank)
            leaf.setToolTip(0, tooltip)
            leaf.setToolTip(1, tooltip)

            if t:
                leaf.setData(0, Qt.UserRole, t.rank_value_offset)
            else:
                leaf.setForeground(0, Qt.gray)
                leaf.setData(0, Qt.UserRole, row_name)

            sb = QSpinBox()
            current_rank = int(t.rank) if t else 0
            sb.setRange(0, max(int(self.TALENT_OVERCAP_UI_MAX), max_rank, current_rank))
            sb.setValue(int(t.rank) if t else 0)
            if t:
                sb.valueChanged.connect(
                    lambda v, off=t.rank_value_offset: self._talent_rank_changed(off, v)
                )
            else:
                sb.valueChanged.connect(
                    lambda v, rn=row_name: self._talent_rank_set_or_add(rn, v)
                )
            sb.setToolTip(tooltip)
            self.talent_tree.setItemWidget(leaf, 1, sb)

        self.talent_tree.expandAll()
        self.talent_tree.resizeColumnToContents(1)

    def _name_changed(self, text: str = "") -> None:
        if not self.mount:
            return
        name = text if isinstance(text, str) else self.ed_name.text()
        self.mount["MountName"] = name
        data = self._blob_data()
        if data is not None:
            mount_blob_set_fstring(data, "MountName", "StrProperty", name)
        self._update_title()
        self._refresh_talents()
        self.changed.emit()

    def _type_changed(self, text: str) -> None:
        if not self.mount:
            return
        mtype = text.strip() if isinstance(text, str) else ""
        if not mtype:
            return
        prev_type = self.mount.get("MountType", "")
        prev_type_s = prev_type.strip() if isinstance(prev_type, str) else ""
        self.mount["MountType"] = mtype
        data = self._blob_data()
        if data is not None:
            actor_class = self._guess_actor_class_name(mtype).strip()
            ai = self._mount_ai_setup.get(mtype)
            ai_s = ai.strip() if isinstance(ai, str) else ""

            current_actor = (
                mount_blob_get_fstring(data, "ActorClassName", None) or ""
            ).strip()
            current_ai = (
                mount_blob_get_fstring(data, "AISetupRowName", None) or ""
            ).strip()

            same_type = bool(prev_type_s and prev_type_s == mtype)
            preserve_custom_binding = same_type and (
                (actor_class and current_actor and current_actor != actor_class)
                or (ai_s and current_ai and current_ai != ai_s)
            )

            if not preserve_custom_binding:
                if actor_class:
                    mount_blob_set_fstring(data, "ActorClassName", None, actor_class)

                    obj = (
                        mount_blob_get_fstring(data, "ObjectFName", "NameProperty") or ""
                    )
                    m = re.search(r"_(\d+)$", obj)
                    suffix = m.group(1) if m else ""
                    new_obj = f"{actor_class}_{suffix}" if suffix else actor_class
                    mount_blob_set_fstring(data, "ObjectFName", "NameProperty", new_obj)

                    path = (
                        mount_blob_get_fstring(data, "ActorPathName", "StrProperty")
                        or ""
                    )
                    if "." in path:
                        prefix = path.rsplit(".", 1)[0]
                        mount_blob_set_fstring(
                            data, "ActorPathName", "StrProperty", prefix + "." + new_obj
                        )

                if ai_s:
                    mount_blob_set_fstring(data, "AISetupRowName", None, ai_s)
        self._update_xp_level_label()
        self._update_title()
        self._refresh_genetics()
        self._refresh_talents()
        self.changed.emit()

    def _level_changed(self, v: int) -> None:
        if not self.mount:
            return
        level = max(0, int(v))
        self.mount["MountLevel"] = level

        data = self._blob_data()
        if data is None:
            self.changed.emit()
            return

        cap, _use_pet, curve = self._level_meta()
        target = min(level, cap)
        xp = (
            int(curve.value_at(float(target)))
            if isinstance(curve, ExperienceCurve)
            else 0
        )
        changed = False
        if mount_blob_set_int(data, "Experience", int(xp)):
            changed = True
        self.mount["MountLevel"] = int(target)
        if target != level:
            self.sb_level.blockSignals(True)
            self.sb_level.setValue(int(target))
            self.sb_level.blockSignals(False)
        self.sb_xp.blockSignals(True)
        self.sb_xp.setValue(int(xp))
        self.sb_xp.blockSignals(False)
        self._update_xp_level_label()
        if changed or target != level:
            self.changed.emit()

    def _set_max_level(self) -> None:
        cap, _use_pet, _curve = self._level_meta()
        self.sb_level.setValue(int(cap))

    def _set_all_talents_max(self, state: int) -> None:
        if state not in (Qt.Checked, Qt.Checked.value):
            return
        # Defer heavy work to let the checkbox paint its new state.
        self.cb_all_talents_max.setEnabled(False)
        QTimer.singleShot(0, self._set_all_talents_max_impl)

    def _set_all_talents_max_impl(self) -> None:
        try:
            if not self.mount:
                return
            data = self._blob_data()
            if not data:
                return

            changed = False
            try:
                cap, _use_pet, curve = self._level_meta()
                cur_xp = mount_blob_get_int(data, "Experience") or 0
                target_xp = (
                    int(curve.value_at(float(cap)))
                    if isinstance(curve, ExperienceCurve)
                    else int(cur_xp)
                )
                if int(cur_xp) < int(target_xp):
                    if mount_blob_set_int(data, "Experience", int(target_xp)):
                        self.sb_xp.blockSignals(True)
                        self.sb_xp.setValue(int(target_xp))
                        self.sb_xp.blockSignals(False)
                        changed = True
                if int(self.mount.get("MountLevel", 0) or 0) != int(cap):
                    self.mount["MountLevel"] = int(cap)
                    changed = True
                self.sb_level.blockSignals(True)
                self.sb_level.setValue(int(cap))
                self.sb_level.blockSignals(False)
            except Exception:
                pass

            mtype = self.mount.get("MountType", "")
            mtype_s = mtype.strip() if isinstance(mtype, str) else ""
            all_rows = self._talent_catalog.get(mtype_s, [])
            if not all_rows:
                all_rows = sorted({t.row_name for t in mount_blob_list_talents(data)})

            for rn in all_rows:
                meta = self._talent_info.get(rn)
                max_rank = int(meta.max_rank) if meta and meta.max_rank else 4
                if mount_blob_add_talent(data, rn, max_rank):
                    changed = True

            if changed:
                self._update_xp_level_label()
                self.changed.emit()
                self._refresh_talents()
        finally:
            self.cb_all_talents_max.setEnabled(True)

    @staticmethod
    def _guess_actor_class_name(mtype: str) -> str:
        t = (mtype or "").strip()
        if not t:
            return ""
        if t.lower() == "cat":
            return "BP_Tame_Cat_C"
        if t.lower() == "snow_wolf":
            return "BP_Tamed_Wolf_Snow_C"
        return f"BP_Mount_{t}_C"

    def _talent_rank_changed(self, value_offset: int, v: int) -> None:
        data = self._blob_data()
        if not data:
            return
        if mount_blob_set_int_at_offset(data, int(value_offset), int(v)):
            self.changed.emit()

    def _talent_rank_set_or_add(self, row_name: str, v: int) -> None:
        if int(v) == 0:
            return
        data = self._blob_data()
        if not data:
            return
        resolved = self._resolve_talent_row_name(row_name)
        if not resolved:
            return
        if mount_blob_add_talent(data, resolved, int(v)):
            self.changed.emit()
            self._refresh_talents()

    def _add_missing_talents(self) -> None:
        if not self.mount:
            return
        mtype = self.mount.get("MountType", "")
        if not isinstance(mtype, str) or not mtype.strip():
            return
        mtype = mtype.strip()

        data = self._blob_data()
        if not data:
            return

        known = self._talent_catalog.get(mtype, [])
        if not known:
            return

        existing = {t.row_name for t in mount_blob_list_talents(data)}
        missing = [rn for rn in known if rn not in existing]
        if not missing:
            return

        added = mount_blob_add_missing_talents(data, missing)
        if added:
            self.changed.emit()
            self._refresh_talents()

    def _add_talent_dialog(self) -> None:
        data = self._blob_data()
        if not data:
            return

        existing = {t.row_name for t in mount_blob_list_talents(data)}
        rows = self._talent_picker_rows(existing)
        if not rows:
            QMessageBox.warning(
                self,
                "Добавить навык",
                "Не удалось собрать список навыков для этого питомца.",
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить навык")
        dlg.resize(960, 560)
        lay = QVBoxLayout(dlg)

        search = QLineEdit()
        search.setPlaceholderText("Поиск по названию, описанию или RowName…")
        lay.addWidget(search)

        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["Навык", "Описание"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setTextElideMode(Qt.ElideNone)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for title, desc, rn, max_rank in rows:
            row = tbl.rowCount()
            tbl.insertRow(row)
            it_name = QTableWidgetItem(title)
            it_name.setData(Qt.UserRole, rn)
            it_name.setData(Qt.UserRole + 1, int(max_rank))
            it_name.setToolTip(f"{title}\nRowName: {rn}")
            it_desc = QTableWidgetItem(desc)
            it_desc.setToolTip(f"{desc}\n\nRowName: {rn}")
            tbl.setItem(row, 0, it_name)
            tbl.setItem(row, 1, it_desc)
        tbl.resizeRowsToContents()
        lay.addWidget(tbl, 1)

        lbl_info = QLabel("")
        lbl_info.setStyleSheet("color:#B5BAC1;")
        lbl_info.setWordWrap(True)
        lay.addWidget(lbl_info)

        sb = QSpinBox()
        sb.setRange(0, int(self.TALENT_OVERCAP_UI_MAX))
        sb.setValue(0)

        form = QFormLayout()
        form.addRow("Ранг", sb)
        lay.addLayout(form)

        def refresh_selection_info() -> None:
            row = tbl.currentRow()
            if row < 0:
                lbl_info.setText("Выбери навык из списка.")
                return
            it = tbl.item(row, 0)
            if not it:
                lbl_info.setText("Выбери навык из списка.")
                return
            rn = str(it.data(Qt.UserRole) or "").strip()
            max_rank = int(it.data(Qt.UserRole + 1) or 1)
            lbl_info.setText(
                f"RowName: {rn}\nШтатный максимум: {max_rank}. Редактор позволяет ставить больше."
            )

        def apply_filter() -> None:
            q = search.text().strip().casefold()
            first_visible = -1
            for row in range(tbl.rowCount()):
                it_name = tbl.item(row, 0)
                it_desc = tbl.item(row, 1)
                rn = str(it_name.data(Qt.UserRole) or "") if it_name else ""
                hay = " ".join(
                    [
                        it_name.text() if it_name else "",
                        it_desc.text() if it_desc else "",
                        rn,
                    ]
                ).casefold()
                hide = bool(q and q not in hay)
                tbl.setRowHidden(row, hide)
                if not hide and first_visible < 0:
                    first_visible = row
            current = tbl.currentRow()
            if first_visible >= 0 and (current < 0 or tbl.isRowHidden(current)):
                tbl.selectRow(first_visible)
            refresh_selection_info()

        search.textChanged.connect(apply_filter)
        tbl.currentCellChanged.connect(lambda *_args: refresh_selection_info())
        tbl.itemDoubleClicked.connect(lambda *_args: dlg.accept())

        if tbl.rowCount() > 0:
            tbl.selectRow(0)
        refresh_selection_info()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        row = tbl.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Добавить навык", "Сначала выбери навык из списка.")
            return
        it = tbl.item(row, 0)
        rn = str(it.data(Qt.UserRole) or "").strip() if it else ""
        rn = self._resolve_talent_row_name(rn)
        if not rn:
            QMessageBox.warning(
                self,
                "Добавить навык",
                "Не удалось распознать навык. Выбери его из списка или укажи корректный полный RowName.",
            )
            return

        if mount_blob_add_talent(data, rn, int(sb.value())):
            self.changed.emit()
            self._refresh_talents()

    def _stat_changed(self, prop: str, v: int) -> None:
        data = self._blob_data()
        if not data:
            return
        if mount_blob_set_int(data, prop, int(v)):
            if prop == "Experience":
                lvl = self._sync_level_spin_from_xp()
                if self.mount:
                    self.mount["MountLevel"] = int(lvl)
                    self._update_xp_level_label()
            elif self.mount:
                current_level = int(self.sb_level.value())
                self.mount["MountLevel"] = current_level
            self.changed.emit()


class PetsTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None
        self._mounts: List[Dict[str, Any]] = []
        self._talent_catalog: Dict[str, List[str]] = {}
        self._xp_curve_mount: ExperienceCurve = DEFAULT_MOUNT_XP_CURVE
        self._xp_curve_pet: ExperienceCurve = DEFAULT_PET_XP_CURVE
        self._edited_mount_ids: Set[int] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)

        self.info = QLabel("")
        self.info.setStyleSheet("color:#B5BAC1;")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по имени/типу…")
        self.search.setFixedWidth(180)
        self.search.textChanged.connect(self._apply_filter)

        top = QHBoxLayout()
        top.addWidget(self.info, 1)
        self.btn_add = QPushButton("Добавить питомца")
        self.btn_add.clicked.connect(self._add_pet)
        top.addWidget(self.btn_add)
        self.btn_custom = QPushButton("Кастомный моб/босс")
        self.btn_custom.clicked.connect(self._add_custom_mount)
        top.addWidget(self.btn_custom)
        self.btn_clone = QPushButton("Клонировать")
        self.btn_clone.clicked.connect(self._clone_pet)
        self.btn_clone.setEnabled(False)
        top.addWidget(self.btn_clone)
        self.btn_return = QPushButton("Вернуть из миров")
        self.btn_return.setToolTip(
            "Попытаться восстановить питомцев из Prospects/*.json обратно в Mounts.json"
        )
        self.btn_return.clicked.connect(self._return_from_worlds)
        self.btn_return.setEnabled(False)
        top.addWidget(self.btn_return)
        top.addWidget(QLabel("Поиск:"))
        top.addWidget(self.search)
        root.addLayout(top)

        self.split = QSplitter(Qt.Horizontal)
        root.addWidget(self.split, 1)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)

        self.tbl = QTableWidget(0, 9)
        self.tbl.setHorizontalHeaderLabels(
            ["Питомец", "Тип", "Ур. (XP)", "ХП", "Стам.", "Еда", "Вода", "Кисл.", "XP"]
        )
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setTextElideMode(Qt.ElideNone)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSortingEnabled(True)
        header = self.tbl.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(2, 9):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.currentCellChanged.connect(self._sel_changed)
        self.tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._pets_context_menu)
        left_l.addWidget(self.tbl, 1)
        self.split.addWidget(left)

        self.details = MountDetails()
        self.details.changed.connect(self._details_changed)
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.details_scroll.setStyleSheet("QScrollArea { border: none; }")
        self.details_scroll.setWidget(self.details)
        self.split.addWidget(self.details_scroll)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 3)

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data
        if game_data:
            self._xp_curve_mount = (
                game_data.get_experience_curve("C_MountExperienceGrowth")
                or DEFAULT_MOUNT_XP_CURVE
            )
            self._xp_curve_pet = (
                game_data.get_experience_curve("C_PetExperienceGrowth")
                or DEFAULT_PET_XP_CURVE
            )
            self.details.set_experience_curves(self._xp_curve_mount, self._xp_curve_pet)
            self.details.set_talent_info(game_data.talents)
            self.details.set_mount_phenotypes(game_data.mount_phenotypes)
            self.details.set_mount_ai_setup(game_data.mount_ai_setup)
            self.details.set_genetics_info(
                game_data.genetic_value_titles,
                game_data.genetic_lineage_titles,
                game_data.genetic_value_short,
                game_data.genetic_value_order,
            )
            for t in game_data.mount_types:
                if self.details.cmb_type.findText(t) < 0:
                    self.details.cmb_type.addItem(t)
        else:
            self._xp_curve_mount = DEFAULT_MOUNT_XP_CURVE
            self._xp_curve_pet = DEFAULT_PET_XP_CURVE
            self.details.set_experience_curves(self._xp_curve_mount, self._xp_curve_pet)
            self.details.set_talent_info({})
            self.details.set_mount_phenotypes({})
            self.details.set_mount_ai_setup({})
            self.details.set_genetics_info({}, {}, {}, [])

    def load(self) -> None:
        self._edited_mount_ids.clear()
        if not self.model.mounts_path:
            self.info.setText("Mounts.json не найден в выбранном сейве.")
            self._mounts = []
            self._talent_catalog = {}
            self.tbl.setRowCount(0)
            self.details.set_talent_catalog({})
            self.details.set_talent_info({})
            self.details.set_mount(None)
            self.btn_add.setEnabled(False)
            self.btn_custom.setEnabled(False)
            self.btn_clone.setEnabled(False)
            self.btn_return.setEnabled(False)
            return

        mounts = self.model.mounts.get("SavedMounts", [])
        self._mounts = mounts if isinstance(mounts, list) else []
        self._talent_catalog = self._build_talent_catalog()
        self.details.set_talent_catalog(self._talent_catalog)
        if self._game_data:
            self.details.set_talent_info(self._game_data.talents)
        self.info.setText("")
        self.btn_add.setEnabled(True)
        self.btn_custom.setEnabled(True)
        self.btn_clone.setEnabled(False)
        self.btn_return.setEnabled(True)

        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._apply_filter()

    def _build_talent_catalog(self) -> Dict[str, List[str]]:
        out: Dict[str, set[str]] = {}
        if self._game_data and self._game_data.mount_talents:
            for k, v in self._game_data.mount_talents.items():
                out[k] = set(v)
        for m in self._mounts:
            if not isinstance(m, dict):
                continue
            mtype = m.get("MountType", "")
            if not isinstance(mtype, str) or not mtype.strip():
                continue
            mtype = mtype.strip()
            rec = m.get("RecorderBlob")
            if not isinstance(rec, dict):
                continue
            data = rec.get("BinaryData")
            if not isinstance(data, list):
                continue
            talents = mount_blob_list_talents(data)
            if not talents:
                continue
            out.setdefault(mtype, set()).update(t.row_name for t in talents)
        return {k: sorted(v) for k, v in out.items()}

    def _known_types(self) -> List[str]:
        defaults = ["Horse", "Horse_Standard", "Cat", "Moa"]
        found = []
        for m in self._mounts:
            if not isinstance(m, dict):
                continue
            t = m.get("MountType", "")
            if isinstance(t, str) and t.strip() and t.strip() not in found:
                found.append(t.strip())
        out = []
        game_types = self._game_data.mount_types if self._game_data else []
        for t in defaults + game_types + found:
            if t and t not in out:
                out.append(t)
        return out

    def _preferred_custom_mount_template(self) -> Optional[Dict[str, Any]]:
        return _pick_custom_mount_template(self._mounts)

    def _custom_mob_choices(self) -> List[GameCustomMobChoice]:
        if not self._game_data:
            return []
        return list(self._game_data.custom_mob_choices or [])

    @staticmethod
    def _custom_mob_warning(choice: GameCustomMobChoice) -> str:
        actor = _custom_choice_actor_class(choice)
        parts = [f"AISetup: {choice.ai_setup}", f"ActorClass: {actor}"]
        if choice.tags:
            parts.append("Теги: " + ", ".join(choice.tags))
        if choice.is_boss:
            parts.append(
                "Для боссов используется совместимый pet-actor, как в старом рабочем IceBreaker."
            )
        else:
            parts.append(
                "Обычный NPC-класс тоже может оказаться неуправляемым, это ограничение игры."
            )
        return "\n".join(parts)

    @staticmethod
    def _mount_blob_data(mount: Dict[str, Any]) -> Optional[List[int]]:
        rec = mount.get("RecorderBlob")
        if not isinstance(rec, dict):
            return None
        data = rec.get("BinaryData")
        if not isinstance(data, list) or not all(isinstance(x, int) for x in data):
            return None
        return data

    @staticmethod
    def _extract_obj_suffix(obj_name: str) -> Optional[int]:
        if not isinstance(obj_name, str):
            return None
        m = re.search(r"_(\d+)$", obj_name)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _used_mount_icon_ids(self, mounts_list: List[Dict[str, Any]]) -> set[int]:
        used: set[int] = set()
        for m in mounts_list:
            if not isinstance(m, dict):
                continue
            icon = m.get("MountIconName")
            if isinstance(icon, str) and icon.isdigit():
                used.add(int(icon))
            data = self._mount_blob_data(m)
            if data:
                gid = mount_blob_get_int(data, "IcarusActorGUID")
                if isinstance(gid, int):
                    used.add(int(gid))
        if self.model.root:
            d = os.path.join(self.model.root, "Mounts")
            try:
                for fn in os.listdir(d):
                    if fn.lower().endswith(".exr"):
                        stem = fn[:-4]
                        if stem.isdigit():
                            used.add(int(stem))
            except Exception:
                pass
        return used

    def _used_object_suffixes(self, mounts_list: List[Dict[str, Any]]) -> set[int]:
        used: set[int] = set()
        for m in mounts_list:
            if not isinstance(m, dict):
                continue
            data = self._mount_blob_data(m)
            if not data:
                continue
            obj = mount_blob_get_fstring(data, "ObjectFName", "NameProperty")
            suf = self._extract_obj_suffix(obj or "")
            if isinstance(suf, int):
                used.add(suf)
        return used

    @staticmethod
    def _mount_guid(mount: Any) -> Optional[str]:
        if not isinstance(mount, dict):
            return None
        guid = mount.get("DatabaseGUID")
        if isinstance(guid, str) and guid and guid.lower() != "noguid":
            return guid.upper()
        return None

    @staticmethod
    def _mount_icon(mount: Any) -> Optional[str]:
        if not isinstance(mount, dict):
            return None
        icon = mount.get("MountIconName")
        if isinstance(icon, str) and icon.strip():
            return icon.strip()
        return None

    def _same_mount(self, a: Any, b: Any) -> bool:
        if a is b:
            return True
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        ia, ib = self._mount_icon(a), self._mount_icon(b)
        if ia and ib and ia == ib:
            return True
        ga, gb = self._mount_guid(a), self._mount_guid(b)
        if ga and gb and ga == gb:
            return True
        return a == b

    def _mount_index_of(self, mount: Any) -> Optional[int]:
        if not isinstance(mount, dict):
            return None
        for idx, cur in enumerate(self._mounts):
            if self._same_mount(cur, mount):
                return idx
        return None

    def _mount_from_item(
        self, item: Optional[QTableWidgetItem]
    ) -> Optional[Dict[str, Any]]:
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._mounts):
            mount = self._mounts[idx]
            if isinstance(mount, dict):
                return mount
        raw = item.data(Qt.UserRole + 1)
        if isinstance(raw, dict):
            idx = self._mount_index_of(raw)
            if isinstance(idx, int) and 0 <= idx < len(self._mounts):
                mount = self._mounts[idx]
                if isinstance(mount, dict):
                    return mount
        return None

    def _row_for_mount(self, mount: Any) -> int:
        for row in range(self.tbl.rowCount()):
            if self._same_mount(self._mount_from_item(self.tbl.item(row, 0)), mount):
                return row
        return -1

    @staticmethod
    def _alloc_unused_int(
        used: set[int], lo: int, hi: int, max_tries: int = 2000
    ) -> int:
        for _ in range(max_tries):
            v = random.randint(int(lo), int(hi))
            if v not in used:
                used.add(v)
                return v
        v = int(hi)
        while v in used:
            v -= 1
            if v <= lo:
                raise RuntimeError("Не удалось подобрать уникальный идентификатор.")
        used.add(v)
        return v

    def _copy_mount_icon(self, src_icon: Optional[int], dst_icon: int) -> None:
        if not self.model.root:
            return
        d = os.path.join(self.model.root, "Mounts")
        if not os.path.isdir(d):
            return
        dst = os.path.join(d, f"{int(dst_icon)}.exr")
        if os.path.exists(dst):
            return
        src = ""
        if src_icon:
            cand = os.path.join(d, f"{int(src_icon)}.exr")
            if os.path.isfile(cand):
                src = cand
        if not src:
            try:
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith(".exr"):
                        cand = os.path.join(d, fn)
                        if os.path.isfile(cand):
                            src = cand
                            break
            except Exception:
                src = ""
        if not src:
            return
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass

    def _apply_mount_identity(
        self, mount: Dict[str, Any], new_icon_id: int, new_obj_suffix: int
    ) -> None:
        mount["MountIconName"] = str(int(new_icon_id))
        data = self._mount_blob_data(mount)
        if not data:
            return
        mount_blob_set_int(data, "IcarusActorGUID", int(new_icon_id))

        actor_class = mount_blob_get_fstring(data, "ActorClassName") or ""
        actor_class = actor_class.strip()
        if actor_class:
            obj = f"{actor_class}_{int(new_obj_suffix)}"
            mount_blob_set_fstring(data, "ObjectFName", "NameProperty", obj)

            path = mount_blob_get_fstring(data, "ActorPathName", "StrProperty") or ""
            if "." in path:
                prefix = path.rsplit(".", 1)[0]
                mount_blob_set_fstring(
                    data, "ActorPathName", "StrProperty", prefix + "." + obj
                )

    def _flush_pending_mount_editor(self) -> None:
        try:
            self.details.commit_pending_edits()
        except Exception:
            focus = QApplication.focusWidget()
            if focus is not None and self.details.isAncestorOf(focus):
                try:
                    focus.clearFocus()
                except Exception:
                    pass
            for sb in self.details.findChildren(QAbstractSpinBox):
                try:
                    sb.interpretText()
                except Exception:
                    pass

    def prepare_save(self) -> None:
        self._flush_pending_mount_editor()
        if not self._edited_mount_ids:
            return
        mounts_list = self.model.mounts.get("SavedMounts", [])
        if not isinstance(mounts_list, list) or not mounts_list:
            self._edited_mount_ids.clear()
            return

        used_icons = self._used_mount_icon_ids(mounts_list)
        used_suffixes = self._used_object_suffixes(mounts_list)
        touched = False
        selected_replacement: Optional[Dict[str, Any]] = None
        current_mount = (
            self.details.mount if isinstance(self.details.mount, dict) else None
        )

        for idx, mount in enumerate(mounts_list):
            if not isinstance(mount, dict) or id(mount) not in self._edited_mount_ids:
                continue

            clone = copy.deepcopy(mount)
            old_icon = None
            icon_raw = mount.get("MountIconName")
            if isinstance(icon_raw, str) and icon_raw.isdigit():
                old_icon = int(icon_raw)

            new_icon = self._alloc_unused_int(used_icons, 100000, 9999999)
            new_suffix = self._alloc_unused_int(used_suffixes, 2000000000, 2147483647)
            self._apply_mount_identity(clone, new_icon, new_suffix)
            self._copy_mount_icon(old_icon, new_icon)

            guid = clone.get("DatabaseGUID")
            if isinstance(guid, str) and guid and guid.lower() != "noguid":
                clone["DatabaseGUID"] = uuid.uuid4().hex.upper()

            mounts_list[idx] = clone
            if current_mount is mount:
                selected_replacement = clone

            touched = True

        self._edited_mount_ids.clear()
        if not touched:
            return

        if selected_replacement is not None:
            self.details.mount = selected_replacement
        row = self._row_for_mount(self.details.mount)
        if row >= 0 and self.details.mount:
            was_sorting = self.tbl.isSortingEnabled()
            self.tbl.setSortingEnabled(False)
            self.tbl.blockSignals(True)
            self._set_row(row, self.details.mount)
            self.tbl.blockSignals(False)
            self.tbl.setSortingEnabled(was_sorting)
            for c in range(2, 9):
                self.tbl.resizeColumnToContents(c)

    def _add_pet(self) -> None:
        if not self.model.mounts_path:
            QMessageBox.warning(
                self, "Нельзя добавить", "Mounts.json не найден в сейве."
            )
            return

        if not self._mounts:
            QMessageBox.warning(
                self,
                "Нельзя добавить",
                "Не найден шаблон питомца (нужен хотя бы один существующий).",
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить питомца")
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        ed_name = QLineEdit()
        cmb_type = QComboBox()
        cmb_type.setEditable(True)
        cmb_type.addItems(self._known_types())
        sb_level = QSpinBox()
        sb_level.setRange(0, 10**9)
        sb_level.setValue(1)

        form.addRow("Имя", ed_name)
        form.addRow("Тип", cmb_type)
        form.addRow("Уровень", sb_level)
        lay.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name = ed_name.text().strip() or "New Pet"
        mtype = cmb_type.currentText().strip()
        level = int(sb_level.value())

        template = None
        if mtype:
            for m in self._mounts:
                if isinstance(m, dict) and m.get("MountType") == mtype:
                    template = m
                    break
        if not isinstance(template, dict):
            template = (
                self.details.mount
                if isinstance(self.details.mount, dict)
                else self._mounts[0]
            )

        new_mount = copy.deepcopy(template)
        new_mount["MountName"] = name
        if mtype:
            new_mount["MountType"] = mtype
        new_mount["MountLevel"] = level

        # Keep RecorderBlob consistent with top-level fields and ensure unique identity.
        data = self._mount_blob_data(new_mount)
        if data is not None:
            mount_blob_set_fstring(data, "MountName", "StrProperty", name)

            template_type = (
                template.get("MountType") if isinstance(template, dict) else ""
            )
            type_changed = (
                bool(mtype)
                and isinstance(template_type, str)
                and template_type != mtype
            )
            if mtype and type_changed:
                ai = (
                    self._game_data.mount_ai_setup.get(mtype) if self._game_data else ""
                )
                if isinstance(ai, str) and ai:
                    mount_blob_set_fstring(data, "AISetupRowName", None, ai)
                actor_class = MountDetails._guess_actor_class_name(mtype)
                if actor_class:
                    mount_blob_set_fstring(data, "ActorClassName", None, actor_class)

            # ✅ ВАЖНО: сброс прогресса, иначе новый питомец наследует XP/таланты шаблона и становится "максимальным"
            mount_blob_set_int(data, "Experience", 0)  # XP
            mount_blob_set_int(
                data, "MountLevel", level
            )  # если такой IntProperty реально есть в blob — проставим

            # таланты: обнуляем ранги (чтобы не было сразу 50 потраченных поинтов)
            try:
                for t in mount_blob_list_talents(data):
                    mount_blob_set_int_at_offset(data, t.rank_value_offset, 0)
            except Exception:
                pass

        mounts_list = self.model.mounts.setdefault("SavedMounts", [])
        if not isinstance(mounts_list, list):
            self.model.mounts["SavedMounts"] = mounts_list = []

        used_icons = self._used_mount_icon_ids(mounts_list)
        used_suffixes = self._used_object_suffixes(mounts_list)
        new_icon = self._alloc_unused_int(used_icons, 100000, 9999999)
        new_suffix = self._alloc_unused_int(used_suffixes, 2000000000, 2147483647)

        src_icon = None
        icon_raw = template.get("MountIconName") if isinstance(template, dict) else None
        if isinstance(icon_raw, str) and icon_raw.isdigit():
            src_icon = int(icon_raw)

        self._apply_mount_identity(new_mount, new_icon, new_suffix)
        self._copy_mount_icon(src_icon, new_icon)

        mounts_list.append(new_mount)
        self._mounts = mounts_list

        self.model.dirty_mounts = True
        self.search.setText("")
        self._apply_filter()

        # выделяем добавленного питомца по уникальному MountIconName (надёжнее, чем `is`)
        new_icon_name = new_mount.get("MountIconName")
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, 0)
            if not it:
                continue
            d = self._mount_from_item(it)
            if isinstance(d, dict) and d.get("MountIconName") == new_icon_name:
                self.tbl.selectRow(r)
                break

        self.mark_dirty()

    def _create_custom_mount_from_template(
        self,
        template: Dict[str, Any],
        name: str,
        mtype: str,
        level: int,
        actor_class: str,
        ai_setup: str,
        preserve_progress: bool,
        reset_talents: bool,
        copy_icon: bool,
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        warns: List[str] = []
        new_mount = copy.deepcopy(template)
        new_mount["MountName"] = name
        if mtype:
            new_mount["MountType"] = mtype
        if preserve_progress:
            new_mount["MountLevel"] = int(template.get("MountLevel", level) or level)
        else:
            new_mount["MountLevel"] = int(level)

        data = self._mount_blob_data(new_mount)
        if data is not None:
            mount_blob_set_fstring(data, "MountName", "StrProperty", name)

            if actor_class:
                if not mount_blob_set_fstring(data, "ActorClassName", None, actor_class):
                    warns.append(
                        "ActorClassName: не удалось записать в RecorderBlob."
                    )

            if ai_setup:
                if not mount_blob_set_fstring(data, "AISetupRowName", None, ai_setup):
                    warns.append("AISetupRowName: не удалось записать в RecorderBlob.")
            elif mtype and self._game_data:
                ai = self._game_data.mount_ai_setup.get(mtype)
                if isinstance(ai, str) and ai:
                    mount_blob_set_fstring(data, "AISetupRowName", None, ai)

            if not preserve_progress:
                try:
                    xp_curve = (
                        self._xp_curve_pet
                        if actor_class.lower().startswith("bp_tame_")
                        else self._xp_curve_mount
                    )
                    xp = int(xp_curve.value_at(float(int(level))))
                except Exception:
                    xp = 0
                mount_blob_set_int(data, "Experience", int(xp))
                mount_blob_set_int(data, "MountLevel", int(level))

            if reset_talents:
                try:
                    for t in mount_blob_list_talents(data):
                        mount_blob_set_int_at_offset(data, t.rank_value_offset, 0)
                except Exception:
                    pass

            if actor_class and not actor_class.lower().startswith(
                ("bp_mount_", "bp_tame_")
            ):
                warns.append(
                    "NPC/босс-класс может призваться как неуправляемый моб. Это ограничение игры, а не identity сейва."
                )

        mounts_list = self.model.mounts.setdefault("SavedMounts", [])
        if not isinstance(mounts_list, list):
            self.model.mounts["SavedMounts"] = mounts_list = []

        used_icons = self._used_mount_icon_ids(mounts_list)
        used_suffixes = self._used_object_suffixes(mounts_list)
        new_icon = self._alloc_unused_int(used_icons, 100000, 9999999)
        new_suffix = self._alloc_unused_int(used_suffixes, 2000000000, 2147483647)

        src_icon = None
        icon_raw = template.get("MountIconName") if isinstance(template, dict) else None
        if isinstance(icon_raw, str) and icon_raw.isdigit():
            src_icon = int(icon_raw)

        self._apply_mount_identity(new_mount, new_icon, new_suffix)
        if copy_icon:
            self._copy_mount_icon(src_icon, new_icon)

        mounts_list.append(new_mount)
        self._mounts = mounts_list
        self.model.dirty_mounts = True
        return new_mount, warns

    def _add_custom_mount(self) -> None:
        if not self.model.mounts_path:
            QMessageBox.warning(
                self, "Кастомный моб", "Mounts.json не найден в сейве."
            )
            return
        template = self._preferred_custom_mount_template()
        if not isinstance(template, dict):
            QMessageBox.warning(
                self,
                "Кастомный моб",
                "Нужен хотя бы один существующий питомец как шаблон.",
            )
            return
        choices = self._custom_mob_choices()
        if not choices:
            QMessageBox.warning(
                self,
                "Кастомный моб",
                "Не удалось загрузить каталог мобов из data.pak (AISetup).",
            )
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Кастомный моб / босс")
        lay = QVBoxLayout(dlg)

        form = QFormLayout()
        ed_name = QLineEdit()
        cmb_mob = QComboBox()
        cmb_mob.setEditable(True)
        for choice in choices:
            cmb_mob.addItem(choice.picker_label, choice)
        try:
            comp = QCompleter([c.picker_label for c in choices], cmb_mob)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            cmb_mob.setCompleter(comp)
        except Exception:
            pass

        template_name = str(template.get("MountName", "") or "(без имени)")
        template_type = str(template.get("MountType", "") or "")
        lbl_template = QLabel(
            f"Основа: {template_name}" + (f" ({template_type})" if template_type else "")
        )
        lbl_template.setStyleSheet("color:#B5BAC1;")
        lbl_warn = QLabel("")
        lbl_warn.setStyleSheet("color:#B5BAC1;")
        lbl_warn.setWordWrap(True)

        form.addRow("Имя", ed_name)
        form.addRow("Моб / босс", cmb_mob)
        lay.addLayout(form)
        lay.addWidget(lbl_template)
        lay.addWidget(lbl_warn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        def selected_choice() -> Optional[GameCustomMobChoice]:
            raw = cmb_mob.currentData()
            if isinstance(raw, GameCustomMobChoice):
                return raw
            text = cmb_mob.currentText().strip().casefold()
            for item in choices:
                if text in {
                    item.picker_label.casefold(),
                    item.ai_setup.casefold(),
                    item.display_name.casefold(),
                    item.default_name.casefold(),
                }:
                    return item
            return None

        def sync_choice() -> None:
            choice = selected_choice()
            if not choice:
                return
            if not ed_name.text().strip():
                ed_name.setText(choice.default_name)
            lbl_warn.setText(self._custom_mob_warning(choice))

        cmb_mob.currentIndexChanged.connect(sync_choice)
        cmb_mob.currentTextChanged.connect(lambda _text: sync_choice())
        sync_choice()

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        choice = selected_choice()
        if not choice:
            QMessageBox.warning(
                self,
                "Кастомный моб",
                "Выбери моба из списка игры.",
            )
            return
        name = ed_name.text().strip() or choice.default_name or "CUSTOM_MOB"
        mtype = str(template.get("MountType", "") or "").strip()
        template_data = self._mount_blob_data(template)
        template_actor = (
            mount_blob_get_fstring(template_data, "ActorClassName")
            if template_data is not None
            else ""
        ) or ""
        actor_class = _custom_choice_actor_class(choice, template_actor)

        new_mount, warns = self._create_custom_mount_from_template(
            template=template,
            name=name,
            mtype=mtype,
            level=int(template.get("MountLevel", 1) or 1),
            actor_class=actor_class,
            ai_setup=choice.ai_setup,
            preserve_progress=True,
            reset_talents=False,
            copy_icon=True,
        )
        if not isinstance(new_mount, dict):
            return

        self.search.setText("")
        self._apply_filter()
        new_icon_name = new_mount.get("MountIconName")
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, 0)
            if not it:
                continue
            d = self._mount_from_item(it)
            if isinstance(d, dict) and d.get("MountIconName") == new_icon_name:
                self.tbl.selectRow(r)
                break

        self.mark_dirty()
        msg = f"Готово: добавлена сущность «{name}»."
        if warns:
            msg += "\n\n" + "\n".join(warns)
        QMessageBox.information(self, "Кастомный моб", msg)

    def _clone_pet(self) -> None:
        if not self.model.mounts_path:
            QMessageBox.warning(
                self, "Нельзя клонировать", "Mounts.json не найден в сейве."
            )
            return

        self._flush_pending_mount_editor()

        r = self.tbl.currentRow()
        if r < 0:
            QMessageBox.information(self, "Клонирование", "Выбери питомца слева.")
            return
        name_item = self.tbl.item(r, 0)
        if not name_item:
            QMessageBox.information(self, "Клонирование", "Выбери питомца слева.")
            return
        src = self._mount_from_item(name_item)
        if not isinstance(src, dict):
            QMessageBox.information(self, "Клонирование", "Выбери питомца слева.")
            return

        try:
            count, ok = QInputDialog.getInt(
                self, "Клонировать питомца", "Сколько копий создать?", 1, 1, 10**6, 1
            )
        except Exception:
            count, ok = (1, True)
        if not ok or count <= 0:
            return
        self._clone_selected_pet(int(count))

    def _return_from_worlds(self) -> None:
        if not self.model.root:
            QMessageBox.information(self, "Питомцы", "Сначала открой папку сейва.")
            return
        if not self.model.mounts_path:
            QMessageBox.warning(self, "Питомцы", "Mounts.json не найден в сейве.")
            return

        prospects_dir = os.path.join(self.model.root, "Prospects")
        if not os.path.isdir(prospects_dir):
            QMessageBox.information(self, "Питомцы", "Папка Prospects не найдена.")
            return

        def is_main_prospect_file(fn: str) -> bool:
            f = fn.lower()
            if not f.endswith(".json"):
                return False
            if ".backup" in f or ".bak" in f:
                return False
            return True

        prospect_files = [
            os.path.join(prospects_dir, fn)
            for fn in os.listdir(prospects_dir)
            if is_main_prospect_file(fn)
        ]
        if not prospect_files:
            QMessageBox.information(self, "Питомцы", "В Prospects нет файлов *.json.")
            return

        mounts_list = self.model.mounts.get("SavedMounts", [])
        if not isinstance(mounts_list, list):
            self.model.mounts["SavedMounts"] = mounts_list = []

        existing_icons: set[int] = set()
        for m in mounts_list:
            if not isinstance(m, dict):
                continue
            icon = m.get("MountIconName")
            if isinstance(icon, str) and icon.isdigit():
                existing_icons.add(int(icon))
            rec = m.get("RecorderBlob")
            if isinstance(rec, dict):
                data = rec.get("BinaryData")
                if isinstance(data, list):
                    gid = mount_blob_get_int(data, "IcarusActorGUID")
                    if isinstance(gid, int):
                        existing_icons.add(int(gid))

        marker = b"/Script/Icarus.IcarusMountCharacterRecorderComponent"
        pat_bin = _ascii_fstring_bytes("BinaryData")
        pat_name = _ascii_fstring_bytes("MountName")
        mounts_found: Dict[int, Dict[str, Any]] = {}

        for pf in sorted(prospect_files):
            try:
                raw, _enc = read_json(pf)
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            pinfo = raw.get("ProspectInfo") or {}
            pid = ""
            if isinstance(pinfo, dict):
                v = pinfo.get("ProspectID", "")
                pid = str(v) if isinstance(v, (str, int)) else ""
            if not pid:
                pid = os.path.splitext(os.path.basename(pf))[0]

            pb = raw.get("ProspectBlob") or {}
            if not isinstance(pb, dict):
                continue
            b64 = pb.get("BinaryBlob")
            if not isinstance(b64, str) or not b64:
                continue

            try:
                comp = zlib.decompress(base64.b64decode(b64))
            except Exception:
                continue

            starts: List[int] = []
            pos = 0
            while True:
                idx = comp.find(marker, pos)
                if idx < 0:
                    break
                starts.append(idx)
                pos = idx + 1

            for si, st in enumerate(starts):
                end = starts[si + 1] if si + 1 < len(starts) else len(comp)
                chunk = comp[st:end]

                # Mount name
                mount_name = ""
                try:
                    j = chunk.find(pat_name)
                    if j >= 0:
                        tag = _parse_mount_blob_tag(chunk, j)
                        if tag.name == "MountName" and tag.type_name == "StrProperty":
                            mount_name, _ = _read_fstring(chunk, tag.value_offset)
                except Exception:
                    mount_name = ""

                # Mount type from blueprint class name
                mount_type = ""
                m = re.search(rb"BP_Mount_([A-Za-z0-9_]+)_C", chunk)
                if m:
                    try:
                        mount_type = m.group(1).decode("ascii", "ignore")
                    except Exception:
                        mount_type = ""

                # BinaryData (mount blob)
                blob_bytes = None
                try:
                    j = chunk.find(pat_bin)
                    if j >= 0:
                        tag = _parse_mount_blob_tag(chunk, j)
                        if (
                            tag.name == "BinaryData"
                            and tag.type_name == "ArrayProperty"
                        ):
                            (count,) = struct.unpack_from("<i", chunk, tag.value_offset)
                            if 0 < count < 50_000_000:
                                blob_bytes = chunk[
                                    tag.value_offset + 4 : tag.value_offset + 4 + count
                                ]
                except Exception:
                    blob_bytes = None

                if not blob_bytes:
                    continue

                data_list = list(blob_bytes)
                icon_id = mount_blob_get_int(data_list, "IcarusActorGUID")
                if not isinstance(icon_id, int):
                    continue

                # уже есть в Mounts.json — значит уже "на станции"
                if icon_id in existing_icons:
                    continue

                mounts_found[icon_id] = {
                    "prospect_id": pid,
                    "mount_name": mount_name or mount_type or str(icon_id),
                    "mount_type": mount_type,
                    "icon_id": int(icon_id),
                    "data": data_list,
                }

        if not mounts_found:
            QMessageBox.information(
                self,
                "Питомцы",
                "Не нашёл питомцев, которых можно вернуть (или они уже в Mounts.json).",
            )
            return

        # выбор, кого возвращать
        dlg = QDialog(self)
        dlg.setWindowTitle("Вернуть питомцев из миров")
        dlg.resize(980, 520)
        lay = QVBoxLayout(dlg)

        tbl = QTableWidget(0, 5)
        tbl.setHorizontalHeaderLabels(["Вернуть", "Мир", "Тип", "Имя", "IconID"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        for icon_id, rec in sorted(
            mounts_found.items(), key=lambda kv: (kv[1].get("prospect_id", ""), kv[0])
        ):
            row = tbl.rowCount()
            tbl.insertRow(row)

            it_chk = QTableWidgetItem("")
            it_chk.setFlags(it_chk.flags() | Qt.ItemIsUserCheckable)
            it_chk.setCheckState(Qt.Checked)
            it_chk.setData(Qt.UserRole, rec)

            it_world = QTableWidgetItem(str(rec.get("prospect_id", "")))
            it_type = QTableWidgetItem(str(rec.get("mount_type", "")))
            it_name = QTableWidgetItem(str(rec.get("mount_name", "")))
            it_id = QTableWidgetItem(str(int(icon_id)))
            it_id.setTextAlignment(Qt.AlignCenter)

            tbl.setItem(row, 0, it_chk)
            tbl.setItem(row, 1, it_world)
            tbl.setItem(row, 2, it_type)
            tbl.setItem(row, 3, it_name)
            tbl.setItem(row, 4, it_id)

        lay.addWidget(tbl, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        exr_dir = os.path.join(self.model.root, "Mounts")
        exr_fallback = None
        if os.path.isdir(exr_dir):
            try:
                for fn in os.listdir(exr_dir):
                    if fn.lower().endswith(".exr") and fn[:-4].isdigit():
                        exr_fallback = os.path.join(exr_dir, fn)
                        break
            except Exception:
                exr_fallback = None

        added = 0
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            if not it or it.checkState() != Qt.Checked:
                continue
            rec = it.data(Qt.UserRole)
            if not isinstance(rec, dict):
                continue

            icon_id = rec.get("icon_id")
            data_list = rec.get("data")
            if not isinstance(icon_id, int) or not isinstance(data_list, list):
                continue
            if icon_id in existing_icons:
                continue

            new_mount = {
                "DatabaseGUID": uuid.uuid4().hex.upper(),
                "RecorderBlob": {"BinaryData": data_list},
                "MountName": str(rec.get("mount_name", "")),
                "MountLevel": 0,
                "MountType": str(rec.get("mount_type", "")),
                "MountIconName": str(int(icon_id)),
            }

            try:
                mount_blob_set_fstring(
                    data_list, "MountName", "StrProperty", new_mount["MountName"]
                )
            except Exception:
                pass

            if os.path.isdir(exr_dir):
                dst = os.path.join(exr_dir, f"{int(icon_id)}.exr")
                if not os.path.isfile(dst) and exr_fallback:
                    try:
                        shutil.copy2(exr_fallback, dst)
                    except Exception:
                        pass

            mounts_list.append(new_mount)
            existing_icons.add(icon_id)
            added += 1

        if not added:
            QMessageBox.information(self, "Питомцы", "Ничего не добавлено.")
            return

        self._mounts = mounts_list
        self._talent_catalog = self._build_talent_catalog()
        self.details.set_talent_catalog(self._talent_catalog)
        self.model.dirty_mounts = True
        self.search.setText("")
        self._apply_filter()
        self.mark_dirty()
        QMessageBox.information(self, "Питомцы", f"Добавлено питомцев: {added}")

    def _pets_context_menu(self, pos) -> None:
        row = self.tbl.rowAt(pos.y())
        if row < 0:
            return

        # чтобы ПКМ работал по строке под курсором
        self.tbl.selectRow(row)

        it0 = self.tbl.item(row, 0)
        src = self._mount_from_item(it0)
        if not isinstance(src, dict):
            return

        menu = QMenu(self)

        # (опционально) подсветка пунктов меню при наведении
        menu.setStyleSheet(
            """
            QMenu { background: #1E1F22; color: #DBDEE1; border: 1px solid #111214; }
            QMenu::item { padding: 6px 12px; background: transparent; }
            QMenu::item:selected { background: #4752C4; color: #ffffff; }
            QMenu::separator { height: 1px; background: #111214; margin: 4px 8px; }
            QMenu::item:disabled { color: #8e9297; }
        """
        )

        act_copy = menu.addAction("Копировать")
        act_del = menu.addAction("Удалить")

        # Надёжнее, чем chosen is act_*
        act_copy.triggered.connect(
            lambda _=False, s=src: self._clone_selected_pet(1, src_override=s)
        )
        act_del.triggered.connect(
            lambda _=False, s=src: self._delete_selected_pet(src_override=s)
        )

        exec_fn = getattr(menu, "exec", None) or getattr(menu, "exec_", None)
        exec_fn(self.tbl.viewport().mapToGlobal(pos))

    def _clone_selected_pet(
        self, count: int, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        if not self.model.mounts_path:
            QMessageBox.information(
                self, "Копирование", "Mounts.json не найден в выбранном сейве."
            )
            return

        self._flush_pending_mount_editor()

        def _guid(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            g = x.get("DatabaseGUID")
            if isinstance(g, str) and g and g.lower() != "noguid":
                return g.upper()
            return None

        def _icon(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            v = x.get("MountIconName")
            if isinstance(v, str) and v.strip():
                return v.strip()
            return None

        def _same(a: Any, b: Any) -> bool:
            if a is b:
                return True
            if not isinstance(a, dict) or not isinstance(b, dict):
                return False
            ia, ib = _icon(a), _icon(b)
            if ia and ib and ia == ib:
                return True
            ga, gb = _guid(a), _guid(b)
            if ga and gb and ga == gb:
                return True
            return a == b

        src = src_override
        if src is None:
            r = self.tbl.currentRow()
            if r < 0:
                QMessageBox.information(self, "Копирование", "Выбери питомца слева.")
                return
            name_item = self.tbl.item(r, 0)
            src = self._mount_from_item(name_item)

        if not isinstance(src, dict):
            QMessageBox.information(self, "Копирование", "Выбери питомца слева.")
            return

        if isinstance(self.details.mount, dict) and _same(self.details.mount, src):
            src = self.details.mount

        src_data = self._mount_blob_data(src)
        src_actual_type = MountDetails._mount_type_from_blob(src_data)
        if src_actual_type:
            src["MountType"] = src_actual_type

        mounts_list = self.model.mounts.setdefault("SavedMounts", [])
        if not isinstance(mounts_list, list):
            self.model.mounts["SavedMounts"] = mounts_list = []

        insert_at = None
        for i, m in enumerate(mounts_list):
            if _same(m, src):
                insert_at = i + 1
                break

        base_name_raw = src.get("MountName", "")
        base_name = base_name_raw.strip() if isinstance(base_name_raw, str) else ""
        if not base_name:
            base_name = "Pet"

        existing_names = set()
        for m in mounts_list:
            if isinstance(m, dict):
                n = m.get("MountName", "")
                if isinstance(n, str) and n:
                    existing_names.add(n)

        def next_name(seed: str) -> str:
            if seed not in existing_names:
                existing_names.add(seed)
                return seed
            for i in range(2, 10**6):
                cand = f"{seed} ({i})"
                if cand not in existing_names:
                    existing_names.add(cand)
                    return cand
            return f"{seed} ({uuid.uuid4().hex[:6]})"

        src_icon = None
        icon_raw = src.get("MountIconName")
        if isinstance(icon_raw, str) and icon_raw.isdigit():
            src_icon = int(icon_raw)

        used_icons = self._used_mount_icon_ids(mounts_list)
        used_suffixes = self._used_object_suffixes(mounts_list)

        clones: List[Dict[str, Any]] = []
        for _ in range(int(count)):
            c = copy.deepcopy(src)
            c_name = next_name(f"{base_name} (копия)")
            c["MountName"] = c_name
            data = self._mount_blob_data(c)
            if data is not None:
                actual_type = MountDetails._mount_type_from_blob(data)
                if actual_type:
                    c["MountType"] = actual_type
                mount_blob_set_fstring(data, "MountName", "StrProperty", c_name)

            new_icon = self._alloc_unused_int(used_icons, 100000, 9999999)
            new_suffix = self._alloc_unused_int(used_suffixes, 2000000000, 2147483647)
            self._apply_mount_identity(c, new_icon, new_suffix)
            self._copy_mount_icon(src_icon, new_icon)

            guid = c.get("DatabaseGUID")
            if isinstance(guid, str) and guid and guid.lower() != "noguid":
                c["DatabaseGUID"] = uuid.uuid4().hex.upper()

            clones.append(c)

        if insert_at is None:
            mounts_list.extend(clones)
        else:
            mounts_list[insert_at:insert_at] = clones

        self._mounts = mounts_list
        self.model.dirty_mounts = True
        self._apply_filter()

        # попытаться выделить последний клон (не полагаемся только на `is`)
        target = clones[-1] if clones else None
        if target:
            for rr in range(self.tbl.rowCount()):
                it = self.tbl.item(rr, 0)
                if not it:
                    continue
                d = self._mount_from_item(it)
                if _same(d, target):
                    self.tbl.selectRow(rr)
                    break

        self.mark_dirty()

    def _delete_selected_pet(
        self, src_override: Optional[Dict[str, Any]] = None
    ) -> None:
        if not self.model.mounts_path:
            return

        def _guid(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            g = x.get("DatabaseGUID")
            if isinstance(g, str) and g and g.lower() != "noguid":
                return g.upper()
            return None

        def _icon(x: Any) -> Optional[str]:
            if not isinstance(x, dict):
                return None
            v = x.get("MountIconName")
            if isinstance(v, str) and v.strip():
                return v.strip()
            return None

        def _same(a: Any, b: Any) -> bool:
            if a is b:
                return True
            if not isinstance(a, dict) or not isinstance(b, dict):
                return False
            ia, ib = _icon(a), _icon(b)
            if ia and ib and ia == ib:
                return True
            ga, gb = _guid(a), _guid(b)
            if ga and gb and ga == gb:
                return True
            return a == b

        src = src_override
        if src is None:
            r = self.tbl.currentRow()
            if r < 0:
                return
            name_item = self.tbl.item(r, 0)
            src = self._mount_from_item(name_item)
        if not isinstance(src, dict):
            return

        mounts_list = self.model.mounts.get("SavedMounts", [])
        if not isinstance(mounts_list, list):
            return

        removed_obj = None
        for i, m in enumerate(mounts_list):
            if _same(m, src):
                removed_obj = m
                del mounts_list[i]
                break

        if removed_obj is None:
            return

        if _same(self.details.mount, removed_obj) or _same(self.details.mount, src):
            self.details.set_mount(None)

        self._mounts = mounts_list
        self.model.dirty_mounts = True
        self._apply_filter()
        self.mark_dirty()

    def _stats_for(self, mount: Dict[str, Any]) -> Dict[str, int]:
        rec = mount.get("RecorderBlob")
        if not isinstance(rec, dict):
            return {}
        data = rec.get("BinaryData")
        if not isinstance(data, list):
            return {}
        return {
            "CurrentHealth": mount_blob_get_int(data, "CurrentHealth") or 0,
            "Stamina": mount_blob_get_int(data, "Stamina") or 0,
            "FoodLevel": mount_blob_get_int(data, "FoodLevel") or 0,
            "WaterLevel": mount_blob_get_int(data, "WaterLevel") or 0,
            "OxygenLevel": mount_blob_get_int(data, "OxygenLevel") or 0,
            "Experience": mount_blob_get_int(data, "Experience") or 0,
        }

    def _level_from_xp(self, mount: Dict[str, Any], xp: int) -> Tuple[int, bool, str]:
        rec = mount.get("RecorderBlob")
        actor_class = ""
        if isinstance(rec, dict):
            data = rec.get("BinaryData")
            if isinstance(data, list):
                actor_class = mount_blob_get_fstring(data, "ActorClassName") or ""

        use_pet = actor_class.lower().startswith("bp_tame_")
        if not use_pet:
            mt = mount.get("MountType", "")
            use_pet = isinstance(mt, str) and mt.strip().lower() == "cat"

        if use_pet:
            return (
                self._xp_curve_pet.level_for_xp(int(xp), max_level=25),
                True,
                actor_class,
            )
        return (
            self._xp_curve_mount.level_for_xp(int(xp), max_level=50),
            False,
            actor_class,
        )

    def _set_row(self, row: int, mount: Dict[str, Any]) -> None:
        def mk_num(v: int) -> QTableWidgetItem:
            it = QTableWidgetItem()
            it.setData(Qt.DisplayRole, int(v))
            it.setTextAlignment(Qt.AlignCenter)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            return it

        name = mount.get("MountName", "")
        name_s = name if isinstance(name, str) else ""
        mtype = mount.get("MountType", "")
        mtype_s = mtype if isinstance(mtype, str) else ""
        rec = mount.get("RecorderBlob")
        data = rec.get("BinaryData") if isinstance(rec, dict) else None
        actual_type = MountDetails._mount_type_from_blob(data)
        display_type = actual_type or mtype_s
        raw_level = mount.get("MountLevel", 0)
        raw_level_i = int(raw_level) if isinstance(raw_level, int) else 0
        st = self._stats_for(mount)
        xp = st.get("Experience", 0)
        lvl, is_pet, actor_class = self._level_from_xp(mount, xp)
        mount_index = self._mount_index_of(mount)

        it_name = QTableWidgetItem(name_s)
        it_name.setData(
            Qt.UserRole, int(mount_index) if isinstance(mount_index, int) else -1
        )
        it_name.setData(Qt.UserRole + 1, mount)
        it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)
        it_type = QTableWidgetItem(display_type)
        it_type.setFlags(it_type.flags() & ~Qt.ItemIsEditable)
        if actual_type and mtype_s and actual_type != mtype_s:
            it_type.setToolTip(
                "Тип сверху и тип внутри RecorderBlob не совпадают.\n"
                f"MountType: {mtype_s}\n"
                f"По Actor/AI используется: {actual_type}"
            )

        self.tbl.setItem(row, 0, it_name)
        self.tbl.setItem(row, 1, it_type)
        it_lvl = mk_num(lvl)
        tip = f"Уровень из XP ({'Pet' if is_pet else 'Mount'} curve)."
        if actor_class:
            tip += f"\nActorClassName: {actor_class}"
        tip += f"\nMountLevel (в файле): {raw_level_i}"
        it_lvl.setToolTip(tip)
        self.tbl.setItem(row, 2, it_lvl)
        self.tbl.setItem(row, 3, mk_num(st.get("CurrentHealth", 0)))
        self.tbl.setItem(row, 4, mk_num(st.get("Stamina", 0)))
        self.tbl.setItem(row, 5, mk_num(st.get("FoodLevel", 0)))
        self.tbl.setItem(row, 6, mk_num(st.get("WaterLevel", 0)))
        self.tbl.setItem(row, 7, mk_num(st.get("OxygenLevel", 0)))
        self.tbl.setItem(row, 8, mk_num(xp))

    def _apply_filter(self) -> None:
        self._flush_pending_mount_editor()
        q = self.search.text().strip().lower()
        mounts: List[Dict[str, Any]] = []
        for m in self._mounts:
            if not isinstance(m, dict):
                continue
            name = m.get("MountName", "")
            mtype = m.get("MountType", "")
            rec = m.get("RecorderBlob")
            data = rec.get("BinaryData") if isinstance(rec, dict) else None
            actual_type = MountDetails._mount_type_from_blob(data)
            s = f"{name} {mtype} {actual_type}".lower()
            if not q or q in s:
                mounts.append(m)

        self.tbl.blockSignals(True)
        self.tbl.setSortingEnabled(False)
        self.tbl.setRowCount(0)

        for m in mounts:
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            self._set_row(row, m)

        self.tbl.setSortingEnabled(True)
        self.tbl.blockSignals(False)
        for c in range(1, 9):
            self.tbl.resizeColumnToContents(c)
        self.tbl.resizeRowsToContents()

        self.details.set_mount(None)
        if self.tbl.rowCount() > 0:
            self.tbl.selectRow(0)

    def _sel_changed(
        self, currentRow: int, currentColumn: int, prevRow: int, prevCol: int
    ) -> None:
        self._flush_pending_mount_editor()
        if currentRow < 0:
            self.details.set_mount(None)
            self.btn_clone.setEnabled(False)
            return
        name_item = self.tbl.item(currentRow, 0)
        if not name_item:
            self.details.set_mount(None)
            self.btn_clone.setEnabled(False)
            return
        mount = self._mount_from_item(name_item)
        self.details.set_mount(mount)
        self.btn_clone.setEnabled(mount is not None)

    def _details_changed(self) -> None:
        self.model.dirty_mounts = True
        if self.details.mount:
            self._edited_mount_ids.add(id(self.details.mount))

        row = self._row_for_mount(self.details.mount)
        if row >= 0 and self.details.mount:
            was_sorting = self.tbl.isSortingEnabled()
            self.tbl.setSortingEnabled(False)
            self.tbl.blockSignals(True)
            self._set_row(row, self.details.mount)
            self.tbl.blockSignals(False)
            self.tbl.setSortingEnabled(was_sorting)
            for c in range(2, 9):
                self.tbl.resizeColumnToContents(c)

        self.mark_dirty()


def _default_engine_ini_path() -> Optional[str]:
    localapp = _guess_localappdata_dir()
    if not localapp:
        return None
    return os.path.join(
        localapp, "Icarus", "Saved", "Config", "WindowsNoEditor", "Engine.ini"
    )


class OtherTab(QWidget):
    ENGINE_SECTION = r"[/Script/Engine.RendererSettings]"
    ENGINE_KEYS = [
        "r.fog",
        "r.VolumetricFog",
        "r.MotionBlurQuality",
        "r.BloomQuality",
        "r.DepthOfFieldQuality",
        "r.LensFlareQuality",
        "r.SceneColorFringeQuality",
        "r.VSync",
    ]
    WORLD_ILLEGAL_AI_TOKENS = ("Scorpion_Boss", "LavaHunter", "IceBreaker")

    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._engine_ini: Optional[str] = None
        self._game_data: Optional[IcarusGameData] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.info = QLabel("")
        self.info.setStyleSheet("color:#B5BAC1;")
        root.addWidget(self.info)

        g = QGroupBox("Графика (Engine.ini)")
        lay = QVBoxLayout(g)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(8)

        path_row = QHBoxLayout()
        self.ed_engine = QLineEdit()
        self.ed_engine.setReadOnly(True)
        btn_pick = QPushButton("…")
        btn_pick.setFixedWidth(40)
        btn_pick.clicked.connect(self.pick_engine_ini)
        btn_reload = QPushButton("Обновить")
        btn_reload.clicked.connect(self._load_engine_values)
        path_row.addWidget(QLabel("Engine.ini:"))
        path_row.addWidget(self.ed_engine, 1)
        path_row.addWidget(btn_pick)
        path_row.addWidget(btn_reload)
        lay.addLayout(path_row)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self._controls: Dict[str, QComboBox] = {}

        def add_bool(key: str, label: str) -> None:
            cmb = QComboBox()
            cmb.addItem("Вкл", 1)
            cmb.addItem("Выкл", 0)
            self._controls[key] = cmb
            form.addRow(label, cmb)

        def add_int_range(key: str, label: str, lo: int, hi: int) -> None:
            cmb = QComboBox()
            for v in range(int(lo), int(hi) + 1):
                cmb.addItem(str(v), int(v))
            self._controls[key] = cmb
            form.addRow(label, cmb)

        add_bool("r.fog", "Туман (r.fog)")
        add_bool("r.VolumetricFog", "Объёмный туман (r.VolumetricFog)")
        add_int_range("r.MotionBlurQuality", "Motion Blur (качество)", 0, 4)
        add_int_range("r.BloomQuality", "Bloom (качество)", 0, 5)
        add_int_range("r.DepthOfFieldQuality", "Depth of Field (качество)", 0, 4)
        add_int_range("r.LensFlareQuality", "Lens Flare (качество)", 0, 3)
        add_int_range("r.SceneColorFringeQuality", "Хром. аберрации (качество)", 0, 1)
        add_bool("r.VSync", "VSync (r.VSync)")

        lay.addLayout(form)

        btn_apply = QPushButton("Применить")
        btn_apply.clicked.connect(self.apply_engine_settings)
        lay.addWidget(btn_apply, 0, Qt.AlignLeft)

        root.addWidget(g)

        g_world = QGroupBox("Мир (Prospects)")
        wlay = QVBoxLayout(g_world)
        wlay.setContentsMargins(8, 10, 8, 8)
        wlay.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Мир:"))
        self.cmb_world_tools = QComboBox()
        self.cmb_world_tools.currentIndexChanged.connect(self._world_tools_changed)
        top.addWidget(self.cmb_world_tools, 1)
        btn_world_reload = QPushButton("Обновить")
        btn_world_reload.clicked.connect(self._reload_world_tools)
        top.addWidget(btn_world_reload)
        wlay.addLayout(top)

        diff_row = QHBoxLayout()
        diff_row.addWidget(QLabel("Сложность:"))
        self.cmb_world_difficulty = QComboBox()
        for diff in ("Easy", "Medium", "Hard", "Extreme"):
            self.cmb_world_difficulty.addItem(diff, diff)
        diff_row.addWidget(self.cmb_world_difficulty)
        self.btn_world_apply_difficulty = QPushButton("Применить сложность")
        self.btn_world_apply_difficulty.clicked.connect(self._apply_world_difficulty)
        diff_row.addWidget(self.btn_world_apply_difficulty)
        diff_row.addStretch(1)
        wlay.addLayout(diff_row)

        tools_row = QHBoxLayout()
        self.btn_world_scan = QPushButton("Сканировать boss-следы")
        self.btn_world_scan.clicked.connect(self._scan_world_boss_traces)
        tools_row.addWidget(self.btn_world_scan)
        self.btn_world_restore_clean = QPushButton("Откатить на чистый backup")
        self.btn_world_restore_clean.clicked.connect(self._restore_world_clean_backup)
        tools_row.addWidget(self.btn_world_restore_clean)
        tools_row.addStretch(1)
        wlay.addLayout(tools_row)

        self.lbl_world_tools = QLabel("")
        self.lbl_world_tools.setStyleSheet("color:#B5BAC1;")
        self.lbl_world_tools.setWordWrap(True)
        wlay.addWidget(self.lbl_world_tools)

        self.lbl_world_weather = QLabel("")
        self.lbl_world_weather.setStyleSheet("color:#B5BAC1;")
        self.lbl_world_weather.setWordWrap(True)
        wlay.addWidget(self.lbl_world_weather)

        root.addWidget(g_world)
        root.addStretch(1)

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data

    def load(self) -> None:
        self._engine_ini = _default_engine_ini_path()
        self.ed_engine.setText(_mask_path_for_display(self._engine_ini or ""))
        self._load_engine_values()
        self._reload_world_tools()

    def pick_engine_ini(self) -> None:
        start = self._engine_ini or os.path.expanduser("~")
        path, _flt = QFileDialog.getOpenFileName(
            self, "Выбери Engine.ini", start, "Engine.ini (Engine.ini);;Все файлы (*.*)"
        )
        if not path:
            return
        self._engine_ini = path
        self.ed_engine.setText(_mask_path_for_display(path))
        self._load_engine_values()

    def _load_engine_values(self) -> None:
        p = self._engine_ini
        if not p or not os.path.isfile(p):
            self.info.setText("Engine.ini не найден. Укажи путь вручную.")
            return

        cur = ini_get_section_values(
            p, self.ENGINE_SECTION, list(self._controls.keys())
        )
        for k, cmb in self._controls.items():
            v = cur.get(k)
            try:
                iv = int(str(v)) if v is not None and str(v).strip() != "" else None
            except Exception:
                iv = None
            if iv is None:
                continue
            idx = cmb.findData(iv)
            if idx >= 0:
                cmb.blockSignals(True)
                cmb.setCurrentIndex(idx)
                cmb.blockSignals(False)

        self.info.setText("")

    def apply_engine_settings(self) -> None:
        p = self._engine_ini
        if not p or not os.path.isfile(p):
            QMessageBox.warning(
                self, "Графика", "Engine.ini не найден. Укажи путь вручную."
            )
            return

        desired: Dict[str, int] = {}
        for k, cmb in self._controls.items():
            v = cmb.currentData()
            if isinstance(v, int):
                desired[k] = int(v)

        cur = ini_get_section_values(p, self.ENGINE_SECTION, list(desired.keys()))
        need_change = any(cur.get(k) != str(int(v)) for k, v in desired.items())
        if not need_change:
            QMessageBox.information(
                self, "Графика", "Ничего менять не пришлось: значения уже стоят."
            )
            return

        backup_dir = os.path.join(os.path.dirname(p), "IcarusEditorBackups")
        bak = create_backup_zip(os.path.dirname(p), [p], backup_dir, prefix="engine")

        try:
            changed = ini_ensure_section_keys(p, self.ENGINE_SECTION, desired)
        except Exception as e:
            QMessageBox.critical(self, "Графика", str(e))
            return

        if changed:
            QMessageBox.information(
                self, "Графика", f"Готово: Engine.ini обновлён.\n\nБэкап: {bak}"
            )
        else:
            QMessageBox.information(
                self, "Графика", "Ничего менять не пришлось: нужные значения уже стоят."
            )

    def _selected_world_tools_path(self) -> Optional[str]:
        p = self.cmb_world_tools.currentData()
        return p if isinstance(p, str) and p else None

    def _reload_world_tools(self) -> None:
        enabled = bool(self.model.root and self.model.prospect_paths)
        for w in (
            self.cmb_world_tools,
            self.cmb_world_difficulty,
            self.btn_world_apply_difficulty,
            self.btn_world_scan,
            self.btn_world_restore_clean,
        ):
            w.setEnabled(enabled)

        self.cmb_world_tools.blockSignals(True)
        cur = self._selected_world_tools_path()
        self.cmb_world_tools.clear()
        if enabled:
            for p in self.model.prospect_paths:
                self.cmb_world_tools.addItem(os.path.splitext(os.path.basename(p))[0], p)
            idx = self.cmb_world_tools.findData(cur) if cur else -1
            self.cmb_world_tools.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_world_tools.blockSignals(False)
        self._world_tools_changed()

    def _world_tools_changed(self) -> None:
        prospect_path = self._selected_world_tools_path()
        if not prospect_path:
            self.lbl_world_tools.setText("Prospects не найдены.")
            self.lbl_world_weather.setText("")
            return

        cur_diff = self.model.prospect_difficulty(prospect_path)
        idx = self.cmb_world_difficulty.findData(cur_diff)
        if idx >= 0:
            self.cmb_world_difficulty.blockSignals(True)
            self.cmb_world_difficulty.setCurrentIndex(idx)
            self.cmb_world_difficulty.blockSignals(False)
        self._update_world_tools_labels(prospect_path)

    def _update_world_tools_labels(self, prospect_path: str) -> None:
        counts = self.model.prospect_blob_ai_counts(
            prospect_path, self.WORLD_ILLEGAL_AI_TOKENS
        )
        suspicious = {k: int(v) for k, v in counts.items() if int(v) > 0}
        clean_backup = self.model.find_latest_clean_prospect_backup(
            prospect_path, self.WORLD_ILLEGAL_AI_TOKENS
        )
        cur_diff = self.model.prospect_difficulty(prospect_path) or "?"
        if suspicious:
            boss_text = ", ".join(f"{k}={v}" for k, v in suspicious.items())
            clean_text = (
                os.path.basename(clean_backup) if isinstance(clean_backup, str) else "не найден"
            )
            self.lbl_world_tools.setText(
                f"Текущая сложность: {cur_diff}\n"
                f"Подозрительные boss-AI в ProspectBlob: {boss_text}\n"
                f"Последний чистый backup: {clean_text}"
            )
        else:
            self.lbl_world_tools.setText(
                f"Текущая сложность: {cur_diff}\n"
                "Подозрительных boss-AI следов не найдено."
            )

        self.lbl_world_weather.setText(
            "Погода/хазарды: в сейве не найден поддерживаемый world-toggle для серных дождей, "
            "кислотных/серных озёр и прочих biome hazards. По локальным таблицам игры это задаётся "
            "через Prospect row + Forecast/WeatherPool, а не через ProspectInfo.CustomSettings."
        )

    def _apply_world_difficulty(self) -> None:
        prospect_path = self._selected_world_tools_path()
        if not prospect_path:
            return
        diff = self.cmb_world_difficulty.currentData()
        if not isinstance(diff, str) or not diff:
            return
        if self.model.set_prospect_difficulty(prospect_path, diff):
            self.mark_dirty()
            QMessageBox.information(
                self,
                "Мир",
                f"Сложность для `{os.path.basename(prospect_path)}` изменена на {diff}.\n\n"
                "Нажми «Сохранить всё», чтобы записать это в сейв.",
            )
        self._update_world_tools_labels(prospect_path)

    def _scan_world_boss_traces(self) -> None:
        prospect_path = self._selected_world_tools_path()
        if not prospect_path:
            return
        self._update_world_tools_labels(prospect_path)
        counts = self.model.prospect_blob_ai_counts(
            prospect_path, self.WORLD_ILLEGAL_AI_TOKENS
        )
        suspicious = {k: int(v) for k, v in counts.items() if int(v) > 0}
        if suspicious:
            details = ", ".join(f"{k}={v}" for k, v in suspicious.items())
            QMessageBox.warning(
                self,
                "Мир",
                f"В мире найдены подозрительные boss-AI следы: {details}",
            )
        else:
            QMessageBox.information(
                self,
                "Мир",
                "Подозрительных boss-AI следов в выбранном мире не найдено.",
            )

    def _restore_world_clean_backup(self) -> None:
        prospect_path = self._selected_world_tools_path()
        if not prospect_path:
            return
        backup = self.model.find_latest_clean_prospect_backup(
            prospect_path, self.WORLD_ILLEGAL_AI_TOKENS
        )
        if not backup:
            QMessageBox.warning(
                self,
                "Мир",
                "Не удалось найти чистый backup без boss-AI следов.",
            )
            return
        base = os.path.basename(prospect_path)
        r = QMessageBox.question(
            self,
            "Мир",
            f"Заменить `{base}` содержимым из `{os.path.basename(backup)}`?\n\n"
            "Изменение попадёт в сейв после «Сохранить всё».",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        self.model.replace_prospect_from_file(prospect_path, backup)
        self.mark_dirty()
        self._world_tools_changed()
        QMessageBox.information(
            self,
            "Мир",
            f"Выбран чистый backup: {os.path.basename(backup)}.\n\n"
            "Нажми «Сохранить всё», чтобы записать его в сейв.",
        )


class TestTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._game_data: Optional[IcarusGameData] = None
        self._xp_curve_mount: ExperienceCurve = DEFAULT_MOUNT_XP_CURVE
        self._xp_curve_pet: ExperienceCurve = DEFAULT_PET_XP_CURVE

        # World inventory cache (selected prospect)
        self._world_prospect_path: Optional[str] = None
        self._world_prospect_pid: str = ""
        self._world_uc: Optional[bytearray] = None
        self._world_tag: Optional[_MountBlobTagEx] = None
        self._world_data_start: int = 0
        self._world_data_end: int = 0
        self._world_binary: bytes = b""
        self._world_containers: List[Dict[str, Any]] = []

        # Decompile index (for custom pets)
        self._decompile_root: Optional[str] = None
        self._decompile_actor_classes: List[str] = []
        self._decompile_scan_started = False

        self._populating_containers = False
        self._populating_slots = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.info = QLabel("TEST режим включён. Здесь будут экспериментальные функции.")
        self.info.setStyleSheet("color:#B5BAC1;")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.subtabs = QTabWidget()
        root.addWidget(self.subtabs, 1)

        # --- World inventories ---
        self.tab_world = QWidget()
        world_root = QVBoxLayout(self.tab_world)
        world_root.setContentsMargins(0, 0, 0, 0)
        world_root.setSpacing(8)

        world_top = QHBoxLayout()
        self.cmb_world_prospect = QComboBox()
        self.cmb_world_prospect.currentIndexChanged.connect(
            self._world_prospect_changed
        )
        btn_world_reload = QPushButton("Обновить")
        btn_world_reload.clicked.connect(self._reload_world)
        world_top.addWidget(QLabel("Мир (Prospects):"))
        world_top.addWidget(self.cmb_world_prospect, 1)
        world_top.addWidget(btn_world_reload)
        world_root.addLayout(world_top)

        self.lbl_world_status = QLabel("")
        self.lbl_world_status.setStyleSheet("color:#B5BAC1;")
        self.lbl_world_status.setWordWrap(True)
        world_root.addWidget(self.lbl_world_status)

        self.world_split = QSplitter(Qt.Horizontal)
        world_root.addWidget(self.world_split, 1)

        # Left: containers
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(6)
        left_l.addWidget(QLabel("Инвентари в мире (игроки и объекты):"))

        world_filter = QWidget()
        world_filter_l = QHBoxLayout(world_filter)
        world_filter_l.setContentsMargins(0, 0, 0, 0)
        world_filter_l.setSpacing(6)
        self.ed_world_search = QLineEdit()
        self.ed_world_search.setPlaceholderText(
            "Поиск предмета/RowName/(id)/число(stack)/inv…"
        )
        self.ed_world_search.textChanged.connect(self._world_filter_changed)
        self.cb_world_hide_empty = QCheckBox("Скрыть пустые")
        self.cb_world_hide_empty.stateChanged.connect(self._world_filter_changed)
        world_filter_l.addWidget(QLabel("Поиск:"))
        world_filter_l.addWidget(self.ed_world_search, 1)
        world_filter_l.addWidget(self.cb_world_hide_empty, 0)
        left_l.addWidget(world_filter)

        self.tabs_world_containers = QTabWidget()
        self.tabs_world_containers.currentChanged.connect(self._containers_tab_changed)

        self.tbl_containers_player = QTableWidget(0, 4)
        self.tbl_containers_player.setHorizontalHeaderLabels(
            ["Инвентарь", "Слотов", "Кол-во", "Содержимое"]
        )
        self.tbl_containers_player.verticalHeader().setVisible(False)
        self.tbl_containers_player.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_containers_player.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_containers_player.setAlternatingRowColors(True)
        self.tbl_containers_player.setSortingEnabled(True)
        hdr_p = self.tbl_containers_player.horizontalHeader()
        hdr_p.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_p.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_p.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr_p.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_containers_player.currentCellChanged.connect(
            self._container_sel_changed
        )

        self.tbl_containers_chest = QTableWidget(0, 4)
        self.tbl_containers_chest.setHorizontalHeaderLabels(
            ["Инвентарь", "Слотов", "Кол-во", "Содержимое"]
        )
        self.tbl_containers_chest.verticalHeader().setVisible(False)
        self.tbl_containers_chest.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_containers_chest.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_containers_chest.setAlternatingRowColors(True)
        self.tbl_containers_chest.setSortingEnabled(True)
        hdr_c = self.tbl_containers_chest.horizontalHeader()
        hdr_c.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_c.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr_c.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_containers_chest.currentCellChanged.connect(
            self._container_sel_changed
        )

        self.tabs_world_containers.addTab(self.tbl_containers_chest, "Сундук/объект")
        self.tabs_world_containers.addTab(self.tbl_containers_player, "Игрок")
        left_l.addWidget(self.tabs_world_containers, 1)

        self.world_split.addWidget(left)

        # Right: slots
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(6)

        slots_top = QHBoxLayout()
        self.btn_slot_add = QPushButton("Добавить предмет…")
        self.btn_slot_add.clicked.connect(self._world_add_item_dialog)
        slots_top.addWidget(self.btn_slot_add)
        slots_top.addStretch(1)
        right_l.addLayout(slots_top)

        self.tbl_slots = QTableWidget(0, 4)
        self.tbl_slots.setHorizontalHeaderLabels(
            ["Слот", "Предмет", "Кол-во", "Прочн."]
        )
        self.tbl_slots.verticalHeader().setVisible(False)
        self.tbl_slots.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_slots.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.tbl_slots.setAlternatingRowColors(True)
        self.tbl_slots.setSortingEnabled(True)
        hdr2 = self.tbl_slots.horizontalHeader()
        hdr2.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr2.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr2.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_slots.cellChanged.connect(self._slot_cell_changed)
        self.tbl_slots.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl_slots.customContextMenuRequested.connect(self._slots_context_menu)
        right_l.addWidget(self.tbl_slots, 1)

        self.world_split.addWidget(right)
        self.world_split.setStretchFactor(0, 2)
        self.world_split.setStretchFactor(1, 3)

        self.subtabs.addTab(self.tab_world, "Инвентари мира")

        # --- Custom pets (experimental) ---
        self.tab_custom_pets = QWidget()
        cp_root = QVBoxLayout(self.tab_custom_pets)
        cp_root.setContentsMargins(0, 0, 0, 0)
        cp_root.setSpacing(8)

        warn = QLabel(
            "Эксперимент: программа клонирует питомца Saitama и подменяет ему ActorClassName + AISetupRowName.\n"
            "Для мобов и боссов это не гарантирует управление: часть AI спавнится как обычный NPC."
        )
        warn.setStyleSheet("color:#B5BAC1;")
        warn.setWordWrap(True)
        cp_root.addWidget(warn)

        self.lbl_decompile = QLabel("Основа: ищу Saitama в Mounts.json…")
        self.lbl_decompile.setStyleSheet("color:#B5BAC1;")
        self.lbl_decompile.setWordWrap(True)
        cp_root.addWidget(self.lbl_decompile)

        g = QGroupBox("Создать кастомного питомца")
        g_form = QFormLayout(g)
        g_form.setContentsMargins(8, 10, 8, 8)
        g_form.setHorizontalSpacing(10)
        g_form.setVerticalSpacing(6)

        self.cmb_custom_template = QComboBox()
        self.btn_custom_reload = QPushButton("Обновить")
        self.btn_custom_reload.clicked.connect(self._load_custom_templates)
        self.cmb_custom_template.currentIndexChanged.connect(
            self._custom_template_changed
        )

        self.ed_custom_name = QLineEdit()
        self.ed_custom_name.setPlaceholderText("Имя моба/босса…")
        g_form.addRow("Имя", self.ed_custom_name)

        self.cmb_custom_profile = QComboBox()
        for profile in CUSTOM_SPAWN_PROFILES:
            self.cmb_custom_profile.addItem(profile.title, profile)
        self.cmb_custom_profile.currentIndexChanged.connect(
            self._custom_profile_changed
        )

        self.cmb_custom_type = QComboBox()
        self.cmb_custom_type.setEditable(True)

        self.sb_custom_level = QSpinBox()
        self.sb_custom_level.setRange(0, 10**9)
        self.sb_custom_level.setValue(1)

        self.ed_custom_actor = QLineEdit()
        self.ed_custom_actor.setPlaceholderText("Напр.: BP_NPC_Spider_Character_C")

        self.cmb_custom_mob = QComboBox()
        self.cmb_custom_mob.setEditable(True)
        self.cmb_custom_mob.setToolTip(
            "Выбери моба или босса из AISetup. Поиск работает по имени, тегам и RowName."
        )
        self.cmb_custom_mob.currentTextChanged.connect(self._custom_mob_selected)
        g_form.addRow("Моб / босс", self.cmb_custom_mob)

        self.ed_custom_ai = QLineEdit()
        self.ed_custom_ai.setPlaceholderText("Опционально: AISetupRowName")

        self.cb_custom_reset_talents = QCheckBox(
            "Сбросить таланты/прогресс у нового питомца"
        )
        self.cb_custom_reset_talents.setChecked(False)

        self.cb_custom_copy_icon = QCheckBox(
            "Скопировать иконку (Mounts/*.exr) от шаблона"
        )
        self.cb_custom_copy_icon.setChecked(True)

        self.btn_custom_create = QPushButton("Создать")
        self.btn_custom_create.clicked.connect(self._create_custom_pet)
        g_form.addRow("", self.btn_custom_create)

        cp_root.addWidget(g)
        cp_root.addStretch(1)

        self.lbl_custom_pets = QLabel("")
        self.lbl_custom_pets.setStyleSheet("color:#B5BAC1;")
        self.lbl_custom_pets.setWordWrap(True)
        cp_root.addWidget(self.lbl_custom_pets)
        self.subtabs.addTab(self.tab_custom_pets, "Кастомные питомцы")

    def set_game_data(self, game_data: Optional[IcarusGameData]) -> None:
        self._game_data = game_data
        if game_data:
            self._xp_curve_mount = (
                game_data.get_experience_curve("C_MountExperienceGrowth")
                or DEFAULT_MOUNT_XP_CURVE
            )
            self._xp_curve_pet = (
                game_data.get_experience_curve("C_PetExperienceGrowth")
                or DEFAULT_PET_XP_CURVE
            )
        else:
            self._xp_curve_mount = DEFAULT_MOUNT_XP_CURVE
            self._xp_curve_pet = DEFAULT_PET_XP_CURVE
        self._populate_custom_mobs()

    def load(self) -> None:
        enabled = bool(self.model.root)
        self.cmb_world_prospect.setEnabled(enabled)
        self.tabs_world_containers.setEnabled(enabled)
        self.ed_world_search.setEnabled(enabled)
        self.cb_world_hide_empty.setEnabled(enabled)
        self.tbl_slots.setEnabled(enabled)
        self.btn_slot_add.setEnabled(enabled)

        # custom pets
        self.btn_custom_reload.setEnabled(enabled and bool(self.model.mounts_path))
        self.ed_custom_name.setEnabled(enabled and bool(self.model.mounts_path))
        self.cmb_custom_mob.setEnabled(enabled and bool(self.model.mounts_path))
        self.btn_custom_create.setEnabled(enabled and bool(self.model.mounts_path))

        self.cmb_world_prospect.blockSignals(True)
        self.cmb_world_prospect.clear()
        if enabled and self.model.prospect_paths:
            for p in self.model.prospect_paths:
                base = os.path.splitext(os.path.basename(p))[0]
                self.cmb_world_prospect.addItem(base, p)
        self.cmb_world_prospect.blockSignals(False)

        if enabled and self.cmb_world_prospect.count() > 0:
            if self._world_prospect_path:
                idx = self.cmb_world_prospect.findData(self._world_prospect_path)
                if idx >= 0:
                    self.cmb_world_prospect.setCurrentIndex(idx)
                else:
                    self.cmb_world_prospect.setCurrentIndex(0)
            else:
                self.cmb_world_prospect.setCurrentIndex(0)
            self._world_prospect_changed()
        else:
            self._clear_world()

        self._load_custom_templates()
        self._populate_custom_mobs()

    def _clear_world(self) -> None:
        self._world_prospect_path = None
        self._world_prospect_pid = ""
        self._world_uc = None
        self._world_tag = None
        self._world_data_start = 0
        self._world_data_end = 0
        self._world_binary = b""
        self._world_containers = []
        self.lbl_world_status.setText("")
        self.tbl_containers_chest.setRowCount(0)
        self.tbl_containers_player.setRowCount(0)
        self.tbl_slots.setRowCount(0)

    def _reload_world(self) -> None:
        self._world_prospect_changed()

    def _world_prospect_changed(self) -> None:
        p = self.cmb_world_prospect.currentData()
        if not isinstance(p, str) or not p:
            self._clear_world()
            return
        self._world_prospect_path = p
        self._load_world_blob_and_tables(p)

    def _scan_world_inventories(
        self,
        uncompressed: bytes,
        prospect_path: str,
        pid: str,
        member_names_exact: Dict[Tuple[str, int], str],
        member_names_by_user: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []

        def read_f(tag: Optional[_MountBlobTagEx], rec_bin: bytes) -> str:
            if not tag:
                return ""
            try:
                return _ue_read_tag_fstring(rec_bin, tag)
            except Exception:
                return ""

        def read_i32(tag: Optional[_MountBlobTagEx], rec_bin: bytes) -> Optional[int]:
            if not tag:
                return None
            try:
                if tag.type_name != "IntProperty" or tag.size != 4:
                    return None
                return _ue_read_i32(rec_bin, tag.value_offset)
            except Exception:
                return None

        def calc_metrics(slots_list: List[Dict[str, Any]]) -> Tuple[int, int, str]:
            if not slots_list:
                return 0, 0, ""
            total = 0
            by_row: Dict[str, int] = {}
            for s in slots_list:
                if not isinstance(s, dict):
                    continue
                rn = s.get("row_name", "")
                if not isinstance(rn, str) or not rn:
                    continue
                st = s.get("stack", 1)
                try:
                    st_i = int(st) if isinstance(st, int) else int(str(st).strip())
                except Exception:
                    st_i = 1
                if st_i <= 0:
                    st_i = 1
                total += st_i
                by_row[rn] = by_row.get(rn, 0) + st_i

            items = sorted(
                by_row.items(), key=lambda x: (-int(x[1]), str(x[0]).lower())
            )
            parts: List[str] = []
            for rn, st_i in items[:3]:
                title = SaveModel.item_pretty_name(str(rn))
                parts.append(f"{title}×{int(st_i)}")
            if len(items) > 3:
                parts.append(f"+{len(items) - 3}…")
            return len(slots_list), int(total), ", ".join(parts)

        sources = [
            (b"/Script/Icarus.PlayerStateRecorderComponent", "Игрок"),
            (b"/Script/Icarus.DeployableRecorderComponent", "Объект"),
        ]

        for marker, source in sources:
            ranges = _find_marked_ranges(uncompressed, marker)
            for rec_idx, (st, en) in enumerate(ranges):
                tag = _find_tag_in_range(
                    uncompressed, st, en, "BinaryData", "ArrayProperty"
                )
                if not tag:
                    continue
                count = _ue_read_i32(uncompressed, tag.value_offset)
                if count < 0:
                    continue
                data_start = int(tag.value_offset + 4)
                data_end = int(data_start + count)
                if data_end > en or data_end > len(uncompressed):
                    continue
                rec_bin = uncompressed[data_start:data_end]

                try:
                    fields, _pos = _ue_parse_struct_fields(rec_bin, 0, len(rec_bin))
                except Exception:
                    continue

                saved = _ue_find_tag(fields, "SavedInventories")
                if not saved or saved.type_name != "ArrayProperty":
                    continue

                actor_class = read_f(_ue_find_tag(fields, "ActorClassName"), rec_bin)
                actor_path = read_f(_ue_find_tag(fields, "ActorPathName"), rec_bin)
                static_item = read_f(
                    _ue_find_tag(fields, "StaticItemDataRowName"), rec_bin
                )

                owner_user_id = ""
                owner_chr_slot: Optional[int] = None
                display_base = ""
                if source == "Игрок":
                    pc_tag = _ue_find_tag(fields, "PlayerCharacterID")
                    if (
                        pc_tag
                        and pc_tag.type_name == "StructProperty"
                        and pc_tag.size > 0
                    ):
                        end_off = int(pc_tag.value_offset + pc_tag.size)
                        if (
                            0 <= pc_tag.value_offset < len(rec_bin)
                            and 0 < end_off <= len(rec_bin)
                            and end_off > pc_tag.value_offset
                        ):
                            try:
                                pc_fields, _pos2 = _ue_parse_struct_fields(
                                    rec_bin, int(pc_tag.value_offset), end_off
                                )
                                owner_user_id = read_f(
                                    _ue_find_tag(pc_fields, "PlayerID"), rec_bin
                                ) or read_f(_ue_find_tag(pc_fields, "UserID"), rec_bin)
                                owner_chr_slot = read_i32(
                                    _ue_find_tag(pc_fields, "ChrSlot"), rec_bin
                                )
                            except Exception:
                                owner_user_id = ""
                                owner_chr_slot = None

                    if owner_user_id:
                        key = None
                        if isinstance(owner_chr_slot, int):
                            key = (owner_user_id, int(owner_chr_slot))
                        if key and key in member_names_exact:
                            display_base = member_names_exact.get(key, "") or ""
                        if not display_base:
                            display_base = (
                                member_names_by_user.get(owner_user_id, "") or ""
                            )
                    if not display_base:
                        display_base = actor_class or "Игрок"
                else:
                    if static_item:
                        display_base = SaveModel.item_pretty_name(static_item)
                    else:
                        display_base = actor_class or static_item or "Объект"

                invs = saved_inventories_list(rec_bin)
                if not invs:
                    continue

                for inv in invs:
                    inv_id = inv.get("inventory_id")
                    slots = inv.get("slots", [])
                    if not isinstance(inv_id, int):
                        continue
                    slots_list = slots if isinstance(slots, list) else []
                    slot_count, total_items, contents = calc_metrics(slots_list)
                    ckey = f"{marker.decode('ascii', errors='ignore')}#{int(rec_idx)}#{int(inv_id)}"
                    display_name = display_base
                    if isinstance(inv_id, int):
                        display_name = f"{display_name}  [inv {int(inv_id)}]"
                    entries.append(
                        {
                            "source": source,
                            "marker": marker.decode("ascii", errors="ignore"),
                            "record_index": int(rec_idx),
                            "actor_class": actor_class,
                            "actor_path": actor_path,
                            "static_item": static_item,
                            "inventory_id": int(inv_id),
                            "slots": slots_list,
                            "slot_count": int(slot_count),
                            "total_items": int(total_items),
                            "contents": contents,
                            "display_name": display_name,
                            "owner_user_id": owner_user_id,
                            "owner_chr_slot": owner_chr_slot,
                            "container_key": ckey,
                            "_binary_tag": tag,
                            "_data_start": int(data_start),
                            "_data_end": int(data_end),
                            "prospect_path": prospect_path,
                            "prospect_id": pid,
                        }
                    )

        def sort_key(c: Dict[str, Any]) -> Tuple[int, str, int]:
            src = c.get("source", "")
            src_k = 0 if src == "Игрок" else 1
            actor = c.get("actor_class", "") or ""
            inv_id = c.get("inventory_id", 0)
            return (src_k, actor.lower(), int(inv_id) if isinstance(inv_id, int) else 0)

        entries.sort(key=sort_key)
        return entries

    def _load_world_blob_and_tables(self, prospect_path: str) -> None:
        try:
            raw, _enc = self.model.load_prospect(prospect_path)
        except Exception as e:
            self._clear_world()
            self.lbl_world_status.setText(f"Не удалось открыть мир: {e}")
            return

        pinfo = raw.get("ProspectInfo") or {}
        member_names_exact: Dict[Tuple[str, int], str] = {}
        member_names_by_user: Dict[str, str] = {}
        if isinstance(pinfo, dict):
            members = pinfo.get("AssociatedMembers", [])
            if isinstance(members, list):
                for m in members:
                    if not isinstance(m, dict):
                        continue
                    uid = (
                        m.get("UserID", "")
                        if isinstance(m.get("UserID", ""), (str, int))
                        else ""
                    )
                    if not uid:
                        uid = (
                            m.get("PlayerID", "")
                            if isinstance(m.get("PlayerID", ""), (str, int))
                            else ""
                        )
                    uid_s = str(uid).strip() if uid else ""
                    if not uid_s:
                        continue
                    slot_raw = m.get("ChrSlot", None)
                    try:
                        slot_i = (
                            int(slot_raw)
                            if isinstance(slot_raw, int)
                            else int(str(slot_raw).strip())
                        )
                    except Exception:
                        slot_i = None

                    cn = m.get("CharacterName", "")
                    an = m.get("AccountName", "")
                    name = ""
                    if isinstance(cn, str) and cn.strip():
                        name = cn.strip()
                    elif isinstance(an, str) and an.strip():
                        name = an.strip()
                    else:
                        name = uid_s

                    if isinstance(slot_i, int):
                        member_names_exact[(uid_s, int(slot_i))] = name
                    if uid_s not in member_names_by_user:
                        member_names_by_user[uid_s] = name

        pid = ""
        if isinstance(pinfo, dict):
            v = pinfo.get("ProspectID", "")
            if isinstance(v, (str, int)) and str(v).strip():
                pid = str(v).strip()
        if not pid:
            pid = os.path.splitext(os.path.basename(prospect_path))[0]
        self._world_prospect_pid = pid

        try:
            uc = prospect_blob_decompress(raw)
        except Exception as e:
            self._clear_world()
            self.lbl_world_status.setText(f"Не удалось распаковать ProspectBlob.\n{e}")
            return

        self._world_uc = bytearray(uc)
        self._world_tag = None
        self._world_data_start = 0
        self._world_data_end = 0
        self._world_binary = b""

        try:
            self._world_containers = self._scan_world_inventories(
                bytes(self._world_uc),
                prospect_path,
                pid,
                member_names_exact,
                member_names_by_user,
            )
        except Exception as e:
            self._world_containers = []
            self.lbl_world_status.setText(f"Не удалось распарсить инвентари мира.\n{e}")
            self._rebuild_containers_tables()
            return

        self.lbl_world_status.setText(
            f"Инвентари мира (экспериментально): найдено {len(self._world_containers)}. "
            "Источник: Игрок (PlayerState) + Объекты/постройки (Deployable)."
        )
        self._rebuild_containers_tables()

    def _active_containers_table(self) -> QTableWidget:
        w = self.tabs_world_containers.currentWidget()
        return w if isinstance(w, QTableWidget) else self.tbl_containers_chest

    def _containers_tab_changed(self, *_args) -> None:
        if self._populating_containers:
            return
        # QTabWidget can emit currentChanged during __init__ (when adding the first tab).
        if not hasattr(self, "tbl_slots"):
            return
        self._rebuild_slots_table()

    def _world_filter_changed(self, *_args) -> None:
        # lightweight: rebuild list only
        self._rebuild_containers_tables()

    def _rebuild_containers_tables(self) -> None:
        self._populating_containers = True
        try:
            tbl_active = self._active_containers_table()
            active_key = None
            active_tab = self.tabs_world_containers.currentIndex()
            cur_c = self._current_container()
            if isinstance(cur_c, dict):
                ck = cur_c.get("container_key")
                if isinstance(ck, str) and ck:
                    active_key = ck

            for tbl in (self.tbl_containers_chest, self.tbl_containers_player):
                tbl.blockSignals(True)
                tbl.setSortingEnabled(False)
                tbl.setRowCount(0)

            q = (
                self.ed_world_search.text().strip().lower()
                if hasattr(self, "ed_world_search")
                else ""
            )
            hide_empty = (
                self.cb_world_hide_empty.isChecked()
                if hasattr(self, "cb_world_hide_empty")
                else False
            )

            def container_matches(c: Dict[str, Any]) -> bool:
                if not q:
                    return True
                q_str = q

                # (id) helper
                id_q = ""
                m = re.search(r"\(([^)]+)\)", q_str)
                if m:
                    id_q = m.group(1).strip().lower()

                num_q: Optional[int] = int(q_str) if q_str.isdigit() else None
                num_id_q: Optional[int] = int(id_q) if id_q.isdigit() else None

                fields = [
                    str(c.get("display_name", "")),
                    str(c.get("actor_class", "")),
                    str(c.get("static_item", "")),
                    str(c.get("actor_path", "")),
                    str(c.get("inventory_id", "")),
                    str(c.get("container_key", "")),
                ]
                hay = " ".join(f.lower() for f in fields if isinstance(f, str) and f)
                if num_q is None:
                    if q_str and q_str in hay:
                        return True
                    if id_q and id_q in hay:
                        return True
                else:
                    inv_id = c.get("inventory_id", None)
                    if isinstance(inv_id, int) and int(inv_id) == int(num_q):
                        return True
                    try:
                        if int(c.get("slot_count", -1)) == int(num_q):
                            return True
                    except Exception:
                        pass
                    try:
                        if int(c.get("total_items", -1)) == int(num_q):
                            return True
                    except Exception:
                        pass

                slots = c.get("slots", [])
                if not isinstance(slots, list):
                    return False
                for s in slots:
                    if not isinstance(s, dict):
                        continue
                    rn = s.get("row_name", "")
                    rn_s = rn.lower() if isinstance(rn, str) else ""
                    title = SaveModel.item_pretty_name(str(rn)).lower()
                    if num_q is None:
                        if q_str and (q_str in rn_s or q_str in title):
                            return True
                        if id_q and (id_q in rn_s or id_q in title):
                            return True
                    else:
                        loc = s.get("slot_location", None)
                        if isinstance(loc, int) and int(loc) == int(num_q):
                            return True
                        st = s.get("stack", None)
                        try:
                            st_i = (
                                int(st) if isinstance(st, int) else int(str(st).strip())
                            )
                        except Exception:
                            st_i = None
                        if st_i is not None and int(st_i) == int(num_q):
                            return True
                        if num_id_q is not None:
                            if isinstance(loc, int) and int(loc) == int(num_id_q):
                                return True
                            if st_i is not None and int(st_i) == int(num_id_q):
                                return True
                return False

            def add_row(tbl: QTableWidget, c: Dict[str, Any]) -> None:
                title = c.get("display_name", "")
                slot_count = c.get("slot_count", 0)
                total_items = c.get("total_items", 0)
                contents = c.get("contents", "")

                row = tbl.rowCount()
                tbl.insertRow(row)

                it0 = QTableWidgetItem(str(title) if isinstance(title, str) else "")
                it0.setData(Qt.UserRole, c)
                it0.setFlags(it0.flags() & ~Qt.ItemIsEditable)

                it1 = QTableWidgetItem(
                    str(int(slot_count) if isinstance(slot_count, int) else 0)
                )
                it1.setData(Qt.UserRole, c)
                it1.setTextAlignment(Qt.AlignCenter)
                it1.setFlags(it1.flags() & ~Qt.ItemIsEditable)
                it1.setData(
                    Qt.EditRole, int(slot_count) if isinstance(slot_count, int) else 0
                )

                it2 = QTableWidgetItem(
                    str(int(total_items) if isinstance(total_items, int) else 0)
                )
                it2.setData(Qt.UserRole, c)
                it2.setTextAlignment(Qt.AlignCenter)
                it2.setFlags(it2.flags() & ~Qt.ItemIsEditable)
                it2.setData(
                    Qt.EditRole, int(total_items) if isinstance(total_items, int) else 0
                )

                it3 = QTableWidgetItem(
                    str(contents) if isinstance(contents, str) else ""
                )
                it3.setData(Qt.UserRole, c)
                it3.setFlags(it3.flags() & ~Qt.ItemIsEditable)

                tip: List[str] = []
                ap = c.get("actor_path", "")
                if isinstance(ap, str) and ap:
                    tip.append(ap)
                si = c.get("static_item", "")
                if isinstance(si, str) and si:
                    tip.append(f"StaticItemDataRowName: {si}")
                ac = c.get("actor_class", "")
                if isinstance(ac, str) and ac:
                    tip.append(f"ActorClassName: {ac}")
                mk = c.get("marker", "")
                if isinstance(mk, str) and mk:
                    tip.append(f"marker: {mk}  rec#{c.get('record_index', '')}")
                if tip:
                    it0.setToolTip("\n".join(tip))

                tbl.setItem(row, 0, it0)
                tbl.setItem(row, 1, it1)
                tbl.setItem(row, 2, it2)
                tbl.setItem(row, 3, it3)

            for c in self._world_containers:
                if not isinstance(c, dict):
                    continue
                slots = c.get("slots", [])
                slot_count = len(slots) if isinstance(slots, list) else 0
                if hide_empty and slot_count <= 0:
                    continue
                if not container_matches(c):
                    continue

                src = c.get("source", "")
                if src == "Игрок":
                    add_row(self.tbl_containers_player, c)
                else:
                    add_row(self.tbl_containers_chest, c)

            for tbl in (self.tbl_containers_chest, self.tbl_containers_player):
                tbl.setSortingEnabled(True)
                tbl.blockSignals(False)

            # restore selection if possible
            target_tbl = (
                tbl_active
                if tbl_active in (self.tbl_containers_chest, self.tbl_containers_player)
                else self.tbl_containers_chest
            )
            if active_key:
                found = False
                for r in range(target_tbl.rowCount()):
                    it = target_tbl.item(r, 0)
                    c = it.data(Qt.UserRole) if it else None
                    if isinstance(c, dict) and c.get("container_key") == active_key:
                        target_tbl.selectRow(r)
                        found = True
                        break
                if found:
                    self._rebuild_slots_table()
                    return

            # select first row in active tab, else clear slots
            if target_tbl.rowCount() > 0:
                target_tbl.selectRow(0)
                self._rebuild_slots_table()
            else:
                self.tbl_slots.setRowCount(0)
        finally:
            for tbl in (self.tbl_containers_chest, self.tbl_containers_player):
                tbl.blockSignals(False)
                tbl.setSortingEnabled(True)
            self._populating_containers = False

    def _current_container(self) -> Optional[Dict[str, Any]]:
        tbl = self._active_containers_table()
        r = tbl.currentRow()
        if r < 0:
            return None
        it0 = tbl.item(r, 0)
        c = it0.data(Qt.UserRole) if it0 else None
        return c if isinstance(c, dict) else None

    def _world_container_record_binary(
        self, container: Dict[str, Any]
    ) -> Optional[bytes]:
        if self._world_uc is None or not isinstance(container, dict):
            return None
        data_start = container.get("_data_start")
        data_end = container.get("_data_end")
        if not isinstance(data_start, int) or not isinstance(data_end, int):
            return None
        if data_start < 0 or data_end <= data_start or data_end > len(self._world_uc):
            return None
        return bytes(self._world_uc[int(data_start) : int(data_end)])

    def _world_extract_slot_bytes(
        self, container: Dict[str, Any], slot: Dict[str, Any]
    ) -> Optional[bytes]:
        rec_bin = self._world_container_record_binary(container)
        inv_id = container.get("inventory_id") if isinstance(container, dict) else None
        row_name = slot.get("row_name") if isinstance(slot, dict) else None
        slot_loc = slot.get("slot_location") if isinstance(slot, dict) else None
        if (
            rec_bin is None
            or not isinstance(inv_id, int)
            or not isinstance(row_name, str)
            or not isinstance(slot_loc, int)
        ):
            return None
        try:
            return saved_inventories_extract_slot_bytes(
                rec_bin, int(inv_id), int(slot_loc), row_name
            )
        except Exception:
            return None

    def _world_template_rowname_from_text(self, raw_value: Any) -> str:
        if isinstance(raw_value, str):
            raw = raw_value.strip()
        elif raw_value is None:
            raw = ""
        else:
            raw = str(raw_value).strip()
        if not raw:
            return ""
        m = re.search(r"\(([^)]+)\)\s*$", raw)
        return (m.group(1).strip() if m else raw).strip()

    def _describe_world_container(self, container: Dict[str, Any]) -> str:
        if not isinstance(container, dict):
            return ""
        title = container.get("display_name", "")
        if not isinstance(title, str) or not title.strip():
            title = container.get("inventory_info", "")
        if not isinstance(title, str) or not title.strip():
            title = f"inv {container.get('inventory_id', '?')}"
        return title.strip()

    def _find_world_slot_template_info(
        self,
        row_name: str,
        *,
        preferred_container: Optional[Dict[str, Any]] = None,
        preferred_slot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row_name_s = row_name.strip() if isinstance(row_name, str) else ""
        if not row_name_s:
            return None

        if isinstance(preferred_container, dict) and isinstance(preferred_slot, dict):
            if preferred_slot.get("row_name") == row_name_s:
                data = self._world_extract_slot_bytes(
                    preferred_container, preferred_slot
                )
                if data:
                    return {
                        "slot_bytes": data,
                        "container": preferred_container,
                        "slot": preferred_slot,
                        "container_title": self._describe_world_container(
                            preferred_container
                        ),
                    }

        ordered: List[Dict[str, Any]] = []
        if isinstance(preferred_container, dict):
            ordered.append(preferred_container)
        for c in self._world_containers:
            if isinstance(c, dict) and c is not preferred_container:
                ordered.append(c)

        for c in ordered:
            slots = c.get("slots", []) if isinstance(c, dict) else []
            if not isinstance(slots, list):
                continue
            for s in slots:
                if not isinstance(s, dict):
                    continue
                if s.get("row_name") != row_name_s:
                    continue
                data = self._world_extract_slot_bytes(c, s)
                if data:
                    return {
                        "slot_bytes": data,
                        "container": c,
                        "slot": s,
                        "container_title": self._describe_world_container(c),
                    }
        return None

    def _find_world_slot_template_bytes(
        self,
        row_name: str,
        *,
        preferred_container: Optional[Dict[str, Any]] = None,
        preferred_slot: Optional[Dict[str, Any]] = None,
    ) -> Optional[bytes]:
        info = self._find_world_slot_template_info(
            row_name,
            preferred_container=preferred_container,
            preferred_slot=preferred_slot,
        )
        if isinstance(info, dict):
            data = info.get("slot_bytes")
            return bytes(data) if isinstance(data, (bytes, bytearray)) else None
        return None

    def _world_template_hint_text(
        self,
        row_name: str,
        *,
        preferred_container: Optional[Dict[str, Any]] = None,
        preferred_slot: Optional[Dict[str, Any]] = None,
    ) -> str:
        row_name_s = row_name.strip() if isinstance(row_name, str) else ""
        if not row_name_s:
            return "Подсказка: предмет добавляется/заменяется через клонирование реального world-slot шаблона."

        info = self._find_world_slot_template_info(
            row_name_s,
            preferred_container=preferred_container,
            preferred_slot=preferred_slot,
        )
        if not isinstance(info, dict):
            return (
                f"Шаблон для `{row_name_s}` в текущем мире не найден. "
                "Без шаблона редактор не добавляет слот, чтобы не повредить мир."
            )

        src_container = info.get("container_title", "")
        slot = info.get("slot")
        slot_loc = slot.get("slot_location") if isinstance(slot, dict) else None
        if isinstance(slot_loc, int):
            return f"Шаблон найден: {src_container}, слот {int(slot_loc)}. Будет склонирован полный world-slot со скрытыми DynamicData."
        return f"Шаблон найден: {src_container}. Будет склонирован полный world-slot со скрытыми DynamicData."

    def _container_sel_changed(
        self, currentRow: int, currentColumn: int, prevRow: int, prevCol: int
    ) -> None:
        if self._populating_containers:
            return
        self._rebuild_slots_table()

    def _rebuild_slots_table(self) -> None:
        c = self._current_container()
        self._populating_slots = True
        try:
            self.tbl_slots.blockSignals(True)
            self.tbl_slots.setSortingEnabled(False)
            self.tbl_slots.setRowCount(0)

            slots = c.get("slots", []) if isinstance(c, dict) else []
            if not isinstance(slots, list):
                slots = []

            for s in slots:
                if not isinstance(s, dict):
                    continue
                row_name = s.get("row_name", "")
                slot_loc = s.get("slot_location", 0)
                stack = s.get("stack", 1)
                dur = s.get("durability", 0)

                row = self.tbl_slots.rowCount()
                self.tbl_slots.insertRow(row)

                it_loc = QTableWidgetItem(str(int(slot_loc)))
                it_loc.setData(Qt.UserRole, s)
                it_loc.setTextAlignment(Qt.AlignCenter)
                it_loc.setFlags(it_loc.flags() & ~Qt.ItemIsEditable)

                title = SaveModel.item_pretty_name(str(row_name))
                it_name = QTableWidgetItem(f"{title} ({row_name})")
                it_name.setData(Qt.UserRole, s)
                it_name.setToolTip(str(row_name))
                it_name.setFlags(it_name.flags() & ~Qt.ItemIsEditable)

                it_stack = QTableWidgetItem(str(int(stack)))
                it_stack.setData(Qt.UserRole, s)
                it_stack.setTextAlignment(Qt.AlignCenter)

                it_dur = QTableWidgetItem(str(int(dur)))
                it_dur.setData(Qt.UserRole, s)
                it_dur.setTextAlignment(Qt.AlignCenter)

                self.tbl_slots.setItem(row, 0, it_loc)
                self.tbl_slots.setItem(row, 1, it_name)
                self.tbl_slots.setItem(row, 2, it_stack)
                self.tbl_slots.setItem(row, 3, it_dur)

            self.tbl_slots.setSortingEnabled(True)
            self.tbl_slots.blockSignals(False)
            self.tbl_slots.resizeColumnToContents(0)
            self.tbl_slots.resizeColumnToContents(2)
            self.tbl_slots.resizeColumnToContents(3)
        finally:
            self.tbl_slots.blockSignals(False)
            self.tbl_slots.setSortingEnabled(True)
            self._populating_slots = False

    def _slot_cell_changed(self, row: int, col: int) -> None:
        if self._populating_slots:
            return
        if col not in (2, 3):
            return
        if not self._world_prospect_path or self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            return
        inv_id = c.get("inventory_id")
        if not isinstance(inv_id, int):
            return
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(data_start, int)
            or not isinstance(data_end, int)
            or data_start < 0
            or data_end <= data_start
        ):
            return

        it_any = self.tbl_slots.item(row, 0) or self.tbl_slots.item(row, 1)
        slot = it_any.data(Qt.UserRole) if it_any else None
        if not isinstance(slot, dict):
            return
        row_name = slot.get("row_name", "")
        slot_loc = slot.get("slot_location", None)
        if (
            not isinstance(row_name, str)
            or not row_name
            or not isinstance(slot_loc, int)
        ):
            return

        cell = self.tbl_slots.item(row, col)
        if not cell:
            return
        try:
            v = int(str(cell.text()).strip())
        except Exception:
            self._rebuild_slots_table()
            return

        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            if col == 2:
                if v <= 0:
                    v = 1
                new_rec = saved_inventories_set_slot_stack(
                    rec_bin, int(inv_id), int(slot_loc), row_name, int(v)
                )
            else:
                if v < 0:
                    v = 0
                new_rec = saved_inventories_set_slot_durability(
                    rec_bin, int(inv_id), int(slot_loc), row_name, int(v)
                )
        except Exception as e:
            QMessageBox.warning(
                self, "Инвентари мира", f"Не удалось применить изменение.\n\n{e}"
            )
            self._load_world_blob_and_tables(self._world_prospect_path)
            return

        self._commit_world_record_binary(c, new_rec)
        self._load_world_blob_and_tables(self._world_prospect_path)
        self.mark_dirty()

    def _commit_world_record_binary(
        self, container: Dict[str, Any], new_binary: bytes
    ) -> None:
        if not self._world_prospect_path or self._world_uc is None:
            raise RuntimeError("world not loaded")

        tag = container.get("_binary_tag")
        data_start = container.get("_data_start")
        data_end = container.get("_data_end")
        if (
            not isinstance(tag, _MountBlobTagEx)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            raise RuntimeError("invalid record tag offsets")

        self._world_uc[int(data_start) : int(data_end)] = bytes(new_binary)

        new_count = int(len(new_binary))
        struct.pack_into("<i", self._world_uc, int(tag.value_offset), new_count)
        struct.pack_into("<i", self._world_uc, int(tag.size_offset), new_count + 4)

        raw, _enc = self.model.load_prospect(self._world_prospect_path)
        prospect_blob_update(raw, bytes(self._world_uc))
        self.model.dirty_prospects = True
        self.model.dirty_prospect_paths.add(self._world_prospect_path)

    def _slots_context_menu(self, pos) -> None:
        row = self.tbl_slots.rowAt(pos.y())
        if row < 0:
            return
        self.tbl_slots.selectRow(row)

        it = self.tbl_slots.item(row, 0) or self.tbl_slots.item(row, 1)
        slot = it.data(Qt.UserRole) if it else None
        if not isinstance(slot, dict):
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu { background: #1E1F22; color: #DBDEE1; border: 1px solid #111214; }
            QMenu::item { padding: 6px 12px; background: transparent; }
            QMenu::item:selected { background: #4752C4; color: #ffffff; }
            QMenu::separator { height: 1px; background: #111214; margin: 4px 8px; }
            QMenu::item:disabled { color: #8e9297; }
        """
        )

        act_export = menu.addAction("Забрать в орбитальный сташ")
        act_copy = menu.addAction("Копировать (в следующий свободный слот)")
        act_replace = menu.addAction("Заменить предмет…")
        act_delete = menu.addAction("Удалить")

        act_export.triggered.connect(
            lambda _=False, s=slot: self._world_export_slot_to_stash(s)
        )
        act_copy.triggered.connect(lambda _=False, s=slot: self._world_copy_slot(s))
        act_replace.triggered.connect(
            lambda _=False, s=slot: self._world_replace_item_dialog(s)
        )
        act_delete.triggered.connect(lambda _=False, s=slot: self._world_delete_slot(s))

        exec_fn = getattr(menu, "exec", None) or getattr(menu, "exec_", None)
        exec_fn(self.tbl_slots.viewport().mapToGlobal(pos))

    def _world_copy_slot(self, slot: Dict[str, Any]) -> None:
        if not isinstance(slot, dict) or not self._world_prospect_path:
            return
        if self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            return
        inv_id = c.get("inventory_id")
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(inv_id, int)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            return

        row_name = slot.get("row_name", "")
        stack = slot.get("stack", 1)
        dur = slot.get("durability", 0)
        if not isinstance(row_name, str) or not row_name:
            return
        try:
            stack_i = int(stack) if isinstance(stack, int) else int(str(stack).strip())
        except Exception:
            stack_i = 1
        if stack_i <= 0:
            stack_i = 1
        try:
            dur_i = int(dur) if isinstance(dur, int) else int(str(dur).strip())
        except Exception:
            dur_i = 0
        if dur_i < 0:
            dur_i = 0

        slot_location = self._world_next_free_slot_location(c)
        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            template_slot = self._world_extract_slot_bytes(c, slot)
            if not template_slot:
                raise ValueError("template slot not found")
            new_slot = _ue_clone_world_slot_bytes(
                template_slot,
                int(slot_location),
                stack=int(stack_i),
                durability=int(dur_i),
            )
            new_rec = saved_inventories_insert_slot_bytes(
                rec_bin, int(inv_id), new_slot
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Инвентари мира", f"Не удалось копировать предмет.\n\n{e}"
            )
            return

        self._commit_world_record_binary(c, new_rec)
        self._load_world_blob_and_tables(self._world_prospect_path)
        self.mark_dirty()

    def _world_export_slot_to_stash(self, slot: Dict[str, Any]) -> None:
        if not isinstance(slot, dict) or not self._world_prospect_path:
            return
        if self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            return
        inv_id = c.get("inventory_id")
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(inv_id, int)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            return

        row_name = slot.get("row_name", "")
        slot_loc = slot.get("slot_location", None)
        stack = slot.get("stack", 1)
        dur = slot.get("durability", 0)
        if (
            not isinstance(row_name, str)
            or not row_name
            or not isinstance(slot_loc, int)
        ):
            return
        if not isinstance(stack, int) or stack <= 0:
            stack = 1
        if not isinstance(dur, int) or dur < 0:
            dur = 0

        # add to MetaInventory.json
        meta_items = self.model.meta.get("Items")
        if not isinstance(meta_items, list):
            self.model.meta["Items"] = meta_items = []
        new_item = SaveModel.new_meta_item(row_name)
        SaveModel.set_dyn(new_item, "ItemableStack", int(stack))
        if int(dur) > 0:
            SaveModel.set_dyn(new_item, "Durability", int(dur))
        meta_items.append(new_item)
        self.model.dirty_meta = True

        # remove from world
        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            new_rec = saved_inventories_remove_slot(
                rec_bin, int(inv_id), int(slot_loc), row_name
            )
            self._commit_world_record_binary(c, new_rec)
        except Exception as e:
            QMessageBox.warning(
                self, "Сташ", f"Не удалось забрать предмет из мира.\n\n{e}"
            )
            return

        self.mark_dirty()
        self._load_world_blob_and_tables(self._world_prospect_path)

    def _world_delete_slot(self, slot: Dict[str, Any]) -> None:
        if not isinstance(slot, dict) or not self._world_prospect_path:
            return
        if self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            return
        inv_id = c.get("inventory_id")
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(inv_id, int)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            return
        row_name = slot.get("row_name", "")
        slot_loc = slot.get("slot_location", None)
        if (
            not isinstance(row_name, str)
            or not row_name
            or not isinstance(slot_loc, int)
        ):
            return
        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            new_rec = saved_inventories_remove_slot(
                rec_bin, int(inv_id), int(slot_loc), row_name
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Инвентари мира", f"Не удалось удалить слот.\n\n{e}"
            )
            return
        self._commit_world_record_binary(c, new_rec)
        self._load_world_blob_and_tables(self._world_prospect_path)
        self.mark_dirty()

    def _world_next_free_slot_location(self, container: Dict[str, Any]) -> int:
        used: Set[int] = set()
        slots = container.get("slots", [])
        if isinstance(slots, list):
            for s in slots:
                if isinstance(s, dict) and isinstance(s.get("slot_location"), int):
                    used.add(int(s["slot_location"]))
        loc = 0
        while loc in used:
            loc += 1
        return int(loc)

    def _pick_item_rowname_dialog(
        self, title: str, default_rowname: str = ""
    ) -> Optional[str]:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        form = QFormLayout()

        cmb = QComboBox()
        cmb.setEditable(True)
        if self._game_data and self._game_data.items:
            for it in sorted(
                self._game_data.items.values(),
                key=lambda x: (x.display_name.lower(), x.row_name.lower()),
            ):
                t = it.display_name or it.row_name
                cmb.addItem(f"{t} ({it.row_name})", it.row_name)
        else:
            # fallback: no table, allow raw typing
            cmb.addItem(default_rowname or "", default_rowname or "")
        cmb.setCurrentText(default_rowname or "")
        cmb.lineEdit().setPlaceholderText("Поиск (имя или RowName)…")
        try:
            comp = QCompleter(cmb.model(), cmb)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            cmb.setCompleter(comp)
        except Exception:
            pass

        form.addRow("RowName", cmb)
        lay.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        row_name = cmb.currentData()
        if not isinstance(row_name, str) or not row_name.strip():
            raw = cmb.currentText().strip()
            m = re.search(r"\(([^)]+)\)\s*$", raw)
            row_name = m.group(1).strip() if m else raw
        row_name = row_name.strip()
        return row_name or None

    def _world_add_item_dialog(self) -> None:
        if not self._world_prospect_path:
            return
        if self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            QMessageBox.information(self, "Инвентари мира", "Выбери контейнер слева.")
            return
        inv_id = c.get("inventory_id")
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(inv_id, int)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Добавить предмет в контейнер")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()

        cmb_row = QComboBox()
        cmb_row.setEditable(True)
        if self._game_data and self._game_data.items:
            for it in sorted(
                self._game_data.items.values(),
                key=lambda x: (x.display_name.lower(), x.row_name.lower()),
            ):
                t = it.display_name or it.row_name
                cmb_row.addItem(f"{t} ({it.row_name})", it.row_name)
        cmb_row.setCurrentText("")
        cmb_row.lineEdit().setPlaceholderText("Поиск (имя или RowName)…")
        try:
            comp = QCompleter(cmb_row.model(), cmb_row)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            cmb_row.setCompleter(comp)
        except Exception:
            pass

        sb_stack = QSpinBox()
        sb_stack.setRange(1, 10**9)
        sb_stack.setValue(1)
        sb_dur = QSpinBox()
        sb_dur.setRange(0, 10**9)
        sb_dur.setValue(0)

        form.addRow("RowName", cmb_row)
        lbl_hint = QLabel("")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color:#B5BAC1;")
        form.addRow("", lbl_hint)
        form.addRow("Кол-во (stack)", sb_stack)
        form.addRow("Прочность (durability)", sb_dur)
        lay.addLayout(form)

        def refresh_template_hint(*_args) -> None:
            row_guess = cmb_row.currentData()
            if not isinstance(row_guess, str) or not row_guess.strip():
                row_guess = self._world_template_rowname_from_text(
                    cmb_row.currentText()
                )
            txt = self._world_template_hint_text(
                str(row_guess or ""), preferred_container=c
            )
            lbl_hint.setText(txt)
            if txt.startswith("Шаблон найден:"):
                lbl_hint.setStyleSheet("color:#7DD3FC;")
            elif row_guess:
                lbl_hint.setStyleSheet("color:#FBBF24;")
            else:
                lbl_hint.setStyleSheet("color:#B5BAC1;")

        cmb_row.currentTextChanged.connect(refresh_template_hint)
        refresh_template_hint()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        row_name = cmb_row.currentData()
        if not isinstance(row_name, str) or not row_name.strip():
            raw = cmb_row.currentText().strip()
            m = re.search(r"\(([^)]+)\)\s*$", raw)
            row_name = m.group(1).strip() if m else raw
        row_name = row_name.strip()
        if not row_name:
            QMessageBox.warning(self, "Инвентари мира", "RowName обязателен.")
            return

        slot_location = self._world_next_free_slot_location(c)
        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            template_info = self._find_world_slot_template_info(
                row_name, preferred_container=c
            )
            if not template_info:
                raise ValueError(
                    self._world_template_hint_text(row_name, preferred_container=c)
                )
            template_slot = template_info.get("slot_bytes")
            if not isinstance(template_slot, (bytes, bytearray)) or not template_slot:
                raise ValueError("template slot bytes missing")
            new_slot = _ue_clone_world_slot_bytes(
                template_slot,
                int(slot_location),
                stack=int(sb_stack.value()),
                durability=int(sb_dur.value()),
            )
            new_rec = saved_inventories_insert_slot_bytes(
                rec_bin, int(inv_id), new_slot
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Инвентари мира", f"Не удалось добавить предмет.\n\n{e}"
            )
            return

        self._commit_world_record_binary(c, new_rec)
        self._load_world_blob_and_tables(self._world_prospect_path)
        self.mark_dirty()

    def _world_replace_item_dialog(self, slot: Dict[str, Any]) -> None:
        if not isinstance(slot, dict) or not self._world_prospect_path:
            return
        if self._world_uc is None:
            return
        c = self._current_container()
        if not isinstance(c, dict):
            return
        inv_id = c.get("inventory_id")
        data_start = c.get("_data_start")
        data_end = c.get("_data_end")
        if (
            not isinstance(inv_id, int)
            or not isinstance(data_start, int)
            or not isinstance(data_end, int)
        ):
            return

        old_row = slot.get("row_name", "")
        slot_loc = slot.get("slot_location", None)
        if not isinstance(old_row, str) or not old_row or not isinstance(slot_loc, int):
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Заменить предмет в слоте")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()

        cmb_row = QComboBox()
        cmb_row.setEditable(True)
        if self._game_data and self._game_data.items:
            for it in sorted(
                self._game_data.items.values(),
                key=lambda x: (x.display_name.lower(), x.row_name.lower()),
            ):
                t = it.display_name or it.row_name
                cmb_row.addItem(f"{t} ({it.row_name})", it.row_name)
        cmb_row.setCurrentText(old_row)
        cmb_row.lineEdit().setPlaceholderText("Поиск (имя или RowName)…")
        try:
            comp = QCompleter(cmb_row.model(), cmb_row)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            cmb_row.setCompleter(comp)
        except Exception:
            pass

        sb_stack = QSpinBox()
        sb_stack.setRange(1, 10**9)
        sb_stack.setValue(int(slot.get("stack", 1) or 1))
        sb_dur = QSpinBox()
        sb_dur.setRange(0, 10**9)
        sb_dur.setValue(int(slot.get("durability", 0) or 0))

        form.addRow("RowName", cmb_row)
        lbl_hint = QLabel("")
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color:#B5BAC1;")
        form.addRow("", lbl_hint)
        form.addRow("Кол-во (stack)", sb_stack)
        form.addRow("Прочность (durability)", sb_dur)
        lay.addLayout(form)

        def refresh_template_hint(*_args) -> None:
            row_guess = cmb_row.currentData()
            if not isinstance(row_guess, str) or not row_guess.strip():
                row_guess = self._world_template_rowname_from_text(
                    cmb_row.currentText()
                )
            txt = self._world_template_hint_text(
                str(row_guess or ""),
                preferred_container=c,
                preferred_slot=slot,
            )
            lbl_hint.setText(txt)
            if txt.startswith("Шаблон найден:"):
                lbl_hint.setStyleSheet("color:#7DD3FC;")
            elif row_guess:
                lbl_hint.setStyleSheet("color:#FBBF24;")
            else:
                lbl_hint.setStyleSheet("color:#B5BAC1;")

        cmb_row.currentTextChanged.connect(refresh_template_hint)
        refresh_template_hint()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_row = cmb_row.currentData()
        if not isinstance(new_row, str) or not new_row.strip():
            raw = cmb_row.currentText().strip()
            m = re.search(r"\(([^)]+)\)\s*$", raw)
            new_row = m.group(1).strip() if m else raw
        new_row = new_row.strip()
        if not new_row:
            QMessageBox.warning(self, "Инвентари мира", "RowName обязателен.")
            return

        try:
            rec_bin = bytes(self._world_uc[int(data_start) : int(data_end)])
            if new_row == old_row:
                template_info = self._find_world_slot_template_info(
                    new_row,
                    preferred_container=c,
                    preferred_slot=slot,
                )
            else:
                template_info = self._find_world_slot_template_info(
                    new_row, preferred_container=c
                )
            if not template_info:
                raise ValueError(
                    self._world_template_hint_text(
                        new_row,
                        preferred_container=c,
                        preferred_slot=slot,
                    )
                )
            template_slot = template_info.get("slot_bytes")
            if not isinstance(template_slot, (bytes, bytearray)) or not template_slot:
                raise ValueError("template slot bytes missing")
            new_slot = _ue_clone_world_slot_bytes(
                template_slot,
                int(slot_loc),
                stack=int(sb_stack.value()),
                durability=int(sb_dur.value()),
            )
            rec_mid = saved_inventories_remove_slot(
                rec_bin, int(inv_id), int(slot_loc), old_row
            )
            new_rec = saved_inventories_insert_slot_bytes(
                rec_mid, int(inv_id), new_slot
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Инвентари мира", f"Не удалось заменить предмет.\n\n{e}"
            )
            return

        self._commit_world_record_binary(c, new_rec)
        self._load_world_blob_and_tables(self._world_prospect_path)
        self.mark_dirty()

    # --- Custom pets (experimental) ---

    def _current_custom_profile(self) -> CustomSpawnProfile:
        return CUSTOM_SPAWN_PROFILES[0]

    def _selected_custom_choice(self) -> Optional[GameCustomMobChoice]:
        raw = self.cmb_custom_mob.currentData()
        if isinstance(raw, GameCustomMobChoice):
            return raw
        text = self.cmb_custom_mob.currentText().strip()
        if not text or not self._game_data:
            return None
        text_cf = text.casefold()
        for choice in self._game_data.custom_mob_choices:
            if text_cf in {
                choice.picker_label.casefold(),
                choice.ai_setup.casefold(),
                choice.display_name.casefold(),
                choice.default_name.casefold(),
            }:
                return choice
        return None

    def _preferred_custom_template_index(self) -> int:
        for idx in range(self.cmb_custom_template.count()):
            mount = self.cmb_custom_template.itemData(idx)
            if mount is _pick_custom_mount_template(
                [
                    self.cmb_custom_template.itemData(i)
                    for i in range(self.cmb_custom_template.count())
                    if isinstance(self.cmb_custom_template.itemData(i), dict)
                ]
            ):
                return idx
        return 0

    def _set_custom_advanced_enabled(self, enabled: bool) -> None:
        for w in (
            self.ed_custom_actor,
            self.ed_custom_ai,
            self.cmb_custom_type,
            self.sb_custom_level,
            self.cb_custom_reset_talents,
            self.cb_custom_copy_icon,
        ):
            w.setEnabled(enabled)

    def _custom_profile_changed(self) -> None:
        return

    def _load_custom_templates(self) -> None:
        self.cmb_custom_template.blockSignals(True)
        try:
            self.cmb_custom_template.clear()

            if not self.model.root:
                self.lbl_decompile.setText("Основа: сейв не загружен.")
                self.lbl_custom_pets.setText("Сейв не загружен.")
                return
            if not self.model.mounts_path:
                self.lbl_decompile.setText("Основа: Mounts.json не найден.")
                self.lbl_custom_pets.setText("Mounts.json не найден в сейве.")
                return

            mounts_list = self.model.mounts.get("SavedMounts", [])
            if not isinstance(mounts_list, list) or not mounts_list:
                self.lbl_decompile.setText("Основа: нет шаблона.")
                self.lbl_custom_pets.setText(
                    "В Mounts.json нет SavedMounts — нужен хотя бы один питомец как шаблон."
                )
                return

            for m in mounts_list:
                if not isinstance(m, dict):
                    continue
                name = m.get("MountName", "")
                mtype = m.get("MountType", "")
                title = name if isinstance(name, str) and name else "(без имени)"
                if isinstance(mtype, str) and mtype:
                    title += f" — {mtype}"
                self.cmb_custom_template.addItem(title, m)

            self.lbl_custom_pets.setText("")
        finally:
            self.cmb_custom_template.blockSignals(False)

        if self.cmb_custom_template.count() > 0:
            self.cmb_custom_template.setCurrentIndex(
                self._preferred_custom_template_index()
            )
            self._custom_template_changed()

    def _custom_template_changed(self) -> None:
        tpl = self.cmb_custom_template.currentData()
        if not isinstance(tpl, dict):
            self.lbl_decompile.setText("Основа: шаблон не выбран.")
            return

        name = tpl.get("MountName", "")
        mtype = tpl.get("MountType", "")
        shown = name if isinstance(name, str) and name.strip() else "(без имени)"
        if isinstance(mtype, str) and mtype.strip():
            shown += f" ({mtype.strip()})"
        self.lbl_decompile.setText(f"Основа: {shown}")

    def _custom_mob_selected(self, text: str) -> None:
        if not isinstance(text, str):
            return
        s = text.strip()
        if not s:
            return
        choice = self._selected_custom_choice()
        if not choice:
            self.lbl_custom_pets.setText("Выбери моба из каталога AISetup.")
            return
        if not self.ed_custom_name.text().strip():
            self.ed_custom_name.setText(choice.default_name)
        details = [
            f"AISetup: {choice.ai_setup}",
            f"ActorClass: {_custom_choice_actor_class(choice, 'BP_Mount_Snow_Wolf_C')}",
        ]
        if choice.tags:
            details.append("Теги: " + ", ".join(choice.tags))
        self.lbl_custom_pets.setText("\n".join(details))

    def _populate_custom_mobs(self) -> None:
        choices = self._game_data.custom_mob_choices if self._game_data else []
        labels = [choice.picker_label for choice in choices]

        self.cmb_custom_mob.blockSignals(True)
        try:
            cur = self.cmb_custom_mob.currentText().strip()
            self.cmb_custom_mob.clear()
            for choice in choices:
                self.cmb_custom_mob.addItem(choice.picker_label, choice)
            if cur:
                self.cmb_custom_mob.setCurrentText(cur)
            elif self.cmb_custom_mob.count() > 0:
                self.cmb_custom_mob.setCurrentIndex(0)
        finally:
            self.cmb_custom_mob.blockSignals(False)

        try:
            comp = QCompleter(labels, self.cmb_custom_mob)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            self.cmb_custom_mob.setCompleter(comp)
        except Exception:
            pass
        if choices:
            self._custom_mob_selected(self.cmb_custom_mob.currentText())
        elif self._game_data:
            self.lbl_custom_pets.setText("Каталог AISetup пуст.")
        else:
            self.lbl_custom_pets.setText("Данные игры не загружены.")

    def _pick_decompile_class(
        self, preferred: List[str], contains_any: List[str]
    ) -> Optional[str]:
        classes = self._decompile_actor_classes or []
        for exact in preferred:
            if exact and exact in classes:
                return exact
        lows = [c.lower() for c in contains_any if isinstance(c, str) and c]
        for c in classes:
            if not isinstance(c, str) or not c:
                continue
            cl = c.lower()
            if any(x in cl for x in lows):
                return c
        return None

    def _add_spider_pet(self) -> None:
        for idx in range(self.cmb_custom_mob.count()):
            choice = self.cmb_custom_mob.itemData(idx)
            if isinstance(choice, GameCustomMobChoice) and choice.ai_setup == "Spider":
                self.cmb_custom_mob.setCurrentIndex(idx)
                break
        self._create_custom_pet()

    def _add_lava_broodling_pet(self) -> None:
        for idx in range(self.cmb_custom_mob.count()):
            choice = self.cmb_custom_mob.itemData(idx)
            if isinstance(choice, GameCustomMobChoice) and (
                choice.ai_setup == "LavaHunter"
                or choice.actor_class == "BP_NPC_LavaBroodling_C"
            ):
                self.cmb_custom_mob.setCurrentIndex(idx)
                break
        self._create_custom_pet()

    def _ensure_decompile_scan_started(self) -> None:
        if self._decompile_scan_started:
            return
        self._decompile_scan_started = True
        self.lbl_decompile.setText("decompile: готовлю кеш…")
        QTimer.singleShot(0, self._run_decompile_prepare_and_scan)

    @staticmethod
    def _guess_decompile_roots() -> List[str]:
        roots: List[str] = []
        env = os.getenv("ICARUS_DECOMPILE_PATH")
        appdata_cache = _default_drago_icarus_cache_dir()
        candidates = [
            env,
            appdata_cache,
            r"E:\decompile\icarus",
            r"D:\decompile\icarus",
            r"C:\decompile\icarus",
            "/mnt/e/decompile/icarus",
            "/mnt/d/decompile/icarus",
            "/mnt/c/decompile/icarus",
            os.path.join(os.path.expanduser("~"), "decompile", "icarus"),
        ]
        for r in candidates:
            if not r or not isinstance(r, str):
                continue
            if os.path.isfile(
                os.path.join(r, "MANIFEST_data_pak_tables.json")
            ) or os.path.isdir(os.path.join(r, "data_pak_tables")):
                if r not in roots:
                    roots.append(r)
        return roots

    @staticmethod
    def _scan_decompile_actor_classes(root: str) -> List[str]:
        tables_dir = os.path.join(root, "data_pak_tables")
        if not os.path.isdir(tables_dir):
            return []

        rx = re.compile(r"\bBP_[A-Za-z0-9_]+_C\b")
        out: set[str] = set()
        try:
            files = [
                os.path.join(tables_dir, fn)
                for fn in os.listdir(tables_dir)
                if fn.lower().endswith(".json")
            ]
        except Exception:
            files = []

        for p in files:
            try:
                with open(p, "rb") as f:
                    blob = f.read()
            except Exception:
                continue
            try:
                text = blob.decode("utf-8", errors="ignore")
            except Exception:
                try:
                    text = blob.decode("utf-16-le", errors="ignore")
                except Exception:
                    continue
            for m in rx.findall(text):
                out.add(m)

        return sorted(out)

    def _run_decompile_prepare_and_scan(self) -> None:
        cache_root = _default_drago_icarus_cache_dir()
        if cache_root:
            try:
                os.makedirs(cache_root, exist_ok=True)
            except Exception:
                cache_root = None

        if cache_root:
            tables_dir = os.path.join(cache_root, "data_pak_tables")
            have_tables = False
            if os.path.isdir(tables_dir):
                try:
                    have_tables = any(
                        fn.lower().endswith(".json") for fn in os.listdir(tables_dir)
                    )
                except Exception:
                    have_tables = False

            if not have_tables:
                game_root = self._game_data.game_root if self._game_data else None
                if not game_root:
                    roots = IcarusGameData.guess_game_roots()
                    game_root = roots[0] if roots else None

                if not game_root:
                    self.lbl_decompile.setText(
                        "decompile: не найден путь игры (задай ICARUS_GAME_PATH)"
                    )
                else:
                    data_pak = os.path.join(
                        game_root, "Icarus", "Content", "Data", "data.pak"
                    )
                    self.lbl_decompile.setText(f"decompile: извлекаю {data_pak} …")
                    try:
                        decompile_data_pak_tables(data_pak, cache_root)
                    except Exception as e:
                        self.lbl_decompile.setText(
                            f"decompile: ошибка декомпиляции: {e}"
                        )

        self._run_decompile_scan()

    def _run_decompile_scan(self) -> None:
        root = None
        for r in self._guess_decompile_roots():
            if r and os.path.isdir(r):
                root = r
                break
        if not root:
            self.lbl_decompile.setText(
                "decompile: не найден (ожидаю E:\\decompile\\icarus или ICARUS_DECOMPILE_PATH)"
            )
            return

        classes = self._scan_decompile_actor_classes(root)
        self._decompile_root = root
        self._decompile_actor_classes = classes

        if not classes:
            self.lbl_decompile.setText(
                f"decompile: {root} — классы не найдены (data_pak_tables пуст?)"
            )
            self._populate_custom_mobs()
            return

        self.lbl_decompile.setText(
            f"decompile: {root} — найдено классов: {len(classes)}"
        )
        self._populate_custom_mobs()
        try:
            comp = QCompleter(classes, self.ed_custom_actor)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            self.ed_custom_actor.setCompleter(comp)
        except Exception:
            pass

    @staticmethod
    def _mount_blob_data(mount: Dict[str, Any]) -> Optional[List[int]]:
        rec = mount.get("RecorderBlob")
        if not isinstance(rec, dict):
            return None
        data = rec.get("BinaryData")
        if not isinstance(data, list) or not all(isinstance(x, int) for x in data):
            return None
        return data

    @staticmethod
    def _extract_obj_suffix(obj_name: str) -> Optional[int]:
        if not isinstance(obj_name, str):
            return None
        m = re.search(r"_(\d+)$", obj_name)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _used_mount_icon_ids(self, mounts_list: List[Dict[str, Any]]) -> set[int]:
        used: set[int] = set()
        for m in mounts_list:
            if not isinstance(m, dict):
                continue
            icon = m.get("MountIconName")
            if isinstance(icon, str) and icon.isdigit():
                used.add(int(icon))
            data = self._mount_blob_data(m)
            if data:
                gid = mount_blob_get_int(data, "IcarusActorGUID")
                if isinstance(gid, int):
                    used.add(int(gid))
        if self.model.root:
            d = os.path.join(self.model.root, "Mounts")
            try:
                for fn in os.listdir(d):
                    if fn.lower().endswith(".exr"):
                        stem = fn[:-4]
                        if stem.isdigit():
                            used.add(int(stem))
            except Exception:
                pass
        return used

    def _used_object_suffixes(self, mounts_list: List[Dict[str, Any]]) -> set[int]:
        used: set[int] = set()
        for m in mounts_list:
            if not isinstance(m, dict):
                continue
            data = self._mount_blob_data(m)
            if not data:
                continue
            obj = mount_blob_get_fstring(data, "ObjectFName", "NameProperty")
            suf = self._extract_obj_suffix(obj or "")
            if isinstance(suf, int):
                used.add(suf)
        return used

    @staticmethod
    def _alloc_unused_int(
        used: set[int], lo: int, hi: int, max_tries: int = 2000
    ) -> int:
        for _ in range(max_tries):
            v = random.randint(int(lo), int(hi))
            if v not in used:
                used.add(v)
                return v
        v = int(hi)
        while v in used:
            v -= 1
            if v <= lo:
                raise RuntimeError("Не удалось подобрать уникальный идентификатор.")
        used.add(v)
        return v

    def _copy_mount_icon(self, src_icon: Optional[int], dst_icon: int) -> None:
        if not self.model.root:
            return
        d = os.path.join(self.model.root, "Mounts")
        if not os.path.isdir(d):
            return
        dst = os.path.join(d, f"{int(dst_icon)}.exr")
        if os.path.exists(dst):
            return
        src = ""
        if src_icon:
            cand = os.path.join(d, f"{int(src_icon)}.exr")
            if os.path.isfile(cand):
                src = cand
        if not src:
            try:
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith(".exr"):
                        cand = os.path.join(d, fn)
                        if os.path.isfile(cand):
                            src = cand
                            break
            except Exception:
                src = ""
        if not src:
            return
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass

    def _apply_mount_identity(
        self, mount: Dict[str, Any], new_icon_id: int, new_obj_suffix: int
    ) -> None:
        mount["MountIconName"] = str(int(new_icon_id))
        mount["DatabaseGUID"] = uuid.uuid4().hex.upper()
        data = self._mount_blob_data(mount)
        if not data:
            return
        mount_blob_set_int(data, "IcarusActorGUID", int(new_icon_id))

        actor_class = mount_blob_get_fstring(data, "ActorClassName") or ""
        actor_class = actor_class.strip()
        if actor_class:
            obj = f"{actor_class}_{int(new_obj_suffix)}"
            mount_blob_set_fstring(data, "ObjectFName", "NameProperty", obj)

            path = mount_blob_get_fstring(data, "ActorPathName", "StrProperty") or ""
            if "." in path:
                prefix = path.rsplit(".", 1)[0]
                mount_blob_set_fstring(
                    data, "ActorPathName", "StrProperty", prefix + "." + obj
                )

    def _create_custom_pet(self) -> None:
        if not self.model.root:
            QMessageBox.information(
                self, "Кастомный питомец", "Сначала открой папку сейва."
            )
            return
        if not self.model.mounts_path:
            QMessageBox.warning(
                self, "Кастомный питомец", "Mounts.json не найден в сейве."
            )
            return

        mounts_list = self.model.mounts.setdefault("SavedMounts", [])
        if not isinstance(mounts_list, list) or not mounts_list:
            QMessageBox.warning(
                self,
                "Кастомный питомец",
                "В Mounts.json нет SavedMounts — нужен хотя бы один шаблон.",
            )
            return

        template = self.cmb_custom_template.currentData()
        if not isinstance(template, dict):
            QMessageBox.information(
                self,
                "Кастомный питомец",
                "Не найден шаблон Saitama. Нужен хотя бы один питомец в Mounts.json.",
            )
            return

        choice = self._selected_custom_choice()
        if not choice:
            QMessageBox.warning(
                self,
                "Кастомный питомец",
                "Выбери моба или босса из каталога AISetup.",
            )
            return

        name = self.ed_custom_name.text().strip() or choice.default_name or "CUSTOM_MOB"
        mtype = str(template.get("MountType", "") or "").strip()
        level = int(template.get("MountLevel", 1) or 1)
        template_data = self._mount_blob_data(template)
        template_actor = (
            mount_blob_get_fstring(template_data, "ActorClassName")
            if template_data is not None
            else ""
        ) or ""
        actor_class = _custom_choice_actor_class(choice, template_actor)
        ai_setup = choice.ai_setup
        warns: List[str] = []

        new_mount = copy.deepcopy(template)
        new_mount["MountName"] = name
        if mtype:
            new_mount["MountType"] = mtype
        new_mount["MountLevel"] = int(template.get("MountLevel", level) or level)

        data = self._mount_blob_data(new_mount)
        if data is not None:
            mount_blob_set_fstring(data, "MountName", "StrProperty", name)

            if actor_class:
                if not mount_blob_set_fstring(
                    data, "ActorClassName", None, actor_class
                ):
                    warns.append(
                        "ActorClassName: не удалось записать в RecorderBlob (тег не найден/тип не строковый)."
                    )

            if not mount_blob_set_fstring(data, "AISetupRowName", None, ai_setup):
                warns.append(
                    "AISetupRowName: не удалось записать в RecorderBlob (тег не найден/тип не строковый)."
                )

            if actor_class and not actor_class.lower().startswith(
                ("bp_mount_", "bp_tame_")
            ):
                warns.append(
                    "Внимание: BP_NPC_* и прочие классы обычно НЕ совместимы с системой маунтов/питомцев — игра может заспавнить обычного."
                )

        used_icons = self._used_mount_icon_ids(mounts_list)
        used_suffixes = self._used_object_suffixes(mounts_list)
        new_icon = self._alloc_unused_int(used_icons, 100000, 9999999)
        new_suffix = self._alloc_unused_int(used_suffixes, 2000000000, 2147483647)

        src_icon = None
        icon_raw = template.get("MountIconName") if isinstance(template, dict) else None
        if isinstance(icon_raw, str) and icon_raw.isdigit():
            src_icon = int(icon_raw)

        self._apply_mount_identity(new_mount, new_icon, new_suffix)
        self._copy_mount_icon(src_icon, new_icon)

        mounts_list.append(new_mount)
        self.model.dirty_mounts = True
        self.mark_dirty()

        self._load_custom_templates()
        msg = f"Готово: добавлен питомец «{name}»."
        if warns:
            msg += "\n\n" + "\n".join(warns)
        QMessageBox.information(self, "Кастомный питомец", msg)


class TestServerTab(QWidget):
    def __init__(self, model: SaveModel, mark_dirty_cb) -> None:
        super().__init__()
        self.model = model
        self.mark_dirty = mark_dirty_cb
        self._proc: Optional[QProcess] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.info = QLabel(
            "TEST1: запуск/остановка dedicated server процесса.\n"
            "Важно: чтобы «создавать сервер без твинка», нужен отдельный dedicated server билд (Steam tool/SteamCMD). "
            "Здесь — только менеджер процесса: укажи .exe и аргументы."
        )
        self.info.setStyleSheet("color:#B5BAC1;")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        g = QGroupBox("Процесс сервера")
        form = QFormLayout(g)
        form.setContentsMargins(8, 10, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        exe_row = QWidget()
        exe_lay = QHBoxLayout(exe_row)
        exe_lay.setContentsMargins(0, 0, 0, 0)
        exe_lay.setSpacing(6)
        self.ed_exe = QLineEdit()
        self.ed_exe.setPlaceholderText("Путь к dedicated server .exe…")
        btn_pick = QPushButton("…")
        btn_pick.setFixedWidth(40)
        btn_pick.clicked.connect(self._pick_exe)
        exe_lay.addWidget(self.ed_exe, 1)
        exe_lay.addWidget(btn_pick)
        form.addRow("Executable", exe_row)

        self.ed_workdir = QLineEdit()
        self.ed_workdir.setPlaceholderText("Рабочая папка (опционально)…")
        form.addRow("WorkDir", self.ed_workdir)

        self.ed_args = QLineEdit()
        self.ed_args.setPlaceholderText("Аргументы запуска…")
        form.addRow("Args", self.ed_args)

        btn_row = QWidget()
        btn_lay = QHBoxLayout(btn_row)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(6)
        self.btn_start = QPushButton("Старт")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("Стоп")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        btn_lay.addWidget(self.btn_start)
        btn_lay.addWidget(self.btn_stop)
        btn_lay.addStretch(1)
        form.addRow("", btn_row)

        root.addWidget(g)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#B5BAC1;")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        try:
            self.log.setFont(QFont("Consolas", 9))
        except Exception:
            pass
        root.addWidget(self.log, 1)

    def load(self) -> None:
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        running = (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        )
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def _pick_exe(self) -> None:
        start = (
            os.path.dirname(self.ed_exe.text().strip())
            if self.ed_exe.text().strip()
            else os.path.expanduser("~")
        )
        path, _flt = QFileDialog.getOpenFileName(
            self,
            "Выбери dedicated server .exe",
            start,
            "Executable (*.exe);;Все файлы (*.*)",
        )
        if not path:
            return
        self.ed_exe.setText(path)
        if not self.ed_workdir.text().strip():
            self.ed_workdir.setText(os.path.dirname(path))

    @staticmethod
    def _split_args(raw: str) -> List[str]:
        s = (raw or "").strip()
        if not s:
            return []
        try:
            return shlex.split(s, posix=not sys.platform.startswith("win"))
        except Exception:
            return [p for p in s.split(" ") if p]

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def _start(self) -> None:
        if (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        ):
            return

        exe = self.ed_exe.text().strip()
        if not exe or not os.path.isfile(exe):
            QMessageBox.warning(
                self, "Server", "Укажи существующий путь к dedicated server .exe."
            )
            return

        workdir = self.ed_workdir.text().strip() or os.path.dirname(exe)
        args = self._split_args(self.ed_args.text())

        self.log.clear()
        self._append_log(f"> {exe} {' '.join(args)}\n")

        p = QProcess(self)
        self._proc = p
        p.setProgram(exe)
        p.setArguments(args)
        if workdir and os.path.isdir(workdir):
            p.setWorkingDirectory(workdir)

        p.readyReadStandardOutput.connect(self._read_stdout)
        p.readyReadStandardError.connect(self._read_stderr)
        p.errorOccurred.connect(self._proc_error)
        p.finished.connect(self._proc_finished)

        p.start()
        started = p.waitForStarted(3000)
        if not started:
            self._append_log("! Не удалось запустить процесс.\n")
            self._proc = None
        else:
            self.lbl_status.setText("Сервер запущен.")

        self._sync_buttons()

    def _stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            if not self._proc.waitForFinished(3000):
                self._proc.kill()
        except Exception:
            pass
        self._sync_buttons()

    def _read_stdout(self) -> None:
        if not self._proc:
            return
        try:
            data = bytes(self._proc.readAllStandardOutput()).decode(errors="ignore")
        except Exception:
            data = ""
        self._append_log(data)

    def _read_stderr(self) -> None:
        if not self._proc:
            return
        try:
            data = bytes(self._proc.readAllStandardError()).decode(errors="ignore")
        except Exception:
            data = ""
        self._append_log(data)

    def _proc_error(self, err) -> None:
        self.lbl_status.setText(f"Ошибка процесса: {err}")
        self._sync_buttons()

    def _proc_finished(self, code: int, status) -> None:
        self.lbl_status.setText(f"Процесс завершён: code={code}, status={status}")
        self._sync_buttons()


class BackupRestoreDialog(QDialog):
    def __init__(self, parent: QWidget, backups_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Откат из бэкапа")
        self._backups_dir = backups_dir
        self._selected_zip: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.cmb = QComboBox()
        self.cmb.currentIndexChanged.connect(self._selection_changed)
        root.addWidget(QLabel("Бэкап:"))
        root.addWidget(self.cmb)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(180)
        root.addWidget(self.details, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Откатить")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._btn_restore = btns.button(QDialogButtonBox.StandardButton.Ok)
        self._btn_restore.setEnabled(False)

        self._load_list()

    def selected_zip(self) -> Optional[str]:
        return self._selected_zip

    def _load_list(self) -> None:
        self.cmb.blockSignals(True)
        self.cmb.clear()

        zips: List[str] = []
        try:
            for fn in os.listdir(self._backups_dir):
                if fn.lower().endswith(".zip"):
                    zips.append(os.path.join(self._backups_dir, fn))
        except Exception:
            zips = []

        def sort_key(p: str) -> float:
            info = read_backup_zip_info(p) or {}
            created = info.get("created_at")
            if isinstance(created, str) and created:
                try:
                    dt = datetime.fromisoformat(created)
                    return dt.timestamp()
                except Exception:
                    pass
            try:
                return os.path.getmtime(p)
            except Exception:
                return 0.0

        zips.sort(key=sort_key, reverse=True)

        for p in zips:
            info = read_backup_zip_info(p) or {}
            created = info.get("created_at")
            created_s = (
                created
                if isinstance(created, str) and created
                else datetime.fromtimestamp(sort_key(p)).isoformat(timespec="seconds")
            )
            files = info.get("files", [])
            file_count = len(files) if isinstance(files, list) else 0
            self.cmb.addItem(
                f"{created_s} — {os.path.basename(p)} ({file_count} файлов)", p
            )

        self.cmb.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        p = self.cmb.currentData()
        self._selected_zip = p if isinstance(p, str) and p else None

        if not self._selected_zip or not os.path.isfile(self._selected_zip):
            self.details.setPlainText("Бэкапы не найдены.")
            self._btn_restore.setEnabled(False)
            return

        info = read_backup_zip_info(self._selected_zip) or {}
        created = info.get("created_at", "")
        base_dir = info.get("base_dir", "")
        files = info.get("files", [])

        lines: List[str] = []
        lines.append(f"Файл: {self._selected_zip}")
        if isinstance(created, str) and created:
            lines.append(f"Дата: {created}")
        if isinstance(base_dir, str) and base_dir:
            lines.append(f"Источник: {base_dir}")
        lines.append("")
        lines.append("Будет восстановлено:")

        restored: List[str] = []
        if isinstance(files, list):
            for rec in files:
                if not isinstance(rec, dict):
                    continue
                arc = rec.get("arcname")
                if isinstance(arc, str) and arc:
                    restored.append(arc.replace("\\", "/"))
        if restored:
            for a in restored:
                lines.append(f"- {a}")
        else:
            lines.append("(нет списка файлов)")

        self.details.setPlainText("\n".join(lines))
        self._btn_restore.setEnabled(True)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Icarus — редактор сохранений")

        global GAME_DATA
        try:
            GAME_DATA = IcarusGameData.try_load_default()
        except Exception:
            GAME_DATA = None

        self.model = SaveModel()

        self.tabs = QTabWidget()
        self.tab_main = MainTab(self.model, self._mark_dirty)
        self.tab_unlocks = UnlocksTab(self.model, self._mark_dirty)
        self.tab_achievements = AchievementsTab(self.model, self._mark_dirty)
        self.tab_player = PlayerTab(self.model, self._mark_dirty)
        self.tab_inv = InventoryTab(self.model, self._mark_dirty)
        self.tab_pets = PetsTab(self.model, self._mark_dirty)
        self.tab_other = OtherTab(self.model, self._mark_dirty)
        self.tab_test: Optional[TestTab] = None
        self.tab_test1: Optional[TestServerTab] = None
        self.tab_main.set_game_data(GAME_DATA)
        self.tab_achievements.set_game_data(GAME_DATA)
        self.tab_player.set_game_data(GAME_DATA)
        self.tab_inv.set_game_data(GAME_DATA)
        self.tab_pets.set_game_data(GAME_DATA)
        self.tab_other.set_game_data(GAME_DATA)

        self.tabs.addTab(self.tab_main, "Главная")
        self.tabs.addTab(self.tab_unlocks, "Разблокировки")
        self.tabs.addTab(self.tab_achievements, "Ачивки")
        self.tabs.addTab(self.tab_player, "Игрок")
        self.tabs.addTab(self.tab_inv, "Инвентарь")
        self.tabs.addTab(self.tab_pets, "Питомцы")
        self.tabs.addTab(self.tab_other, "Другое")
        if APP_TEST_MODE:
            self.tab_test = TestTab(self.model, self._mark_dirty)
            self.tab_test.set_game_data(GAME_DATA)
            self.tab_test1 = TestServerTab(self.model, self._mark_dirty)
            self.tabs.addTab(self.tab_test, "test")
            self.tabs.addTab(self.tab_test1, "test1")

        wrapper = QWidget()
        lay = QVBoxLayout(wrapper)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.lbl_status = QLabel("Авто-поиск сейва…")
        self.lbl_status.setStyleSheet("color:#B5BAC1;")
        self.lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.btn_save_all = QPushButton("Сохранить всё")
        self.btn_save_all.clicked.connect(self.save_all)
        self.btn_save_all.setEnabled(False)

        btn_open = QPushButton("Открыть папку…")
        btn_open.clicked.connect(self.pick_folder)

        self.btn_restore = QPushButton("Откат…")
        self.btn_restore.setToolTip(
            "Восстановить сейв из бэкапа (IcarusEditorBackups/*.zip)"
        )
        self.btn_restore.clicked.connect(self.restore_from_backup)
        self.btn_restore.setEnabled(False)

        top.addWidget(btn_open)
        top.addWidget(self.btn_save_all)
        top.addWidget(self.btn_restore)
        top.addWidget(self.lbl_status, 1)
        lay.addLayout(top)
        lay.addWidget(self.tabs, 1)

        self.setCentralWidget(wrapper)

        act_open = QAction("Открыть папку…", self)
        act_open.triggered.connect(self.pick_folder)

        act_save = QAction("Сохранить всё", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.save_all)

        act_restore = QAction("Откат из бэкапа…", self)
        act_restore.triggered.connect(self.restore_from_backup)

        self.menuBar().addAction(act_open)
        self.menuBar().addAction(act_save)
        self.menuBar().addAction(act_restore)

        QTimer.singleShot(0, self.try_autoload)
        self.tab_other.load()

    def _mark_dirty(self) -> None:
        self._sync_title_and_status()

    def _sync_title_and_status(self) -> None:
        base_title = "Icarus — редактор сохранений"
        dirty = " *" if self.model.has_any_dirty() else ""
        self.setWindowTitle(base_title + dirty)

        if not self.model.root:
            self.btn_save_all.setEnabled(False)
            self.btn_restore.setEnabled(False)
            return
        self.btn_save_all.setEnabled(self.model.has_any_dirty())
        self.btn_restore.setEnabled(True)

        parts = []
        if self.model.dirty_profile:
            parts.append("Профиль")
        if self.model.dirty_meta:
            parts.append("Инвентарь")
        if self.model.dirty_loadouts:
            parts.append("Снаряжение")
        if self.model.dirty_mounts:
            parts.append("Питомцы")
        if self.model.dirty_characters:
            parts.append("Игрок")
        if self.model.dirty_accolades or self.model.dirty_bestiary:
            parts.append("Ачивки")
        if self.model.dirty_prospects:
            parts.append("Мир (Prospects)")
        tail = f" — изменено: {', '.join(parts)}" if parts else ""
        shown_root = _mask_path_for_display(self.model.root)
        self.lbl_status.setText(f"Загружено: {shown_root}{tail}")

    def load_folder(self, folder: str) -> None:
        self.model.load_from_folder(folder)
        self.tab_main.load()
        self.tab_unlocks.load()
        self.tab_achievements.load()
        self.tab_player.load()
        self.tab_inv.load()
        self.tab_pets.load()
        self.tab_other.load()
        if self.tab_test:
            self.tab_test.load()
        if self.tab_test1:
            self.tab_test1.load()
        self._sync_title_and_status()

    def try_autoload(self) -> None:
        folders = guess_save_folders()
        best = pick_best_folder(folders) if folders else None
        if best:
            try:
                self.load_folder(best)
                return
            except Exception as e:
                QMessageBox.warning(self, "Авто-поиск не сработал", str(e))

        base = _default_playerdata_base()
        if base:
            self.lbl_status.setText(
                f"Не нашёл сейв автоматически. Проверь: {_mask_path_for_display(base)}"
            )
        else:
            self.lbl_status.setText(
                "Не нашёл сейв автоматически. Нажми «Открыть другую папку»."
            )

    def pick_folder(self) -> None:
        start_dir = _default_playerdata_base() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(
            self, "Выбери папку PlayerData/<SteamID>", start_dir
        )
        if not folder:
            return
        try:
            self.load_folder(folder)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def save_all(self) -> None:
        # Flush in-progress edits from spinboxes/line edits before checking dirty flags.
        focus = QApplication.focusWidget()
        if focus is not None:
            try:
                focus.clearFocus()
            except Exception:
                pass
        for sb in self.findChildren(QAbstractSpinBox):
            try:
                sb.interpretText()
            except Exception:
                pass

        if not self.model.root:
            return
        try:
            self.tab_pets.prepare_save()
        except Exception:
            pass
        if not self.model.has_any_dirty():
            QMessageBox.information(self, "ОК", "Нет изменений для сохранения.")
            self._sync_title_and_status()
            return
        try:
            saved = self.model.save_all()
            if saved:
                bak = self.model.last_backup_path
                tail = f"\n\nБэкап: {bak}" if bak else ""
                QMessageBox.information(
                    self, "ОК", "Сохранено: " + ", ".join(saved) + tail
                )
            else:
                QMessageBox.information(self, "ОК", "Нет изменений для сохранения.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
        finally:
            self._sync_title_and_status()

    def restore_from_backup(self) -> None:
        if not self.model.root:
            QMessageBox.information(self, "Откат", "Сначала открой папку сейва.")
            return

        if self.model.has_any_dirty():
            r = QMessageBox.question(
                self,
                "Откат",
                "Есть несохранённые изменения. Продолжить и потерять их?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        backups_dir = self.model.backups_dir()
        if not backups_dir or not os.path.isdir(backups_dir):
            QMessageBox.information(
                self, "Откат", "Папка с бэкапами не найдена: IcarusEditorBackups"
            )
            return

        dlg = BackupRestoreDialog(self, backups_dir)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        zip_path = dlg.selected_zip()
        if not zip_path:
            return

        try:
            restored = self.model.restore_from_backup(zip_path)
        except Exception as e:
            QMessageBox.critical(self, "Откат", str(e))
            return

        try:
            self.load_folder(self.model.root)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Откат",
                f"Файлы восстановлены, но перезагрузка сейва не удалась:\n{e}",
            )
            return

        QMessageBox.information(
            self,
            "Откат",
            "Восстановлено файлов: " + str(len(restored)),
        )


def main() -> int:
    try:
        app = QApplication(sys.argv)
    except Exception:
        _fatal_message("Ошибка запуска", traceback.format_exc())
        return 1

    app.setStyleSheet(DISCORD_QSS)
    try:
        w = MainWindow()
        w.resize(1200, 780)
        w.show()
    except Exception:
        QMessageBox.critical(None, "Ошибка", traceback.format_exc())
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
