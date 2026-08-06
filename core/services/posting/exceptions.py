from django.core.exceptions import ValidationError


class PostingError(ValidationError):
    pass


class ClosedPeriodError(PostingError):
    pass


class InvalidTransition(PostingError):
    pass


class IdempotencyConflict(PostingError):
    pass
