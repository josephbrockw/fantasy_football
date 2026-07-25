import importlib
import os
from types import ModuleType
from unittest import mock

from django.test import SimpleTestCase


class SettingsFromEnvTests(SimpleTestCase):
    """The settings module must be driven entirely by the environment.

    Each test reloads ``config.settings`` under a patched environment, then
    restores the module so later tests see the real configuration.
    """

    def _reload_with(self, **env: str) -> ModuleType:
        import config.settings

        self.addCleanup(importlib.reload, config.settings)
        # Neutralise read_env: otherwise these assertions depend on whether the
        # developer happens to have a .env, and what's in it.
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch("environ.Env.read_env"),
        ):
            return importlib.reload(config.settings)

    def test_database_url_is_parsed(self) -> None:
        settings = self._reload_with(DATABASE_URL="postgres://u:p@somehost:6000/somedb")
        default = settings.DATABASES["default"]
        self.assertEqual(default["NAME"], "somedb")
        self.assertEqual(default["USER"], "u")
        self.assertEqual(default["HOST"], "somehost")
        self.assertEqual(default["PORT"], 6000)

    def test_sleeper_username_is_read(self) -> None:
        settings = self._reload_with(SLEEPER_USERNAME="dynastyguy")
        self.assertEqual(settings.SLEEPER_USERNAME, "dynastyguy")

    def test_sleeper_username_defaults_to_empty(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = self._reload_with()
            self.assertEqual(settings.SLEEPER_USERNAME, "")

    def test_debug_defaults_to_false(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = self._reload_with()
            self.assertFalse(settings.DEBUG)

    def test_debug_can_be_enabled(self) -> None:
        settings = self._reload_with(DJANGO_DEBUG="True")
        self.assertTrue(settings.DEBUG)

    def test_allowed_hosts_parsed_as_list(self) -> None:
        settings = self._reload_with(DJANGO_ALLOWED_HOSTS="example.com,foo.test")
        self.assertEqual(settings.ALLOWED_HOSTS, ["example.com", "foo.test"])

    def test_core_app_is_installed(self) -> None:
        settings = self._reload_with()
        self.assertIn("apps.core", settings.INSTALLED_APPS)
