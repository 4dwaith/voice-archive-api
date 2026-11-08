"""Collect accepted embedding batches and reconcile interrupted submissions."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, JsonValue, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.db.models.embedding_batch import EmbeddingBatchJob, EmbeddingBatchRequest
from voice_archive_api.db.models.windows import WindowEmbedding
from voice_archive_api.logic.batches import EmbeddingBatchState
from voice_archive_api.services.batch_queue import release_batch_queue_slot

logger = logging.getLogger(__name__)


class EmbeddingVector(BaseModel):
    values: list[Annotated[float, Field(strict=True, allow_inf_nan=False)]] = Field(min_length=1)


class EmbeddingResponse(BaseModel):
    embedding: EmbeddingVector


class EmbeddingRequestResult(BaseModel):
    key: UUID
    response: EmbeddingResponse | None = None
    error: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> EmbeddingRequestResult:
        if (self.response is None) == (self.error is None):
            raise ValueError("Expected either an embedding response or a request error")
        return self


def publish_embedding_results(
    factory: sessionmaker[Session], settings: Settings, batch_id: UUID, payload: bytes
) -> None:
    results = [EmbeddingRequestResult.model_validate_json(line) for line in payload.splitlines() if line.strip()]
    by_id = {result.key: result for result in results}

    with factory.begin() as db:
        batch = db.get(EmbeddingBatchJob, batch_id, with_for_update=True)
        if batch is None or batch.state != EmbeddingBatchState.SUBMITTED:
            return

        requests = db.scalars(select(EmbeddingBatchRequest).where(EmbeddingBatchRequest.batch_id == batch_id)).all()

        if len(by_id) != len(results) or set(by_id) != {request.id for request in requests}:
            raise ValueError("Batch output has duplicate, missing or unknown request keys")

        failures = 0
        for request in requests:
            result = by_id[request.id]
            if result.response is None:
                failures += 1
                log_exception(
                    logger,
                    f"Embedding request failed: batch={batch_id} request={request.id}",
                    ValueError(str((result.error or {}).get("message", "No provider reason supplied"))),
                    settings=settings,
                )
                continue

            vector = result.response.embedding.values
            embedding = db.get(WindowEmbedding, request.embedding_id, with_for_update=True)
            if embedding is None or embedding.input_hash != request.input_hash:
                continue

            if len(vector) != embedding.dimensions or not any(vector):
                raise ValueError(f"Invalid embedding vector dimensions or magnitude: request={request.id}")

            if embedding.vector is None:
                embedding.vector = vector

        batch.state = EmbeddingBatchState.COMPLETED_WITH_ERRORS if failures else EmbeddingBatchState.COMPLETED
        release_batch_queue_slot(db, batch_id)

        for request in requests:
            request.active = False

        if failures:
            logger.warning("Embedding batch has failed requests: batch=%s count=%s", batch_id, failures)


def reconcile_embedding_submission(
    factory: sessionmaker[Session], batch_id: UUID, provider_jobs: list[types.BatchJob]
) -> None:
    matches = [job for job in provider_jobs if job.display_name == str(batch_id)]
    if len(matches) > 1 or (matches and not matches[0].name):
        raise ValueError(f"Cannot uniquely reconcile embedding batch: batch={batch_id}")
    with factory.begin() as db:
        batch = db.get(EmbeddingBatchJob, batch_id, with_for_update=True)
        if batch is None or batch.state != EmbeddingBatchState.SUBMITTING or batch.provider_name is not None:
            return
        if matches:
            batch.provider_name = matches[0].name
            batch.state = EmbeddingBatchState.SUBMITTED
        else:
            batch.state = EmbeddingBatchState.FAILED
            release_batch_queue_slot(db, batch_id)
            db.execute(
                update(EmbeddingBatchRequest).where(EmbeddingBatchRequest.batch_id == batch_id).values(active=False)
            )


def collect_embedding_batches(factory: sessionmaker[Session], settings: Settings) -> int:
    """Return the number of collection/reconciliation failures; never submit work."""
    with factory() as db:
        batches = db.scalars(
            select(EmbeddingBatchJob).where(
                EmbeddingBatchJob.state.in_((EmbeddingBatchState.SUBMITTING, EmbeddingBatchState.SUBMITTED))
            )
        ).all()

    if not batches:
        return 0

    key = settings.effective_gemini_batch_api_key
    if not key or not key.get_secret_value():
        raise ValueError("Configure a Gemini batch API key to collect embedding batches")

    cutoff = datetime.now(UTC) - timedelta(seconds=settings.embedding_submission_grace_seconds)
    failures = 0

    with genai.Client(
        api_key=key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=settings.gemini_answer_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ) as client:
        provider_jobs = None
        if any(batch.state == EmbeddingBatchState.SUBMITTING and batch.created_at <= cutoff for batch in batches):
            try:
                # Exhaust pagination before treating absence as evidence of non-acceptance.
                pager = client.batches.list()
                listed_jobs = list(pager.page)
                while pager.config.get("page_token"):
                    listed_jobs.extend(pager.next_page())
                provider_jobs = listed_jobs
            except Exception as exc:
                failures += 1
                log_exception(logger, "Embedding batch reconciliation listing failed", exc, settings=settings)

        for batch in batches:
            try:
                if batch.state == EmbeddingBatchState.SUBMITTING:
                    if batch.created_at <= cutoff and provider_jobs is not None:
                        reconcile_embedding_submission(factory, batch.id, provider_jobs)
                    continue

                # These batches are submitted
                if not batch.provider_name:
                    raise ValueError("Submitted embedding batch has no provider name")

                remote = client.batches.get(name=batch.provider_name)

                if remote.state == types.JobState.JOB_STATE_SUCCEEDED:
                    if not remote.dest or not remote.dest.file_name:
                        raise ValueError("Completed embedding batch has no output file")
                    payload = client.files.download(file=remote.dest.file_name)
                    if not payload:
                        raise ValueError("Completed embedding batch has empty output")
                    publish_embedding_results(factory, settings, batch.id, payload)
                elif remote.state in (
                    types.JobState.JOB_STATE_FAILED,
                    types.JobState.JOB_STATE_CANCELLED,
                    types.JobState.JOB_STATE_EXPIRED,
                ):
                    logger.warning(
                        "Embedding batch terminated: batch=%s state=%s error=%s",
                        batch.id,
                        remote.state,
                        remote.error.code if remote.error else None,
                    )
                    if remote.error and remote.error.message:
                        log_exception(
                            logger,
                            f"Embedding provider failure: batch={batch.id}",
                            ValueError(remote.error.message),
                            settings=settings,
                        )
                    with factory.begin() as db:
                        current = db.get(EmbeddingBatchJob, batch.id, with_for_update=True)
                        if current is not None and current.state == EmbeddingBatchState.SUBMITTED:
                            current.state = EmbeddingBatchState.FAILED
                            release_batch_queue_slot(db, batch.id)
                            db.execute(
                                update(EmbeddingBatchRequest)
                                .where(EmbeddingBatchRequest.batch_id == batch.id)
                                .values(active=False)
                            )
            except Exception as exc:
                failures += 1
                log_exception(logger, f"Embedding collection failed: batch={batch.id}", exc, settings=settings)
    return failures
