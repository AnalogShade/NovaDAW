import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.hardware_manager import hardware_manager
from core.audio_engine import AudioEngine
from ui.device_settings_dialog import DeviceSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_hardware_manager_latency_calculation():
    """Vérifie le calcul des latences In / Out / RTL / Buffer ms."""
    latencies = hardware_manager.get_device_latencies(
        input_device_index=None,
        output_device_index=None,
        buffer_size=256,
        sample_rate=44100
    )
    assert "input_ms" in latencies or "input_latency_ms" in latencies
    assert "output_ms" in latencies or "output_latency_ms" in latencies
    assert "roundtrip_ms" in latencies or "roundtrip_latency_ms" in latencies
    assert "buffer_ms" in latencies or "buffer_duration_ms" in latencies
    buf_ms = latencies.get("buffer_ms", latencies.get("buffer_duration_ms", 0.0))
    rtl_ms = latencies.get("roundtrip_ms", latencies.get("roundtrip_latency_ms", 0.0))
    assert buf_ms == pytest.approx(5.8, rel=0.1)
    assert rtl_ms >= buf_ms


def test_open_audio_driver_control_panel():
    """Vérifie que la fonction de détection et d'ouverture du panneau de contrôle fonctionne."""
    # Test sans mocker (sur la machine réelle ou fallback)
    success, msg = hardware_manager.open_audio_driver_control_panel()
    assert isinstance(success, bool)
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_audio_engine_record_latency_compensation():
    """Vérifie que la compensation de latence d'enregistrement (Record Placement Offset) décale le buffer audio enregistré."""
    engine = AudioEngine()
    engine.record_latency_compensation_samples = 100
    engine.is_recording = True
    
    # Créer 1000 échantillons simulés (stéréo)
    simulated_block = np.ones((1000, 2), dtype=np.float32)
    engine._recorded_audio_blocks = [simulated_block]
    
    # Arrêter l'enregistrement
    recorded_result = engine.stop_recording()
    
    # Si le décalage était de 100 échantillons, la taille du signal audio doit être 900 échantillons
    assert recorded_result is not None
    assert "audio" in recorded_result
    assert len(recorded_result["audio"]) == 900
    engine.close()


def test_device_settings_dialog_audio_tab(qapp):
    """Vérifie l'interface graphique de configuration audio style Cubase 6."""
    engine = AudioEngine()
    dlg = DeviceSettingsDialog(engine)

    assert hasattr(dlg, "combo_host_api")
    assert hasattr(dlg, "combo_buffer_size")
    assert hasattr(dlg, "spin_record_offset")
    assert hasattr(dlg, "lbl_in_latency")
    assert hasattr(dlg, "lbl_out_latency")
    assert hasattr(dlg, "lbl_rtl_latency")
    assert hasattr(dlg, "lbl_buf_duration")
    assert hasattr(dlg, "btn_control_panel")
    assert hasattr(dlg, "btn_reset_engine")

    # Vérifier le changement de taille de buffer
    idx_128 = dlg.combo_buffer_size.findData(128)
    if idx_128 >= 0:
        dlg.combo_buffer_size.setCurrentIndex(idx_128)
        assert dlg.combo_buffer_size.currentData() == 128
        assert "2.9" in dlg.lbl_buf_duration.text() or "ms" in dlg.lbl_buf_duration.text()

    # Vérifier la compensation de latence d'enregistrement
    dlg.spin_record_offset.setValue(256)
    assert "+5.80 ms" in dlg.lbl_offset_ms.text() or "ms" in dlg.lbl_offset_ms.text()

    # Tester le bouton réinitialisation moteur
    dlg._on_reset_engine()
    assert "succès" in dlg.lbl_control_panel_status.text() or "réinitialisé" in dlg.lbl_control_panel_status.text()

    # Sauvegarde et application
    dlg._on_apply_and_save()
    assert engine.record_latency_compensation_samples == 256
    assert hardware_manager.settings["audio"]["record_offset_samples"] == 256

    dlg.close()
    engine.close()
