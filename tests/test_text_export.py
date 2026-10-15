import pytest

from app.errors import InvalidInput, NotFound
from app.text_export import TextExportService
from app.tts.exports import ExportQueue
from conftest import SR, fake_wav


@pytest.fixture
def queue(tmp_path, synthesis, wakeup, publish):
    return ExportQueue(tmp_path / "exports", synthesis, wakeup, publish)


@pytest.fixture
def service(queue):
    return TextExportService(queue, max_chars=100)


def finish(queue):
    while task := queue.next_task():
        task.done(fake_wav(), SR)


@pytest.mark.parametrize("text, message", [
    ("   \r\n ", "texto vazio"),
    ("a" * 101, "texto muito longo (máx. 100 caracteres)"),
    ("--- ...\r\n\r\n***", "nenhum texto legível"),
])
def test_create_validates(service, text, message):
    with pytest.raises(InvalidInput, match=message.replace("(", r"\(").replace(")", r"\)")):
        service.create(text, None)


def test_create_splits_text_and_builds_title(service, queue):
    job = service.create("Um dois três quatro cinco seis sete oito nove.\r\n\r\nOutro parágrafo.", " (calmo) ")
    assert job["title"] == "Um dois três quatro cinco seis sete oito"
    assert job["total"] == 2 and job["style"] == "calmo"
    assert queue.next_task().text == "(calmo)Um dois três quatro cinco seis sete oito nove."


def test_create_title_is_truncated_and_blank_style_is_none(service):
    job = service.create("x" * 80, "  ")
    assert job["title"] == "x" * 60 and job["style"] is None


def test_audio_not_available_until_done(service):
    job = service.create("Olá mundo.", None)
    with pytest.raises(NotFound):
        service.audio(job["id"])
    with pytest.raises(NotFound):
        service.audio("inexistente")


def test_audio_when_done_has_safe_filename(service, queue):
    job = service.create("Olá: mundo/teste?", None)
    finish(queue)
    path, name = service.audio(job["id"])
    assert path.exists() and name == "Olá mundoteste.mp3"


def test_audio_missing_file_is_not_found(service, queue):
    job = service.create("Olá.", None)
    finish(queue)
    queue.audio_path(job["id"]).unlink()
    with pytest.raises(NotFound):
        service.audio(job["id"])


def test_audio_name_falls_back_when_title_has_no_word_chars(service, queue):
    job = service.create("!!! ?? a", None)
    finish(queue)
    assert service.audio(job["id"])[1] == "a.mp3"
    job = queue.create("???", ["um"], [0], None)
    finish(queue)
    assert service.audio(job["id"])[1] == "audio.mp3"


def test_list_and_delete(service):
    job = service.create("Olá.", None)
    assert [j["id"] for j in service.list()] == [job["id"]]
    service.delete(job["id"])
    assert service.list() == []
    with pytest.raises(NotFound):
        service.delete(job["id"])
