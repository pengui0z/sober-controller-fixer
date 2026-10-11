#!/usr/bin/env python3
"""Sober Controller Fixer.

GTK3 tool that detects SDL game controllers, tests their buttons/axes live,
and applies the Sober (Roblox on Linux) flatpak fix:
  1. grants the org.vinegarhq.Sober sandbox access to /dev/input
  2. injects a correct SDL_GAMECONTROLLERCONFIG mapping via flatpak override
  3. enables allow_gamepad_permission in Sober's config.json

Fully stdlib + pygobject. SDL is loaded at runtime via ctypes; no pip deps.
"""

import argparse
import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from ctypes import c_char_p, c_int, c_uint8, c_uint16, c_void_p
from pathlib import Path

APP_NAME = "Sober Controller Fixer"
SOBER_FLATPAK_ID = "org.vinegarhq.Sober"
SOBER_CONFIG_PATH = Path.home() / ".var" / "app" / "org.vinegarhq.Sober" / "config" / "sober" / "config.json"

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GLib", "2.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    HAVE_GTK3 = True
except (ImportError, ValueError):
    HAVE_GTK3 = False
HOME_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sober-controller-fixer"
USER_DB_PATH = HOME_CONFIG_DIR / "controller_db.txt"

SDL_INIT_EVENTS = 0x00004000
SDL_INIT_JOYSTICK = 0x00000200
SDL_INIT_GAMECONTROLLER = 0x00002000

HAT_DIRECTIONS = {
    0: "released",
    0x01: "up",
    0x02: "down",
    0x04: "left",
    0x08: "right",
    0x05: "up-left",
    0x09: "up-right",
    0x06: "down-left",
    0x0A: "down-right",
}

LAYOUT_NAMES = {
    "xbox": "Xbox",
    "dualshock": "DualShock / PlayStation",
    "nintendo": "Nintendo (Switch / Pro)",
    "steam": "Steam / Deck",
    "misc": "Misc gamepad",
}

LAYOUT_LABELS = {
    "xbox": {
        "a": "A", "b": "B", "x": "X", "y": "Y",
        "back": "View", "guide": "Guide", "start": "Menu",
        "leftstick": "left stick click", "rightstick": "right stick click",
        "leftshoulder": "LB", "rightshoulder": "RB",
        "lefttrigger": "LT", "righttrigger": "RT", "dp": "D-pad",
    },
    "dualshock": {
        "a": "Cross (X)", "b": "Circle (O)", "x": "Square", "y": "Triangle",
        "back": "Share", "guide": "PS button", "start": "Options",
        "leftstick": "L3", "rightstick": "R3",
        "leftshoulder": "L1", "rightshoulder": "R1",
        "lefttrigger": "L2", "righttrigger": "R2", "dp": "D-pad",
    },
    "nintendo": {
        "a": "A", "b": "B", "x": "X", "y": "Y",
        "back": "Minus", "guide": "Home", "start": "Plus",
        "leftstick": "left stick click", "rightstick": "right stick click",
        "leftshoulder": "L", "rightshoulder": "R",
        "lefttrigger": "ZL", "righttrigger": "ZR", "dp": "D-pad",
    },
    "steam": {
        "a": "A", "b": "B", "x": "X", "y": "Y",
        "back": "Back", "guide": "Steam button", "start": "Menu",
        "leftstick": "left stick click", "rightstick": "right stick click",
        "leftshoulder": "LB", "rightshoulder": "RB",
        "lefttrigger": "LT", "righttrigger": "RT", "dp": "D-pad",
    },
    "misc": {
        "a": "A", "b": "B", "x": "X", "y": "Y",
        "back": "Select", "guide": "Home", "start": "Start",
        "leftstick": "left stick click", "rightstick": "right stick click",
        "leftshoulder": "L1", "rightshoulder": "R1",
        "lefttrigger": "L2", "righttrigger": "R2", "dp": "D-pad",
    },
}

LAYOUT_DESC = {
    "xbox": {
        "a": "the bottom face button (A) in the diamond cluster on the right",
        "b": "the right face button (B) in the diamond cluster on the right",
        "x": "the left face button (X) in the diamond cluster on the right",
        "y": "the top face button (Y) in the diamond cluster on the right",
        "back": "the small View button to the left of the big centre button",
        "guide": "the big Xbox button in the middle of the controller",
        "start": "the small Menu button to the right of the big centre button",
        "leftshoulder": "the LB bumper on the top-left edge, above the trigger",
        "rightshoulder": "the RB bumper on the top-right edge, above the trigger",
        "lefttrigger": "the LT squeeze trigger under the top-left bumper",
        "righttrigger": "the RT squeeze trigger under the top-right bumper",
    },
    "dualshock": {
        "a": "the bottom face button (Cross) in the diamond cluster on the right",
        "b": "the right face button (Circle) in the diamond cluster on the right",
        "x": "the left face button (Square) in the diamond cluster on the right",
        "y": "the top face button (Triangle) in the diamond cluster on the right",
        "back": "the Share button on the left of the touchpad",
        "guide": "the PS button in the middle, just below the touchpad",
        "start": "the Options button on the right of the touchpad",
        "leftshoulder": "the L1 button on the top-left edge, above the trigger",
        "rightshoulder": "the R1 button on the top-right edge, above the trigger",
        "lefttrigger": "the L2 squeeze trigger under the top-left bumper",
        "righttrigger": "the R2 squeeze trigger under the top-right bumper",
    },
    "nintendo": {
        "a": "the right face button (A) in the diamond cluster on the right",
        "b": "the bottom face button (B) in the diamond cluster on the right",
        "x": "the top face button (X) in the diamond cluster on the right",
        "y": "the left face button (Y) in the diamond cluster on the right",
        "back": "the small Minus button on the left of the centre",
        "guide": "the small Home button between the two sticks",
        "start": "the small Plus button on the right of the centre",
        "leftshoulder": "the L button on the top-left shoulder",
        "rightshoulder": "the R button on the top-right shoulder",
        "lefttrigger": "the ZL trigger under the top-left shoulder",
        "righttrigger": "the ZR trigger under the top-right shoulder",
    },
    "steam": {
        "a": "the bottom face button (A) in the diamond cluster on the right",
        "b": "the right face button (B) in the diamond cluster on the right",
        "x": "the left face button (X) in the diamond cluster on the right",
        "y": "the top face button (Y) in the diamond cluster on the right",
        "back": "the small Back button on the left of the centre",
        "guide": "the Steam button in the middle of the controller",
        "start": "the small Menu button on the right of the centre",
        "leftshoulder": "the LB bumper on the top-left edge, above the trigger",
        "rightshoulder": "the RB bumper on the top-right edge, above the trigger",
        "lefttrigger": "the LT squeeze trigger under the top-left bumper",
        "righttrigger": "the RT squeeze trigger under the top-right bumper",
    },
    "misc": {
        "a": "the bottom face button in the diamond cluster on the right",
        "b": "the right face button in the diamond cluster on the right",
        "x": "the left face button in the diamond cluster on the right",
        "y": "the top face button in the diamond cluster on the right",
        "back": "the small select-style button on the left of the centre",
        "guide": "the home / guide button in the middle of the controller",
        "start": "the small start-style button on the right of the centre",
        "leftshoulder": "the left bumper on the top-left edge, above the trigger",
        "rightshoulder": "the right bumper on the top-right edge, above the trigger",
        "lefttrigger": "the left squeeze trigger under the top-left bumper",
        "righttrigger": "the right squeeze trigger under the top-right bumper",
    },
}

GENERIC_DESC = {
    "leftstick": "the left stick - press straight down on the stick itself",
    "rightstick": "the right stick - press straight down on the stick itself",
    "leftx": "the left stick - tilt it all the way to the right",
    "lefty": "the left stick - tilt it all the way down",
    "rightx": "the right stick - tilt it all the way to the right",
    "righty": "the right stick - tilt it all the way down",
    "dpup": "the D-pad (the cross-shaped pad on the lower-left) - press its top edge",
    "dpdown": "the D-pad (the cross-shaped pad on the lower-left) - press its bottom edge",
    "dpleft": "the D-pad (the cross-shaped pad on the lower-left) - press its left edge",
    "dpright": "the D-pad (the cross-shaped pad on the lower-left) - press its right edge",
}


def field_desc(layout, name):
    merged = dict(GENERIC_DESC)
    merged.update(LAYOUT_DESC.get(layout, {}))
    return merged.get(name, "")

BUTTON_FIELDS = [
    ("a", False), ("b", False), ("x", False), ("y", False),
    ("back", False), ("guide", False), ("start", False),
    ("leftstick", False), ("rightstick", False),
    ("leftshoulder", False), ("rightshoulder", False),
    ("lefttrigger", False), ("righttrigger", False),
    ("dpup", True), ("dpdown", True), ("dpleft", True), ("dpright", True),
]

AXIS_FIELDS = [
    ("leftx", "Move the left stick all the way to the right"),
    ("lefty", "Move the left stick all the way down"),
    ("rightx", "Move the right stick all the way to the right"),
    ("righty", "Move the right stick all the way down"),
]

FIELD_ORDER = [f for f, _ in BUTTON_FIELDS] + [f for f, _ in AXIS_FIELDS]
DP_DIRS = {"dpup": "up", "dpdown": "down", "dpleft": "left", "dpright": "right"}
TRIGGER_FIELDS = {"lefttrigger", "righttrigger"}
FIELD_BUTTONISH = {f for f, is_dp in BUTTON_FIELDS if not is_dp}
FIELD_DPAD = {f for f, is_dp in BUTTON_FIELDS if is_dp}
FIELD_AXIS_STEP = {f for f, _ in AXIS_FIELDS}
AXIS_STEP_PROMPTS = dict(AXIS_FIELDS)


class SdlError(RuntimeError):
    pass


class SDLLib:
    """Minimal ctypes wrapper around the host libSDL2."""

    def __init__(self):
        self._lib = self._load()
        self.inited = False

    @staticmethod
    def _load():
        candidates = []
        try:
            from ctypes.util import find_library

            for name in ("SDL2-2.0", "SDL2"):
                lib = find_library(name)
                if lib:
                    candidates.append(lib)
        except Exception:
            pass
        candidates += sorted(glob.glob("/usr/lib*/libSDL2-2.0.so*") + glob.glob("/usr/lib*/libSDL2.so*"))
        tried = []
        for name in candidates:
            try:
                return ctypes.CDLL(name)
            except OSError:
                tried.append(name)
        raise SdlError("could not load libSDL2 (tried: %s)" % (tried or candidates))

    def init(self):
        if self.inited:
            return
        flags = SDL_INIT_EVENTS | SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER
        if self._lib.SDL_Init(flags) != 0:
            raise SdlError("SDL_Init failed: %s" % self.get_error())
        self.inited = True

    def quit(self):
        if self.inited:
            self._lib.SDL_Quit()
            self.inited = False

    def get_error(self):
        self._lib.SDL_GetError.restype = c_char_p
        return (self._lib.SDL_GetError() or b"").decode(errors="replace")

    def num_joysticks(self):
        return int(self._lib.SDL_NumJoysticks())

    def joystick_name_for_index(self, index):
        self._lib.SDL_JoystickNameForIndex.restype = c_char_p
        self._lib.SDL_JoystickNameForIndex.argtypes = [c_int]
        p = self._lib.SDL_JoystickNameForIndex(index)
        return p.decode(errors="replace") if p else ""

    def joystick_get_device_vendor(self, index):
        self._lib.SDL_JoystickGetDeviceVendor.restype = c_uint16
        self._lib.SDL_JoystickGetDeviceVendor.argtypes = [c_int]
        return int(self._lib.SDL_JoystickGetDeviceVendor(index))

    def joystick_get_device_product(self, index):
        self._lib.SDL_JoystickGetDeviceProduct.restype = c_uint16
        self._lib.SDL_JoystickGetDeviceProduct.argtypes = [c_int]
        return int(self._lib.SDL_JoystickGetDeviceProduct(index))

    def joystick_get_device_version(self, index):
        if not hasattr(self._lib, "SDL_JoystickGetDeviceProductVersion"):
            return 0
        self._lib.SDL_JoystickGetDeviceProductVersion.restype = c_uint16
        self._lib.SDL_JoystickGetDeviceProductVersion.argtypes = [c_int]
        return int(self._lib.SDL_JoystickGetDeviceProductVersion(index))

    def joystick_get_device_guid(self, index):
        self._lib.SDL_JoystickGetDeviceGUID.restype = c_uint8 * 16
        self._lib.SDL_JoystickGetDeviceGUID.argtypes = [c_int]
        raw = bytes(self._lib.SDL_JoystickGetDeviceGUID(index))
        return self._reconstruct_guid(raw, index)

    def _reconstruct_guid(self, raw, index):
        gu = bytearray(raw)
        product = self.joystick_get_device_product(index)
        version = self.joystick_get_device_version(index)
        gu[8:10] = product.to_bytes(2, "little")
        gu[10:12] = b"\x00\x00"
        gu[12:14] = version.to_bytes(2, "little")
        gu[14:16] = b"\x00\x00"
        return gu.hex()

    def is_game_controller(self, index):
        self._lib.SDL_IsGameController.restype = c_int
        self._lib.SDL_IsGameController.argtypes = [c_int]
        return bool(self._lib.SDL_IsGameController(index))

    def mapping_for_index(self, index):
        self._lib.SDL_GameControllerOpen.restype = c_void_p
        self._lib.SDL_GameControllerOpen.argtypes = [c_int]
        gc = self._lib.SDL_GameControllerOpen(index)
        if not gc:
            return ""
        try:
            self._lib.SDL_GameControllerMapping.restype = c_void_p
            self._lib.SDL_GameControllerMapping.argtypes = [c_void_p]
            ptr = self._lib.SDL_GameControllerMapping(gc)
            mapping = ctypes.string_at(ptr).decode(errors="replace") if ptr else ""
            self._free(ptr)
        finally:
            self._lib.SDL_GameControllerClose.restype = None
            self._lib.SDL_GameControllerClose.argtypes = [c_void_p]
            self._lib.SDL_GameControllerClose(gc)
        return mapping

    def add_mapping(self, mapping_line):
        self._lib.SDL_GameControllerAddMapping.restype = c_int
        self._lib.SDL_GameControllerAddMapping.argtypes = [c_char_p]
        return int(self._lib.SDL_GameControllerAddMapping(mapping_line.encode()))

    def open_joystick(self, index):
        self._lib.SDL_JoystickOpen.restype = c_void_p
        self._lib.SDL_JoystickOpen.argtypes = [c_int]
        joy = self._lib.SDL_JoystickOpen(index)
        if not joy:
            raise SdlError("SDL_JoystickOpen(%d) failed: %s" % (index, self.get_error()))
        return joy

    def joystick_num_buttons(self, joy):
        self._lib.SDL_JoystickNumButtons.restype = c_int
        self._lib.SDL_JoystickNumButtons.argtypes = [c_void_p]
        return int(self._lib.SDL_JoystickNumButtons(joy))

    def joystick_num_axes(self, joy):
        self._lib.SDL_JoystickNumAxes.restype = c_int
        self._lib.SDL_JoystickNumAxes.argtypes = [c_void_p]
        return int(self._lib.SDL_JoystickNumAxes(joy))

    def joystick_num_hats(self, joy):
        self._lib.SDL_JoystickNumHats.restype = c_int
        self._lib.SDL_JoystickNumHats.argtypes = [c_void_p]
        return int(self._lib.SDL_JoystickNumHats(joy))

    def joystick_get_button(self, joy, button):
        self._lib.SDL_JoystickGetButton.restype = c_uint8
        self._lib.SDL_JoystickGetButton.argtypes = [c_void_p, c_int]
        return bool(self._lib.SDL_JoystickGetButton(joy, button))

    def joystick_get_axis(self, joy, axis):
        self._lib.SDL_JoystickGetAxis.restype = ctypes.c_int16
        self._lib.SDL_JoystickGetAxis.argtypes = [c_void_p, c_int]
        return int(self._lib.SDL_JoystickGetAxis(joy, axis))

    def joystick_get_hat(self, joy, hat):
        self._lib.SDL_JoystickGetHat.restype = c_uint8
        self._lib.SDL_JoystickGetHat.argtypes = [c_void_p, c_int]
        return int(self._lib.SDL_JoystickGetHat(joy, hat))

    def pump(self):
        """Refresh joystick state without draining the event queue."""
        self._lib.SDL_JoystickUpdate.restype = None
        self._lib.SDL_JoystickUpdate.argtypes = []
        self._lib.SDL_JoystickUpdate()

    def close_joystick(self, joy):
        if joy:
            self._lib.SDL_JoystickClose.restype = None
            self._lib.SDL_JoystickClose.argtypes = [c_void_p]
            self._lib.SDL_JoystickClose(joy)

    def _free(self, ptr):
        if ptr:
            self._lib.SDL_free.restype = None
            self._lib.SDL_free.argtypes = [c_void_p]
            self._lib.SDL_free(ptr)


HAVE_SDL = False
SDL_INIT_ERROR = ""
try:
    SDL = SDLLib()
    SDL.init()
    HAVE_SDL = True
except (SdlError, OSError) as e:
    SDL = None
    SDL_INIT_ERROR = str(e)


class ControllerInfo:
    def __init__(self, index, name, guid, vid, pid, is_controller, mapping):
        self.index = index
        self.name = name or "Unknown controller"
        self.guid = guid
        self.vid = vid
        self.pid = pid
        self.is_controller = is_controller
        self.mapping = mapping

    @property
    def ids(self):
        if self.vid and self.pid:
            return "%04x:%04x" % (self.vid, self.pid)
        return "----:----"


def _script_dir():
    return Path(__file__).resolve().parent


def enumerate_devices():
    if not HAVE_SDL:
        raise SdlError(SDL_INIT_ERROR)
    out = []
    n = SDL.num_joysticks()
    for i in range(n):
        try:
            guid = SDL.joystick_get_device_guid(i)
            out.append(
                ControllerInfo(
                    i,
                    SDL.joystick_name_for_index(i),
                    guid,
                    SDL.joystick_get_device_vendor(i),
                    SDL.joystick_get_device_product(i),
                    SDL.is_game_controller(i),
                    SDL.mapping_for_index(i),
                )
            )
        except SdlError:
            out.append(ControllerInfo(i, "error enumerating device", "", 0, 0, False, ""))
    return out


def load_mapping_db():
    lines = {}
    for path in (_script_dir() / "controller_db.txt", USER_DB_PATH):
        if not path.is_file():
            continue
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            guid = line.split(",", 1)[0]
            if re.fullmatch(r"[0-9a-fA-F]{32}", guid):
                lines[guid.lower()] = line
    return lines


def mapping_for_guid(mapping_db, guid):
    if not guid:
        return None
    return mapping_db.get(guid.lower())


def mapping_name(mapping_line):
    try:
        return mapping_line.split(",", 2)[1]
    except IndexError:
        return mapping_line.split(",", 1)[0]


def save_user_mapping(line):
    USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USER_DB_PATH.open("a") as fh:
        fh.write(line.rstrip() + "\n")
    return USER_DB_PATH


def strip_jsonc(text):
    return re.sub(r"(?m)^[ \t]*//.*$", "", text)


def validate_jsonc(text):
    return json.loads(strip_jsonc(text))


def set_gamepad_permission(cfg_path, enable=True):
    cfg_path = Path(cfg_path)
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text('{\n  "allow_gamepad_permission": %s\n}\n' % ("true" if enable else "false"))
        return True, "created config with allow_gamepad_permission=%s" % enable
    text = cfg_path.read_text(errors="replace")
    target = "true" if enable else "false"
    pattern = re.compile(r'("allow_gamepad_permission"\s*:\s*)(true|false)')
    match = pattern.search(text)
    if match:
        if match.group(2) == target:
            return False, "allow_gamepad_permission already %s" % target
        new_text = pattern.sub(r"\g<1>" + target, text, count=1)
    else:
        brace = text.find("{")
        if brace == -1:
            raise SdlError("unexpected Sober config format (no opening brace)")
        new_text = text[: brace + 1] + '\n  "allow_gamepad_permission": %s,' % target + text[brace + 1 :]
    validate_jsonc(new_text)
    cfg_path.write_text(new_text)
    return True, "allow_gamepad_permission set to %s" % target


def flatpak_apply(mapping_line):
    cmds = [
        ["override", "--user", "--device=input", SOBER_FLATPAK_ID],
        ["override", "--user", "--env=SDL_GAMECONTROLLERCONFIG=" + mapping_line, SOBER_FLATPAK_ID],
    ]
    logs = []
    for cmd in cmds:
        proc = subprocess.run([_flatpak()] + cmd, capture_output=True, text=True)
        logs.append("$ flatpak %s" % " ".join(cmd))
        if proc.returncode != 0:
            logs.append(
                "FAILED (%d): %s" % (proc.returncode, (proc.stderr or proc.stdout or "").strip())
            )
        else:
            logs.append("ok")
    try:
        changed, note = set_gamepad_permission(SOBER_CONFIG_PATH, enable=True)
        logs.append("config.json: %s" % note)
    except (SdlError, ValueError) as e:
        logs.append("config.json WRITE FAILED: %s" % e)
    logs.append("Restart Sober for the fix to take effect.")
    return logs


def flatpak_revert():
    subcmd = ["override", "--user", "--unset-env=SDL_GAMECONTROLLERCONFIG", SOBER_FLATPAK_ID]
    proc = subprocess.run([_flatpak()] + subcmd, capture_output=True, text=True)
    logs = ["$ flatpak %s" % " ".join(subcmd)]
    if proc.returncode != 0:
        logs.append("CMD FAILED (%d): %s" % (proc.returncode, (proc.stderr or proc.stdout or "").strip()))
    else:
        logs.append("ok (SDL_GAMECONTROLLERCONFIG unset)")
    try:
        _, note = set_gamepad_permission(SOBER_CONFIG_PATH, enable=False)
        logs.append("config.json: %s" % note)
    except (SdlError, ValueError) as e:
        logs.append("config.json WRITE FAILED: %s" % e)
    logs.append("Note: --device=input was left enabled; it only grants controller access.")
    return logs


def _flatpak():
    return shutil.which("flatpak") or "flatpak"


def flatpak_show_overrides():
    proc = subprocess.run(
        [_flatpak(), "override", "--user", "--show", SOBER_FLATPAK_ID], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def parse_overrides(rc, out):
    status = {"device": False, "env": False, "env_value": ""}
    section = None
    for line in out.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]").lower()
            continue
        if not line.strip():
            continue
        if section == "context":
            key, _, value = line.partition("=")
            if key.strip() == "devices" and "input" in value.split(";")[0].split():
                status["device"] = True
        elif section == "environment":
            if line.startswith("SDL_GAMECONTROLLERCONFIG="):
                status["env"] = True
                status["env_value"] = line.split("=", 1)[1]
    return status


class WizardSession:
    """Pure capture state machine for the mapping wizard (no GUI/SDL deps).

    Each step waits until the controller returns to rest, then waits for exactly
    one new activation on an unused control and records its raw b/a/h index.
    """

    AXIS_DEADZONE = 14000
    DPAD = FIELD_DPAD
    AXIS_STEP = FIELD_AXIS_STEP
    BUTTONISH = FIELD_BUTTONISH

    def __init__(self, fields):
        self.fields = list(fields)
        self.total = len(self.fields)
        self.i = 0
        self.armed = False
        self.done = False
        self.captured = {}
        self.used_buttons = set()
        self.used_hats = set()
        self.used_axes = set()

    def current(self):
        if self.i < self.total:
            return self.fields[self.i]
        return None

    def skip(self):
        if self.i < self.total:
            self.i += 1
            self.armed = False
        if self.i >= self.total:
            self.done = True

    def _advance(self, field, value, kind, key):
        self.captured[field] = value
        if kind == "btn":
            self.used_buttons.add(key)
        elif kind == "hat":
            self.used_hats.add(key)
        else:
            self.used_axes.add(key)
        self.i += 1
        self.armed = False
        if self.i >= self.total:
            self.done = True

    def feed(self, buttons, axes, hats):
        clues = []
        field = self.current()
        if field is None:
            return clues
        name = field

        if not self.armed:
            ready = False
            if name in self.DPAD or name in self.BUTTONISH:
                ready = not any(buttons.values()) and all(s == 0 for s in hats.values())
            elif name in self.AXIS_STEP:
                ready = all(abs(v) < self.AXIS_DEADZONE for v in axes.values())
            if ready:
                self.armed = True
            return clues

        if name in self.BUTTONISH:
            pressed = [i for i, v in buttons.items() if v]
            if len(pressed) == 1 and pressed[0] not in self.used_buttons:
                self._advance(name, "b%d" % pressed[0], "btn", pressed[0])
            elif len(pressed) == 1:
                clues.append("button b%d was already used - pick a different one or Skip" % pressed[0])
            elif len(pressed) > 1:
                clues.append("release the extra button - press one at a time")
            elif name in TRIGGER_FIELDS:
                moved = [i for i, v in axes.items() if abs(v) > self.AXIS_DEADZONE]
                if len(moved) == 1 and (moved[0], 0) not in self.used_axes:
                    i = moved[0]
                    self._advance(name, "+a%d" % i if axes[i] > 0 else "-a%d" % i, "axis", (i, 0))
                elif len(moved) > 1:
                    clues.append("move one trigger at a time")

        elif name in self.DPAD:
            hat_active = [(h, s) for h, s in hats.items() if s != 0]
            axis_active = [(i, v) for i, v in axes.items() if abs(v) > self.AXIS_DEADZONE]
            if len(hat_active) == 1:
                h, s = hat_active[0]
                if (h, s) not in self.used_hats:
                    self._advance(name, "h%d.%d" % (h, s), "hat", (h, s))
                else:
                    clues.append("that D-pad direction (%s) is already used" % DP_DIRS.get(name, name))
            elif len(hat_active) > 1:
                clues.append("keep the D-pad still - one direction at a time")
            elif len(axis_active) == 1:
                i, v = axis_active[0]
                key = (i, 1 if v > 0 else -1)
                if key not in self.used_axes:
                    self._advance(name, "+a%d" % i if v > 0 else "-a%d" % i, "axis", key)
                else:
                    clues.append("that axis direction is already used")
            elif len(axis_active) > 1:
                clues.append("one direction at a time")

        elif name in self.AXIS_STEP:
            active = [
                (i, v) for i, v in axes.items()
                if abs(v) > self.AXIS_DEADZONE and (i, 0) not in self.used_axes
            ]
            if len(active) == 1:
                i = active[0][0]
                self._advance(name, "a%d" % i, "axis", (i, 0))
            elif len(active) > 1:
                clues.append("return to center and move one stick at a time")

        return clues

    def build(self, guid, name):
        parts = [guid, name]
        for f in FIELD_ORDER:
            if f in self.captured:
                parts.append("%s:%s" % (f, self.captured[f]))
        parts.append("platform:Linux,")
        return ",".join(parts)

    def missing(self):
        return [f for f in FIELD_ORDER if f not in self.captured]

    def duplicates(self):
        seen = {}
        for f in FIELD_ORDER:
            v = self.captured.get(f)
            if v:
                seen.setdefault(v, []).append(f)
        return {v: fs for v, fs in seen.items() if len(fs) > 1}


class FixerApp:
    """GTK3 frontend."""

    def __init__(self):
        from gi.repository import Gdk, GLib, Gtk

        self._Gdk = Gdk
        self._GLib = GLib
        self._Gtk = Gtk
        self.mapping_db = load_mapping_db()
        self.devices = []
        self.selected = None
        self.tester_joy = None
        self.tester_source = None
        self.leds = []
        self.axis_bars = []
        self.hat_labels = []
        self.wizard_joy = None
        self.wizard_source = None
        self.wizard_session = None
        self.wizard_dialog = None

        self._setup_css()
        self.window = Gtk.Window(title=APP_NAME)
        self.window.set_default_size(880, 700)
        self.window.connect("destroy", self.on_destroy)

        self._build_ui()
        self.log_line("Mapping DB: %d known fix(es)" % len(self.mapping_db))
        self.refresh_devices()
        self.refresh_sober_status_async()

    # ---- UI construction ------------------------------------------------

    def _setup_css(self):
        css = self._Gtk.CssProvider.new()
        css.load_from_data(
            b"""
            #led { padding: 8px 0; border-radius: 4px; background: #2d2d30; }
            #led.pressed { background: #4caf50; }
            .mono { font-family: monospace; }
            #wizard-prompt {
                font-size: 20px; font-weight: 700;
                color: #eaffea; background-color: #1e4d23;
                padding: 10px 12px; border-radius: 8px;
            }
            #wizard-desc { color: #c8d8c8; }
            #wizard-clue { color: #ffb27a; font-weight: 600; }
            """
        )
        self._Gtk.StyleContext.add_provider_for_screen(
            self._Gdk.Screen.get_default(), css, self._Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        root = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.window.add(root)

        split = self._Gtk.Box(orientation=self._Gtk.Orientation.HORIZONTAL, spacing=10)
        self.left = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=8)
        self.right = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=8)
        self.left.set_hexpand(True)
        self.right.set_hexpand(True)
        split.pack_start(self.left, True, True, 0)
        split.pack_start(self.right, True, True, 0)
        root.pack_start(split, True, True, 0)

        self._build_device_panel()
        self._build_details_panel()
        self._build_tester_panel()
        self._build_wizard_panel()
        self._build_sober_panel()

        self.log_buffer = self._Gtk.TextBuffer.new(None)
        self.log_view = self._Gtk.TextView.new_with_buffer(self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_wrap_mode(self._Gtk.WrapMode.CHAR)
        self.log_view.get_style_context().add_class("mono")
        sw = self._Gtk.ScrolledWindow()
        sw.set_policy(self._Gtk.PolicyType.AUTOMATIC, self._Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(110)
        sw.add(self.log_view)
        root.pack_start(sw, False, False, 0)

        self.statusbar = self._Gtk.Label(label="No device selected", halign=self._Gtk.Align.START)
        root.pack_start(self.statusbar, False, False, 0)

    def _build_device_panel(self):
        label = self._Gtk.Label(label="Connected controllers", halign=self._Gtk.Align.START)
        self.left.pack_start(label, False, False, 0)

        self.device_store = self._Gtk.ListStore(str, str, str, str, str, int)
        self.device_tree = self._Gtk.TreeView(model=self.device_store)
        for title, col in (
            ("Name", 0),
            ("VID:PID", 1),
            ("GUID", 2),
            ("Status", 3),
            ("SDL mapping", 4),
        ):
            renderer = self._Gtk.CellRendererText()
            renderer.set_property("ellipsize-set", True)
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            column = self._Gtk.TreeViewColumn(title, renderer, text=col)
            self.device_tree.append_column(column)
        self.device_tree.get_selection().connect("changed", self.on_device_selected)

        sw = self._Gtk.ScrolledWindow()
        sw.set_policy(self._Gtk.PolicyType.AUTOMATIC, self._Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(140)
        sw.add(self.device_tree)
        self.left.pack_start(sw, True, True, 0)

        refresh = self._Gtk.Button(label="Refresh devices")
        refresh.connect("clicked", lambda *a: self.refresh_devices())
        self.left.pack_start(refresh, False, False, 0)

    def _build_details_panel(self):
        expander = self._Gtk.Expander(label="Controller details")
        grid = self._Gtk.Grid(column_spacing=8, row_spacing=4, margin=6)
        grid.set_hexpand(True)

        def add_row(y, title):
            t = self._Gtk.Label(label=title, halign=self._Gtk.Align.START)
            v = self._Gtk.Label(label="", halign=self._Gtk.Align.START, selectable=True)
            v.set_hexpand(True)
            grid.attach(t, 0, y, 1, 1)
            grid.attach(v, 1, y, 1, 1)
            return v

        self.d_name = add_row(0, "Name:")
        self.d_ids = add_row(1, "IDs:")
        self.d_guid = add_row(2, "SDL GUID:")
        self.d_status = add_row(3, "Status:")
        self.d_known = add_row(4, "Known fix:")
        add_row(5, "Active SDL mapping:")
        self.mapping_label = self._Gtk.Label(label="", halign=self._Gtk.Align.START, selectable=True, wrap=True)
        self.mapping_label.get_style_context().add_class("mono")
        grid.attach(self.mapping_label, 1, 5, 1, 1)

        expander.add(grid)
        self.left.pack_start(expander, False, False, 0)

    def _build_tester_panel(self):
        expander = self._Gtk.Expander(label="Button / axis tester")
        box = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=6, margin=6)

        hint = self._Gtk.Label(
            label="Select a controller above, then Start. Move the sticks and press every button; "
            "the lit index is the b/a value SDL reports for that physical control.",
            halign=self._Gtk.Align.START,
            wrap=True,
        )
        box.pack_start(hint, False, False, 0)

        btn_row = self._Gtk.Box(spacing=6)
        self.test_start = self._Gtk.Button(label="Start testing")
        self.test_start.connect("clicked", self.start_tester)
        self.test_stop = self._Gtk.Button(label="Stop")
        self.test_stop.set_sensitive(False)
        self.test_stop.connect("clicked", self.stop_tester)
        btn_row.pack_start(self.test_start, False, False, 0)
        btn_row.pack_start(self.test_stop, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        self.tester_container = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(self.tester_container, False, False, 0)

        expander.add(box)
        self.right.pack_start(expander, False, False, 0)

    def _build_wizard_panel(self):
        expander = self._Gtk.Expander(label="Mapping wizard - build a mapping for any controller")
        box = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=6, margin=6)

        pickers = self._Gtk.Box(spacing=8)
        self.wizard_device = self._Gtk.ComboBoxText()
        self.wizard_layout = self._Gtk.ComboBoxText()
        for key, display in LAYOUT_NAMES.items():
            self.wizard_layout.append(key, display)
        self.wizard_layout.set_active(0)
        dev_label = self._Gtk.Label(label="Controller:", halign=self._Gtk.Align.START)
        lay_label = self._Gtk.Label(label="Layout:", halign=self._Gtk.Align.START)
        pickers.pack_start(dev_label, False, False, 0)
        pickers.pack_start(self.wizard_device, False, False, 0)
        pickers.pack_start(lay_label, False, False, 0)
        pickers.pack_start(self.wizard_layout, True, True, 0)
        box.pack_start(pickers, False, False, 0)

        self.wizard_instruction = self._Gtk.Label(
            label="Choose a controller and layout, then Start.", halign=self._Gtk.Align.START
        )
        self.wizard_instruction.set_name("wizard-prompt")
        self.wizard_instruction.set_line_wrap(True)
        self.wizard_instruction.set_selectable(True)
        box.pack_start(self.wizard_instruction, False, False, 0)

        self.wizard_desc = self._Gtk.Label(label="", halign=self._Gtk.Align.START)
        self.wizard_desc.set_line_wrap(True)
        self.wizard_desc.set_selectable(True)
        self.wizard_desc.set_name("wizard-desc")
        box.pack_start(self.wizard_desc, False, False, 0)

        self.wizard_clue = self._Gtk.Label(label="", halign=self._Gtk.Align.START)
        self.wizard_clue.set_line_wrap(True)
        self.wizard_clue.set_name("wizard-clue")
        box.pack_start(self.wizard_clue, False, False, 0)

        self.wizard_live = self._Gtk.Label(
            label="", halign=self._Gtk.Align.START, selectable=True
        )
        self.wizard_live.get_style_context().add_class("mono")
        box.pack_start(self.wizard_live, False, False, 0)

        step_row = self._Gtk.Box(spacing=8)
        self.wizard_step_label = self._Gtk.Label(label="", halign=self._Gtk.Align.START)
        self.wizard_progress = self._Gtk.ProgressBar()
        step_row.pack_start(self.wizard_step_label, False, False, 0)
        step_row.pack_start(self.wizard_progress, True, True, 0)
        box.pack_start(step_row, False, False, 0)

        cap = self._Gtk.Label(label="Captured so far", halign=self._Gtk.Align.START)
        box.pack_start(cap, False, False, 0)
        self.wizard_captured_buf = self._Gtk.TextBuffer.new(None)
        self.wizard_captured = self._Gtk.TextView.new_with_buffer(self.wizard_captured_buf)
        self.wizard_captured.set_editable(False)
        self.wizard_captured.get_style_context().add_class("mono")
        sw = self._Gtk.ScrolledWindow()
        sw.set_policy(self._Gtk.PolicyType.AUTOMATIC, self._Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(80)
        sw.add(self.wizard_captured)
        box.pack_start(sw, False, False, 0)

        btn_row = self._Gtk.Box(spacing=6)
        self.wizard_start = self._Gtk.Button(label="Start wizard")
        self.wizard_start.connect("clicked", self.start_wizard)
        self.wizard_skip = self._Gtk.Button(label="Skip this control")
        self.wizard_skip.connect("clicked", self.skip_wizard_step)
        self.wizard_cancel = self._Gtk.Button(label="Cancel")
        self.wizard_cancel.connect("clicked", self.stop_wizard)
        self.wizard_skip.set_sensitive(False)
        self.wizard_cancel.set_sensitive(False)
        btn_row.pack_start(self.wizard_start, False, False, 0)
        btn_row.pack_start(self.wizard_skip, False, False, 0)
        btn_row.pack_start(self.wizard_cancel, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        expander.add(box)
        self.right.pack_start(expander, False, False, 0)

    def _build_sober_panel(self):
        expander = self._Gtk.Expander(label="Apply fix to Sober (flatpak)")
        box = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=6, margin=6)

        self.sober_status = self._Gtk.Label(
            label="Checking Sober overrides...", halign=self._Gtk.Align.START, wrap=True, selectable=True
        )
        self.sober_status.get_style_context().add_class("mono")
        box.pack_start(self.sober_status, False, False, 0)

        btn_row = self._Gtk.Box(spacing=6)
        self.apply_btn = self._Gtk.Button(label="Apply fix for selected controller")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", self.apply_fix)
        self.revert_btn = self._Gtk.Button(label="Revert (unset env)")
        self.revert_btn.connect("clicked", self.revert_fix)
        self.copy_btn = self._Gtk.Button(label="Copy mapping")
        self.copy_btn.set_sensitive(False)
        self.copy_btn.connect("clicked", self.copy_mapping)
        btn_row.pack_start(self.apply_btn, False, False, 0)
        btn_row.pack_start(self.revert_btn, False, False, 0)
        btn_row.pack_start(self.copy_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        hint = self._Gtk.Label(
            label="Runs: flatpak override --user --device=input org.vinegarhq.Sober and "
            "--env=SDL_GAMECONTROLLERCONFIG=<mapping>, then enables allow_gamepad_permission. "
            "Restart Sober afterwards.",
            halign=self._Gtk.Align.START,
            wrap=True,
        )
        box.pack_start(hint, False, False, 0)

        expander.add(box)
        self.right.pack_start(expander, False, False, 0)

    # ---- logging / status ----------------------------------------------

    def log_line(self, text):
        self._GLib.idle_add(
            lambda: (self.log_buffer.insert(self.log_buffer.get_end_iter(), text + "\n"), False)[1]
        )

    def set_status(self, text):
        self.statusbar.set_text(text)

    # ---- device list ----------------------------------------------------

    def refresh_devices(self):
        if not HAVE_SDL:
            self.log_line("No SDL: %s" % SDL_INIT_ERROR)
            self.set_status("libSDL2 unavailable")
            return
        try:
            self.devices = enumerate_devices()
        except SdlError as e:
            self.log_line("enumerate failed: %s" % e)
            return
        self.device_store.clear()
        self.wizard_device.remove_all()
        for dev in self.devices:
            known = mapping_for_guid(self.mapping_db, dev.guid)
            if known:
                badge = "KNOWN FIX"
            elif dev.mapping:
                badge = "mapped"
            else:
                badge = "unmapped"
            self.device_store.append([dev.name, dev.ids, dev.guid or "-", badge, dev.mapping or "-", dev.index])
            self.wizard_device.append_text(dev.name)
        if self.devices:
            self.device_tree.get_selection().select_path(0)
            self.wizard_device.set_active(0)
            self.wizard_start.set_sensitive(True)
        else:
            self.wizard_start.set_sensitive(False)
        self.log_line("Found %d controller(s)" % len(self.devices))
        if self.devices:
            self.device_tree.get_selection().select_path(0)

    def on_device_selected(self, selection):
        model, treeiter = selection.get_selected()
        if treeiter is None:
            return
        dev = next((d for d in self.devices if d.index == model[treeiter][5]), None)
        if not dev:
            return
        self.selected = dev
        known = mapping_for_guid(self.mapping_db, dev.guid)
        self.d_name.set_text(dev.name)
        self.d_ids.set_text(dev.ids)
        self.d_guid.set_text(dev.guid or "-")
        self.d_status.set_text("SDL mapping active" if dev.mapping else "unmapped (raw joystick)")
        self.d_known.set_text(mapping_name(known) if known else "none bundled")
        self.mapping_label.set_text(dev.mapping or "(no mapping)")
        self.apply_btn.set_sensitive(bool(known))
        self.copy_btn.set_sensitive(bool(dev.mapping))
        self.set_status("%s  [%s]  %s" % (dev.name, dev.ids, "known fix available" if known else "no bundled fix"))

    def _selected_mapping(self):
        if not self.selected:
            return None
        return mapping_for_guid(self.mapping_db, self.selected.guid)

    # ---- tester ----------------------------------------------------------

    def start_tester(self, *_):
        if not self.selected:
            self.set_status("Select a controller first")
            return
        dev = self.selected
        try:
            joy = SDL.open_joystick(dev.index)
            n_buttons = SDL.joystick_num_buttons(joy)
            n_axes = SDL.joystick_num_axes(joy)
            n_hats = SDL.joystick_num_hats(joy)
        except SdlError as e:
            self.log_line("open failed: %s" % e)
            self.set_status("Could not open %s (device permission?)" % dev.name)
            return
        self.tester_joy = joy
        self._build_tester_widgets(n_buttons, n_axes, n_hats)
        self.test_start.set_sensitive(False)
        self.test_stop.set_sensitive(True)
        self.tester_source = self._GLib.timeout_add(33, self._poll_tester)
        self.set_status("Testing %s (buttons=%d, axes=%d, hats=%d)" % (dev.name, n_buttons, n_axes, n_hats))

    def _build_tester_widgets(self, n_buttons, n_axes, n_hats):
        self.tester_container.foreach(lambda w: w.destroy())
        self.leds = []
        self.axis_bars = []
        self.hat_labels = []

        cap = self._Gtk.Label(label="Buttons (b-index)", halign=self._Gtk.Align.START)
        self.tester_container.pack_start(cap, False, False, 0)
        grid = self._Gtk.Grid(column_spacing=4, row_spacing=4)
        for b in range(max(n_buttons, 1)):
            lbl = self._Gtk.Label(label="B%d" % b)
            lbl.set_name("led")
            grid.attach(lbl, b % 6, b // 6, 1, 1)
            self.leds.append(lbl)
        self.tester_container.pack_start(grid, False, False, 0)

        cap = self._Gtk.Label(label="Axes (a-index)", halign=self._Gtk.Align.START)
        self.tester_container.pack_start(cap, False, False, 0)
        axes_box = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=3)
        for a in range(max(n_axes, 1)):
            bar = self._Gtk.ProgressBar()
            bar.set_show_text(True)
            bar.set_size_request(-1, 18)
            row = self._Gtk.Box(spacing=6)
            lbl = self._Gtk.Label(label="a%d" % a, width_chars=4, halign=self._Gtk.Align.START)
            row.pack_start(lbl, False, False, 0)
            row.pack_start(bar, True, True, 0)
            axes_box.pack_start(row, False, False, 0)
            self.axis_bars.append(bar)
        self.tester_container.pack_start(axes_box, False, False, 0)

        if n_hats:
            cap = self._Gtk.Label(label="Hats", halign=self._Gtk.Align.START)
            self.tester_container.pack_start(cap, False, False, 0)
            for h in range(n_hats):
                lbl = self._Gtk.Label(label="hat%d: released" % h, halign=self._Gtk.Align.START)
                self.tester_container.pack_start(lbl, False, False, 0)
                self.hat_labels.append(lbl)
        self.tester_container.show_all()

    def _poll_tester(self):
        if not self.tester_joy:
            return False
        joy = self.tester_joy
        SDL.pump()
        for b, led in enumerate(self.leds):
            if b >= SDL.joystick_num_buttons(joy):
                break
            ctx = led.get_style_context()
            if SDL.joystick_get_button(joy, b):
                ctx.add_class("pressed")
            else:
                ctx.remove_class("pressed")
        n_axes = min(len(self.axis_bars), SDL.joystick_num_axes(joy))
        for a in range(n_axes):
            value = SDL.joystick_get_axis(joy, a)
            self.axis_bars[a].set_fraction((value + 32768) / 65535.0)
            self.axis_bars[a].set_text("%+5d" % value)
        for h in range(min(len(self.hat_labels), SDL.joystick_num_hats(joy))):
            state = SDL.joystick_get_hat(joy, h)
            self.hat_labels[h].set_text("hat%d: %s" % (h, HAT_DIRECTIONS.get(state, "state:%d" % state)))
        return True

    def stop_tester(self, *_):
        if self.tester_source is not None:
            self._GLib.source_remove(self.tester_source)
            self.tester_source = None
        if self.tester_joy:
            SDL.close_joystick(self.tester_joy)
            self.tester_joy = None
        self.test_start.set_sensitive(True)
        self.test_stop.set_sensitive(False)
        self.set_status("Testing stopped")

    def copy_mapping(self, *_):
        if not (self.selected and self.selected.mapping):
            return
        self._Gtk.Clipboard.get(self._Gdk.SELECTION_CLIPBOARD).set_text(self.selected.mapping, -1)
        self.log_line("Mapping copied to clipboard")

    # ---- mapping wizard ---------------------------------------------------

    def _wizard_device_index(self):
        active = self.wizard_device.get_active()
        if active < 0 or active >= len(self.devices):
            return None
        return self.devices[active].index

    def _wizard_dev(self):
        active = self.wizard_device.get_active()
        if active < 0 or active >= len(self.devices):
            return None
        return self.devices[active]

    def _wizard_prompt(self, name):
        layout = self.wizard_layout.get_active_id() or "misc"
        labels = LAYOUT_LABELS.get(layout, LAYOUT_LABELS["misc"])
        if name in DP_DIRS:
            return "Press and hold the D-pad %s" % DP_DIRS[name]
        if name in FIELD_AXIS_STEP:
            return AXIS_STEP_PROMPTS[name]
        if name in TRIGGER_FIELDS:
            return "Press and hold %s" % labels[name]
        return "Press and hold %s" % labels[name]

    def start_wizard(self, *_):
        if not HAVE_SDL or not self.devices:
            self.set_status("No controllers available")
            return
        dev = self._wizard_dev()
        if dev is None or self._wizard_device_index() is None:
            self.set_status("Select a controller in the wizard first")
            return
        try:
            joy = SDL.open_joystick(dev.index)
            n_buttons = SDL.joystick_num_buttons(joy)
            n_axes = SDL.joystick_num_axes(joy)
            n_hats = SDL.joystick_num_hats(joy)
        except SdlError as e:
            self.log_line("wizard open failed: %s" % e)
            self.set_status("Could not open %s (device permission?)" % dev.name)
            return
        self.stop_tester()
        self.wizard_joy = joy
        self.wizard_n_buttons = n_buttons
        self.wizard_n_axes = n_axes
        self.wizard_n_hats = n_hats
        self.wizard_session = WizardSession(FIELD_ORDER)
        self.wizard_device.set_sensitive(False)
        self.wizard_layout.set_sensitive(False)
        self.wizard_start.set_sensitive(False)
        self.wizard_skip.set_sensitive(True)
        self.wizard_cancel.set_sensitive(True)
        self._render_wizard_step()
        self.wizard_source = self._GLib.timeout_add(33, self._poll_wizard)
        self.set_status("Wizard: mapping %s" % dev.name)

    def _render_wizard_step(self):
        session = self.wizard_session
        current = session.current()
        if current is None:
            return
        name = current
        layout = self.wizard_layout.get_active_id() or "misc"
        self.wizard_instruction.set_text(self._wizard_prompt(name))
        desc = field_desc(layout, name)
        if not desc:
            if name in TRIGGER_FIELDS and name not in FIELD_AXIS_STEP:
                desc = "a squeeze trigger - digital buttons and analog triggers both work"
        if name in FIELD_AXIS_STEP:
            desc = (desc or "") + (". Keep all other sticks and triggers released" if desc else "Keep all other controls at rest")
        self.wizard_desc.set_text(desc)
        self.wizard_clue.set_text("")
        self.wizard_step_label.set_text(
            "Step %d/%d  %s" % (session.i + 1, session.total, name)
        )
        self.wizard_progress.set_fraction(session.i / float(session.total))
        parts = []
        for f in FIELD_ORDER:
            if f in session.captured:
                parts.append("%s:%s" % (f, session.captured[f]))
        self.wizard_captured_buf.set_text("\n".join(parts) if parts else "(none yet)")

    def _update_wizard_live(self, buttons, axes, hats):
        live_parts = []
        live_buttons = [i for i, v in buttons.items() if v]
        if live_buttons:
            live_parts.append("buttons: %s" % ", ".join("b%d" % i for i in live_buttons))
        live_axes = []
        for i, v in axes.items():
            if abs(v) > self.wizard_session.AXIS_DEADZONE:
                live_axes.append("a%d=%+d" % (i, int(v)))
        if live_axes:
            live_parts.append("axes: %s" % ", ".join(live_axes))
        live_hats = [(h, s) for h, s in hats.items() if s != 0]
        if live_hats:
            live_parts.append("hats: %s" % ", ".join("h%d.%d" % (h, s) for h, s in live_hats))
        self.wizard_live.set_text("live: " + "   ".join(live_parts) if live_parts else "live: (rest)")

    def _poll_wizard(self):
        if not self.wizard_joy:
            return False
        joy = self.wizard_joy
        SDL.pump()
        buttons = {b: SDL.joystick_get_button(joy, b) for b in range(self.wizard_n_buttons)}
        axes = {a: SDL.joystick_get_axis(joy, a) for a in range(self.wizard_n_axes)}
        hats = {h: SDL.joystick_get_hat(joy, h) for h in range(self.wizard_n_hats)}
        clues = self.wizard_session.feed(buttons, axes, hats)
        self._update_wizard_live(buttons, axes, hats)
        if not self.wizard_session.current():
            self._render_wizard_step()
            self.wizard_progress.set_fraction(1.0)
            self._finish_wizard()
            return False
        self._render_wizard_step()
        if clues:
            self.wizard_clue.set_text(clues[0])
        return True

    def skip_wizard_step(self, *_):
        if not self.wizard_session:
            return
        self.wizard_session.skip()
        if not self.wizard_session.current():
            self.wizard_progress.set_fraction(1.0)
            self._finish_wizard()
            return
        self._render_wizard_step()

    def stop_wizard(self, *_):
        if self.wizard_source is not None:
            self._GLib.source_remove(self.wizard_source)
            self.wizard_source = None
        if self.wizard_joy:
            SDL.close_joystick(self.wizard_joy)
            self.wizard_joy = None
        self.wizard_session = None
        self.wizard_device.set_sensitive(True)
        self.wizard_layout.set_sensitive(True)
        if self.devices:
            self.wizard_start.set_sensitive(True)
        self.wizard_skip.set_sensitive(False)
        self.wizard_cancel.set_sensitive(False)
        self.wizard_instruction.set_text("Choose a controller and layout, then Start.")
        self.wizard_desc.set_text("")
        self.wizard_clue.set_text("")
        self.wizard_step_label.set_text("")
        self.wizard_progress.set_fraction(0.0)
        self.wizard_captured_buf.set_text("")
        self.set_status("Wizard cancelled")

    def _finish_wizard(self):
        if self.wizard_source is not None:
            self._GLib.source_remove(self.wizard_source)
            self.wizard_source = None
        if self.wizard_joy:
            SDL.close_joystick(self.wizard_joy)
            self.wizard_joy = None
        dev = self._wizard_dev()
        if not dev:
            self.stop_wizard()
            return
        self.wizard_device.set_sensitive(True)
        self.wizard_layout.set_sensitive(True)
        self.wizard_start.set_sensitive(True)
        self.wizard_skip.set_sensitive(False)
        self.wizard_cancel.set_sensitive(False)
        line = self.wizard_session.build(dev.guid, dev.name)
        self.set_status("Wizard: mapping built for %s" % dev.name)
        self._show_wizard_result(line)

    def _show_wizard_result(self, line):
        session = self.wizard_session
        self.wizard_session = None
        missing = session.missing()
        dups = session.duplicates()

        dlg = self._Gtk.Dialog(
            title="Mapping ready",
            transient_for=self.window,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            "Save to my controller DB", 1,
            "Apply to Sober", 2,
            "Copy", 3,
            "Close", 0,
        )
        box = dlg.get_content_area()
        info = self._Gtk.Label(
            label="Built a mapping: GUID %s.\nSave it to your controller DB, apply it to Sober, "
            "or copy it to paste into gamecontrollerdb-style configs." % line.split(",", 1)[0],
            halign=self._Gtk.Align.START,
            wrap=True,
        )
        box.pack_start(info, False, False, 0)
        buf = self._Gtk.TextBuffer.new(None)
        buf.set_text(line)
        tv = self._Gtk.TextView.new_with_buffer(buf)
        tv.set_editable(True)
        tv.set_wrap_mode(self._Gtk.WrapMode.CHAR)
        tv.get_style_context().add_class("mono")
        sw = self._Gtk.ScrolledWindow()
        sw.set_policy(self._Gtk.PolicyType.AUTOMATIC, self._Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(110)
        sw.add(tv)
        box.pack_start(sw, False, False, 0)
        warnings = []
        if missing:
            warnings.append("Not captured (skipped): %s" % ", ".join(missing))
        if dups:
            for value, fields in dups.items():
                warnings.append("Duplicate %s: %s" % (value, ", ".join(fields)))
        if warnings:
            warn = self._Gtk.Label(label="\n".join(warnings), halign=self._Gtk.Align.START, wrap=True)
            warn.set_line_wrap(True)
            box.pack_start(warn, False, False, 0)
        box.show_all()
        self.wizard_dialog = dlg
        dlg.connect("response", self._on_wizard_dialog_response, line)
        dlg.run()
        dlg.destroy()
        self.wizard_dialog = None

    def _on_wizard_dialog_response(self, dlg, response, line):
        if response == 1:
            path = save_user_mapping(line)
            self.mapping_db = load_mapping_db()
            self.refresh_devices()
            self.log_line("Saved mapping to %s" % path)
            self.set_status("Wizard: mapping saved to %s" % path)
        elif response == 2:
            self.apply_btn.set_sensitive(False)
            self._run_bg(flatpak_apply, line, self._finish_apply)
            self.log_line("Applying wizard mapping to Sober...")
        elif response == 3:
            self._Gtk.Clipboard.get(self._Gdk.SELECTION_CLIPBOARD).set_text(line, -1)
            self.log_line("Wizard mapping copied to clipboard")
        else:
            self.set_status("Wizard cancelled")

    # ---- Sober application ----------------------------------------------

    def apply_fix(self, *_):
        mapping = self._selected_mapping()
        if not mapping:
            self.set_status("No bundled fix for the selected controller")
            return
        self.apply_btn.set_sensitive(False)
        self._run_bg(flatpak_apply, mapping, self._finish_apply)

    def revert_fix(self, *_):
        self.apply_btn.set_sensitive(False)
        self._run_bg(flatpak_revert, None, self._finish_apply)

    def _run_bg(self, fn, arg, done):
        self.set_status("Working...")

        def worker():
            try:
                result = fn() if arg is None else fn(arg)
            except Exception as e:
                result = ["ERROR: %s" % e]
            self._GLib.idle_add(done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_apply(self, logs):
        self.apply_btn.set_sensitive(bool(self._selected_mapping()))
        for line in logs:
            self.log_line(line)
        self.refresh_sober_status_async()
        self.set_status("Done (see log)")

    def refresh_sober_status_async(self):
        def worker():
            rc, out, err = flatpak_show_overrides()
            status = parse_overrides(rc, out) if rc == 0 else {"error": err or "flatpak override --show failed"}
            self._GLib.idle_add(self._render_sober_status, status)

        threading.Thread(target=worker, daemon=True).start()

    def _render_sober_status(self, status):
        lines = []
        if "error" in status:
            lines.append("flatpak override --show failed: %s" % status["error"])
        else:
            lines.append("sandbox device access : %s" % ("granted (--device=input)" if status["device"] else "NOT granted"))
            if status["env"]:
                lines.append("SDL_GAMECONTROLLERCONFIG : set (GUID %s)" % status["env_value"].split(",", 1)[0])
            else:
                lines.append("SDL_GAMECONTROLLERCONFIG : not set")
            if SOBER_CONFIG_PATH.exists():
                try:
                    cfg = validate_jsonc(SOBER_CONFIG_PATH.read_text(errors="replace"))
                    lines.append("allow_gamepad_permission: %s" % cfg.get("allow_gamepad_permission", False))
                except ValueError:
                    lines.append("allow_gamepad_permission: config unparseable")
            else:
                lines.append("allow_gamepad_permission: config missing")
        self.sober_status.set_text("\n".join(lines))

    def on_destroy(self, *_):
        self.stop_tester()
        self.stop_wizard()
        SDL.quit() if HAVE_SDL else None
        self._Gtk.main_quit()


def cli_debug():
    db = load_mapping_db()
    print("mapping DB entries: %d" % len(db))
    if not HAVE_SDL:
        print("SDL unavailable: %s" % SDL_INIT_ERROR)
        return 1
    devices = enumerate_devices()
    print("controllers found: %d" % len(devices))
    for dev in devices:
        known = mapping_for_guid(db, dev.guid)
        print("- %s" % dev.name)
        print("    ids:      %s" % dev.ids)
        print("    guid:     %s" % (dev.guid or "-"))
        print("    mapped:   %s" % ("yes" if dev.mapping else "no"))
        if known:
            print("    known fix: yes (%s)" % mapping_name(known))
        else:
            print("    known fix: no")
    if devices:
        dev = devices[0]
        known = mapping_for_guid(db, dev.guid)
        if known:
            rc = SDL.add_mapping(known)
            print(
                "add mapping for GUID %s -> SDL returned %d (1=added, 0=updated, -1=parse error)"
                % (dev.guid, rc)
            )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sober_controller_fixer")
    parser.add_argument("--debug", action="store_true", help="print detected controllers, then exit")
    args = parser.parse_args(argv)

    if args.debug:
        return cli_debug()

    if not HAVE_GTK3:
        print("GTK3 (python-gobject and GTK3 bindings) is required for the GUI.", file=sys.stderr)
        print("You can run with --debug for a headless controller listing.", file=sys.stderr)
        return 2

    from gi.repository import Gtk

    app = FixerApp()
    app.window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())