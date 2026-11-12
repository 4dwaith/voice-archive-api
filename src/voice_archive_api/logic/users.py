"""Email identity rules shared by account provisioning and sign-in."""

from email_validator import EmailNotValidError, validate_email


def normalize_email_address(email: str) -> str:
    try:
        # Account identity is case-insensitive. Syntax validation must not depend
        # on DNS availability or imply ownership verification of the mailbox.
        return validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Enter a valid email address") from None
