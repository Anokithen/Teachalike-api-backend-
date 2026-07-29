"""Railway pre-deploy entry point for idempotent database preparation."""

from time import monotonic

from app import create_app


def main():
    """Create and upgrade the schema once before the web deployment starts."""
    started_at = monotonic()
    print("Starting database schema preparation.", flush=True)
    create_app(initialize_database=True)
    elapsed_seconds = monotonic() - started_at
    print(
        f"Database schema preparation completed in {elapsed_seconds:.2f}s.",
        flush=True,
    )


if __name__ == "__main__":
    main()
