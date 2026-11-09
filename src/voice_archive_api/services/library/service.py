import asyncio
import logging

import httpx
from google.genai import errors
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from voice_archive_api.core.config import Settings
from voice_archive_api.core.logging import log_exception
from voice_archive_api.logic.books.validation import resolve_primary_language
from voice_archive_api.logic.library import EvidenceSegment
from voice_archive_api.logic.search.dense import resolve_query_texts
from voice_archive_api.logic.search.retrieval import (
    extract_segment_candidates,
    fuse_window_rankings,
    select_segment_ids,
)
from voice_archive_api.services.library import provider
from voice_archive_api.services.library.schemas import AnswerReference, AskRequest, AskResult
from voice_archive_api.services.library.search import expansion, reranking, retrieval

logger = logging.getLogger(__name__)


async def answer_library_question(factory: sessionmaker[Session], settings: Settings, request: AskRequest) -> AskResult:
    try:
        async with asyncio.timeout(settings.gemini_answer_timeout_seconds):
            if request.use_whole_book:
                snapshot = await run_in_threadpool(retrieval.read_source_snapshot, factory, request)
                if not snapshot.books or any(
                    book.content_version is None or not book.has_text for book in snapshot.books
                ):
                    return AskResult(
                        status="unavailable",
                        answer="",
                        limitations=["The selected source is unavailable for the requested answer mode."],
                    )
                evidence = await run_in_threadpool(
                    retrieval.read_evidence, factory, settings, request, snapshot, segment_ids=[]
                )
                return await generate_answer_result(factory, settings, request, snapshot, evidence)

            snapshot, excluded = await run_in_threadpool(retrieval.read_source_availability, factory, settings, request)
            if (
                not snapshot.books
                or (request.series_id is not None and snapshot.series is None)
                or len(excluded) == len(snapshot.books)
                or request.book_id in excluded
            ):
                return AskResult(
                    status="unavailable",
                    answer="",
                    limitations=["The selected source is unavailable for the requested answer mode."],
                )
            routes: dict[tuple[str, str], list[int]] = {}
            for book in snapshot.books:
                if book.id in excluded:
                    continue

                if not request.query_en:
                    return AskResult(
                        status="unavailable",
                        answer="",
                        limitations=["Supply query_en for English retrieval; copy an English question unchanged."],
                    )
                queries = resolve_query_texts(resolve_primary_language(book.language), request.query, request.query_en)
                for kind, query in queries.items():
                    routes.setdefault((kind, query), []).append(book.id)
            vectors: dict[str, list[float]] = {}
            for _, query in routes:
                if query not in vectors:
                    vectors[query] = await provider.embed_query(settings, query)

            ranked_lists, unprepared = await run_in_threadpool(
                retrieval.search_windows, factory, settings, request, snapshot, routes, vectors
            )
            excluded.update(unprepared)
            if request.book_id in excluded or len(excluded) == len(snapshot.books):
                return AskResult(
                    status="unavailable",
                    answer="",
                    limitations=["The selected scope has no usable prepared passage search."],
                )
            book_ids = [book.id for book in snapshot.books]
            ranked_windows = fuse_window_rankings(ranked_lists, book_ids)
            candidates = extract_segment_candidates(ranked_windows, book_ids)
            context_ids: dict[int, list[int]] = {}
            if settings.reranker_base_url is not None and candidates:
                try:
                    async with asyncio.timeout(settings.reranker_timeout_seconds):
                        expanded = await run_in_threadpool(
                            expansion.expand_segment_candidates, factory, settings, request, snapshot, candidates
                        )
                        reranked = await reranking.rerank_segment_candidates(settings, request.query, expanded)
                    candidates = reranked
                    context_ids = {
                        item.candidate.segment_id: [
                            segment.segment_id
                            for segment in (*item.preceding_segments, item.evidence, *item.following_segments)
                        ]
                        for item in expanded
                    }
                except retrieval.SourceChangedError:
                    raise
                except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                    log_exception(
                        logger,
                        f"Reranking failed; using hybrid ordering: book={request.book_id} series={request.series_id}",
                        exc,
                        settings=settings,
                    )
            selected = select_segment_ids(candidates)
            while True:
                try:
                    evidence = await run_in_threadpool(
                        retrieval.read_evidence,
                        factory,
                        settings,
                        request,
                        snapshot,
                        list(
                            dict.fromkeys(
                                identity for target in selected for identity in context_ids.get(target, [target])
                            )
                        ),
                    )
                    result = await generate_answer_result(factory, settings, request, snapshot, evidence)
                    if excluded:
                        result.limitations.append(
                            "Some scoped books were excluded because passage search is not prepared."
                        )
                    return result
                except provider.BookTooLargeError, retrieval.EvidenceTooLargeError:
                    if len(selected) <= 1:
                        raise
                    selected = selected[: len(selected) // 2]
    except retrieval.SourceChangedError:
        return AskResult(
            status="stale", answer="", limitations=["Source content or scope changed while answering; ask again."]
        )
    except provider.BookTooLargeError, retrieval.EvidenceTooLargeError:
        return AskResult(
            status="oversize", answer="", limitations=["The selected evidence exceeds the safe input budget."]
        )
    except (errors.APIError, httpx.HTTPError, TimeoutError, ValueError, KeyError, SQLAlchemyError) as exc:
        log_exception(
            logger, f"Library answer failed: book={request.book_id} series={request.series_id}", exc, settings=settings
        )
        return AskResult(status="unavailable", answer="", limitations=["An answer is unavailable; please retry later."])


async def generate_answer_result(
    factory: sessionmaker[Session],
    settings: Settings,
    request: AskRequest,
    snapshot: retrieval.LibrarySourceSnapshot,
    evidence: list[EvidenceSegment],
) -> AskResult:
    if not evidence:
        return AskResult(
            status="insufficient_evidence",
            answer="No supporting passage was found.",
            limitations=["The retrieved evidence is limited."],
        )
    draft = await provider.generate_answer(settings, request.query, evidence, request.effort)
    supplied = {segment.segment_id: segment for segment in evidence}
    references = [
        AnswerReference(**supplied[ref.segment_id].model_dump(), relevance=ref.relevance) for ref in draft.references
    ]
    if not await run_in_threadpool(retrieval.is_source_current, factory, request, snapshot):
        raise retrieval.SourceChangedError
    return AskResult(status=draft.status, answer=draft.answer, limitations=draft.limitations, references=references)
