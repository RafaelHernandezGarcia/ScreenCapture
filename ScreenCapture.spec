# -*- mode: python ; coding: utf-8 -*-
import sys
import os

# Bundle the native sc_audio_helper binary so SystemAudioCapture can
# find it inside the frozen .app.
_extra_binaries = []
if sys.platform == 'darwin' and os.path.exists('sc_audio_helper'):
    _extra_binaries.append(('sc_audio_helper', '.'))

# Carbon hotkeys + AVFoundation webcam + system-audio helpers are
# imported inside function bodies, so PyInstaller's static analysis
# doesn't discover them. List them here.
_hidden = [
    # sc/ package (Carbon hotkey is dynamically imported by main.py)
    'sc', 'sc.hotkey', 'sc.capture', 'sc.overlay', 'sc.tray',
    'sc.clipboard', 'sc.config', 'sc.paths', 'sc.permissions', 'sc.app',
    # local modules imported inside function bodies
    'mac_tray', 'platform_utils',
    # heavy libs imported lazily inside functions (recorder/webcam)
    'cv2', 'av', 'mss', 'numpy', 'PIL',
    'sounddevice', 'imageio_ffmpeg',
    # pyobjc frameworks used via lazy imports
    'AVFoundation', 'objc', 'Foundation', 'AppKit', 'Cocoa',
    'ApplicationServices', 'Quartz', 'CoreMedia', 'CoreAudio', 'CoreText',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_extra_binaries,
    datas=[('assets', 'assets')],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon_file = ['assets/icon.icns'] if sys.platform == 'darwin' else ['assets\\icon.ico']

# onedir mode (exclude_binaries=True + COLLECT): the executable lives at a
# FIXED path inside the .app, so macOS TCC can pin a stable identity and the
# Screen Recording / Camera grants stick. onefile would unpack to a random
# /tmp dir each launch and break that.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenCapture',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScreenCapture',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='ScreenCapture.app',
        icon='assets/icon.icns',
        bundle_identifier='com.screencapture.app',
        info_plist={
            'LSUIElement': True,
            'NSCameraUsageDescription':
                'ScreenCapture uses the camera for the webcam picture-in-picture overlay during recordings.',
            'NSMicrophoneUsageDescription':
                'ScreenCapture records microphone audio as the voiceover track of your recording.',
            'NSHighResolutionCapable': True,
        },
    )
