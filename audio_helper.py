"""
Audio Helper - Pro-quality audio capture for screen recording.

System audio:
    macOS   Native Objective-C helper using ScreenCaptureKit (macOS 13+).
            Writes raw float32 stereo PCM to a temp file. PyObjC's
            ScreenCaptureKit audio bindings are broken on macOS 15, so a
            compiled helper binary is used instead.
    Windows WASAPI loopback of the default output device through the
            `soundcard` package (what you hear = what gets recorded).
            WASAPI loopback only delivers data while SOMETHING is playing,
            so a silent keep-alive output stream runs alongside it and any
            remaining gap is padded with zeros from the wall clock.
Microphone: sounddevice (PortAudio) on every platform.

Audio processing chain (Loom-style, deliberately simple - see CLAUDE.md):
  Mic -> gentle compression -> mix with system audio at a fixed level
      -> limiter -> one static normalize -> stereo AAC
"""
import sys
import os
import time
import threading
import numpy as np

from platform_utils import IS_MACOS, IS_WINDOWS, recordings_dir

SAMPLE_RATE = 48000  # industry standard for video (OBS default)

# Path to the compiled native helper (sits next to this Python file)
_HELPER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sc_audio_helper"
)


def _get_recordings_dir():
    """Writable dir for temp files (avoids /var/folders/ issues on macOS)."""
    return recordings_dir()


def system_audio_available():
    """Can this machine record what it plays? (Cheap check, no devices opened.)"""
    if IS_MACOS:
        return os.path.isfile(_HELPER_PATH)
    if IS_WINDOWS:
        try:
            import soundcard  # noqa: F401
            return True
        except Exception:
            return False
    return False


class SystemAudioCapture:
    """Captures system audio (what the computer plays) as float32 stereo.

    Dispatches to the macOS native helper or the Windows WASAPI loopback.
    Raises RuntimeError from start() when the platform cannot do it, so the
    recorder can carry on with mic-only audio.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._impl = None
        if IS_MACOS:
            self._impl = _MacSystemAudio(sample_rate)
        elif IS_WINDOWS:
            self._impl = _WindowsLoopbackAudio(sample_rate)

    def start(self):
        if self._impl is None:
            raise RuntimeError("System audio capture is not supported on this platform")
        self._impl.start()

    def stop(self):
        if self._impl is not None:
            self._impl.stop()

    def set_paused(self, paused: bool):
        # Both backends record continuously; paused segments are removed in
        # post-processing by remove_paused_segments().
        if self._impl is not None and hasattr(self._impl, "set_paused"):
            self._impl.set_paused(paused)

    def get_audio_stereo(self):
        if self._impl is None:
            return np.zeros((0, 2), dtype=np.float32)
        return self._impl.get_audio_stereo()


class _MacSystemAudio:
    """macOS: ScreenCaptureKit via the compiled sc_audio_helper binary."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self._process = None
        self._output_path = None

    def start(self):
        import subprocess
        import select
        self._output_path = os.path.join(
            _get_recordings_dir(), f"_temp_sysaudio_{os.getpid()}_{int(time.time())}.raw"
        )

        if not os.path.isfile(_HELPER_PATH):
            raise RuntimeError(
                f"sc_audio_helper not found at {_HELPER_PATH}. "
                "Compile with: clang -O2 -fobjc-arc -framework Foundation "
                "-framework ScreenCaptureKit -framework CoreMedia "
                "sc_audio_helper.m -o sc_audio_helper"
            )

        self._process = subprocess.Popen(
            [_HELPER_PATH, self._output_path, str(self.sample_rate)],
            stderr=subprocess.PIPE,
        )

        # Wait for READY signal (up to 5 seconds)
        deadline = time.time() + 5
        ready = False
        while time.time() < deadline:
            if self._process.poll() is not None:
                err = ""
                if self._process.stderr:
                    err = self._process.stderr.read().decode()
                raise RuntimeError(f"sc_audio_helper exited early: {err}")
            if self._process.stderr:
                rlist, _, _ = select.select([self._process.stderr], [], [], 0.1)
                if rlist:
                    line = self._process.stderr.readline().decode().strip()
                    if line == "READY":
                        ready = True
                        break

        if not ready:
            self.stop()
            raise RuntimeError("sc_audio_helper did not signal READY")

        print("[audio] System audio capture started (native helper)")

    def stop(self):
        import signal as _signal
        if self._process and self._process.poll() is None:
            self._process.send_signal(_signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
        self._process = None

    def get_audio_stereo(self):
        """Read captured audio from the raw PCM file as float32 (N, 2)."""
        if not self._output_path or not os.path.isfile(self._output_path):
            return np.zeros((0, 2), dtype=np.float32)
        try:
            file_size = os.path.getsize(self._output_path)
            if file_size == 0:
                print("[audio] System audio file is empty (0 bytes)")
                return np.zeros((0, 2), dtype=np.float32)
            data = np.fromfile(self._output_path, dtype=np.float32)
            print(f"[audio] System audio: {len(data)} float32 samples ({file_size} bytes)")
            try:
                os.remove(self._output_path)
            except OSError:
                pass
            if len(data) % 2 != 0:
                data = data[:len(data) - 1]
            return data.reshape(-1, 2)
        except Exception as e:
            print(f"[audio] System audio read error: {e}")
            return np.zeros((0, 2), dtype=np.float32)


class _WindowsLoopbackAudio:
    """Windows: WASAPI loopback of the default speaker via `soundcard`.

    Two things make this reliable:
    1. A silent keep-alive output stream (sounddevice, blocking writes on a
       thread) so the loopback endpoint always has an active session and
       keeps delivering buffers even when no app is playing.
    2. Wall-clock gap padding: chunks are time-stamped; if the loopback
       still stalls (some drivers do), silence is inserted so the captured
       stream stays the same length as real time and stays in sync with
       the video.
    """

    BLOCK = 2048

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self._chunks = []          # list of (t_arrival, ndarray (n, 2))
        self._running = False
        self._thread = None
        self._keepalive_thread = None
        self._start_time = None
        self._error = None
        self._ready = threading.Event()

    def start(self):
        try:
            import soundcard  # noqa: F401
        except Exception as e:
            raise RuntimeError(f"soundcard package not available ({e}); "
                               "pip install soundcard")
        self._running = True
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name="sc-sys-keepalive")
        self._keepalive_thread.start()
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="sc-sys-loopback")
        self._thread.start()
        # Give the loopback a moment to open; surface open errors early.
        if not self._ready.wait(timeout=4.0):
            self._running = False
            raise RuntimeError(self._error or "WASAPI loopback did not start")
        if self._error:
            self._running = False
            raise RuntimeError(self._error)
        print("[audio] System audio capture started (WASAPI loopback)")

    def _keepalive_loop(self):
        """Play digital silence so the loopback session never goes idle."""
        try:
            import sounddevice as sd
            zeros = np.zeros((self.BLOCK, 2), dtype=np.float32)
            with sd.OutputStream(samplerate=self.sample_rate, channels=2,
                                 dtype="float32", blocksize=self.BLOCK) as out:
                while self._running:
                    out.write(zeros)
        except Exception as e:
            # Not fatal: capture still works while other apps play sound.
            print(f"[audio] keep-alive stream unavailable: {e}")

    def _capture_loop(self):
        try:
            try:
                import pythoncom  # pywin32: COM must be initialised per thread
                pythoncom.CoInitialize()
            except Exception:
                pass
            import soundcard as sc
            spk = sc.default_speaker()
            mic = sc.get_microphone(spk.name, include_loopback=True)
            with mic.recorder(samplerate=self.sample_rate, channels=2,
                              blocksize=self.BLOCK) as rec:
                self._start_time = time.perf_counter()
                self._ready.set()
                while self._running:
                    data = rec.record(numframes=self.BLOCK)
                    if data is None or len(data) == 0:
                        continue
                    if data.ndim == 1:
                        data = np.stack([data, data], axis=1)
                    elif data.shape[1] == 1:
                        data = np.repeat(data, 2, axis=1)
                    self._chunks.append((time.perf_counter(), data.astype(np.float32, copy=True)))
        except Exception as e:
            self._error = f"loopback capture failed: {e}"
            print(f"[audio] {self._error}")
            self._ready.set()

    def stop(self):
        self._running = False
        self._stop_time = time.perf_counter()
        for t in (self._thread, self._keepalive_thread):
            if t is not None:
                t.join(timeout=2.5)
        self._thread = None
        self._keepalive_thread = None

    def set_paused(self, paused: bool):
        pass

    def get_audio_stereo(self):
        """Concatenate chunks, padding wall-clock gaps with silence."""
        if not self._chunks or self._start_time is None:
            return np.zeros((0, 2), dtype=np.float32)
        sr = self.sample_rate
        out = []
        written = 0  # samples emitted so far
        for t_arrival, chunk in self._chunks:
            # Where should this chunk END according to the wall clock?
            expected_end = int((t_arrival - self._start_time) * sr)
            expected_start = expected_end - len(chunk)
            gap = expected_start - written
            # Only pad clearly stalled stretches (> 1 block); jitter is ignored.
            if gap > self.BLOCK * 2:
                out.append(np.zeros((gap, 2), dtype=np.float32))
                written += gap
            out.append(chunk)
            written += len(chunk)
        data = np.concatenate(out) if out else np.zeros((0, 2), dtype=np.float32)
        print(f"[audio] System audio: {len(data)} frames ({len(self._chunks)} chunks)")
        return data


class MicCapture:
    """Captures microphone audio via sounddevice (mono float32, 48 kHz).

    Uses blocking reads on a Python thread instead of a C callback.
    The callback approach caused SIGSEGV in ffi_closure_SYSV_inner on
    macOS because PortAudio's CoreAudio IO thread invokes a cffi C
    function pointer that can become invalid during GC or teardown.
    Blocking reads avoid the cffi closure entirely (and are just as good
    on Windows).
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks = []
        self._stream = None
        self._running = False
        self._read_thread = None
        self._paused = False
        self._muted = False

    def set_paused(self, paused: bool):
        self._paused = paused

    def set_muted(self, muted: bool):
        self._muted = muted

    def start(self):
        import sounddevice as sd

        # Open stream WITHOUT a callback - use blocking reads instead.
        # A roomy buffer + default ("high") latency is what keeps the audio
        # CLEAN: too small a buffer drops samples on any hiccup and you hear
        # the voice cut out. Lip-sync is handled by start-time alignment in
        # the recorder, not by starving the buffer.
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype='float32',
            blocksize=2048,
        )
        self._stream.start()
        try:
            self.input_latency = float(self._stream.latency)
        except Exception:
            self.input_latency = 0.0
        self._running = True

        self._read_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="mic-read"
        )
        self._read_thread.start()

    def _read_loop(self):
        """Read audio blocks in a plain Python thread (no cffi callback)."""
        while self._running and self._stream:
            try:
                data, overflowed = self._stream.read(2048)
                if self._paused or self._muted:
                    self._chunks.append(np.zeros((len(data), 1), dtype=np.float32))
                else:
                    self._chunks.append(data.copy())
            except Exception:
                if self._running:
                    break

    def stop(self):
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=2)
            self._read_thread = None
        if self._stream:
            try:
                self._stream.abort()
                time.sleep(0.05)
                self._stream.close()
            except Exception as e:
                print(f"[mic] Error stopping stream: {e}")
            self._stream = None

    def get_audio_mono(self):
        """Return captured audio as float32 numpy array shaped (N,)."""
        if not self._chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(self._chunks).flatten()


# ---------------------------------------------------------------------------
# Audio processing - what OBS / ScreenFlow / Loom apply under the hood
# ---------------------------------------------------------------------------

def noise_gate(audio, threshold_db=-50, hold_ms=200, sample_rate=SAMPLE_RATE):
    """Noise gate: attenuate blocks below threshold (kept for reference; the
    default mix does NOT use it - it clipped quiet word endings)."""
    if len(audio) == 0:
        return audio
    threshold = 10 ** (threshold_db / 20.0)
    block_size = int(sample_rate * hold_ms / 1000)
    out = audio.copy()
    for start in range(0, len(out), block_size):
        block = out[start:start + block_size]
        rms = np.sqrt(np.mean(block ** 2))
        if rms < threshold:
            out[start:start + block_size] *= 0.05
    return out


def soft_compress(audio, threshold_db=-24, ratio=2.5, makeup_db=12):
    """Soft-knee compressor for voice: tames peaks, lifts quiet parts."""
    if len(audio) == 0:
        return audio
    threshold = 10 ** (threshold_db / 20.0)
    makeup = 10 ** (makeup_db / 20.0)
    out = audio.copy()
    abs_out = np.abs(out)
    mask = abs_out > threshold
    if np.any(mask):
        over_db = 20 * np.log10(abs_out[mask] / threshold + 1e-10)
        compressed_db = over_db / ratio
        gain = (threshold * 10 ** (compressed_db / 20.0)) / (abs_out[mask] + 1e-10)
        out[mask] *= gain
    out *= makeup
    return out


def peak_normalize(audio, target_db=-1.0):
    """Normalize audio to target peak level."""
    if len(audio) == 0:
        return audio
    peak = np.max(np.abs(audio))
    if peak < 1e-8:
        return audio
    target = 10 ** (target_db / 20.0)
    return audio * (target / peak)


def mix_and_master(system_stereo, mic_mono, sample_rate=SAMPLE_RATE):
    """Clean, Loom-style mix: voice forward over a steady-level background.

    Deliberately simple - what keeps it clean:
      - NO sidechain ducking (dynamic ducking pumps the volume up/down).
      - NO hard noise gate (it clips quiet word-endings -> "cuts").
      - Just: gentle mic compression for a consistent voice level, the system
        audio held at a fixed lower level (background), a peak limiter, and a
        single overall normalize (one static gain, so no level pumping).
    Returns: float32 numpy array shaped (N, 2)
    """
    SYSTEM_LEVEL = 0.45   # system audio sits steadily under the voice
    if len(mic_mono) > 0:
        mic_mono = soft_compress(mic_mono, threshold_db=-24, ratio=3.0, makeup_db=10)

    sys_frames = len(system_stereo)
    mic_frames = len(mic_mono)
    out_frames = max(sys_frames, mic_frames)
    if out_frames == 0:
        return np.zeros((0, 2), dtype=np.float32)

    out = np.zeros((out_frames, 2), dtype=np.float32)
    if sys_frames > 0:
        out[:sys_frames] += system_stereo[:sys_frames] * SYSTEM_LEVEL
    if mic_frames > 0:
        out[:mic_frames, 0] += mic_mono[:mic_frames]
        out[:mic_frames, 1] += mic_mono[:mic_frames]

    out = _limiter_stereo(out, threshold_db=-1.0)
    out = _normalize_stereo(out, target_db=-1.0)
    return out


def _limiter_stereo(stereo, threshold_db=-1.0):
    """Hard limiter on stereo signal - prevents clipping."""
    threshold = 10 ** (threshold_db / 20.0)
    return np.clip(stereo, -threshold, threshold)


def _normalize_stereo(stereo, target_db=-0.5):
    """Peak-normalize stereo audio."""
    peak = np.max(np.abs(stereo))
    if peak < 1e-8:
        return stereo
    target = 10 ** (target_db / 20.0)
    return stereo * (target / peak)


def remove_paused_segments(audio_stereo, pause_intervals, sample_rate):
    """Remove audio samples that correspond to paused time intervals.

    Args:
        audio_stereo: (N, 2) float32 array
        pause_intervals: list of (start_seconds, end_seconds) relative to
                         recording start
        sample_rate: int
    Returns:
        trimmed (M, 2) float32 array
    """
    if not pause_intervals or len(audio_stereo) == 0:
        return audio_stereo
    mask = np.ones(len(audio_stereo), dtype=bool)
    for start, end in pause_intervals:
        s = max(0, int(start * sample_rate))
        e = min(len(audio_stereo), int(end * sample_rate))
        if s < e:
            mask[s:e] = False
    return audio_stereo[mask]
