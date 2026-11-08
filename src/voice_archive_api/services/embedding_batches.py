"""Claim missing window vectors, then submit asynchronous embedding batches."""

import io
import json
import logging
from uuid import UUID, uuid4

from google import genai
from google.genai import errors, types
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import RetrievalWindow, WindowEmbedding
from voice_archive_api.logic.windows import compute_embedding_input_hash, format_embedding_input
from voice_archive_api.services.books.service import BookNotFound, lock_book_for_series_write

logger = logging.getLogger(__name__)


def claim_embedding_batch(factory: sessionmaker[Session], book_id: int, model: str) -> tuple[UUID, bytes, int] | None:
    with factory.begin() as db:
        if lock_book_for_series_write(db, book_id, {}) is None:
            raise BookNotFound

        outstanding = (
            select(EmbeddingBatchRequest.id)
            .where(
                EmbeddingBatchRequest.embedding_id == WindowEmbedding.id,
                EmbeddingBatchRequest.input_hash == WindowEmbedding.input_hash,
                EmbeddingBatchRequest.active.is_(True),
            )
            .exists()
        )

        rows = db.execute(
            select(WindowEmbedding, RetrievalWindow)
            .join(RetrievalWindow, WindowEmbedding.window_id == RetrievalWindow.id)
            .where(
                RetrievalWindow.book_id == book_id,
                WindowEmbedding.model == model,
                WindowEmbedding.vector.is_(None),
                ~outstanding,
            )
            .order_by(RetrievalWindow.sequence, WindowEmbedding.id)
        ).all()

        if not rows:
            return None

        batch_id = uuid4()
        db.add(EmbeddingBatchJob(id=batch_id, book_id=book_id, model=model, state="submitting"))
        db.flush()
        lines = []
        for embedding, window in rows:
            text = window.primary_text if embedding.kind == "primary" else window.translated_text
            if text is None:
                raise ValueError("Embedding input is missing; rebuild windows")
            if embedding.input_hash != compute_embedding_input_hash(
                window.path, text, model=model, dimensions=embedding.dimensions, input_format=embedding.input_format
            ):
                raise ValueError("Embedding input hash does not match stored window text; rebuild windows")
            request_id = uuid4()
            db.add(
                EmbeddingBatchRequest(
                    id=request_id,
                    batch_id=batch_id,
                    embedding_id=embedding.id,
                    input_hash=embedding.input_hash,
                    active=True,
                )
            )
            lines.append(
                json.dumps(
                    {
                        "key": str(request_id),
                        "request": {
                            "content": {"parts": [{"text": format_embedding_input(window.path, text)}]},
                            "output_dimensionality": embedding.dimensions,
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return batch_id, ("\n".join(lines) + "\n").encode("utf-8"), len(rows)


def release_failed_embedding_batch(factory: sessionmaker[Session], batch_id: UUID) -> None:
    with factory.begin() as db:
        db.execute(update(EmbeddingBatchJob).where(EmbeddingBatchJob.id == batch_id).values(state="failed"))
        db.execute(update(EmbeddingBatchRequest).where(EmbeddingBatchRequest.batch_id == batch_id).values(active=False))


def submit_book_embedding_batches(
    factory: sessionmaker[Session], settings: Settings, book_id: int
) -> list[dict[str, object]]:
    key = settings.effective_gemini_batch_api_key
    if not key or not key.get_secret_value():
        raise ValueError("Configure VOICE_ARCHIVE_GEMINI_BATCH_API_KEY or VOICE_ARCHIVE_GEMINI_API_KEY")

    with factory.begin() as db:
        if lock_book_for_series_write(db, book_id, {}) is None:
            raise BookNotFound
        models = db.scalars(
            select(WindowEmbedding.model)
            .join(RetrievalWindow)
            .where(RetrievalWindow.book_id == book_id, WindowEmbedding.vector.is_(None))
            .distinct()
            .order_by(WindowEmbedding.model)
        ).all()

        if not db.scalar(select(RetrievalWindow.id).where(RetrievalWindow.book_id == book_id).limit(1)):
            raise ValueError("Build the book's windows before submitting embeddings")

    results: list[dict[str, object]] = []
    with genai.Client(
        api_key=key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=settings.gemini_answer_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ) as client:
        for model in models:
            claimed = claim_embedding_batch(factory, book_id, model)

            if claimed is None:
                continue

            batch_id, payload, count = claimed

            try:
                uploaded = client.files.upload(
                    file=io.BytesIO(payload),
                    config=types.UploadFileConfig(mime_type="application/jsonl", display_name=str(batch_id)),
                )
                if not uploaded.name:
                    raise ValueError("Upload returned no file name")
                with factory.begin() as db:
                    db.execute(
                        update(EmbeddingBatchJob)
                        .where(EmbeddingBatchJob.id == batch_id)
                        .values(input_file_name=uploaded.name)
                    )
            except Exception as exc:
                log_exception(
                    logger, f"Embedding input upload failed: batch={batch_id} book={book_id}", exc, settings=settings
                )
                # No batch creation was attempted, so these inputs can safely be retried.
                release_failed_embedding_batch(factory, batch_id)
                raise ValueError(f"Batch {batch_id}: input upload failed; submission can be retried") from None

            try:
                created = client.batches.create_embeddings(
                    model=model,
                    src=types.EmbeddingsBatchJobSource(file_name=uploaded.name),
                    config=types.CreateEmbeddingsBatchJobConfig(display_name=str(batch_id)),
                )
                if not created.name:
                    raise ValueError("Submission returned no batch name")
                with factory.begin() as db:
                    db.execute(
                        update(EmbeddingBatchJob)
                        .where(EmbeddingBatchJob.id == batch_id)
                        .values(provider_name=created.name, state="submitted")
                    )
            except errors.ClientError as exc:
                log_exception(
                    logger,
                    f"Embedding batch rejected: batch={batch_id} book={book_id} model={model}",
                    exc,
                    settings=settings,
                )
                reason = f"HTTP {exc.code} {exc.status or 'UNKNOWN'}: {exc.message or 'No provider reason supplied'}"
                for configured_key in (settings.gemini_api_key, settings.gemini_batch_api_key):
                    if configured_key and (secret := configured_key.get_secret_value()):
                        reason = reason.replace(secret, "[redacted]")
                if exc.code == 408:
                    raise ValueError(
                        f"Batch {batch_id}: submission timed out ({reason}); claims retained for reconciliation"
                    ) from None
                release_failed_embedding_batch(factory, batch_id)
                raise ValueError(
                    f"Batch {batch_id}: provider rejected submission ({reason}); submission can be retried"
                ) from None
            except Exception as exc:
                log_exception(
                    logger,
                    f"Embedding submission outcome uncertain: batch={batch_id} book={book_id}",
                    exc,
                    settings=settings,
                )
                # Includes a lost response or failed local publication after provider acceptance.
                raise ValueError(
                    f"Batch {batch_id}: submission outcome uncertain; claims retained. "
                    "Reconcile Google's batch with this display name before releasing or retrying."
                ) from None
            results.append({"batch_id": str(batch_id), "provider_name": created.name, "request_count": count})
    return results
