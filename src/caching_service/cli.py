import json
import sys
from pathlib import Path

import httpx
from pydantic import AnyHttpUrl, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict, SettingsError

from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput


class CliSettings(BaseSettings):
    """Create and read a payload repeatedly; write one JSON object per iteration."""

    model_config = SettingsConfigDict(
        env_prefix="CACHE_CLI_",
        cli_prog_name="cache-cli",
        cli_exit_on_error=True,
        cli_hide_none_type=True,
        cli_shortcuts={"repeat": "r", "input": "i", "json": "j", "output": "o"},
    )

    host: AnyHttpUrl = Field(default=AnyHttpUrl("http://127.0.0.1:8000"))
    repeat: int = Field(default=1, ge=1, le=10_000)
    input: str | None = Field(default=None, description="Input JSON file, or - for stdin")
    json_data: str | None = Field(default=None, alias="json", description="Inline JSON input")
    output: str = Field(default="-", description="Output JSONL file, or - for stdout")

    @model_validator(mode="after")
    def valid_source_and_host(self) -> "CliSettings":
        if self.input is not None and self.json_data is not None:
            raise ValueError("--input and --json are mutually exclusive")
        if self.host.query or self.host.fragment or self.host.username or self.host.password:
            raise ValueError("--host must not include credentials, query parameters, or a fragment")
        return self

    def cli_cmd(self) -> None:
        if self.json_data is not None:
            raw = self.json_data
        elif self.input is None or self.input == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(self.input).read_text(encoding="utf-8")
        payload = PayloadInput.model_validate_json(raw)
        host = str(self.host).rstrip("/")
        # Open output only after reading and validating input, including same-file usage.
        output_file = None
        try:
            if self.output != "-":
                output_file = Path(self.output).open("w", encoding="utf-8")
            stream = output_file or sys.stdout
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                expected_id: str | None = None
                for _ in range(self.repeat):
                    response = client.post(f"{host}/payload", json=payload.model_dump())
                    response.raise_for_status()
                    identifier = PayloadCreated.model_validate(response.json()).id
                    if expected_id is not None and identifier != expected_id:
                        raise ValueError("Repeated input returned a different payload identifier")
                    expected_id = identifier
                    response = client.get(f"{host}/payload/{identifier}")
                    response.raise_for_status()
                    result = PayloadOutput.model_validate(response.json())
                    stream.write(
                        json.dumps({"id": identifier, **result.model_dump()}, ensure_ascii=False)
                        + "\n"
                    )
        finally:
            if output_file is not None:
                output_file.close()


def main() -> None:
    try:
        CliApp.run(CliSettings)
    except (ValidationError, SettingsError, ValueError) as error:
        print(f"cache-cli: invalid input: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    except (OSError, httpx.HTTPError) as error:
        print(f"cache-cli: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
