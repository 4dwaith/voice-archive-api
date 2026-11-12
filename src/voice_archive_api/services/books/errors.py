from voice_archive_api.core.errors import ApplicationError


class BookNotFound(ApplicationError):
    code = "book_not_found"
    message = "Book not found"
