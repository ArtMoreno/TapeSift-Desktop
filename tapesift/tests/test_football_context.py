"""Situation geometry and persistence must keep unknown facts unknown."""
import sqlite3

import pytest

from tapesift.database.migrations import MIGRATIONS
from tapesift.models.clip import Clip
from tapesift.services.football_context import FieldContext, parse_ball
from tapesift.services.project_service import ProjectSession, edit_clip_metadata
from tapesift.services.detail_service import merge_editor_details

IDS = ["cfbd:team:2390", "cfbd:team:57"]


def context(ball="OWN 4", down="1st & 10", direction="right", offense=IDS[0]):
    return FieldContext.from_details({"ball_on":ball,"down_distance":down,
        "attack_direction":direction,"offense_team_id":offense}, IDS)


@pytest.mark.parametrize("ball,down,los,target", [
    ("OWN 4","1st & 10",4,14), ("OPP 25","3rd & 7",75,82),
    ("50","2nd & 5",50,55), ("MIDFIELD","2nd & 5",50,55),
    ("OWN 50","2nd & 5",50,55), ("OPP 4","1st & Goal",96,100),
    ("OPP 4","1st & 10",96,None), ("25","1st & 10",None,None),
    ("OWN 51","1st & 10",None,None), ("","1st & 10",None,None),
    ("OWN 4","",4,None), ("OWN 4","2nd & 100",4,None),
    ("OWN 4","2nd",4,None), ("OWN 4","To Go 7",4,11),
    ("OPP 4","To Go Goal",96,100),
])
def test_field_uses_saved_side_and_distance_without_inventing_goal(ball,down,los,target):
    c = context(ball,down)
    assert (c.los_yards,c.to_gain_yards)==(los,target)


def test_field_orientation_is_fixed_and_does_not_require_team_identity():
    right, left = context(), context(direction="left")
    for yard in (4,14):
        assert left.fraction(yard)==pytest.approx(right.fraction(yard))
    assert left.fraction(4)<left.fraction(14)
    assert context(direction="").los_yards == 4
    assert context(offense="cfbd:team:87").offense_id == ""
    assert context(offense="").los_yards == 4
    assert FieldContext.from_details({"ball_on":"OPP 25"}, []).los_yards == 75
    assert parse_ball("4")==("",4)
    assert context("OPP 0","1st & Goal").to_gain_yards==100


def test_library_changes_preserve_authoritative_unknowns_and_explicit_removal():
    current={"ball_on":"OWN 4","game_clock":"12:30","custom":"  exact  ","score:"+IDS[0]:"0"}
    edited={"ball_on":"", "result":" Completion; First Down "}
    saved=merge_editor_details(current,edited)
    assert saved=={"game_clock":"12:30","custom":"  exact  ","score:"+IDS[0]:"0","result":"Completion; First Down"}
    assert "custom" not in merge_editor_details(saved,{"custom":""})
    assert "already_deleted" not in saved
    qb = {"quarterback": "Starter QB", "logging_saved": "1"}
    cleared = merge_editor_details(qb, {"quarterback": ""})
    assert cleared == {"logging_saved": "1", "quarterback_cleared": "1"}
    assert merge_editor_details(cleared, {"quarterback": "Backup QB"}) == {
        "logging_saved": "1", "quarterback": "Backup QB"}
    assert "quarterback_cleared" not in merge_editor_details(
        {"run_pass": "Run"}, {"run_pass": "Pass", "quarterback": ""})


def test_legacy_game_identity_and_closed_library_round_trip(tmp_path):
    path=tmp_path/'legacy.tapesift'
    conn=sqlite3.connect(path)
    for i,sql in enumerate(MIGRATIONS[:-1],1):
        conn.executescript(sql)
        conn.execute(f'PRAGMA user_version={i}')
    conn.execute("INSERT INTO projects(name,created_at,updated_at) VALUES('Legacy','before','before')")
    conn.commit(); conn.close()
    session=ProjectSession.open(path)
    assert session.project.game_team_ids==[]
    assert session.project.name=='Legacy'
    session.project.game_team_ids=IDS.copy()
    clip=Clip(0,1000,details={"ball_on":"OWN 4","game_clock":"12:30","custom":"  exact  ","offense_team_id":IDS[0],"score:"+IDS[0]:"0","quarterback":"Starter QB"})
    session.add_clip(clip); session.save(); session.conn.close()
    edit_clip_metadata(path,clip.id,clip_title="Edited",tags=[],notes="Library note",details={"ball_on":"OWN 4","result":"Completion; First Down"})
    reopened=ProjectSession.open(path)
    assert reopened.project.game_team_ids==IDS
    saved=reopened.get_clip(clip.id)
    assert saved.notes=='Library note'
    assert saved.details['custom']=='  exact  '
    assert saved.details['game_clock']=='12:30'
    assert saved.details['offense_team_id']==IDS[0]
    assert saved.details['score:'+IDS[0]]=='0'
    assert saved.details.get('quarterback', '') == ''
    assert saved.details['quarterback_cleared'] == '1'
    reopened.conn.close()
