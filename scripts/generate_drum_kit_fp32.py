"""
scripts/generate_drum_kit_fp32.py - Génération d'un kit de batterie complet en FP32 (32-bit Float)
Moteur : Stable Audio 3 Medium (1.4B) + SAME-Large sur GPU CUDA en pleine précision FP32 (model_half=False).
"""
import os
import sys
import time
import gc
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as AF
import torch.nn.functional as F

# Racine du dépôt Stable Audio 3
STABLE_AUDIO_DIR = Path(r"C:\Users\Utilisateur\Documents\Dev\stable-audio-3")
if str(STABLE_AUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(STABLE_AUDIO_DIR))

from stable_audio_3 import StableAudioModel

# Destination des samples dans NovaDAW
NOVA_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR_PLUGIN = NOVA_DIR / "plugins" / "drum_machine" / "samples" / "default_kit"
OUTPUT_DIR_ASSETS = NOVA_DIR / "assets" / "drum_kits" / "default_kit"

DRUM_ITEMS = [
    {
        "name": "kick",
        "prompt": "TrackType: SFX, centered stereo, punchy acoustic studio bass drum kick, deep 50Hz sub thump, clean click transient attack, tight punch, isolated single hit",
        "duration": 1.2,
        "seed": 90101,
        "max_length": 1.0,
        "hp_cutoff": 25.0,
        "lp_cutoff": 12000.0,
        "peak_db": -1.0,
    },
    {
        "name": "snare",
        "prompt": "TrackType: SFX, centered stereo, acoustic studio snare drum hit, crisp wooden rim crack, bright snare wire buzz and sizzle, punchy acoustic snare, isolated hit",
        "duration": 1.5,
        "seed": 90202,
        "max_length": 1.2,
        "hp_cutoff": 60.0,
        "lp_cutoff": 16000.0,
        "peak_db": -1.0,
    },
    {
        "name": "hihat_closed",
        "prompt": "TrackType: SFX, centered stereo, crisp closed hi-hat cymbal tap, clean wooden drumstick hit, bright bronze sizzle, fast transient decay, isolated single hit",
        "duration": 0.8,
        "seed": 90303,
        "max_length": 0.5,
        "hp_cutoff": 300.0,
        "lp_cutoff": 18000.0,
        "peak_db": -2.0,
    },
    {
        "name": "hihat_open",
        "prompt": "TrackType: SFX, centered stereo, open hi-hat cymbal strike, bright acoustic bronze wash, sizzle decay, clean studio recording, isolated hit",
        "duration": 1.8,
        "seed": 90404,
        "max_length": 1.5,
        "hp_cutoff": 250.0,
        "lp_cutoff": 18000.0,
        "peak_db": -2.0,
    },
    {
        "name": "tom_low",
        "prompt": "TrackType: SFX, centered stereo, deep acoustic floor tom drum strike, resonant wooden shell, round low-end boom decay, isolated single hit",
        "duration": 1.6,
        "seed": 90505,
        "max_length": 1.4,
        "hp_cutoff": 40.0,
        "lp_cutoff": 14000.0,
        "peak_db": -1.5,
    },
    {
        "name": "tom_mid",
        "prompt": "TrackType: SFX, centered stereo, punchy mid rack tom drum strike, resonant acoustic tone, clear pitch decay, warm studio drum, isolated hit",
        "duration": 1.4,
        "seed": 90606,
        "max_length": 1.2,
        "hp_cutoff": 60.0,
        "lp_cutoff": 15000.0,
        "peak_db": -1.5,
    },
    {
        "name": "tom_high",
        "prompt": "TrackType: SFX, centered stereo, high tuned rack tom drum hit, fast sharp attack, bright acoustic resonance, isolated single hit",
        "duration": 1.3,
        "seed": 90707,
        "max_length": 1.0,
        "hp_cutoff": 90.0,
        "lp_cutoff": 16000.0,
        "peak_db": -1.5,
    },
    {
        "name": "crash",
        "prompt": "TrackType: SFX, centered stereo, explosive acoustic crash cymbal crash, shimmering bronze sizzle, brilliant high frequencies, expansive natural decay, isolated hit",
        "duration": 3.0,
        "seed": 90808,
        "max_length": 2.8,
        "hp_cutoff": 200.0,
        "lp_cutoff": 19000.0,
        "peak_db": -1.0,
    },
    {
        "name": "ride",
        "prompt": "TrackType: SFX, centered stereo, acoustic ride cymbal stick ping on bronze bell, sweet bright resonance, clean sustain wash, isolated hit",
        "duration": 2.5,
        "seed": 90909,
        "max_length": 2.4,
        "hp_cutoff": 200.0,
        "lp_cutoff": 18000.0,
        "peak_db": -1.5,
    },
]


def postprocess_drum_sample(raw_tensor: torch.Tensor, sr: int, item: dict) -> np.ndarray:
    """
    Conditionne le sample de batterie :
    1. Alignement immédiat sur le premier transitoire pour zéro latence.
    2. Filtrage passe-haut et passe-bas.
    3. Fondu de sortie (fade out) naturel pour éviter tout clic.
    4. Centrage stéréo.
    5. Normalisation crête en FP32.
    """
    # Déplacer immédiatement sur CPU en float32 pour le post-traitement
    audio = raw_tensor.detach().cpu().float()
    if audio.dim() == 1:
        audio = audio.unsqueeze(0).repeat(2, 1)
    elif audio.dim() == 3:
        audio = audio.squeeze(0)

    # Filtrage
    hp = item.get("hp_cutoff", 30.0)
    lp = item.get("lp_cutoff", 16000.0)
    if hp > 0:
        audio = AF.highpass_biquad(audio, sr, hp)
    if lp < sr * 0.49:
        audio = AF.lowpass_biquad(audio, sr, lp)

    # Détection de l'attaque (transitoire)
    mono_env = audio.abs().mean(dim=0)
    # Chercher le pic maximal dans les premières 400ms
    search_window = min(len(mono_env), int(0.40 * sr))
    peak_idx = int(torch.argmax(mono_env[:search_window]).item())
    
    # Trouver le franchissement de seuil avant le pic (seuil à 5% de la crête)
    peak_val = mono_env[peak_idx].item()
    thresh = peak_val * 0.05
    start_idx = 0
    for idx in range(peak_idx, -1, -1):
        if mono_env[idx] < thresh:
            start_idx = max(0, idx - int(0.002 * sr))  # 2ms de garde
            break

    # Découper à partir de start_idx
    max_samples = int(item.get("max_length", 2.0) * sr)
    audio = audio[:, start_idx:]
    if audio.shape[-1] > max_samples:
        audio = audio[:, :max_samples]

    # Fondu d'attaque très rapide (1ms) pour éviter clic au départ
    attack_samples = min(int(0.001 * sr), audio.shape[-1] // 8)
    if attack_samples > 0:
        audio[:, :attack_samples] *= torch.linspace(0.0, 1.0, attack_samples)

    # Fondu de fin doux (fade out)
    fadeout_dur = min(0.15, audio.shape[-1] / sr * 0.25)
    fadeout_samples = int(fadeout_dur * sr)
    if fadeout_samples > 0 and audio.shape[-1] > fadeout_samples:
        audio[:, -fadeout_samples:] *= torch.linspace(1.0, 0.0, fadeout_samples)

    # Centrage stéréo doux
    L = audio[0]
    R = audio[1]
    M = 0.5 * (L + R)
    S = 0.5 * (L - R)
    # 40% de largeur stéréo pour cohérence studio
    width = 0.40
    L_c = M + width * S
    R_c = M - width * S
    audio = torch.stack([L_c, R_c])

    # Normalisation crête en FP32
    peak = float(audio.abs().max())
    if peak > 1e-6:
        target_lin = 10.0 ** (item.get("peak_db", -1.0) / 20.0)
        audio = audio * (target_lin / peak)

    # Conversion en numpy float32 (samples, 2)
    np_audio = audio.cpu().numpy().T.astype(np.float32)
    return np_audio


def main():
    OUTPUT_DIR_PLUGIN.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR_ASSETS.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("NOVA DRUMS - Génération du Kit de Batterie FP32")
    print("Moteur : Stable Audio 3 Medium (1.4B) + SAME-Large (FP32)")
    print(f"Destination : {OUTPUT_DIR_PLUGIN}")
    print("=" * 60)

    t_start = time.time()

    print("[Stable Audio 3] Initialisation du modèle en FP32 sur GPU CUDA...")
    model = StableAudioModel.from_pretrained("medium", device="cuda", model_half=False)
    print("[Stable Audio 3] Modèle prêt en FP32.")

    sr = 44100
    generated_files = []

    for idx, item in enumerate(DRUM_ITEMS, 1):
        name = item["name"]
        prompt = item["prompt"]
        duration = item["duration"]
        seed = item["seed"]

        print(f"\n[{idx}/{len(DRUM_ITEMS)}] Génération de '{name}' (seed={seed}, dur={duration}s)...")
        t0 = time.time()

        with torch.no_grad():
            raw = model.generate(prompt=prompt, duration=duration, steps=16, seed=seed)
            if raw.dim() == 3:
                raw = raw.squeeze(0)

        processed = postprocess_drum_sample(raw, sr, item)

        # Enregistrement en WAV IEEE 32-bit Float
        out_plugin_path = OUTPUT_DIR_PLUGIN / f"{name}.wav"
        out_asset_path = OUTPUT_DIR_ASSETS / f"{name}.wav"

        sf.write(str(out_plugin_path), processed, sr, subtype='FLOAT')
        sf.write(str(out_asset_path), processed, sr, subtype='FLOAT')

        info = sf.info(str(out_plugin_path))
        elapsed = time.time() - t0
        dur_actual = len(processed) / sr
        peak = float(np.max(np.abs(processed)))
        rms = float(np.sqrt(np.mean(processed ** 2)))

        print(f" -> '{name}.wav' généré en {elapsed:.2f}s | Durée={dur_actual:.2f}s | Subtype={info.subtype} | Crête={peak:.2f} | RMS={rms:.4f}")
        generated_files.append(out_plugin_path)

        del raw, processed
        gc.collect()
        torch.cuda.empty_cache()

    total_time = time.time() - t_start
    print("\n" + "=" * 60)
    print(f"SUCCÈS : {len(generated_files)} samples de batterie générés en FP32 en {total_time:.2f}s !")
    print("=" * 60)


if __name__ == "__main__":
    main()
