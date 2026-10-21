# Database field size limits
TITLE_LIMIT = 750
AUTHOR_LIMIT = 450
DESCRIPTION_LIMIT = 15_000

# Database constraint names used to translate integrity errors in the service.
UNIQUE_BOOK_IDENTITY = "uq_books_identity"
UNIQUE_BOOK_COVER_FILE = "uq_books_cover_file_id"
UNIQUE_BOOK_SOURCE_PDF_FILE = "uq_books_source_pdf_file_id"
FOREIGN_KEY_BOOK_COVER_FILE = "fk_books_cover_file_id_files"
FOREIGN_KEY_BOOK_SOURCE_PDF_FILE = "fk_books_source_pdf_file_id_files"
