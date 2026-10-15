"""Raiz de composição: o único lugar que instancia as implementações concretas e as liga entre si."""
import threading
from dataclasses import dataclass

from . import config
from .documents.repository import DocumentRepository
from .documents.service import DocumentService
from .events import EventBus
from .text.pdf import build_document
from .text_export import TextExportService
from .tts.config import SynthesisConfig
from .tts.exports import ExportQueue
from .tts.reader import LOOKAHEAD, URGENT_AHEAD, ReaderQueue
from .tts.synthesizer import Synthesizer, VoxCPMSynthesizer
from .tts.worker import TtsWorker


@dataclass
class Container:
    bus: EventBus
    synthesis: SynthesisConfig
    reader: ReaderQueue
    worker: TtsWorker
    documents: DocumentService
    exports: TextExportService


def build_container(synthesizer: Synthesizer | None = None) -> Container:
    bus = EventBus()
    wakeup = threading.Condition()  # protege o estado lido pelo worker e o acorda quando algo muda
    synthesis = SynthesisConfig(config.DATA_DIR, wakeup)
    reader = ReaderQueue(config.DOCS_DIR, synthesis, wakeup, bus.publish)
    queue = ExportQueue(config.EXPORTS_DIR, synthesis, wakeup, bus.publish)
    # prioridade: próximos trechos do leitor > exportações > resto do buffer do leitor
    worker = TtsWorker(synthesizer or VoxCPMSynthesizer(), wakeup, [
        lambda: reader.next_task(URGENT_AHEAD),
        queue.next_task,
        lambda: reader.next_task(LOOKAHEAD),
    ])
    documents = DocumentService(DocumentRepository(config.DOCS_DIR), reader, build_document, bus.publish)
    return Container(bus, synthesis, reader, worker, documents, TextExportService(queue, config.EXPORT_MAX_CHARS))
