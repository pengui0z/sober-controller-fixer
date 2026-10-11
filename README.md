# Sober Controller Fixer

GTK3 tool for fixing gamepad SDL mappings in [Sober](https://sober.vinegarhq.org)
(the unofficial Roblox client for Linux), built around the real-world issue where
some controllers (e.g. **BDA NSW Spectra Wired Controller**, USB `20d6:a714`)
ship with a broken or missing SDL mapping, so buttons are scrambled in-game.

The app detects game controllers via SDL, lets you test every button/axis live,
and applies a one-click flatpak sandbox override so Sober sees a correct mapping.

![stack](https://img.shields.io/badge/stack-Python%20+%20GTK3-4caf50)

## Features

- **Controller detection** – lists every SDL-visible joystick with its name,
  vendor/product ID, connection type (USB, Bluetooth, ...), 32-char SDL GUID,
  and whether a mapping is active.
- **Live button/axis tester** – opens the raw joystick and shows which `b`/`a`/`h`
  index each physical control reports (no need to disassemble layouts by hand).
- **Mapping wizard** – builds a correct SDL mapping for *any* controller by
  prompting for each control (with Xbox / PlayStation / Nintendo / Steam / generic
  layouts) and recording the button, axis, or D-pad the device actually reports.
  The finished line can be saved to your personal DB, applied to Sober, or copied.
- **Known-fix database** – bundled (and user-extendable) SDL gamecontrollerdb
  lines keyed by GUID; matching devices get an "Apply fix" button.
- **Bluetooth support** – wireless controllers are detected the same as wired
  ones. If only a USB variant of your pad is in the database, the fix is matched
  by vendor/product and re-keyed to the Bluetooth GUID automatically, so the
  same one-click apply works over Bluetooth (and vice versa).
- **One-click Sober integration** – applies the exact same fix that worked on a
  real machine:
  1. `flatpak override --user --device=input org.vinegarhq.Sober`
  2. `flatpak override --user --env=SDL_GAMECONTROLLERCONFIG=<mapping> org.vinegarhq.Sober`
  3. sets `allow_gamepad_permission: true` in Sober's `config.json`
- **Status panel** – shows the current sandbox device access, the active
  `SDL_GAMECONTROLLERCONFIG` value, and the gamepad permission flag.
- **Revert** – unsets the env override (deliberately keeps `--device=input`,
  which only grants controller access).
- **Headless mode** – `python3 sober_controller_fixer.py --debug` prints a DIAGNOSTICS
  listing for debug/CI/issue reports.

## Requirements

- Python 3.9+
- `pygobject` with GTK3 bindings (`gir1.2-gtk-3.0`)
- `flatpak`
- A system copy of libSDL2 (`libsdl2`) loaded at runtime via `ctypes` – no pip packages.

Arch notes (CachyOS):

```bash
sudo pacman -S python-gobject gtk3 sdl2 flatpak
```

## Usage

```bash
python3 sober_controller_fixer.py        # GUI
python3 sober_controller_fixer.py --debug  # headless controller listing
```

1. Select your controller in the list. A "KNOWN FIX" status means a bundled
   mapping exists for it. If it was matched across connection types (e.g. a
   Bluetooth pad using the USB fix), the status shows where it came from, e.g.
   "KNOWN FIX (USB)".
2. (Optional) hit **Start testing** and press every button / move every axis;
   the lit `b`/`a` indices are what SDL currently reports.
3. Click **Apply fix for selected controller**.
4. **Restart Sober** – the sandbox env override only applies on the next launch.

## Mapping wizard

Use the **Mapping wizard** panel to generate a mapping for a controller that has
no known fix:

1. Pick the controller and the layout that matches its labels (Xbox, DualShock,
   Nintendo, Steam, or generic). The layout only changes the on-screen prompts
   ("Press B" vs "Press Circle"); it does not change how bindings are stored.
2. Click **Start wizard**. For each SDL logical input it waits until the
   controller is back at rest, then asks you to press/hold/pull the matching
   physical control. It accepts buttons, analog-trigger axes, and D-pads that
   surface as either a hat or an axis.
3. Use **Skip this control** to leave a control unmapped (e.g. a controller
    without a Guide button). **Cancel** aborts the session. Each step shows a
    big, highlighted prompt naming the control plus a short description of
    where to find it on the chosen layout (Xbox/DualShock/Nintendo/Steam).
4. When all 21 controls are done a preview dialog opens. From there you can:
   - **Save to my controller DB** – appends the line to
     `~/.config/sober-controller-fixer/controller_db.txt` (merged on next load,
     so the device immediately shows "KNOWN FIX");
   - **Apply to Sober** – runs the same flatpak fix with the new mapping;
   - **Copy** – put the line on the clipboard for gamecontrollerdb-style configs.

Warnings are shown for controls you skipped and for any resource used twice
(for example the same button index claimed by two controls).

## Adding your own controller

The bundled database lives in `controller_db.txt` (one SDL gamecontrollerdb line
per device). You can also drop a personal file at
`~/.config/sober-controller-fixer/controller_db.txt` – both are merged.

Line format:

```
<32-hex-char-guid>,<name>,a:<binding>,b:<binding>,...,platform:Linux,
```

To build one for an unmapped device, the easiest path is the built-in
**mapping wizard** (see above) – it generates the full line automatically.

To write one by hand:

1. Run `--debug` (or check the details panel) to get the device's SDL GUID.
2. Use the built-in tester to find which physical button reports which `b` index
   (and which physical axis/hat reports which `a`/`h` index).
3. Write the mapping, mapping each SDL logical input to that index, e.g.
   `a:b0,b:b1,x:b2,y:b3,...`.
4. Relaunch the app – the device should now show "KNOWN FIX" and become applyable.

The bundled entry for the NSW Spectra Wired Controller:

```
03008d8dd620000014a7000011010000,Bensussen Deutsch & Associates NSW Spectra Wired Controller,a:b2,b:b1,x:b3,y:b0,back:b8,guide:b12,start:b9,leftstick:b10,rightstick:b11,leftshoulder:b4,rightshoulder:b5,lefttrigger:b6,righttrigger:b7,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,rightx:a2,righty:a3,platform:Linux,
```

Gate buttons (A/B/X/Y), triggers, sticks, shoulder buttons, and dpad had hat were
all verified button-by-button against the physical controller.

## How the flatpak fix works

Sober runs sandboxed by flatpak. Two things block controllers:

1. **Device access** – the sandbox can't read `/dev/input` unless granted:
   `flatpak override --user --device=input org.vinegarhq.Sober`
2. **SDL mapping** – even with access, SDL's built-in database may map the
   controller incorrectly. Injecting an override makes every SDL game see the
   right layout:
   `flatpak override --user --env=SDL_GAMECONTROLLERCONFIG=<mapping> org.vinegarhq.Sober`

`config.json`'s `allow_gamepad_permission` flag is edited JSONC-safely
(comment lines preserved; the file still validated after the edit).

## Notes & limitations

- The env override maps controllers inside the Sober sandbox only; the host SDL
  isn't affected.
- Changes take effect when Sober restarts.
- The 32-char SDL GUID is reconstructed from `SDL_JoystickGetDeviceGUID`'s
  first 8 bytes plus the product/version getters, because returning a 16-byte
  struct by value is unreliable through `ctypes` on x86-64 (SSE-register ABI).
  Layout was verified against the known-good GUID `03008d8dd620000014a7000011010000`.
- The first two GUID bytes (little-endian) encode the connection bus (0x03 USB,
  0x05 Bluetooth, ...). A pad connected over Bluetooth therefore has a different
  GUID than the same pad on USB; the known-fix lookup falls back to
  vendor:product matching so one entry covers both, applying under the device's
  own GUID.
- `--device=input` is intentionally left in place by Revert.

## Debugging

`python3 sober_controller_fixer.py --debug` outputs:

```
mapping DB entries: 1
controllers found: 1
- BDA NSW Spectra Wired Controller
    ids:      20d6:a714
    conn:     USB
    guid:     03008d8dd620000014a7000011010000
    mapped:   yes
    known fix: yes (Bensussen Deutsch & Associates NSW Spectra Wired Controller)
```

The same pad over Bluetooth reports `conn: Bluetooth` with a `0500...` GUID and
shows `known fix: yes (<name>, via USB variant, re-keyed)`.

## Disclaimer

Not affiliated with Roblox, VinegarHQ, or Sober. Use at your own risk.