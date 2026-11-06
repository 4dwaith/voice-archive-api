"""Real row-lock contention and relational constraints for ordered membership."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Barrier
from time import monotonic, sleep

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from voice_archive_api.db.models.book import Book
from voice_archive_api.services.books import service as books
from voice_archive_api.services.books.schemas import BookCreate, BookPatch
from voice_archive_api.services.series import service
from voice_archive_api.services.series.schemas import SeriesCreate


@pytest.fixture
def catalog_ids(test_engine):
    with Session(test_engine) as db:
        main, other = [service.create_series(db, SeriesCreate(title=title)).id for title in ["Main", "Other"]]
        first, second = [
            books.create_book(db, BookCreate(title=title, language="en", series_id=main)).id
            for title in ["First", "Second"]
        ]
        free = books.create_book(db, BookCreate(title="Free", language="en")).id
        return main, other, first, second, free


def wait_for_database_lock(test_engine, pid):
    deadline = monotonic() + 5
    with test_engine.connect() as connection:
        while monotonic() < deadline:
            if connection.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                return
            sleep(0.01)
    pytest.fail("Writer did not reach a PostgreSQL lock wait")


@pytest.mark.parametrize("index,expected", [({}, ("ok", 4)), ({"series_index": 3}, ("conflict", None))])
def test_competing_appends_validate_after_the_series_lock(test_engine, catalog_ids, index, expected):
    main, _, _, _, _ = catalog_ids
    waiting_pid = Queue()

    def append_book():
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            waiting_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            try:
                record = books.create_book(db, BookCreate(title="Waiting", language="en", series_id=main, **index))
                return "ok", record.series_index
            except service.SeriesOrderConflict:
                return "conflict", None

    with ThreadPoolExecutor(max_workers=1) as pool, Session(test_engine) as holder:
        service.get_series(holder, main, for_update=True)
        pending = pool.submit(append_book)
        wait_for_database_lock(test_engine, waiting_pid.get(timeout=5))

        books.create_book(holder, BookCreate(title="First writer", language="en", series_id=main))

        assert pending.result(timeout=10) == expected


@pytest.mark.parametrize("operation", ["patch", "replace"])
def test_competing_series_cannot_claim_the_same_unassigned_book(test_engine, catalog_ids, operation):
    main, other, _, _, free = catalog_ids
    waiting_pid = Queue()

    def claim_book():
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            waiting_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            if operation == "patch":
                books.patch_book(db, free, BookPatch(series_id=other))
            else:
                service.replace_series_books(db, other, [free])

    with ThreadPoolExecutor(max_workers=1) as pool, Session(test_engine) as holder:
        books.lock_book_for_series_write(holder, free, {"series_id": main})
        pending = pool.submit(claim_book)
        wait_for_database_lock(test_engine, waiting_pid.get(timeout=5))

        books.patch_book(holder, free, BookPatch(series_id=main))

        with pytest.raises(service.SeriesOrderConflict):
            pending.result(timeout=10)
    with Session(test_engine) as db:
        assert db.get(Book, free).series_id == main
        assert service.list_member_book_ids(db, other) == []


def test_append_waits_for_complete_reordering(test_engine, catalog_ids):
    main, _, first, second, _ = catalog_ids
    waiting_pid = Queue()

    def append_book():
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            waiting_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            return books.create_book(db, BookCreate(title="Appended", language="en", series_id=main)).id

    with ThreadPoolExecutor(max_workers=1) as pool, Session(test_engine) as holder:
        service.get_series(holder, main, for_update=True)
        pending = pool.submit(append_book)
        wait_for_database_lock(test_engine, waiting_pid.get(timeout=5))

        service.replace_series_books(holder, main, [second, first])
        appended = pending.result(timeout=10)

    with Session(test_engine) as db:
        assert service.list_member_book_ids(db, main) == [second, first, appended]
        assert db.get(Book, appended).series_index == 3


@pytest.mark.parametrize("operation", ["detach", "delete"])
def test_tail_removal_rechecks_position_after_reordering(test_engine, catalog_ids, operation):
    main, _, first, second, _ = catalog_ids
    waiting_pid = Queue()

    def remove_tail():
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            waiting_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            if operation == "detach":
                books.patch_book(db, second, BookPatch(series_id=None))
            else:
                books.delete_book(db, second)

    with ThreadPoolExecutor(max_workers=1) as pool, Session(test_engine) as holder:
        service.get_series(holder, main, for_update=True)
        pending = pool.submit(remove_tail)
        wait_for_database_lock(test_engine, waiting_pid.get(timeout=5))

        service.replace_series_books(holder, main, [second, first])

        with pytest.raises(service.SeriesOrderConflict):
            pending.result(timeout=10)
    with Session(test_engine) as db:
        assert service.list_member_book_ids(db, main) == [second, first]


def test_series_deletion_rejects_a_waiting_append(test_engine, catalog_ids):
    main, _, first, second, _ = catalog_ids
    waiting_pid = Queue()

    def append_book():
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            waiting_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            books.create_book(db, BookCreate(title="Rejected", language="en", series_id=main))

    with ThreadPoolExecutor(max_workers=1) as pool, Session(test_engine) as holder:
        service.get_series(holder, main, for_update=True)
        pending = pool.submit(append_book)
        wait_for_database_lock(test_engine, waiting_pid.get(timeout=5))

        service.delete_series(holder, main)

        with pytest.raises(service.SeriesNotFound):
            pending.result(timeout=10)
    with Session(test_engine) as db:
        assert [
            (db.get(Book, book_id).series_id, db.get(Book, book_id).series_index) for book_id in [first, second]
        ] == [
            (None, None),
            (None, None),
        ]
        assert db.scalar(select(Book.id).where(Book.title == "Rejected")) is None


def test_opposite_moves_use_consistent_parent_lock_order(test_engine, catalog_ids):
    main, other, first, second, free = catalog_ids
    barrier = Barrier(2)
    with Session(test_engine) as db:
        books.patch_book(db, free, BookPatch(series_id=other))

    def move_book(book_id, target):
        with Session(test_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            barrier.wait(timeout=5)
            try:
                books.patch_book(db, book_id, BookPatch(series_id=target))
                return "ok"
            except service.SeriesOrderConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(move_book, second, other), pool.submit(move_book, free, main)]

        assert sorted(result.result(timeout=15) for result in pending) == ["conflict", "ok"]
    with Session(test_engine) as db:
        for series_id in [main, other]:
            indices = list(
                db.scalars(select(Book.series_index).where(Book.series_id == series_id).order_by(Book.series_index))
            )
            assert indices == list(range(1, len(indices) + 1))
        assert db.get(Book, first).series_index == 1


@pytest.mark.parametrize("owner,index", [(None, 1), ("Main", None), ("Main", 0), ("Main", -1), ("Main", 1)])
def test_database_rejects_unpaired_nonpositive_and_duplicate_positions(test_engine, catalog_ids, owner, index):
    main, _, _, _, free = catalog_ids
    with Session(test_engine) as db:
        book = db.get(Book, free)
        book.series_id = main if owner else None
        book.series_index = index

        with pytest.raises(IntegrityError):
            db.commit()


def test_order_migration_backfills_existing_members(test_engine, migration_config, catalog_ids):
    main, _, first, second, free = catalog_ids
    command.downgrade(migration_config, "0014_series")

    command.upgrade(migration_config, "head")

    with Session(test_engine) as db:
        assert service.list_member_book_ids(db, main) == [first, second]
        assert [db.get(Book, book_id).series_index for book_id in [first, second, free]] == [1, 2, None]
