# Displays

Every Godot run gets an X display of its own, so its window never reaches the user's desktop. Godot always uses its X11 driver and Vulkan. Only the X server behind the display changes:

| `--display` | X server | How a frame reaches it |
|---|---|---|
| `gpu` | Xwayland, rootful, running inside weston's headless backend | DRI3: Vulkan hands the finished frame over as a GPU buffer |
| `xvfb` | Xvfb | No DRI3: Vulkan copies the frame through the CPU |
| `auto` (default) | `gpu` if it starts, else `xvfb`, with a note on stderr | |

`GDH_DISPLAY` sets the default for scripts that run gdh. `capture` prints the display it used, and puts it in `report.json`. `live start` and `live status` print each instance's display, as `display gpu :N`.

## The GPU display

gdh runs one command for each display:

```sh
weston --backend=headless --renderer=gl --width=W --height=H --socket=gdh-wayland --shell=kiosk \
       --no-config --idle-time=0 -- Xwayland -displayfd FD -geometry WxH -nolisten tcp -terminate
```

- **weston** composites on the GPU with its GL renderer, into a virtual output of the window's size. Nothing is shown anywhere. Its kiosk shell shows Xwayland's one window across the whole output. `--no-config` means it never reads the user's `weston.ini`. `--idle-time=0` means it never blanks the output.
- **Xwayland** runs rootful: one X screen, the size of the window, with no window manager, as Xvfb gives. Godot's window sits on it just as it did on Xvfb, so focus, the pointer, mouse capture, warping and the game resizing its own window behave the same (checked on both). `-displayfd` reports a free display number once the server is ready, so concurrent runs never collide. Xwayland never makes its screen smaller than 320x200, so for a smaller window the screen is rounded up to that. The window, and every screenshot, keeps the size asked for.
- **Its own runtime directory.** weston's socket and lock live in `$XDG_RUNTIME_DIR/gdh/displays/<random>/`, and weston and Xwayland are given only that directory, so they never see the user's Wayland session. Godot gets a `WAYLAND_DISPLAY` that doesn't exist, so it can't fall back to the user's Wayland session either.
- **Lifetimes.** Xwayland runs with `-terminate`, so it exits when Godot disconnects, and weston exits when Xwayland does. A display ends with its game even if gdh itself was killed. gdh also stops both process groups (weston starts Xwayland in a session of its own), and removes the runtime directory, on `live stop`, when a session is found dead, from the watchdog when a game ends by itself, and after each capture. Each new GPU display also sweeps away directories no running weston holds (it checks the lock on weston's socket), left by runs that were killed.
- **Nothing of the user's session.** weston and Xwayland make no D-Bus connections (checked with `busctl --user`).
- **Its log** is `display.log`, next to `godot.log` in the output directory: weston's log, and Xwayland's.

### When it can't start

With `--display auto`, gdh falls back to Xvfb, prints why on stderr, and doesn't try again for the rest of that command (several scenes, or several instances). With `--display gpu`, it stops with the same reason instead. The reasons:

- weston or Xwayland isn't installed (Arch: `weston xorg-xwayland`; Debian/Ubuntu: `weston xwayland`)
- weston didn't start, or didn't start Xwayland, within 15 s (no GPU it can use, no EGL)
- weston found no GPU to composite on: its log names no rendering device, as when only a software renderer such as llvmpipe is there
- Xwayland couldn't use the GPU (its glamor fell back to software)

### Which GPU

weston composites on the GPU its EGL picks by default, and its log names it (`Using rendering device: /dev/dri/renderD129`). Xwayland uses the same one. Godot renders on the GPU Vulkan picks, or the one `GDH_GPU_INDEX` names. On a machine with two GPUs, check that they match. If Godot renders on another GPU, each frame crosses between the two (as on a PRIME laptop): still GPU to GPU, but slower.

### V-Sync and frame pacing

gdh starts Godot with `--disable-vsync`. A display has a refresh rate (weston's is 60 Hz), and with V-Sync on, Present on Xwayland held Godot to about 53 frames a second however fast weston refreshed. That was true at 60, 240 and 1000 Hz alike. gdh paces frames itself: 20 a second while a live game is held, the tick rate while it runs, and as fast as the GPU draws while it steps.

A game can still turn V-Sync on itself (`DisplayServer.window_set_vsync_mode`). On the GPU display its steps then run at about 60 frames a second, and the next `step` says so in a note.

## Measurements

On the machine this was built on: an RTX 5080 (driver 615.71.09, open kernel modules), a Ryzen 9 9900X whose integrated Radeon is the second GPU, Arch Linux, Godot 4.7.2, weston 15.0.1, Xwayland 24.1.13. The KDE desktop and its apps were running and kept the GPU 15-30% busy, at 2.2-2.7 GHz, with nothing else running.

**Uncapped frames at 3840x2160**, plain Godot (no gdh) on the testbed's `blind/scene_03.tscn`, the same command on each display. Each figure is a 3000-frame run less a 300-frame run (Xvfb: 600 less 100), so start-up and shader compiling drop out:

| Display | ms a frame | Frames a second | GPU busy | Graphics clock |
|---|---|---|---|---|
| weston + rootful Xwayland, Godot on X11 (`gpu`) | 1.05 | 950 | 99% | 2872 MHz |
| weston + rootless Xwayland, Godot on X11 | 1.17 | 860 | 99% | 2872 MHz |
| weston, Godot on its Wayland driver | 0.93 | 1070 | 91% | 2872 MHz |
| Xvfb (`xvfb`) | 102.5 | 9.8 | 14% | 1845 MHz |

**gdh live stepping at 3840x2160**, the same scene, one `step` over one `live pipe`: 3000 frames took 3.24 s on the GPU display (1.08 ms a frame; GPU 99% busy, 2857 MHz, 274 W). 300 frames took 30.6 s on Xvfb (102 ms a frame; GPU 15% busy, 1860 MHz, 53 W). Godot's own GPU time for a frame was about the same on both (0.87 ms and 0.78 ms). The frame costs the same to render. Only getting it to the display differs.

**Screenshots are the same on both displays.** Checked pixel for pixel on the testbed's 3D scene in all six views, both captured and at the same stepped frame in a live game (`tests/test_display.py`).

### Why rootful Xwayland, with Godot on X11

Godot's Wayland driver was about 0.1 ms a frame faster at 3840x2160. Rootful Xwayland most likely spends it copying the window into its screen on the GPU: about 3% of a 4 ms frame. But the Wayland driver isn't the X11 driver gdh was built on, and a game behaves differently on it:

- the window isn't focused, since a headless weston has no seat, and capturing the mouse raises engine errors (`Parameter "ss" is null`)
- Godot stops drawing when the compositor stops asking for frames
- a window can't place itself

On rootful Xwayland, Godot runs exactly as it did on Xvfb, and every test passes unchanged on both displays. Rootless Xwayland puts weston's window manager in charge of Godot's window, which Xvfb never had, and it measured slower here.

### What else was tried

- **gamescope 3.16 (headless backend)** crashed both ways it was tried with this driver. With its Vulkan layer it segfaulted as Godot made its surface. Without the layer (`DISABLE_GAMESCOPE_WSI=1`), Godot ran, and gamescope segfaulted as it shut down. Both crashes were in gamescope's shader thread (`gamescope-shdr`). Each one leaves a core dump, which a desktop's crash reporter may show the user, so gdh doesn't use it.
- **KDE's `kwin_wayland --virtual`** wasn't used. Started as it is, it would share the user's session bus (where their own KWin owns `org.kde.KWin`) and their KDE config files. It's a whole desktop compositor, and weston already does the job.
- **Xvfb with a faster refresh, or Xorg with a GPU driver and a virtual screen:** Xvfb has no DRI3 at all. A second Xorg would need the GPU's display hardware, which the user's own session holds.
