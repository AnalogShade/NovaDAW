import json
from unittest.mock import patch
import pytest
from core.project import Project, Track, MidiClip, MidiNote
from core.serializer import save_project, load_project


def project_with_notes():
    project = Project.create_empty()
    track = Track(name='Mélodie')
    track.clips = [MidiClip(notes=[MidiNote(20, .25, 2, 42), MidiNote(127, 3, 1, 127)])]
    project.add_track(track)
    return project


def test_atomic_save_roundtrip_and_no_temporary_left(tmp_path):
    path = tmp_path / 'Création.ndaw'
    project = project_with_notes()
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert loaded.tracks[0].clips[0].to_dict() == project.tracks[0].clips[0].to_dict()
    assert project.file_path == str(path)
    assert list(tmp_path.glob('.novadaw-*.tmp')) == []
    project.name = 'Nouvelle version'
    save_project(project, str(path))
    assert load_project(str(path)).name == 'Nouvelle version'


def test_failed_serialization_preserves_existing_project(tmp_path):
    path = tmp_path / 'project.ndaw'
    project = project_with_notes()
    save_project(project, str(path))
    original = path.read_bytes()
    project.name = object()  # JSON cannot serialize this value.
    with pytest.raises(TypeError):
        save_project(project, str(path))
    assert path.read_bytes() == original
    assert list(tmp_path.glob('.novadaw-*.tmp')) == []
    assert load_project(str(path)).tracks[0].name == 'Mélodie'


def test_replacement_failure_preserves_file_and_project_path(tmp_path):
    path = tmp_path / 'project.ndaw'
    path.write_text('{"existing": true}', encoding='utf-8')
    project = project_with_notes()
    previous_path = project.file_path
    with patch('core.serializer.os.replace', side_effect=PermissionError('locked')):
        with pytest.raises(PermissionError):
            save_project(project, str(path))
    assert json.loads(path.read_text(encoding='utf-8')) == {'existing': True}
    assert project.file_path == previous_path
    assert list(tmp_path.glob('.novadaw-*.tmp')) == []


def test_fsync_failure_preserves_existing_file(tmp_path):
    path = tmp_path / 'project.ndaw'
    path.write_text('original', encoding='utf-8')
    with patch('core.serializer.os.fsync', side_effect=OSError('disk error')):
        with pytest.raises(OSError):
            save_project(project_with_notes(), str(path))
    assert path.read_text(encoding='utf-8') == 'original'
    assert list(tmp_path.glob('.novadaw-*.tmp')) == []
