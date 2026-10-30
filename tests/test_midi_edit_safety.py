from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from core.project import Project, Track, MidiClip, MidiNote
from core.actions.midi import (add_midi_notes, clear_midi_notes, remove_midi_notes,
                               transpose_clip, quantize_notes, get_midi_notes, create_midi_clip)


@pytest.fixture
def app():
    a, b = Track(name='A'), Track(name='B')
    a.clips = [MidiClip(name='First', notes=[MidiNote(60, .12, 1)])]
    b.clips = [MidiClip(name='Second', notes=[MidiNote(70, .12, 1)])]
    project = Project(tracks=[a, b])
    return SimpleNamespace(project=project, audio_engine=Mock(), set_dirty=Mock(),
                           piano_roll=SimpleNamespace(current_track=a, current_clip=a.clips[0], note_grid=Mock(), open_clip=Mock()))


def test_unknown_explicit_clip_never_falls_back(app):
    with pytest.raises(ValueError):
        clear_midi_notes(app, 'missing')
    assert len(app.project.tracks[0].clips[0].notes) == 1
    app.set_dirty.assert_not_called()


def test_track_filter_overrides_current_piano_roll(app):
    result = get_midi_notes(app, track_id_or_name='B')
    assert result['notes'][0]['pitch'] == 70
    with pytest.raises(ValueError):
        add_midi_notes(app, [], track_id_or_name='missing')


@pytest.mark.parametrize('action', [
    lambda app, cid: add_midi_notes(app, [{'pitch': 62}], cid),
    lambda app, cid: clear_midi_notes(app, cid),
    lambda app, cid: remove_midi_notes(app, cid),
    lambda app, cid: transpose_clip(app, 12, cid),
    lambda app, cid: quantize_notes(app, .25, cid),
])
def test_all_note_actions_invalidate_target_track(app, action):
    track = app.project.tracks[1]
    action(app, track.clips[0].id)
    app.audio_engine.invalidate_track_cache.assert_called_once_with(track.id, trigger_async=True)
    app.set_dirty.assert_called_once_with(True)


@pytest.mark.parametrize('clear', [False, True])
def test_invalid_batch_is_transactional(app, clear):
    clip = app.project.tracks[0].clips[0]
    original = clip.to_dict()
    with pytest.raises((ValueError, TypeError)):
        add_midi_notes(app, [{'pitch': 62}, {'pitch': 'not a note'}], clip.id, clear_existing=clear)
    assert clip.to_dict() == original
    app.set_dirty.assert_not_called()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_times_rejected_without_mutation(app, value):
    clip = app.project.tracks[0].clips[0]
    with pytest.raises(ValueError):
        add_midi_notes(app, [{'pitch': 60, 'start_beat': value}], clip.id, clear_existing=True)
    assert len(clip.notes) == 1


def test_quantize_extends_clip_so_last_note_is_audible(app):
    clip = app.project.tracks[0].clips[0]
    clip.notes = [MidiNote(60, 3.9, .1)]
    quantize_notes(app, 1, clip.id)
    assert clip.notes[0].start_beat == 4
    assert clip.length_beats == 4.1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_grid_rejected_without_mutation(app, value):
    clip = app.project.tracks[0].clips[0]
    before = clip.to_dict()
    with pytest.raises(ValueError):
        quantize_notes(app, value, clip.id)
    assert clip.to_dict() == before


def test_create_clip_invalidates_its_track(app):
    track = app.project.tracks[1]
    result = create_midi_clip(app, track.id)
    assert result['clip_id'] == track.clips[-1].id
    app.audio_engine.invalidate_track_cache.assert_called_once_with(track.id, trigger_async=True)
