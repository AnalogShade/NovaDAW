"""
scripts/generate_acoustic_kick_fp32.py - Génération de nouveaux samples de grosse caisse acoustique en FP32
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

# Racine du dépôt Stable Audio 3
STABLE_AUDIO_DIR = Path(r"C:\Users\Utilisateur\Documents\Dev\stable-audio-3")
if str(STABLE_AUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(STABLE_AUDIO_DIR))

from stable_audio_3 import StableAudioModel

# Destination des samples dans NovaDAW
NOVA_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR_PLUGIN = NOVA_DIR / "plugins" / "drum_machine" / "samples" / "default_kit"
OUTPUT_DIR_ASSETS = NOVA_DIR / "assets" / "drum_kits" / "default_kit"

KICK_VARIATIONS = [
    {
        "filename": "kick.wav",  # Devient le son par défaut
        "label": "Kick Acoustique Studio (Défaut)",
        "prompt": "TrackType: SFX, centered stereo, natural punchy acoustic bass drum kick, real studio wooden kick drum, beater click transient, tight punchy thump, dampened acoustic drum resonance, fast natural decay, dry studio recording, single drum hit",
        "duration": 0.8,
        "seed": 42101,
        "max_length": 0.55,
        "hp_cutoff": 35.0,
        "lp_cutoff": 12000.0,
        "peak_db": -1.0,
    },
    {
        "filename": "kick_acoustic_punch.wav",
        "label": "Kick Acoustique Punchy",
        "prompt": "TrackType: SFX, centered stereo, tight punchy acoustic kick drum hit, wooden beater strike, round acoustic thump, fast decay, isolated acoustic drum kit, professional studio, single hit",
        "duration": 0.7,
        "seed": 88301,
        "max_length": 0.50,
        "hp_cutoff": 40.0,
        "lp_cutoff": 14000.0,
        "peak_db": -1.0,
    },
    {
        "filename": "kick_acoustic_warm.wav",
        "label": "Kick Acoustique Vintage & Rond",
        "prompt": "TrackType: SFX, centered stereo, warm vintage acoustic bass drum, felt beater hit on resonant drum head, warm round low end, dampened decay, natural studio room, single hit",
        "duration": 0.8,
        "seed": 15402,
        "max_length": 0.60,
        "hp_cutoff": 35.0,
        "lp_cutoff": 10000.0,
        "peak_db": -1.2,
    },
]


def postprocess_kick_sample(raw_tensor: torch.Tensor, sr: int, item: dict) -> np.ndarray:
    """
    Conditionne le sample de grosse caisse acoustique :
    1. Alignement immédiat sur le premier transitoire pour zéro latence.
    2. Filtrage passe-haut (35-40Hz) pour éliminer les infra-basses synthétiques de type DnB / sub bass.
    3. Filtrage passe-bas doux.
    4. Fondu d'attaque immédiat (1ms) et fondu de fin naturel (fade out rapide).
    5. Centrage stéréo.
    6. Normalisation crête en FP32.
    """
    audio = raw_tensor.detach().cpu().float()
    if audio.dim() == 1:
        audio = audio.unsqueeze(0).repeat(2, 1)
    elif audio.dim() == 3:
        audio = audio.squeeze(0)

    # Filtrage passe-haut et passe-bas
    hp = item.get("hp_cutoff", 35.0)
    lp = item.get("lp_cutoff", 12000.0)
    if hp > 0:
        audio = AF.highpass_biquad(audio, sr, hp)
    if lp < sr * 0.49:
        audio = AF.lowpass_biquad(audio, sr, lp)

    # Détection de l'attaque
    mono_env = audio.abs().mean(dim=0)
    search_window = min(len(mono_env), int(0.30 * sr))
    peak_idx = int(torch.argmax(mono_env[:search_window]).item())

    # Franchissement de seuil
    peak_val = mono_env[peak_idx].item()
    thresh = peak_val * 0.05
    start_idx = 0
    for idx in range(peak_idx, -1, -1):
        if mono_env[idx] < thresh:
            start_idx = max(0, idx - int(0.002 * sr))
            break

    # Découper à partir du transitoire
    max_samples = int(item.get("max_length", 0.55) * sr)
    audio = audio[:, start_idx:]
    if audio.shape[-1] > max_samples:
        audio = audio[:, :max_samples]

    # Fondu d'attaque (1ms)
    attack_samples = min(int(0.001 * sr), audio.shape[-1] // 8)
    if attack_samples > 0:
        audio[:, :attack_samples] *= torch.linspace(0.0, 1.0, attack_samples)

    # Fondu de fin naturel (100ms) pour une extinction naturelle de grosse caisse acoustique
    fadeout_dur = min(0.12, audio.shape[-1] / sr * 0.3)
    fadeout_samples = int(fadeout_dur * sr)
    if fadeout_samples > 0 and audio.shape[-1] > fadeout_samples:
        audio[:, -fadeout_samples:] *= torch.linspace(1.0, 0.0, fadeout_samples)

    # Centrage stéréo (grosse caisse centrée au mixage)
    L = audio[0]
    R = audio[1]
    M = 0.5 * (L + R)
    S = 0.5 * (L - R)
    # Stéréo resserrée (25%) pour centrage solide au mixage
    width = 0.25
    L_c = M + width * S
    R_c = M - width * S
    audio = torch.stack([L_c, R_c])

    # Normalisation crête en FP32
    peak = float(audio.abs().max())
    if peak > 1e-6:
        target_lin = 10.0 ** (item.get("peak_db", -1.0) / 20.0)
        audio = audio * (target_lin / peak)

    return audio.cpu().numpy().T.astype(np.float32)


def main():
    OUTPUT_DIR_PLUGIN.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR_ASSETS.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("NOVA DRUMS - Génération de Grosse Caisse Acoustique FP32")
    print("Moteur : Stable Audio 3 Medium (1.4B) + SAME-Large (FP32)")
    print(f"Destination : {OUTPUT_DIR_PLUGIN}")
    print("=" * 60)

    t_start = time.time()
    print("[Stable Audio 3] Initialisation du modèle en FP32 sur GPU CUDA...")
    model = StableAudioModel.from_pretrained("medium", device="cuda", model_half=False)
    print("[Stable Audio 3] Modèle prêt en FP32.")

    sr = 44100
    for idx, item in enumerate(KICK_VARIATIONS, 1):
        filename = item["filename"]
        prompt = item["prompt"]
        duration = item["duration"]
        seed = item["seed"]

        print(f"\n[{idx}/{len(KICK_VARIATIONS)}] Génération de '{filename}' (seed={seed}, dur={duration}s)...")
        t0 = time.time()

        with torch.no_grad():
            raw = model.generate(prompt=prompt, duration=duration, steps=16, seed=seed)
            if raw.dim() == 3:
                raw = raw.squeeze(0)

        processed = postprocess_kick_sample(raw, sr, item)

        out_plugin_path = OUTPUT_DIR_PLUGIN / filename
        out_asset_path = OUTPUT_DIR_ASSETS / filename

        sf.write(str(out_plugin_path), processed, sr, subtype='FLOAT')
        sf.write(str(out_asset_path), processed, sr, subtype='FLOAT')

        info = sf.info(str(out_plugin_path))
        elapsed = time.time() - t0
        dur_actual = len(processed) / sr
        peak = float(np.max(np.abs(processed)))
        rms = float(np.sqrt(np.mean(processed ** 2)))

        print(f" -> '{filename}' généré en {elapsed:.2f}s | Durée={dur_actual:.2f}s | Subtype={info.subtype} | Crête={peak:.2f} | RMS={rms:.4f}")

        del raw, processed
        gc.collect()
        torch.cuda.empty_cache()

    total_time = time.time() - t_start
    print("\n" + "=" * 60)
    print(f"SUCCÈS : {len(KICK_VARIATIONS)} variations de kick générées en FP32 en {total_time:.2f}s !")
    print("=" * 60)


if __name__ == "__main__":
    main()
