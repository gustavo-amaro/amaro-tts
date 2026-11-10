import itertools
import json

import pytest
import soundfile as sf

from app.tts import exports as exports_module
from app.tts.exports import CHANNEL, ExportQueue
from conftest import SR, fake_wav, notifies, wav_bytes


@pytest.fixture
def queue(tmp_path, synthesis, wakeup, publish):
    return ExportQueue(tmp_path / "exports", synthesis, wakeup, publish)


def run_all(queue):
    while task := queue.next_task():
        task.done(fake_wav(), SR)


def test_create_persists_and_hides_texts(queue, tmp_path, wakeup):
    job = None

    def create():
        nonlocal job
        job = queue.create("Título", ["a", "b"], [0, 0], None)

    assert notifies(wakeup, create)
    assert "texts" not in job and "paras" not in job
    assert job["status"] == "queued" and job["total"] == 2 and job["done"] == 0
    assert job["voice"] is False and job["settings"] == {"steps": 6, "cfg": 2.0, "seed": 42}
    saved = json.loads((tmp_path / "exports" / job["id"] / "job.json").read_text(encoding="utf-8"))
    assert saved["texts"] == ["a", "b"]


def test_next_task_is_oldest_job_with_style_and_frozen_voice(queue, synthesis):
    assert queue.next_task() is None
    synthesis.set_voice(wav_bytes(1))
    first = queue.create("A", ["olá"], [0], "calmo")
    synthesis.delete_voice()
    queue.create("B", ["oi"], [0], None)
    task = queue.next_task()
    assert task.job_id == first["id"] and task.idx == 0
    assert task.text == "(calmo)olá"
    assert task.voice is not None and task.voice.name == "voice.wav" and task.voice.exists()
    assert task.label == f"export {first['id'][:8]}#0"


def test_task_without_voice(queue):
    queue.create("A", ["olá"], [0], None)
    assert queue.next_task().voice is None


def test_progress_then_finalize_to_mp3(queue, publish):
    job = queue.create("A", ["um", "dois", "três"], [0, 0, 1], None)
    queue.next_task().done(fake_wav(), SR)
    assert queue.get(job["id"])["done"] == 1 and queue.get(job["id"])["status"] == "running"
    assert publish.of(CHANNEL) == [{"type": "export_progress", "job": job["id"], "done": 1, "total": 3}]
    run_all(queue)
    done = queue.get(job["id"])
    # 3 chunks of 0.1 s + chunk pause (0.12) + paragraph pause (0.30)
    assert done["status"] == "done" and done["duration"] == 0.7
    path = queue.audio_path(job["id"])
    assert sf.info(path).duration == pytest.approx(0.72, abs=0.06)
    assert not (path.parent / "parts").exists()
    assert publish.of(CHANNEL, "export_done") == [{"type": "export_done", "job": job["id"], "duration": 0.7}]
    assert queue.next_task() is None


def test_result_of_cancelled_or_stale_task_is_ignored(queue, publish):
    job = queue.create("A", ["um", "dois"], [0, 0], None)
    task = queue.next_task()
    task.done(fake_wav(), SR)
    task.done(fake_wav(), SR)  # index already saved
    assert queue.get(job["id"])["done"] == 1
    task2 = queue.next_task()
    queue.delete(job["id"])
    task2.done(fake_wav(), SR)
    assert queue.get(job["id"]) is None
    assert len(publish.of(CHANNEL)) == 1


def test_failure_marks_job_as_error(queue, publish):
    job = queue.create("A", ["um"], [0], None)
    task = queue.next_task()
    task.failed("boom")
    assert queue.get(job["id"])["status"] == "error" and queue.get(job["id"])["error"] == "boom"
    assert publish.of(CHANNEL) == [{"type": "export_error", "job": job["id"], "error": "boom"}]
    assert queue.next_task() is None
    task.failed("de novo")  # job already out of the queue
    assert len(publish.of(CHANNEL)) == 1


def test_list_is_newest_first_with_live_progress(queue, tmp_path, monkeypatch):
    # deterministic clock: on Windows time.time() ticks every ~15 ms, so A and B could tie on "created"
    clock = itertools.count(1000)
    monkeypatch.setattr(exports_module.time, "time", lambda: next(clock))
    a = queue.create("A", ["um", "dois"], [0, 0], None)
    b = queue.create("B", ["um"], [0], None)
    queue.next_task().done(fake_wav(), SR)
    # disk holds done=1; simulate a stale disk to check that memory takes precedence
    p = tmp_path / "exports" / a["id"] / "job.json"
    p.write_text(json.dumps({**json.loads(p.read_text(encoding="utf-8")), "done": 0, "status": "queued"}), encoding="utf-8")
    jobs = queue.list()
    assert [j["id"] for j in jobs] == [b["id"], a["id"]]
    assert jobs[1]["done"] == 1 and jobs[1]["status"] == "running"
    assert all("texts" not in j for j in jobs)


def test_get_reads_from_disk_after_finished(queue, tmp_path):
    job = queue.create("A", ["um"], [0], None)
    run_all(queue)
    assert queue.get(job["id"])["status"] == "done"
    assert queue.get("inexistente") is None


def test_delete_removes_folder(queue, tmp_path):
    job = queue.create("A", ["um"], [0], None)
    queue.delete(job["id"])
    assert not (tmp_path / "exports" / job["id"]).exists()
    assert queue.next_task() is None


def test_resume_pending_jobs_from_parts(tmp_path, synthesis, wakeup, publish):
    q1 = ExportQueue(tmp_path / "exports", synthesis, wakeup, publish)
    finished = q1.create("Pronto", ["um"], [0], None)
    run_all(q1)
    failed = q1.create("Falhou", ["um"], [0], None)
    q1.next_task().failed("boom")
    pending = q1.create("Pendente", ["um", "dois", "três"], [0, 0, 0], None)
    q1.next_task().done(fake_wav(), SR)
    q1.next_task().done(fake_wav(), SR)
    # resumed progress comes from the parts on disk, not from the "done" field
    p = tmp_path / "exports" / pending["id"] / "job.json"
    p.write_text(json.dumps({**json.loads(p.read_text(encoding="utf-8")), "done": 0}), encoding="utf-8")

    q2 = ExportQueue(tmp_path / "exports", synthesis, wakeup, publish)
    task = q2.next_task()
    assert (task.job_id, task.idx) == (pending["id"], 2)
    run_all(q2)
    assert q2.next_task() is None
    statuses = {j["id"]: j["status"] for j in q2.list()}
    assert statuses == {finished["id"]: "done", failed["id"]: "error", pending["id"]: "done"}
