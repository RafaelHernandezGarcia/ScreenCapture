"""
Audio Helper - Pro-quality audio capture for screen recording.

System audio: Native Objective-C helper using ScreenCaptureKit (macOS 13+)
              Writes raw float32 stereo PCM to a temp file.
              PyObjC ScreenCaptureKit audio bindings are broken on macOS 15,
              so we use a compiled native helper binary instead.
Microphone:   sounddevice (PortAudio)

Audio processing chain (OBS / ScreenFlow / Loom best practices):
  Mic -> Noise gate -> Soft compression -> Mix with system audio
      -> Limiter -> Peak normalize -> Stereo AAC
"""
import sys
import os
import time
import signal as _signal
import subprocess
import select
import threading
import numpy as np

if sys.platform != "darwin":
    raise ImportError("audio_helper is macOS only")

SAMPLE_RATE = 48000  # industry standard for video (OBS default)

# Path to the compiled native helper (sits next to this Python file)
_HELPER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sc_audio_helper"
)


def _get_recordings_dir():
    """Writable dir for temp files (avoids /var/folders/ issues on macOS)."""
    base = os.path.expanduser("~/Movies/ScreenCapture")
    os.makedirs(base, exist_ok=True)
    return base


class SystemAudioCapture:
    """Captures system audio via a native ScreenCaptureKit helper binary.

    The helper writes raw float32 stereo PCM at 48 kHz to a temp file.
    No PyObjC ScreenCaptureKit dependency — works reliably on macOS 15.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._process = None
        self._output_path = None

    def start(self):
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
                rlist, _, _ = select.select(
                    [self._process.stderr], [], [], 0.1
                )
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
        if self._process and self._process.poll() is None:
            self._process.send_signal(_signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
        self._process = None

    def set_paused(self, paused: bool):
        # The native helper records continuously; paused segments are
        # removed in post-processing by remove_paused_segments().
        pass

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
            print(f"[audio] System audio: {len(data)} float32 samples "
                  f"({file_size} bytes)")

            # Clean up temp file
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


class MicCapture:
    """Captures microphone audio via sounddevice (mono float32, 48 kHz).

    Uses blocking reads on a Python thread instead of a C callback.
    The callback approach caused SIGSEGV in ffi_closure_SYSV_inner on
    macOS because PortAudio's CoreAudio IO thread invokes a cffi C
    function pointer that can become invalid during GC or teardown.
    Blocking reads avoid the cffi closure entirely.
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

        # Open stream WITHOUT a callback — use blocking reads instead.
        # A roomy buffer + default ("high") latency is what keeps the audio
        # CLEAN: too small a buffer drops samples on any hiccup and you hear
        # the voice cut out. Any small lip-sync delay is handled by the
        # "Audio Sync" offset, not by starving the buffer.
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
                    self._chunks.append(
                        np.zeros((len(data), 1), dtype=np.float32)
                    )
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
        """Return captured audio as float32 numpy array shaped (N,).

        Called after stop() — no concurrent access.
        """
        if not self._chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(self._chunks).flatten()


# ---------------------------------------------------------------------------
# Audio processing — what OBS / ScreenFlow / Loom apply under the hood
# ---------------------------------------------------------------------------

def noise_gate(audio, threshold_db=-50, hold_ms=200, sample_rate=SAMPLE_RATE):
    """Noise gate: attenuate blocks below threshold.

    Conservative threshold and longer hold time to avoid chopping speech.
    Only silences true background noise, not quiet speech.
    """
    if len(audio) == 0:
        return audio

    threshold = 10 ** (threshold_db / 20.0)
    block_size = int(sample_rate * hold_ms / 1000)
    out = audio.copy()

    for start in range(0, len(out), block_size):
        block = out[start:start + block_size]
        rms = np.sqrt(np.mean(block ** 2))
        if rms < threshold:
            out[start:start + block_size] *= 0.05  # gentler attenuation

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


def _ducking_gain(mic_mono, sample_rate, duck_db=-14.0,
                  threshold_db=-38.0, attack_ms=12.0, release_ms=320.0):
    """Sidechain ducking gain for the SYSTEM audio (0..1 per sample).

    Drops the system audio when the mic has voice so narration cuts through,
    and smoothly returns to full when you stop talking — exactly what
    Loom / ScreenFlow / OBS (sidechain compressor) do. Computed per 5 ms
    block for speed, then upsampled to the audio rate.
    """
    n = len(mic_mono)
    if n == 0:
        return None
    block = max(1, int(sample_rate * 0.005))          # 5 ms
    thresh = 10 ** (threshold_db / 20.0)
    duck = 10 ** (duck_db / 20.0)                      # e.g. -14 dB -> 0.20
    nblocks = (n + block - 1) // block
    # per-block RMS of the mic (voice detector)
    pad = nblocks * block - n
    mic_p = np.concatenate([mic_mono, np.zeros(pad, dtype=np.float32)]) if pad else mic_mono
    rms = np.sqrt(np.mean(mic_p.reshape(nblocks, block) ** 2, axis=1) + 1e-12)
    # attack/release one-pole smoothing on the gain
    a_atk = np.exp(-1.0 / (sample_rate * (attack_ms / 1000.0) / block))
    a_rel = np.exp(-1.0 / (sample_rate * (release_ms / 1000.0) / block))
    gain = np.ones(nblocks, dtype=np.float32)
    g = 1.0
    for i in range(nblocks):
        target = duck if rms[i] > thresh else 1.0
        coeff = a_atk if target < g else a_rel       # fast to duck, slow to release
        g = coeff * g + (1.0 - coeff) * target
        gain[i] = g
    return np.repeat(gain, block)[:n]


def mix_and_master(system_stereo, mic_mono, sample_rate=SAMPLE_RATE):
    """Professional mix of system audio + mic into stereo output.

    Chain: mic gate+compress -> SIDECHAIN-DUCK the system under the voice ->
    mix (voice forward) -> limiter -> peak normalize. The ducking is what
    keeps your voice clearly on top of music / video audio.
    Returns: float32 numpy array shaped (N, 2)
    """
    # Process mic — gate and compress (no peak_normalize: one transient
    # would squash the whole voice track)
    if len(mic_mono) > 0:
        mic_mono = noise_gate(mic_mono, threshold_db=-50, hold_ms=200,
                              sample_rate=sample_rate)
        mic_mono = soft_compress(mic_mono, threshold_db=-24, ratio=2.5,
                                 makeup_db=12)

    sys_frames = len(system_stereo)
    mic_frames = len(mic_mono)
    out_frames = max(sys_frames, mic_frames)
    if out_frames == 0:
        return np.zeros((0, 2), dtype=np.float32)

    out = np.zeros((out_frames, 2), dtype=np.float32)

    # System audio, ducked under the voice so narration stays on top.
    if sys_frames > 0:
        sysmix = system_stereo[:sys_frames].astype(np.float32).copy()
        duck = _ducking_gain(mic_mono, sample_rate) if mic_frames > 0 else None
        if duck is not None:
            g = duck[:sys_frames]
            if len(g) < sys_frames:  # mic shorter -> full volume after it ends
                g = np.concatenate([g, np.ones(sys_frames - len(g), dtype=np.float32)])
            sysmix *= g[:, None]
        out[:sys_frames] += sysmix

    # Voice — forward in the mix (1.4x) on top of the ducked system audio.
    if mic_frames > 0:
        v = mic_mono[:mic_frames] * 1.4
        out[:mic_frames, 0] += v
        out[:mic_frames, 1] += v

    out = _limiter_stereo(out, threshold_db=-1.0)
    out = _normalize_stereo(out, target_db=-0.5)
    return out


def _limiter_stereo(stereo, threshold_db=-1.0):
    """Hard limiter on stereo signal — prevents clipping."""
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
