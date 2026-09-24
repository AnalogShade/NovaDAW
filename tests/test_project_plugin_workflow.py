import os
import time
import numpy as np
import pytest
from core.vst_host import IsolatedPlugin
from core.project import Project, Track, MidiClip, MidiNote
from core.audio_engine import AudioEngine, SynthVoice
from core.serializer import save_project, load_project
from core.plugin_manager import global_plugin_manager


def crash_worker(connection, path):
    os._exit(17)


def hang_worker(connection, path):
    time.sleep(60)


def fake_worker(connection, path):
    connection.send((True, dict(name='Test',is_instrument=True,is_effect=False)))
    state = b'initial'
    while True:
        cmd,args,kwargs=connection.recv()
        if cmd=='state': result=state
        elif cmd=='restore': state=args[0]; result=None
        elif cmd=='crash': os._exit(18)
        elif cmd=='hang': time.sleep(60)
        else: result=np.ones((2,16),dtype=np.float32)
        connection.send((True,result))


def test_native_crash_contained():
    with pytest.raises(RuntimeError):
        IsolatedPlugin('crash', timeout=5, worker=crash_worker)


def test_hung_load_is_bounded():
    with pytest.raises(RuntimeError):
        IsolatedPlugin('hang', timeout=0.2, worker=hang_worker)


def test_process_state_audio_and_mid_render_crash():
    plugin=IsolatedPlugin('fake',worker=fake_worker)
    try:
        assert plugin.raw_state == b'initial'
        plugin.raw_state=b'bank and parameters'
        assert plugin.raw_state == b'bank and parameters'
        assert plugin([],duration=.1).shape == (2,16)
        with pytest.raises(RuntimeError): plugin.request('crash')
        with pytest.raises(RuntimeError): plugin([])
    finally: plugin.dispose()


def test_render_hang_is_bounded():
    plugin=IsolatedPlugin('fake',worker=fake_worker)
    try:
        with pytest.raises(RuntimeError): plugin.request('hang',timeout=.2)
        assert not plugin._process.is_alive()
    finally: plugin.dispose()


def test_native_instrument_does_not_double_with_synth(monkeypatch):
    engine=AudioEngine()
    class Native:
        is_instrument=True
        plugin_type_id='novadaw.test'
        def render_slice(self,*args): return np.full((128,2),.125,dtype=np.float32)
    track=Track(name='Test',track_type='midi',plugin_path='novadaw.test')
    track.plugins=[Native()]
    monkeypatch.setattr(SynthVoice,'generate_note',lambda *a,**k: pytest.fail('Unexpected fallback'))
    result=engine._render_track_slice(track,0,.1,128,120)
    np.testing.assert_allclose(result,.125)
    engine.close()


def test_unavailable_instrument_is_silent(monkeypatch):
    engine=AudioEngine()
    track=Track(name='Missing',track_type='midi',plugin_path='missing.vst3')
    monkeypatch.setattr(engine,'get_track_plugin',lambda t: None)
    track.clips=[MidiClip(start_beat=0,length_beats=4,notes=[MidiNote(60,0,1)])]
    assert not engine._render_track_slice(track,0,.1,128,120).any()
    engine.close()


def test_vst_tails_and_midi_order(monkeypatch):
    engine=AudioEngine()
    calls=[]
    class Plugin:
        is_instrument=True
        def __call__(self,messages,**kwargs):
            calls.append(messages)
            return np.ones((2,128),dtype=np.float32)
    monkeypatch.setattr(engine,'get_track_plugin',lambda t: Plugin())
    track=Track(name='VST',track_type='midi',plugin_path='fake.vst3')
    track.clips=[MidiClip(start_beat=0,length_beats=4,notes=[MidiNote(60,.08,.01),MidiNote(64,0,.01)])]
    engine._render_track_slice(track,0,.1,128,120)
    assert [t for _,t in calls[0]] == sorted(t for _,t in calls[0])
    assert engine._render_track_slice(track,5,6,128,120).any()
    assert calls[-1] == []
    engine.close()


def test_project_rack_and_state_roundtrip(tmp_path):
    p=Project(name='Routing')
    first=p.add_rack_plugin('synth.vst3','Synth')
    assert p.add_rack_plugin('synth.vst3','Synth') is first
    p.add_track(Track(name='MIDI',track_type='midi',plugin_path='synth.vst3'))
    p.plugin_states={'synth.vst3':'YmFuaw=='}
    path=str(tmp_path/'routing.ndaw'); save_project(p,path)
    restored=load_project(path)
    assert restored.plugin_rack == p.plugin_rack
    assert restored.plugin_states == p.plugin_states
    assert restored.tracks[0].plugin_path == 'synth.vst3'


def test_project_picker_and_midi_assignment(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget
    from ui.vst_rack import VstRackWidget
    from ui.project_plugin_dialog import ProjectPluginDialog
    from ui.dialogs import AddTrackDialog
    from ui.inspector import TrackInspector
    app=QApplication.instance() or QApplication([])
    p=Project(name='User flow')
    rack=VstRackWidget(p)
    picker=ProjectPluginDialog(rack)
    picker.search.setText('no such plugin 93281')
    assert not picker.add.isEnabled()
    picker.search.setText('Nova Drums')
    assert picker.add.isEnabled()
    picker.list.setCurrentRow(0)
    picker.add.click()
    assert p.plugin_rack[0]['file_path']=='novadaw.drum_machine'
    parent=QWidget(); parent.project=p
    creation=AddTrackDialog(parent)
    creation.combo_inst.setCurrentIndex(creation.combo_inst.findData('novadaw.drum_machine'))
    data=creation.get_track_data()
    track=Track(**data); p.add_track(track)
    inspector=TrackInspector(p); inspector.set_track(track)
    inspector.combo_instrument.setCurrentIndex(0)
    assert track.plugin_path is None
    assert not any(getattr(plugin,'is_instrument',False) for plugin in track.plugins)
    inspector.combo_instrument.setCurrentIndex(inspector.combo_instrument.findData('novadaw.drum_machine'))
    assert len([plugin for plugin in track.plugins if plugin.is_instrument]) == 1
    inspector.combo_instrument.setCurrentIndex(0)
    assert not track.plugins
    for widget in (picker,rack,creation,inspector,parent): widget.close()


def test_separate_track_instances_and_saved_settings(monkeypatch):
    import core.plugin_manager as module
    class Fake:
        def __init__(self,path): self.raw_state=b'default'
        def dispose(self): pass
    monkeypatch.setattr(module,'IsolatedPlugin',Fake)
    manager=module.PluginManager()
    template=manager.get_or_load_plugin('synth.vst3')
    template.raw_state=b'project preset'
    first=manager.get_or_load_plugin('synth.vst3','track:1')
    second=manager.get_or_load_plugin('synth.vst3','track:2')
    assert first is not second
    assert first.raw_state == second.raw_state == b'project preset'
    first.raw_state=b'first track preset'
    saved=manager.capture_states()
    manager.restore_states(saved)
    assert manager.get_or_load_plugin('synth.vst3','track:1').raw_state == b'first track preset'
    assert manager.get_or_load_plugin('synth.vst3','track:2').raw_state == b'project preset'
    manager.release_instances()


def test_native_rack_settings_seed_new_track_and_survive_save(tmp_path):
    project=Project(name='Drums settings')
    project.add_rack_plugin('novadaw.drum_machine','Nova Drums')
    prototype=project.get_rack_native_plugin('novadaw.drum_machine')
    prototype.master_volume=.27
    filename=str(tmp_path/'drums.ndaw')
    save_project(project,filename)
    restored=load_project(filename)
    engine=AudioEngine(); engine.set_project(restored)
    track=Track(name='Drums',track_type='midi',plugin_path='novadaw.drum_machine')
    plugin=engine.get_native_instrument(track)
    assert plugin.master_volume == .27
    assert plugin is not restored.get_rack_native_plugin('novadaw.drum_machine')
    engine.close()


def test_master_vst_effect_processing(monkeypatch):
    engine=AudioEngine()
    class Gain:
        is_effect=True
        def __call__(self,data,**kwargs): return data*.5
    monkeypatch.setattr(engine,'get_effect_plugin',lambda *args: Gain())
    master=Track(name='Master',track_type='master',insert_effects=['gain.vst3'])
    out=engine._process_vst_effects(master,np.ones((32,2),dtype=np.float32))
    np.testing.assert_allclose(out,.5)
    engine.close()


def test_missing_assigned_instrument_stays_visible():
    from PySide6.QtWidgets import QApplication
    from ui.inspector import TrackInspector
    app=QApplication.instance() or QApplication([])
    track=Track(name='Missing',track_type='midi',plugin_path='not-installed.vst3',plugin_name='Missing Synth')
    inspector=TrackInspector(Project(name='Missing'))
    inspector.set_track(track)
    assert inspector.combo_instrument.currentData() == 'not-installed.vst3'
    assert 'indisponible' in inspector.combo_instrument.currentText()
    inspector.close()
