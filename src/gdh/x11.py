"""One of gdh's X displays seen from outside the program on it: its windows, its screen, and input through XTest.

A --binary session (blackbox.py) has no harness in the program, so gdh reads and drives the display instead, as a
person's screen, mouse and keyboard would: Pillow reads the screen (ImageGrab, through XCB), and libX11 and libXtst,
loaded with ctypes, list the windows and send the input. XTest events go through the X server like a real device's, so
the program sees the pointer enter its window, move, press and let go, and keys press and release with the keyboard's
own keycodes.

Neither display has a window manager, so nothing gives a window the keyboard's focus. Godot sets the focus on its own
window when it's clicked, which loses the click that does it; gdh gives the window under a click, or the window keys go
to, the focus first, and lets the program take it in before the input arrives.
"""
import ctypes
import time

from gdh.godot import GdhError

# Xlib's constants.
IS_VIEWABLE = 2
INPUT_OUTPUT = 1
REVERT_TO_PARENT = 2
CURRENT_TIME = 0
POINTER_ROOT = 1
NO_SYMBOL = 0
ANY_PROPERTY_TYPE = 0

# How long a program gets to take in a change of focus, a pointer that moved, a button or key held.
SETTLE_S = 0.1
HOLD_S = 0.05
BETWEEN_KEYS_S = 0.03

BUTTONS = {"left": 1, "middle": 2, "right": 3}
WHEEL = {"up": 4, "down": 5, "left": 6, "right": 7}

# Key names in other spellings (Godot's among them), lowercased, to X keysym names.
KEY_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "space": "space", "tab": "Tab",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "meta": "Super_L",
    "super": "Super_L", "win": "Super_L", "menu": "Menu", "capslock": "Caps_Lock", "plus": "plus", "minus": "minus",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}
CHAR_KEYSYMS = {"\n": 0xff0d, "\t": 0xff09}


class X11Error(GdhError):
    pass


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
                ("root", ctypes.c_ulong), ("class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


_libs = None
# Xlib's default error handler exits the process: a window that closes as gdh looks at it would end the command.
_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda display, event: 0)


def libs():
    global _libs
    if _libs is None:
        try:
            x11 = ctypes.CDLL("libX11.so.6")
            xtst = ctypes.CDLL("libXtst.so.6")
        except OSError as e:
            raise X11Error(f"Driving a --binary session needs libX11 and libXtst (Arch: libx11 libxtst, "
                           f"Debian/Ubuntu: libx11-6 libxtst6): {e}") from None
        p, ul, i, u = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
        for name, res, argtypes in (
                ("XOpenDisplay", p, [ctypes.c_char_p]),
                ("XCloseDisplay", i, [p]),
                ("XDefaultRootWindow", ul, [p]),
                ("XDisplayWidth", i, [p, i]),
                ("XDisplayHeight", i, [p, i]),
                ("XQueryTree", i, [p, ul, ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(ctypes.POINTER(ul)),
                                   ctypes.POINTER(u)]),
                ("XGetWindowAttributes", i, [p, ul, ctypes.POINTER(XWindowAttributes)]),
                ("XFetchName", i, [p, ul, ctypes.POINTER(ctypes.c_char_p)]),
                ("XInternAtom", ul, [p, ctypes.c_char_p, i]),
                ("XGetWindowProperty", i, [p, ul, ul, ctypes.c_long, ctypes.c_long, i, ul, ctypes.POINTER(ul),
                                           ctypes.POINTER(i), ctypes.POINTER(ul), ctypes.POINTER(ul),
                                           ctypes.POINTER(ctypes.c_void_p)]),
                ("XFree", i, [p]),
                ("XGetInputFocus", i, [p, ctypes.POINTER(ul), ctypes.POINTER(i)]),
                ("XSetInputFocus", i, [p, ul, i, ul]),
                ("XQueryPointer", i, [p, ul, ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(i),
                                      ctypes.POINTER(i), ctypes.POINTER(i), ctypes.POINTER(i), ctypes.POINTER(u)]),
                ("XSync", i, [p, i]),
                ("XStringToKeysym", ul, [ctypes.c_char_p]),
                ("XDisplayKeycodes", i, [p, ctypes.POINTER(i), ctypes.POINTER(i)]),
                ("XGetKeyboardMapping", ctypes.POINTER(ul), [p, ctypes.c_ubyte, i, ctypes.POINTER(i)]),
                ("XChangeKeyboardMapping", i, [p, i, i, ctypes.POINTER(ul), i]),
                ("XSetErrorHandler", p, [p])):
            getattr(x11, name).restype, getattr(x11, name).argtypes = res, argtypes
        for name, argtypes in (("XTestQueryExtension", [p, *[ctypes.POINTER(i)] * 4]),
                               ("XTestFakeMotionEvent", [p, i, i, i, ul]),
                               ("XTestFakeButtonEvent", [p, u, i, ul]),
                               ("XTestFakeKeyEvent", [p, u, i, ul])):
            getattr(xtst, name).restype, getattr(xtst, name).argtypes = i, argtypes
        x11.XSetErrorHandler(ctypes.cast(_ERROR_HANDLER, ctypes.c_void_p))
        _libs = x11, xtst
    return _libs


def grab(name):
    """The display's whole screen as an RGB image (Pillow reads it through XCB)."""
    from PIL import ImageGrab
    try:
        return ImageGrab.grab(xdisplay=name)
    except OSError as e:
        raise X11Error(f"Couldn't read the screen of display {name}: {e}") from None


class Display:
    """A connection to an X display. Use it in a with block."""

    def __init__(self, name):
        self.x11, self.xtst = libs()
        self.name = name
        self.dpy = self.x11.XOpenDisplay(name.encode())
        if not self.dpy:
            raise X11Error(f"Can't open X display {name}: it has stopped.")
        self.root = self.x11.XDefaultRootWindow(self.dpy)
        self._keymap = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.x11.XCloseDisplay(self.dpy)

    def size(self):
        return self.x11.XDisplayWidth(self.dpy, 0), self.x11.XDisplayHeight(self.dpy, 0)

    # --- Windows ---------------------------------------------------------------

    def children(self, window):
        root, parent, kids, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if not self.x11.XQueryTree(self.dpy, window, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(kids),
                                   ctypes.byref(count)):
            return [], 0
        out = [kids[i] for i in range(count.value)]
        if kids:
            self.x11.XFree(kids)
        return out, parent.value

    def windows(self):
        """The top-level windows shown on the screen, bottom to top: [{"id", "x", "y", "width", "height", "title",
        "popup"}]. A popup (override-redirect: a menu, a tooltip) places itself and never takes the focus."""
        found = []
        attrs = XWindowAttributes()
        for window in self.children(self.root)[0]:
            if not self.x11.XGetWindowAttributes(self.dpy, window, ctypes.byref(attrs)):
                continue
            if attrs.map_state != IS_VIEWABLE or getattr(attrs, "class") != INPUT_OUTPUT:
                continue
            found.append({"id": window, "x": attrs.x, "y": attrs.y, "width": attrs.width, "height": attrs.height,
                          "title": self.title(window), "popup": bool(attrs.override_redirect)})
        return found

    def title(self, window):
        """The window's title: _NET_WM_NAME (UTF-8), else WM_NAME."""
        prop = ctypes.c_void_p()
        kind, fmt, count, after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        atom = self.x11.XInternAtom(self.dpy, b"_NET_WM_NAME", 0)
        utf8 = self.x11.XInternAtom(self.dpy, b"UTF8_STRING", 0)
        if (self.x11.XGetWindowProperty(self.dpy, window, atom, 0, 1024, 0, utf8, ctypes.byref(kind), ctypes.byref(fmt),
                                        ctypes.byref(count), ctypes.byref(after), ctypes.byref(prop)) == 0
                and prop.value):
            text = ctypes.string_at(prop.value, count.value).decode(errors="replace")
            self.x11.XFree(prop)
            if text:
                return text
        name = ctypes.c_char_p()
        if self.x11.XFetchName(self.dpy, window, ctypes.byref(name)) and name.value is not None:
            text = name.value.decode("latin-1")
            self.x11.XFree(ctypes.cast(name, ctypes.c_void_p))
            return text
        return ""

    def window_at(self, x, y):
        """The topmost top-level window shown at screen pixel x, y, or None."""
        for w in reversed(self.windows()):
            if w["x"] <= x < w["x"] + w["width"] and w["y"] <= y < w["y"] + w["height"]:
                return w
        return None

    def top_of(self, window):
        """The top-level window that window is in (itself, or an ancestor), or 0."""
        while window and window != self.root:
            parent = self.children(window)[1]
            if parent == self.root:
                return window
            window = parent
        return 0

    def focus(self):
        window, revert = ctypes.c_ulong(), ctypes.c_int()
        self.x11.XGetInputFocus(self.dpy, ctypes.byref(window), ctypes.byref(revert))
        return window.value

    def give_focus(self, window):
        """Give a top-level window the keyboard's focus if it hasn't it, and let the program take that in."""
        if window is None or window["popup"]:
            return
        focus = self.focus()
        if focus not in (0, POINTER_ROOT) and self.top_of(focus) == window["id"]:
            return
        self.x11.XSetInputFocus(self.dpy, window["id"], REVERT_TO_PARENT, CURRENT_TIME)
        self.x11.XSync(self.dpy, 0)
        time.sleep(SETTLE_S)

    def key_window(self):
        """The window keys go to: the one with the focus, else the one under the pointer, else the topmost."""
        shown = [w for w in self.windows() if not w["popup"]]
        top = self.top_of(self.focus()) if self.focus() not in (0, POINTER_ROOT) else 0
        for w in shown:
            if w["id"] == top:
                return w
        x, y = self.pointer()
        under = self.window_at(x, y)
        if under and not under["popup"]:
            return under
        return shown[-1] if shown else None

    def pointer(self):
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy, mask = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
        self.x11.XQueryPointer(self.dpy, self.root, ctypes.byref(root), ctypes.byref(child), ctypes.byref(rx),
                               ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
        return rx.value, ry.value

    # --- Input -----------------------------------------------------------------

    def check_xtest(self):
        dummy = [ctypes.c_int() for _ in range(4)]
        if not self.xtst.XTestQueryExtension(self.dpy, *[ctypes.byref(d) for d in dummy]):
            raise X11Error(f"Display {self.name} has no XTest extension, so gdh can't send it input.")

    def move(self, x, y):
        self.xtst.XTestFakeMotionEvent(self.dpy, 0, int(x), int(y), CURRENT_TIME)
        self.x11.XSync(self.dpy, 0)
        time.sleep(HOLD_S)

    def button(self, button, pressed):
        self.xtst.XTestFakeButtonEvent(self.dpy, button, 1 if pressed else 0, CURRENT_TIME)
        self.x11.XSync(self.dpy, 0)

    def click(self, x, y, button=1, count=1):
        """Move to x, y, give the window there the focus, and press and let go of a button `count` times."""
        self.move(x, y)
        self.give_focus(self.window_at(x, y))
        for _ in range(count):
            self.button(button, True)
            time.sleep(HOLD_S)
            self.button(button, False)
            time.sleep(HOLD_S)

    def wheel(self, direction, steps=1):
        """Turn the wheel at the pointer, as X sends it: a press and release of button 4 (up) to 7 (right) a step."""
        self.give_focus(self.window_at(*self.pointer()))
        for _ in range(steps):
            self.button(WHEEL[direction], True)
            self.button(WHEEL[direction], False)
            time.sleep(BETWEEN_KEYS_S)

    def keymap(self):
        """{keysym: (keycode, shifted)} for the keyboard's first group, and the keycodes with no keysym at all."""
        if self._keymap is None:
            low, high = ctypes.c_int(), ctypes.c_int()
            self.x11.XDisplayKeycodes(self.dpy, ctypes.byref(low), ctypes.byref(high))
            per = ctypes.c_int()
            count = high.value - low.value + 1
            table = self.x11.XGetKeyboardMapping(self.dpy, low.value, count, ctypes.byref(per))
            found, spare = {}, []
            for i in range(count):
                syms = [table[i * per.value + j] for j in range(per.value)]
                if not any(syms):
                    spare.append(low.value + i)
                for level, sym in enumerate(syms[:2]):
                    if sym and sym not in found:
                        found[sym] = (low.value + i, level == 1)
            self.x11.XFree(ctypes.cast(table, ctypes.c_void_p))
            self._keymap = found, spare, per.value
        return self._keymap

    def key_event(self, keysym, pressed):
        """Press or release the key for a keysym, with Shift for its second level."""
        found, _, _ = self.keymap()
        code, shifted = found[keysym]
        if shifted and pressed:
            self.xtst.XTestFakeKeyEvent(self.dpy, found[0xffe1][0], 1, CURRENT_TIME)
        self.xtst.XTestFakeKeyEvent(self.dpy, code, 1 if pressed else 0, CURRENT_TIME)
        if shifted and not pressed:
            self.xtst.XTestFakeKeyEvent(self.dpy, found[0xffe1][0], 0, CURRENT_TIME)
        self.x11.XSync(self.dpy, 0)

    def keys(self, keysyms, hold=HOLD_S):
        """Press keysyms in order (a chord: ctrl+s), hold them, and release them in reverse. A keysym the keyboard
        hasn't is put on a spare keycode for the time it's pressed, as xdotool does."""
        self.give_focus(self.key_window())
        borrowed = [self.borrow(k) for k in keysyms if k not in self.keymap()[0]]
        try:
            for k in keysyms:
                self.key_event(k, True)
            time.sleep(hold)
            for k in reversed(keysyms):
                self.key_event(k, False)
        finally:
            for code in borrowed:
                self.give_back(code)
        time.sleep(BETWEEN_KEYS_S)

    def borrow(self, keysym):
        found, spare, per = self.keymap()
        if not spare:
            raise X11Error(f"The keyboard has no spare keycode to type {keysym:#x} with.")
        code = spare.pop()
        syms = (ctypes.c_ulong * per)(*([keysym] * per))
        self.x11.XChangeKeyboardMapping(self.dpy, code, per, syms, 1)
        self.x11.XSync(self.dpy, 0)
        time.sleep(SETTLE_S)  # programs refresh their keymap on the MappingNotify
        found[keysym] = (code, False)
        return code

    def give_back(self, code):
        found, spare, per = self.keymap()
        syms = (ctypes.c_ulong * per)(*([NO_SYMBOL] * per))
        self.x11.XChangeKeyboardMapping(self.dpy, code, per, syms, 1)
        self.x11.XSync(self.dpy, 0)
        for sym, (c, _) in list(found.items()):
            if c == code:
                del found[sym]
        spare.append(code)


def keysym_of_char(ch):
    if ch in CHAR_KEYSYMS:
        return CHAR_KEYSYMS[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff:
        return cp
    return 0x01000000 | cp


def parse_key(spec):
    """A key, or a chord joined with +, as keysyms: Return, F5, a, ctrl+s, shift+Tab. Godot's key names (Enter,
    Escape, Space, PageUp) work too."""
    x11, _ = libs()
    syms = []
    parts = spec.split("+") if spec != "+" else ["+"]
    for part in parts:
        if not part:
            raise X11Error(f"--key {spec!r} has an empty key in it (+ joins keys: ctrl+s; the + key is plus).")
        name = KEY_ALIASES.get(part.lower(), part)
        sym = x11.XStringToKeysym(name.encode())
        if not sym and len(part) == 1:
            sym = keysym_of_char(part)
        if not sym:
            raise X11Error(f"Unknown key {part!r} in --key {spec!r}: give an X keysym name (Return, Escape, F5, a, "
                           f"Left, BackSpace) or a chord (ctrl+s).")
        syms.append(sym)
    return syms
