"""Composition root: the only place that instantiates the concrete implementations and wires them together."""
import threading
from dataclasses import dataclass
from pathlib import Path

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


def build_container(synthesizer: Synthesizer | None = None, data_dir: Path = config.DATA_DIR) -> Container:
    docs_dir, exports_dir = data_dir / config.DOCS_DIR.name, data_dir / config.EXPORTS_DIR.name
    bus = EventBus()
    wakeup = threading.Condition()  # guards the state the worker reads and wakes it when something changes
    synthesis = SynthesisConfig(data_dir, wakeup)
    reader = ReaderQueue(docs_dir, synthesis, wakeup, bus.publish)
    queue = ExportQueue(exports_dir, synthesis, wakeup, bus.publish)
    # priority: reader's next chunks > exports > rest of the reader's buffer
    worker = TtsWorker(synthesizer or VoxCPMSynthesizer(), wakeup, [
        lambda: reader.next_task(URGENT_AHEAD),
        queue.next_task,
        lambda: reader.next_task(LOOKAHEAD),
    ])
    documents = DocumentService(DocumentRepository(docs_dir), reader, build_document, bus.publish)
    return Container(bus, synthesis, reader, worker, documents, TextExportService(queue, config.EXPORT_MAX_CHARS))
