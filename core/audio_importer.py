"""
core/audio_importer.py - Module d'importation universelle de fichiers audio pour NovaDAW.

Supporte tous les formats audio majeurs (WAV, MP3, FLAC, OGG, AIFF, AIF, CAF, AU, M4A, AAC, etc.)
en utilisant exclusivement des bibliothèques open-source sous licences hautement permissives
(BSD-3-Clause, Apache 2.0, MIT) :
- `soundfile` (libsndfile - BSD-3-Clause)
- `pedalboard.io` (Apache 2.0)
- `scipy.signal` (BSD-3-Clause)
"""
import os
from typing import Tuple, Optional, List
import numpy as np

try:
    import soundfile as sf
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False

try:
    import pedalboard.io
    HAS_PEDALBOARD = True
except ImportError:
    HAS_PEDALBOARD = False

try:
    from scipy.signal import resample_poly
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


SUPPORTED_EXTENSIONS = [
    ".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif",
    ".caf", ".au", ".raw", ".m4a", ".mp4", ".aac", ".wma"
]

QT_FILE_DIALOG_FILTER = (
    "Tous les fichiers audio supportés (*.wav *.mp3 *.flac *.ogg *.aiff *.aif *.m4a *.mp4 *.aac *.caf *.au *.wma);;"
    "Fichiers WAV (*.wav);;"
    "Fichiers MP3 (*.mp3);;"
    "Fichiers FLAC (*.flac);;"
    "Fichiers OGG (*.ogg);;"
    "Fichiers AIFF (*.aiff *.aif);;"
    "Tous les fichiers (*.*)"
)


def load_audio_file(file_path: str, target_sr: int = 44100) -> Tuple[np.ndarray, int, float]:
    """
    Charge n'importe quel fichier audio depuis le disque, convertit en float32 stéréo normalisé
    et rééchantillonne à `target_sr` si nécessaire.

    Retourne :
        (audio_data: np.ndarray de forme (N, 2), sample_rate: int, duration_seconds: float)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Le fichier audio spécifié n'existe pas : '{file_path}'")

    audio: Optional[np.ndarray] = None
    orig_sr: int = target_sr
    last_error: Optional[Exception] = None

    # 1. Tentative avec soundfile (très rapide pour WAV, FLAC, OGG, AIFF, MP3 natif)
    if HAS_SOUNDFILE:
        try:
            data, sr = sf.read(file_path, dtype="float32")
            audio = data
            orig_sr = sr
        except Exception as e:
            last_error = e

    # 2. Si échec ou format non géré, tentative avec Pedalboard AudioFile (Apache 2.0)
    if audio is None and HAS_PEDALBOARD:
        try:
            with pedalboard.io.AudioFile(file_path) as f:
                orig_sr = int(f.samplerate)
                # Lecture complète
                raw_audio = f.read(f.frames)
                # Pedalboard retourne shape (channels, frames)
                if raw_audio.ndim == 2:
                    audio = raw_audio.T.astype(np.float32)
                else:
                    audio = raw_audio.astype(np.float32)
        except Exception as e:
            last_error = e

    # 3. Fallback wave standard (WAV PCM pur) si les librairies C échouent
    if audio is None:
        try:
            import wave
            with wave.open(file_path, "rb") as wf:
                orig_sr = wf.getframerate()
                n_channels = wf.getnchannels()
                n_frames = wf.getnframes()
                raw_bytes = wf.readframes(n_frames)
                sampwidth = wf.getsampwidth()

                if sampwidth == 2:
                    dtype = np.int16
                    max_val = 32768.0
                elif sampwidth == 4:
                    dtype = np.int32
                    max_val = 2147483648.0
                elif sampwidth == 1:
                    dtype = np.uint8
                    max_val = 128.0
                else:
                    raise ValueError(f"Largeur d'échantillon non supportée: {sampwidth}")

                raw_arr = np.frombuffer(raw_bytes, dtype=dtype)
                if sampwidth == 1:
                    raw_arr = raw_arr.astype(np.float32) - 128.0
                else:
                    raw_arr = raw_arr.astype(np.float32)
                raw_arr /= max_val

                if n_channels > 1:
                    audio = raw_arr.reshape(-1, n_channels)
                else:
                    audio = raw_arr
        except Exception as e:
            last_error = e

    if audio is None:
        raise RuntimeError(f"Impossible de décoder le fichier audio '{file_path}': {last_error}")

    # 4. Formater en Stéréo (N, 2)
    if audio.ndim == 1:
        stereo_audio = np.column_stack((audio, audio)).astype(np.float32)
    elif audio.shape[1] == 1:
        mono = audio[:, 0]
        stereo_audio = np.column_stack((mono, mono)).astype(np.float32)
    else:
        stereo_audio = audio[:, :2].astype(np.float32)

    # 5. Rééchantillonnage vers target_sr si nécessaire
    if orig_sr != target_sr and len(stereo_audio) > 0:
        if HAS_SCIPY:
            from math import gcd
            common_div = gcd(target_sr, orig_sr)
            up = target_sr // common_div
            down = orig_sr // common_div
            resampled_l = resample_poly(stereo_audio[:, 0], up, down).astype(np.float32)
            resampled_r = resample_poly(stereo_audio[:, 1], up, down).astype(np.float32)
            stereo_audio = np.column_stack((resampled_l, resampled_r))
        else:
            # Rééchantillonnage linéaire simple
            orig_len = len(stereo_audio)
            new_len = int(orig_len * (target_sr / orig_sr))
            idx_orig = np.linspace(0, orig_len - 1, new_len)
            resampled_l = np.interp(idx_orig, np.arange(orig_len), stereo_audio[:, 0]).astype(np.float32)
            resampled_r = np.interp(idx_orig, np.arange(orig_len), stereo_audio[:, 1]).astype(np.float32)
            stereo_audio = np.column_stack((resampled_l, resampled_r))

    duration_sec = float(len(stereo_audio) / target_sr) if target_sr > 0 else 0.0
    return stereo_audio, target_sr, duration_sec
