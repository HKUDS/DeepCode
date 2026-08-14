"""Infrastructure errors translated at the persistence boundary."""


class PersistenceConflictError(RuntimeError):
    """A uniqueness or consistency constraint rejected a write."""
