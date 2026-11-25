"""WhatsApp texts from configuration files: templates (for Meta's approval) and session messages.

The texts live in ``whatsapp_templates.toml`` and ``whatsapp_messages.toml`` next to this file,
never in code. Both files are validated with Pydantic when first loaded (and in the tests), so a
template that Meta would reject, or a variable without a value, fails before anything is sent.
"""

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.messaging_rules import MessageKind
from app.models.enums import AssistantTone

HERE = Path(__file__).resolve().parent
TEMPLATES_FILE = HERE / "whatsapp_templates.toml"
MESSAGES_FILE = HERE / "whatsapp_messages.toml"

Tone = Literal["tu", "usted"]
TONES: tuple[Tone, ...] = ("tu", "usted")

VARIABLE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
ADJACENT_VARIABLES = re.compile(r"\}\}\s*\{\{")
NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Meta limits (WhatsApp Cloud API).
TEMPLATE_BODY_MAX = 1024
TEMPLATE_FOOTER_MAX = 60
TEMPLATE_BUTTON_MAX = 25
TEMPLATE_MAX_BUTTONS = 3
SESSION_BODY_MAX = 4096
SESSION_BUTTON_MAX = 20
SESSION_MAX_BUTTONS = 3
LIST_BUTTON_MAX = 20
LIST_ROW_TITLE_MAX = 24
LIST_ROW_DESCRIPTION_MAX = 72
LIST_MAX_ROWS = 10


class TemplateError(ValueError):
    pass


def tone_for(tone: AssistantTone | None) -> Tone:
    return "usted" if tone == AssistantTone.FORMAL else "tu"


def variables_in(text: str) -> list[str]:
    return VARIABLE.findall(text)


def render(text: str, params: dict[str, str]) -> str:
    """Fill {{variables}}; a missing value is an error, never an empty gap."""

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise TemplateError(f"Missing value for {{{{{name}}}}}")
        return str(params[name])

    return VARIABLE.sub(fill, text)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemplateButton(_Strict):
    text: str = Field(min_length=1, max_length=TEMPLATE_BUTTON_MAX)
    action: str = Field(pattern=NAME.pattern)


class TemplateDef(_Strict):
    key: str
    kind: MessageKind
    footer: bool = False
    header: Literal["document"] | None = None
    variables: tuple[str, ...]
    buttons: tuple[TemplateButton, ...] = ()
    example: dict[str, str]
    tu: str
    usted: str

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not NAME.match(value):
            raise ValueError(f"Template name {value!r} must be lowercase letters, digits and _")
        return value

    @model_validator(mode="after")
    def _meta_rules(self) -> "TemplateDef":
        if len(self.buttons) > TEMPLATE_MAX_BUTTONS:
            raise ValueError(f"{self.key}: at most {TEMPLATE_MAX_BUTTONS} buttons")
        if set(self.example) != set(self.variables):
            raise ValueError(f"{self.key}: example must give a value for every variable")
        for tone in TONES:
            body = self.body(tone)
            label = f"{self.key}_{tone}"
            if len(body) > TEMPLATE_BODY_MAX:
                raise ValueError(f"{label}: body is longer than {TEMPLATE_BODY_MAX} characters")
            used = variables_in(body)
            if sorted(set(used)) != sorted(self.variables):
                raise ValueError(f"{label}: variables {sorted(set(used))} != {self.variables}")
            stripped = body.strip()
            if stripped.startswith("{{") or stripped.endswith("}}"):
                raise ValueError(f"{label}: a body cannot start or end with a variable")
            if ADJACENT_VARIABLES.search(body):
                raise ValueError(f"{label}: two variables cannot be next to each other")
        return self

    def body(self, tone: Tone) -> str:
        return self.tu if tone == "tu" else self.usted

    def meta_name(self, tone: Tone) -> str:
        return f"{self.key}_{tone}"


class ListRow(_Strict):
    action: str = Field(pattern=NAME.pattern)
    title: str = Field(min_length=1, max_length=LIST_ROW_TITLE_MAX)
    description: str | None = Field(default=None, max_length=LIST_ROW_DESCRIPTION_MAX)


class SessionMessageDef(_Strict):
    key: str
    variables: tuple[str, ...] = ()
    text: str | None = None
    tu: str | None = None
    usted: str | None = None
    list_button: str | None = Field(default=None, max_length=LIST_BUTTON_MAX)
    rows: tuple[ListRow, ...] = ()

    @model_validator(mode="after")
    def _rules(self) -> "SessionMessageDef":
        if self.text is None and (self.tu is None or self.usted is None):
            raise ValueError(f"{self.key}: needs 'text', or both 'tu' and 'usted'")
        if self.text is not None and (self.tu is not None or self.usted is not None):
            raise ValueError(f"{self.key}: use 'text' or 'tu'/'usted', not both")
        if len(self.rows) > LIST_MAX_ROWS:
            raise ValueError(f"{self.key}: at most {LIST_MAX_ROWS} rows")
        if self.rows and not self.list_button:
            raise ValueError(f"{self.key}: a list needs 'list_button'")
        for tone in TONES:
            body = self.body(tone)
            if len(body) > SESSION_BODY_MAX:
                raise ValueError(f"{self.key}: longer than {SESSION_BODY_MAX} characters")
            used = sorted(set(variables_in(body)))
            if used != sorted(self.variables):
                raise ValueError(f"{self.key} ({tone}): variables {used} != {self.variables}")
        return self

    def body(self, tone: Tone) -> str:
        if self.text is not None:
            return self.text
        return (self.tu if tone == "tu" else self.usted) or ""

    def render(self, tone: Tone, params: dict[str, str] | None = None) -> str:
        return render(self.body(tone), params or {})


class TemplatesFile(_Strict):
    language: str
    category: Literal["UTILITY"]
    footer: dict[Tone, str]
    templates: dict[str, TemplateDef]

    @model_validator(mode="before")
    @classmethod
    def _inject_keys(cls, data: Any) -> Any:
        for key, value in (data.get("templates") or {}).items():
            value.setdefault("key", key)
        return data

    @model_validator(mode="after")
    def _footers(self) -> "TemplatesFile":
        for tone in TONES:
            text = self.footer.get(tone)
            if not text:
                raise ValueError(f"Missing footer for tone {tone!r}")
            if len(text) > TEMPLATE_FOOTER_MAX:
                raise ValueError(f"Footer ({tone}) is longer than {TEMPLATE_FOOTER_MAX}")
            if variables_in(text):
                raise ValueError("Footers cannot have variables")
        return self


class MessagesFile(_Strict):
    labels: dict[str, str]
    messages: dict[str, SessionMessageDef]

    @model_validator(mode="before")
    @classmethod
    def _inject_keys(cls, data: Any) -> Any:
        for key, value in (data.get("messages") or {}).items():
            value.setdefault("key", key)
        return data


class Catalog:
    """Templates and session messages, validated."""

    def __init__(self, templates: TemplatesFile, messages: MessagesFile) -> None:
        self._templates = templates
        self._messages = messages

    @property
    def language(self) -> str:
        return self._templates.language

    @property
    def templates(self) -> dict[str, TemplateDef]:
        return self._templates.templates

    def template(self, key: str) -> TemplateDef:
        try:
            return self._templates.templates[key]
        except KeyError:
            raise TemplateError(f"Unknown WhatsApp template {key!r}") from None

    def footer(self, tone: Tone) -> str:
        return self._templates.footer[tone]

    def message(self, key: str) -> SessionMessageDef:
        try:
            return self._messages.messages[key]
        except KeyError:
            raise TemplateError(f"Unknown WhatsApp session message {key!r}") from None

    def label(self, key: str) -> str:
        return self._messages.labels[key]

    def render_template(self, key: str, tone: Tone, params: dict[str, str]) -> str:
        definition = self.template(key)
        missing = set(definition.variables) - set(params)
        if missing:
            raise TemplateError(f"{key}: missing values for {sorted(missing)}")
        return render(definition.body(tone), params)

    def export_for_meta(self, language: str | None = None) -> list[dict[str, Any]]:
        """Template definitions in the shape of Meta's "create message template" request."""
        result: list[dict[str, Any]] = []
        for definition in self.templates.values():
            for tone in TONES:
                components: list[dict[str, Any]] = []
                if definition.header == "document":
                    components.append(
                        {
                            "type": "HEADER",
                            "format": "DOCUMENT",
                            # Meta needs a sample uploaded with the Resumable Upload API.
                            "example": {"header_handle": ["<HANDLE_DE_UN_PDF_DE_EJEMPLO>"]},
                        }
                    )
                body = definition.body(tone)
                components.append(
                    {
                        "type": "BODY",
                        "text": body,
                        "example": {
                            "body_text_named_params": [
                                {"param_name": name, "example": definition.example[name]}
                                for name in dict.fromkeys(variables_in(body))
                            ]
                        },
                    }
                )
                if definition.footer:
                    components.append({"type": "FOOTER", "text": self.footer(tone)})
                if definition.buttons:
                    components.append(
                        {
                            "type": "BUTTONS",
                            "buttons": [
                                {"type": "QUICK_REPLY", "text": b.text} for b in definition.buttons
                            ],
                        }
                    )
                result.append(
                    {
                        "name": definition.meta_name(tone),
                        "language": language or self.language,
                        "category": self._templates.category,
                        "parameter_format": "NAMED",
                        "components": components,
                    }
                )
        return result


def load_catalog(
    templates_path: Path = TEMPLATES_FILE, messages_path: Path = MESSAGES_FILE
) -> Catalog:
    with templates_path.open("rb") as fh:
        templates = TemplatesFile.model_validate(tomllib.load(fh))
    with messages_path.open("rb") as fh:
        messages = MessagesFile.model_validate(tomllib.load(fh))
    return Catalog(templates, messages)


@lru_cache
def catalog() -> Catalog:
    return load_catalog()
