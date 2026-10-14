import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from caching_service.schemas import PayloadInput, PayloadOutput

# Bump this namespace whenever transformation semantics change.
TRANSFORM_VERSION = "uppercase-v1"


class Base(DeclarativeBase):
    pass


class Transformation(Base):
    __tablename__ = "transformations"

    version: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    result: Mapped[str] = mapped_column(Text)


class Payload(Base):
    __tablename__ = "payloads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[str] = mapped_column(Text)


def transform(value: str) -> str:
    """Stand-in for a deterministic external service."""
    return value.upper()


class Store:
    def __init__(self, data_dir: Path, transformer: Callable[[str], str] = transform):
        self.data_dir = data_dir
        self.files_dir = data_dir / "payloads"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.transformer = transformer
        self.engine = create_engine(
            "sqlite:///" + str((data_dir / "cache.sqlite3").resolve()),
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        Base.metadata.create_all(self.engine)

    def create(self, request: PayloadInput) -> str:
        canonical = json.dumps(
            [TRANSFORM_VERSION, request.list_1, request.list_2],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        identifier = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with Session(self.engine) as session:
            # Lock before checking cache misses: separate workers must not both transform them.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                payload = session.get(Payload, identifier)
                if payload is None:
                    sources = list(dict.fromkeys(request.list_1 + request.list_2))
                    cached: dict[str, str] = {}
                    # Stay below SQLite parameter limits for the maximum request size.
                    for offset in range(0, len(sources), 400):
                        records = session.scalars(
                            select(Transformation).where(
                                Transformation.version == TRANSFORM_VERSION,
                                Transformation.source.in_(sources[offset : offset + 400]),
                            )
                        )
                        cached.update({record.source: record.result for record in records})
                    for source in sources:
                        if source not in cached:
                            cached[source] = self.transformer(source)
                            session.add(
                                Transformation(
                                    version=TRANSFORM_VERSION,
                                    source=source,
                                    result=cached[source],
                                )
                            )
                    output = ", ".join(
                        cached[source]
                        for pair in zip(request.list_1, request.list_2, strict=True)
                        for source in pair
                    )
                    payload = Payload(id=identifier, output=output)
                    session.add(payload)
                output = payload.output
                session.commit()
            except Exception:
                session.rollback()
                raise
        self.materialize(identifier, output)
        return identifier

    def read(self, identifier: str) -> PayloadOutput | None:
        with Session(self.engine) as session:
            payload = session.get(Payload, identifier)
            if payload is None:
                return None
            output = payload.output
        # The DB is authoritative. Rebuild the file even after an interrupted write or deletion.
        path = self.materialize(identifier, output)
        return PayloadOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def materialize(self, identifier: str, output: str) -> Path:
        destination = self.files_dir / f"{identifier}.json"
        content = PayloadOutput(output=output).model_dump_json()
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.files_dir, delete=False
            ) as file:
                temporary = file.name
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return destination

    def close(self) -> None:
        self.engine.dispose()
