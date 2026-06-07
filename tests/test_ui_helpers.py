"""Tests des helpers d'interaction utilisateur (select_properties, prompt_*).

questionary n'est pas conçu pour être testé en CI (curseur, terminal brut).
On monkey-patch les fonctions internes `_select`, `_text`, `_confirm` pour
simuler les réponses utilisateur. Les tests portent sur la **logique de
filtrage / validation** des helpers, pas sur questionary lui-même.
"""

from __future__ import annotations

from typing import Any

import pytest

import download_syndic as ds


# ---------- select_properties ----------


def _prop(label: str, imme: str = "1", copro: str = "1") -> dict:
    return {
        "url": f"https://site/{imme}/{copro}",
        "imme": imme,
        "copro": copro,
        "building_name": label,
        "doc_type": "VOS",
        "label": label,
    }


class TestSelectProperties:
    """Tests de select_properties."""

    async def test_propriete_unique_retournee_directement(self, capsys) -> None:
        props = [_prop("Immeuble A")]
        result = await ds.select_properties(props)
        assert result == props
        captured = capsys.readouterr()
        assert "Propriété unique détectée" in captured.out

    async def test_selection_toutes(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        # On simule que l'utilisateur choisit "TOUTES" (la valeur __ALL__).
        async def fake_select(message, choices, default=None):
            # Trouver le Choice "TOUTES" et retourner sa valeur.
            for c in choices:
                if hasattr(c, "value") and c.value == "__ALL__":
                    return "__ALL__"
            return None

        monkeypatch.setattr(ds, "_select", fake_select)
        props = [_prop("A"), _prop("B"), _prop("C")]
        result = await ds.select_properties(props)
        assert result == props
        assert "Toutes les propriétés sélectionnées" in capsys.readouterr().out

    async def test_selection_une_propriete_specifique(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        target = _prop("Cible", "9", "9")

        async def fake_select(message, choices, default=None):
            for c in choices:
                if hasattr(c, "value") and c.value == target:
                    return target
            return None

        monkeypatch.setattr(ds, "_select", fake_select)
        props = [_prop("A"), target, _prop("B")]
        result = await ds.select_properties(props)
        assert result == [target]
        assert "Cible" in capsys.readouterr().out

    async def test_ctrl_c_retourne_toutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_select(message, choices, default=None):
            return None  # Ctrl+C → None

        monkeypatch.setattr(ds, "_select", fake_select)
        props = [_prop("A"), _prop("B")]
        result = await ds.select_properties(props)
        # None est traité comme "toutes" (fallback gracieux).
        assert result == props

    async def test_choix_toutes_en_premier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Le premier Choice doit être "TOUTES" — vérification d'UX.
        captured_choices: list = []

        async def fake_select(message, choices, default=None):
            captured_choices.extend(choices)
            return "__ALL__"

        monkeypatch.setattr(ds, "_select", fake_select)
        await ds.select_properties([_prop("A"), _prop("B")])
        assert hasattr(captured_choices[0], "value")
        assert captured_choices[0].value == "__ALL__"


# ---------- prompt_start_date ----------


class TestPromptStartDate:
    """Tests de prompt_start_date."""

    async def test_date_par_defaut_si_vide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_text(message, default="", validate=None):
            return default  # vide → default retourné

        monkeypatch.setattr(ds, "_text", fake_text)
        result = await ds.prompt_start_date()
        assert result == "2000-01"

    async def test_date_personnalisee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_text(message, default="", validate=None):
            return "2024-06"

        monkeypatch.setattr(ds, "_text", fake_text)
        result = await ds.prompt_start_date()
        assert result == "2024-06"

    async def test_ctrl_c_retourne_defaut(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_text(message, default="", validate=None):
            return None  # Ctrl+C

        monkeypatch.setattr(ds, "_text", fake_text)
        result = await ds.prompt_start_date()
        assert result == "2000-01"

    def test_validateur_accepte_YYYY_MM(self) -> None:
        # On teste directement la fonction _validate via monkeypatch.
        captured = {}
        original_text = ds._text

        async def fake_text(message, default="", validate=None):
            captured["validate"] = validate
            return default

        import asyncio
        ds._text = fake_text
        try:
            asyncio.run(ds.prompt_start_date())
        finally:
            ds._text = original_text

        v = captured["validate"]
        assert v("") is True
        assert v("2024-01") is True
        assert v("2024-12") is True
        # Formats invalides → message d'erreur (string non-vide).
        assert v("2024/01") is not True
        assert v("2024-1") is not True
        assert v("abc") is not True
        # Note : le validateur ne vérifie que le format, pas la sémantique.
        # "2024-13" (mois invalide) passe la regex — la validation fine
        # est faite par datetime ailleurs.
        assert v("2024-13") is True


# ---------- prompt_action ----------


class TestPromptAction:
    """Tests de prompt_action."""

    async def test_action_lister(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        async def fake_select(message, choices, default=None):
            for c in choices:
                if hasattr(c, "value") and c.value == "1":
                    return "1"
            return None

        monkeypatch.setattr(ds, "_select", fake_select)
        result = await ds.prompt_action()
        assert result == "1"
        assert "Lister" in capsys.readouterr().out

    async def test_action_telecharger(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        async def fake_select(message, choices, default=None):
            for c in choices:
                if hasattr(c, "value") and c.value == "2":
                    return "2"
            return None

        monkeypatch.setattr(ds, "_select", fake_select)
        result = await ds.prompt_action()
        assert result == "2"
        assert "Télécharger" in capsys.readouterr().out

    async def test_defaut_si_ctrl_c(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_select(message, choices, default=None):
            return None  # Ctrl+C

        monkeypatch.setattr(ds, "_select", fake_select)
        result = await ds.prompt_action()
        # Le défaut "1" (Lister) est appliqué.
        assert result == "1"


# ---------- Helpers de bas niveau (smoke tests) ----------


class TestLowLevelHelpers:
    """Tests smoke des helpers bas niveau (mockés via monkeypatch questionary)."""

    async def test_confirm_retourne_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from questionary import Question

        class FakeQuestion:
            def ask_async(self):
                async def _ask():
                    return True
                return _ask()

        monkeypatch.setattr(
            "questionary.confirm", lambda *a, **kw: FakeQuestion()
        )
        result = await ds._confirm("Voulez-vous continuer ?", default=False)
        assert result is True

    async def test_confirm_retourne_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeQuestion:
            def ask_async(self):
                async def _ask():
                    return False
                return _ask()

        monkeypatch.setattr("questionary.confirm", lambda *a, **kw: FakeQuestion())
        result = await ds._confirm("Voulez-vous continuer ?", default=False)
        assert result is False
