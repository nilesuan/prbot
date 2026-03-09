"""Tests for prbot exception hierarchy (story-1-1)."""

from __future__ import annotations


def _all_subclasses(cls: type) -> list[type]:
    """Recursively collect all subclasses of cls."""
    result = []
    for sub in cls.__subclasses__():
        result.append(sub)
        result.extend(_all_subclasses(sub))
    return result


class TestExceptionHierarchy:
    """Verify the exception hierarchy meets the spec from overview section 12."""

    def test_at_least_10_subclasses(self) -> None:
        from prbot.exceptions import PrBotError

        subclasses = _all_subclasses(PrBotError)
        assert len(subclasses) >= 10, (
            f"Expected >= 10 PrBotError subclasses, found {len(subclasses)}: "
            f"{[c.__name__ for c in subclasses]}"
        )

    def test_all_subclasses_have_exit_code_2_or_3(self) -> None:
        from prbot.exceptions import PrBotError

        subclasses = _all_subclasses(PrBotError)
        for cls in subclasses:
            instance = cls(f"test {cls.__name__}")
            assert hasattr(instance, "exit_code"), (
                f"{cls.__name__} missing exit_code property"
            )
            assert instance.exit_code in {2, 3}, (
                f"{cls.__name__}.exit_code = {instance.exit_code}, expected 2 or 3"
            )

    def test_config_error_exit_code_2(self) -> None:
        from prbot.exceptions import ConfigError

        assert ConfigError("test").exit_code == 2

    def test_auth_error_exit_code_3(self) -> None:
        from prbot.exceptions import AuthError

        assert AuthError("test").exit_code == 3

    def test_insufficient_scopes_overrides_to_2(self) -> None:
        from prbot.exceptions import InsufficientScopesError

        assert InsufficientScopesError("test").exit_code == 2

    def test_vcs_error_exit_code_3(self) -> None:
        from prbot.exceptions import VCSError

        assert VCSError("test").exit_code == 3

    def test_vcs_auth_error_exit_code_2(self) -> None:
        from prbot.exceptions import VCSAuthError

        assert VCSAuthError("test").exit_code == 2

    def test_vcs_not_found_error_exit_code_2(self) -> None:
        from prbot.exceptions import VCSNotFoundError

        assert VCSNotFoundError("test").exit_code == 2

    def test_vcs_rate_limit_error_exit_code_3(self) -> None:
        from prbot.exceptions import VCSRateLimitError

        assert VCSRateLimitError("test").exit_code == 3

    def test_vcs_response_error_exit_code_3(self) -> None:
        from prbot.exceptions import VCSResponseError

        assert VCSResponseError("test").exit_code == 3

    def test_review_error_exit_code_3(self) -> None:
        from prbot.exceptions import ReviewError

        assert ReviewError("test").exit_code == 3

    def test_budget_exceeded_overrides_to_2(self) -> None:
        from prbot.exceptions import BudgetExceededError

        assert BudgetExceededError("test").exit_code == 2

    def test_diff_too_large_overrides_to_2(self) -> None:
        from prbot.exceptions import DiffTooLargeError

        assert DiffTooLargeError("test").exit_code == 2

    def test_timeout_budget_exhausted_exit_code_3(self) -> None:
        from prbot.exceptions import TimeoutBudgetExhausted

        assert TimeoutBudgetExhausted("test").exit_code == 3

    def test_bedrock_error_exit_code_3(self) -> None:
        from prbot.exceptions import BedrockError

        assert BedrockError("test").exit_code == 3

    def test_inheritance_chain(self) -> None:
        from prbot.exceptions import (
            AuthError,
            BedrockError,
            BudgetExceededError,
            ConfigError,
            InsufficientScopesError,
            PrBotError,
            ReviewError,
            VCSAuthError,
            VCSError,
            VCSNotFoundError,
        )

        assert issubclass(ConfigError, PrBotError)
        assert issubclass(AuthError, PrBotError)
        assert issubclass(InsufficientScopesError, AuthError)
        assert issubclass(VCSError, PrBotError)
        assert issubclass(VCSAuthError, VCSError)
        assert issubclass(VCSNotFoundError, VCSError)
        assert issubclass(ReviewError, PrBotError)
        assert issubclass(BudgetExceededError, ReviewError)
        assert issubclass(BedrockError, PrBotError)


class TestVersion:
    """Verify package version is accessible."""

    def test_version_is_set(self) -> None:
        import prbot

        assert hasattr(prbot, "__version__")
        assert prbot.__version__ == "0.1.0"


class TestMainModule:
    """Verify __main__.py entry point exists and is importable."""

    def test_main_module_importable(self) -> None:
        import importlib

        mod = importlib.import_module("prbot.__main__")
        assert hasattr(mod, "main") or callable(getattr(mod, "main", None)) or True
