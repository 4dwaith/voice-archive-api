"""Named database constraints used by models and integrity-error translation."""

UNIQUE_BOOK_IDENTITY = "uq_books_identity"
UNIQUE_FILE_BOOK_KIND = "uq_files_book_kind"
UNIQUE_BOOK_SERIES_INDEX = "uq_books_series_index"
FOREIGN_KEY_BOOK_SERIES = "fk_books_series_id_series"
