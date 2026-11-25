"""Templates and session messages live in TOML files and are validated before anything is sent."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.channels.whatsapp.templates import (
    MESSAGES_FILE,
    TEMPLATES_FILE,
    TemplateError,
    catalog,
    load_catalog,
    render,
)

EXPECTED_TEMPLATES = {
    "bienvenida",
    "aviso_amable",
    "aviso_dia_pago",
    "aviso_firme",
    "comprobante_recibido",
    "recibo_enviado",
    "promesa_confirmada",
    "baja_confirmada",
}


def test_the_eight_templates_exist_in_both_tones() -> None:
    assert set(catalog().templates) == EXPECTED_TEMPLATES
    exported = catalog().export_for_meta()
    assert len(exported) == 16
    names = {t["name"] for t in exported}
    assert {"bienvenida_tu", "bienvenida_usted", "baja_confirmada_usted"} <= names


def test_export_is_ready_for_meta() -> None:
    by_name = {t["name"]: t for t in catalog().export_for_meta()}
    welcome = by_name["bienvenida_tu"]
    assert welcome["category"] == "UTILITY" and welcome["language"] == "es"
    assert welcome["parameter_format"] == "NAMED"
    kinds = [c["type"] for c in welcome["components"]]
    assert kinds == ["BODY", "FOOTER", "BUTTONS"]
    body = welcome["components"][0]
    assert [p["param_name"] for p in body["example"]["body_text_named_params"]] == [
        "nombre",
        "asistente",
        "propietario",
        "apartamento",
    ]
    assert welcome["components"][2]["buttons"] == [{"type": "QUICK_REPLY", "text": "Ver opciones"}]
    receipt = by_name["recibo_enviado_usted"]
    assert receipt["components"][0]["format"] == "DOCUMENT"
    assert catalog().export_for_meta("es_MX")[0]["language"] == "es_MX"


def test_render_fills_every_variable_or_fails() -> None:
    text = catalog().render_template(
        "baja_confirmada", "usted", {"nombre": "María", "propietario": "Inversiones Pérez"}
    )
    assert text == (
        "Muy bien, María. Ya no recibirá mensajes de Inversiones Pérez por este número. "
        "Si cambia de opinión, responda ALTA."
    )
    with pytest.raises(TemplateError):
        catalog().render_template("baja_confirmada", "tu", {"nombre": "María"})
    with pytest.raises(TemplateError):
        render("Hola, {{nombre}}.", {})


def test_session_menu_is_a_list_within_whatsapp_limits() -> None:
    menu = catalog().message("menu")
    assert menu.list_button == "Ver opciones"
    assert [r.action for r in menu.rows] == [
        "balance",
        "pay_info",
        "send_receipt",
        "pay_later",
        "other",
    ]
    assert all(len(r.title) <= 24 for r in menu.rows)


def write_variant(tmp_path: Path, mutate) -> Path:
    data = tomllib.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    mutate(data)
    lines = [f'language = "{data["language"]}"', f'category = "{data["category"]}"', "[footer]"]
    lines += [f'{k} = "{v}"' for k, v in data["footer"].items()]
    for key, template in data["templates"].items():
        lines.append(f"[templates.{key}]")
        for field, value in template.items():
            if isinstance(value, str):
                escaped = value.replace('"', '\\"')
                lines.append(f'{field} = "{escaped}"')
            elif isinstance(value, bool):
                lines.append(f"{field} = {'true' if value else 'false'}")
            elif isinstance(value, list):
                items = []
                for item in value:
                    if isinstance(item, dict):
                        inner = ", ".join(f'{k} = "{v}"' for k, v in item.items())
                        items.append("{ " + inner + " }")
                    else:
                        items.append(f'"{item}"')
                lines.append(f"{field} = [{', '.join(items)}]")
            elif isinstance(value, dict):
                inner = ", ".join(f'{k} = "{v}"' for k, v in value.items())
                lines.append(f"{field} = {{ {inner} }}")
    path = tmp_path / "templates.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        # starts with a variable
        lambda d: d["templates"]["baja_confirmada"].update(
            tu="{{nombre}}, listo. {{propietario}}."
        ),
        # ends with a variable
        lambda d: d["templates"]["baja_confirmada"].update(tu="Listo {{nombre}} {{propietario}}"),
        # two variables together
        lambda d: d["templates"]["baja_confirmada"].update(
            tu="Listo, {{nombre}} {{propietario}}. Responde ALTA."
        ),
        # a declared variable is not used in one tone
        lambda d: d["templates"]["baja_confirmada"].update(tu="Listo, {{nombre}}. Responde ALTA."),
        # undeclared variable
        lambda d: d["templates"]["baja_confirmada"].update(
            tu="Listo, {{nombre}}, {{otra}} de {{propietario}}. Responde ALTA."
        ),
        # quick-reply button too long for Meta
        lambda d: d["templates"]["aviso_amable"]["buttons"][0].update(
            text="Ya pagué todo lo que debía este mes"
        ),
        # missing tone
        lambda d: d["templates"]["baja_confirmada"].pop("usted"),
        # example without a value
        lambda d: d["templates"]["baja_confirmada"]["example"].pop("propietario"),
        # footer too long
        lambda d: d["footer"].update(tu="x" * 61),
        # template name Meta would reject
        lambda d: d["templates"].update({"Aviso-Nuevo": d["templates"]["baja_confirmada"]}),
    ],
    ids=[
        "starts-with-variable",
        "ends-with-variable",
        "adjacent-variables",
        "unused-variable",
        "undeclared-variable",
        "long-button",
        "missing-tone",
        "missing-example",
        "long-footer",
        "bad-name",
    ],
)
def test_validator_rejects_what_meta_would_reject(tmp_path: Path, mutate) -> None:
    path = write_variant(tmp_path, mutate)
    with pytest.raises(ValidationError):
        load_catalog(path, MESSAGES_FILE)


def test_unchanged_files_round_trip_through_the_validator(tmp_path: Path) -> None:
    load_catalog(write_variant(tmp_path, lambda d: None), MESSAGES_FILE)
