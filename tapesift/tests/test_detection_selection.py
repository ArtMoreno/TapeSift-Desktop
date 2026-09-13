"""Explicit detection choices survive activation and immutable-capture checks."""
from copy import deepcopy
import pytest
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.services.autodetect_export_service import _validate_candidate_capture


def admission(tmp_path, *, selection=True):
    session = ProjectSession.create('Selection QA', tmp_path, tmp_path/'exports')
    source = tmp_path/'source.mp4'
    source.write_bytes(b'isolated metadata fixture')
    session.project.source_video_path = str(source)
    session.project.source_duration_ms = 10000
    source_record = {'duration_ms':10000,'source':{'path':str(source), 'size_bytes':source.stat().st_size, 'mtime_ns':source.stat().st_mtime_ns}}
    raw = {'plays':[{'start_ms':0,'end_ms':5000}], 'unclassified':[
        {'start_ms':5000,'end_ms':8000}, {'start_ms':9000,'end_ms':10000}]}
    kept = Clip(start_ms=5000,end_ms=8000,notes='  analyst note  ',details={'opaque':'  retain  '})
    candidate = {'candidate_kind':'unclassified','candidate_index':0,
        'detector_start_ms':5000,'detector_end_ms':8000,'created_start_ms':5000,
        'created_end_ms':8000,'angle_starts_ms':[5000],'angle_count':1,'needs_review':True,'review_reason':'uncertain'}
    options = {'candidate_selection': {'schema_version':1,'kept':[['unclassified',0]],
        'dismissed':[['play',0],['unclassified',1]]}} if selection else {}
    args = dict(detector_id='test',detector_version='test',app_version='test',source=source_record,parameters={},result=raw,ui_options=options,runtime_seconds=0.1)
    sid = session.add_detected_clips([kept],[candidate],**args)
    return session,kept,candidate,args,sid


def test_kept_uncertain_range_survives_activation_and_selection_validation(tmp_path):
    session,clip,candidate,args,sid=admission(tmp_path)
    assert session.suppress_legacy_unclassified_clips()==0
    captured=session.autodetect_repo.get_session(sid)
    rows=session.autodetect_repo.list_candidates(sid)
    assert _validate_candidate_capture(captured,rows)['valid']
    path=session.db_path;cid=clip.id;session.close()
    session=ProjectSession.open(path)
    assert session.suppress_legacy_unclassified_clips()==0
    assert session.get_clip(cid).enabled
    assert session.get_clip(cid).details=={'opaque':'  retain  '}
    assert session.get_clip(cid).notes=='  analyst note  '
    session.close()


@pytest.mark.parametrize('overlaps', [True, False])
def test_omitted_play_cannot_shrink_a_batch_scoring_denominator(tmp_path, overlaps):
    from tapesift.core.exceptions import DatabaseError
    from tapesift.services.autodetect_export_service import build_correction_bundle
    session,clip,_,_,sid=admission(tmp_path)
    batch=session.start_autodetect_review_batch(0 if overlaps else 5000,10000 if overlaps else 8000)
    assert session.mark_detection_reviewed([clip.id])==1
    bundle=build_correction_bundle(session.conn,session_selector=sid)
    result=bundle['review_batches'][0]
    assert bundle['candidate_capture_validation']['valid']
    assert not bundle['report']['review_complete']
    assert bool(result['omitted_detector_play_ranges']) is overlaps
    assert result['development_score']['available'] is not overlaps
    if overlaps:
        assert not result['review_complete']
        with pytest.raises(DatabaseError,match='omitted'):
            session.complete_autodetect_review_batch(batch_id=batch['id'])
    session.close()


def test_repeat_keeps_disabled_rows_and_human_metadata(tmp_path):
    session,clip,candidate,args,_=admission(tmp_path)
    clip.enabled=False
    session.save()
    before=deepcopy(clip)
    session.add_detected_clips([Clip(start_ms=5000,end_ms=8000)],[candidate],**args)
    assert len(session.clips)==1
    assert session.clips[0].id==clip.id
    assert not session.clips[0].enabled
    assert session.clips[0].details==before.details
    assert session.clips[0].notes==before.notes
    assert session.suppress_legacy_unclassified_clips()==0
    session.close()


def test_legacy_fragments_still_suppress(tmp_path):
    session,clip,_,_,_=admission(tmp_path,selection=False)
    assert session.suppress_legacy_unclassified_clips()==1
    assert not clip.enabled
    session.close()


@pytest.mark.parametrize('options', [None, [], ['candidate_selection'], {'candidate_selection':None},
    {'candidate_selection':{'schema_version':True,'kept':[],'dismissed':[]}},
    {'candidate_selection':{'schema_version':1,'kept':[['unclassified',0]],'dismissed':[]}},
    {'candidate_selection':{'schema_version':1,'kept':[['play',True]],'dismissed':[]}}])
def test_malformed_selection_fails_closed_before_admission(tmp_path, options):
    from tapesift.services.autodetect_capture_service import selected_candidate_keys
    session,clip,candidate,args,sid=admission(tmp_path)
    captured=session.autodetect_repo.get_session(sid)
    captured['ui_options']=options
    assert not _validate_candidate_capture(captured,session.autodetect_repo.list_candidates(sid))['valid']
    with pytest.raises(ValueError): selected_candidate_keys(args['result'],options)
    before=(deepcopy(session.clips),deepcopy(session._undo_stack),len(session.autodetect_repo.list_sessions(session.project.id)))
    with pytest.raises(ValueError):
        session.add_detected_clips([Clip(start_ms=5000,end_ms=8000)],[candidate],**dict(args,ui_options=options))
    assert session.clips==before[0] and session._undo_stack==before[1]
    assert len(session.autodetect_repo.list_sessions(session.project.id))==before[2]
    session.close()


def test_selection_admission_failure_restores_undo_and_pending_state(tmp_path,monkeypatch):
    session,clip,candidate,args,_=admission(tmp_path)
    session.remove_clips([clip.id])
    before=(deepcopy(session.clips),deepcopy(session._undo_stack),session._pending_autodetect_session)
    def fail(): raise OSError('injected write failure')
    monkeypatch.setattr(session,'_commit_after',fail)
    with pytest.raises(OSError,match='write failure'):
        session.add_detected_clips([Clip(start_ms=5000,end_ms=8000)],[candidate],**args)
    assert session.clips==before[0] and session._undo_stack==before[1]
    assert session._pending_autodetect_session is before[2]
    session.close()


def test_corrupted_outer_capture_stays_invalid_in_full_bundle(tmp_path,monkeypatch):
    from tapesift.services.autodetect_export_service import build_correction_bundle
    session,clip,candidate,args,sid=admission(tmp_path)
    original=session.autodetect_repo.list_sessions(session.project.id)
    for field in ('result','ui_options'):
        for value in (None,[],['not a mapping']):
            corrupted=deepcopy(original)
            corrupted[0][field]=value
            monkeypatch.setattr(type(session.autodetect_repo),'list_sessions',lambda self,pid:corrupted)
            bundle=build_correction_bundle(session.conn,session_selector=sid)
            assert not bundle['candidate_capture_validation']['valid']
            assert not bundle['report']['review_complete']
    session.close()
