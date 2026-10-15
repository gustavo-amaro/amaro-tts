import threading

from app.tts.worker import Task, TtsWorker
from conftest import FakeSynth, wait_until


class StubTask(Task):
    def __init__(self, text, log):
        super().__init__(text, {"seed": 1}, None, label=f"stub {text}")
        self.log = log

    def started(self):
        self.log.append(("started", self.text))

    def done(self, wav, sample_rate):
        self.log.append(("done", self.text, len(wav), sample_rate))

    def failed(self, error):
        self.log.append(("failed", self.text, error))


class ListSource:
    def __init__(self, log, *texts):
        self.tasks = [StubTask(t, log) for t in texts]

    def __call__(self):
        return self.tasks.pop(0) if self.tasks else None


def test_default_task_started_is_noop():
    class Minimal(Task):
        def done(self, wav, sample_rate):
            pass

        def failed(self, error):
            pass

    Minimal("t", {}, None, "l").started()


def test_runs_sources_in_priority_order_and_waits_for_new_work():
    log, wakeup, synth = [], threading.Condition(), FakeSynth()
    high, low = ListSource(log, "a1", "a2"), ListSource(log, "b1")
    worker = TtsWorker(synth, wakeup, [high, low])
    assert worker.status == "loading" and worker._next_task() is None
    worker.start()
    wait_until(lambda: len(log) == 6)
    assert [e[:2] for e in log] == [("started", "a1"), ("done", "a1"), ("started", "a2"), ("done", "a2"),
                                    ("started", "b1"), ("done", "b1")]
    assert log[1][2:] == (1600, synth.sample_rate)
    assert worker.status == "ready"

    with wakeup:  # worker está bloqueado esperando; nova tarefa + notify o acorda
        low.tasks.append(StubTask("b2", log))
        wakeup.notify_all()
    wait_until(lambda: ("done", "b2", 1600, synth.sample_rate) in log)
    assert synth.calls[-1] == ("b2", {"seed": 1}, None)


def test_synthesis_error_calls_failed_and_keeps_running():
    log, wakeup = [], threading.Condition()
    worker = TtsWorker(FakeSynth(), wakeup, [ListSource(log, "FALHA aqui", "ok")])
    worker.start()
    wait_until(lambda: ("done", "ok", 1600, 16000) in log)
    assert ("failed", "FALHA aqui", "boom") in log


def test_load_error_sets_status_and_stops():
    log = []
    worker = TtsWorker(FakeSynth(load_error=RuntimeError("sem GPU")), threading.Condition(), [ListSource(log, "x")])
    worker._run()  # retorna em vez de entrar no laço
    assert worker.status == "error"
    assert log == []
