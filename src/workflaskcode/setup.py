"""First-run and re-runnable setup wizard. Nothing is saved until the final confirmation."""

from __future__ import annotations

import getpass as _getpass
import os
import sys
from typing import Callable

from .config import ConfigStore, ConfigError
from .credentials import CredentialError, CredentialStore, ENV_VARS, mask
from .paths import AgentHome
from .providers import ProviderConfig, ProviderError, build_provider

PROVIDER_CHOICES = [
    ("openrouter", "OpenRouter (one key, many models; live catalogue with pricing)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic (native Messages API)"),
    ("openai_compatible", "Custom OpenAI-compatible endpoint (you give the base URL)"),
]
PERMISSION_CHOICES = [
    ("approve_risky", "Recommended: reads and test runs run freely; writes allowed; other commands and deletes ask"),
    ("approve_all", "Strict: every write, delete and command asks first"),
    ("restricted_auto", "Autonomous: only known-safe commands; anything else is refused"),
    ("read_only", "Read-only: no writes and no commands"),
]


class _Back(Exception):
    pass


class _Quit(Exception):
    pass


class Wizard:
    def __init__(self, input_fn: Callable[[str], str], getpass_fn: Callable[[str], str],
                 out: Callable[[str], None]) -> None:
        self.input_fn = input_fn
        self.getpass_fn = getpass_fn
        self.out = out

    def ask(self, prompt: str, default: str | None = None, secret: bool = False) -> str:
        shown = f" [{default}]" if default not in (None, "") and not secret else ""
        full = f"{prompt}{shown}  (b = back, q = quit): "
        raw = self.getpass_fn(full) if secret else self.input_fn(full)
        text = raw.strip()
        if not secret:
            if text.lower() == "q":
                raise _Quit()
            if text.lower() == "b":
                raise _Back()
        if text == "" and default is not None:
            return default
        return text

    def choose(self, prompt: str, options: list[tuple[str, str]], default: int = 0) -> int:
        for index, (_, label) in enumerate(options, 1):
            self.out(f"  {index}) {label}")
        while True:
            text = self.ask(prompt, str(default + 1))
            if text.isdigit() and 1 <= int(text) <= len(options):
                return int(text) - 1
            self.out(f"  please enter a number from 1 to {len(options)}")

    def confirm(self, prompt: str, default: bool = True) -> bool:
        suffix = "Y/n" if default else "y/N"
        while True:
            text = self.ask(f"{prompt} ({suffix})", "").lower()
            if text == "":
                return default
            if text in ("y", "yes"):
                return True
            if text in ("n", "no"):
                return False
            self.out("  please answer y or n")


def _env_key_for(provider: str) -> str | None:
    var = ENV_VARS.get(provider)
    return os.environ.get(var) if var else None


def run_setup(home: AgentHome, *, transport=None, input_fn=input, getpass_fn=None,
              out: Callable[[str], None] = print) -> bool:
    """Run the wizard. Returns True when configuration was saved."""
    wiz = Wizard(input_fn, getpass_fn or _getpass.getpass, out)
    ctx: dict = {"home": home, "transport": transport}
    try:
        steps = [_welcome, _home, _provider, _key, _verify, _pick_model, _test_model,
                 _permissions, _fallbacks, _git, _browser, _review]
        index = 0
        while index < len(steps):
            try:
                steps[index](wiz, ctx)
                index += 1
            except _Back:
                index = max(0, index - 1)
        return True
    except _Quit:
        out("Setup cancelled. Nothing was saved.")
        return False


def _welcome(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nWelcome to WorkFlaskCode setup.")
    wiz.out("WorkFlaskCode is a terminal coding agent. It uses YOUR API key and YOUR model choice;")
    wiz.out("project files only go to a provider when you start a request that needs one.")
    wiz.ask("Press Enter to begin", "")


def _home(wiz: Wizard, ctx: dict) -> None:
    home: AgentHome = ctx["home"]
    wiz.out(f"\nAgent home: {home.root}")
    if not wiz.confirm("Use this location?", True):
        wiz.out("Re-run with a different location:  workflaskcode --home /path/to/home setup")
        raise _Quit()
    home.ensure()


def _provider(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nChoose a provider.")
    index = wiz.choose("Provider", PROVIDER_CHOICES, default=0)
    kind, _ = PROVIDER_CHOICES[index]
    ctx["kind"] = kind
    ctx["name"] = "custom" if kind == "openai_compatible" else kind
    ctx["base_url"] = ""
    if kind == "openai_compatible":
        while True:
            url = wiz.ask("Base URL (for example https://host/v1)")
            if url.startswith(("http://", "https://")):
                ctx["base_url"] = url.rstrip("/")
                break
            wiz.out("  the base URL must start with http:// or https://")


def _key(wiz: Wizard, ctx: dict) -> None:
    name = ctx["name"]
    env_value = _env_key_for(name)
    if env_value and wiz.confirm(f"Found {ENV_VARS.get(name)} in your environment. Use it?", True):
        ctx["key"], ctx["backend"] = env_value.strip(), "environment"
        wiz.out(f"  using key {mask(ctx['key'])} from the environment (nothing will be stored)")
        return
    wiz.out("\nPaste your API key. It is hidden as you type and never written to config.toml.")
    key = wiz.ask("API key", secret=True)
    if not key:
        wiz.out("  an API key is required")
        raise _Back()
    options = [
        ("file", "Credentials file in the agent home (plain text, file mode 0600). Needs your consent."),
        ("keyring", "OS keyring (needs the optional 'keyring' package)"),
        ("environment", "Do not store it; I will export the environment variable myself"),
    ]
    wiz.out("\nWhere should the key be kept?")
    while True:
        index = wiz.choose("Storage", options, default=0)
        backend = options[index][0]
        if backend == "file":
            wiz.out("  This stores the key in clear text on disk, protected only by file permissions.")
            if not wiz.confirm("Store the key in the credentials file?", False):
                wiz.out("  not stored; choose another option")
                continue
        if backend == "keyring":
            try:
                import keyring  # noqa: F401
            except ImportError:
                wiz.out("  the 'keyring' package is not installed: pip install workflaskcode[keyring]")
                continue
        break
    ctx["key"], ctx["backend"] = key, backend


def _verify(wiz: Wizard, ctx: dict) -> None:
    cfg = ProviderConfig(name=ctx["name"], kind=ctx["kind"], base_url=ctx["base_url"])
    ctx["models"] = []
    wiz.out("\nChecking the key and fetching the model catalogue...")
    while True:
        try:
            provider = build_provider(cfg, ctx["key"], transport=ctx["transport"], timeout=60)
            ctx["models"] = provider.list_models()
            wiz.out(f"  {len(ctx['models'])} models returned by the provider")
            return
        except ProviderError as err:
            wiz.out(f"  could not fetch models: {err.kind}: {err.message}")
            hint = {
                "auth": "The key was rejected. Check it for typos or create a new one.",
                "network": "The network is unreachable. Check your connection or proxy.",
                "timeout": "The provider did not answer in time. Try again.",
                "billing": "The account has no credit or billing is not enabled.",
                "unavailable": "This endpoint does not expose a model list.",
            }.get(err.kind, "The provider returned an unexpected error.")
            wiz.out(f"  {hint}")
            choice = wiz.choose("Next step", [
                ("retry", "Try again"),
                ("manual", "Continue and type a model ID by hand (no live catalogue)"),
                ("back", "Go back and change the key or provider"),
            ])
            if choice == 1:
                ctx["manual_only"] = True
                return
            if choice == 2:
                raise _Back()


def _pick_model(wiz: Wizard, ctx: dict) -> None:
    models = ctx["models"]
    if not models:
        ctx["model"] = wiz.ask("Model ID (exact, as the provider names it)")
        if not ctx["model"]:
            raise _Back()
        return
    wiz.out("\nType part of a name to filter, a number from the list, or an exact model ID.")
    shown = models[:25]
    while True:
        for i, m in enumerate(shown, 1):
            tools = "tools" if m.supports_tools else ""
            wiz.out(f"  {i:>2}) {m.id:<45} {m.price_label():<22} {tools}")
        text = wiz.ask("Model", "")
        if text.isdigit() and 1 <= int(text) <= len(shown):
            ctx["model"] = shown[int(text) - 1].id
            return
        exact = [m for m in models if m.id == text]
        if exact:
            ctx["model"] = exact[0].id
            return
        matches = [m for m in models if text.lower() in m.id.lower() or text.lower() in m.name.lower()]
        if not matches:
            wiz.out("  no models match; try another filter")
            continue
        shown = matches[:25]


def _test_model(wiz: Wizard, ctx: dict) -> None:
    cfg = ProviderConfig(name=ctx["name"], kind=ctx["kind"], base_url=ctx["base_url"])
    while True:
        wiz.out(f"\nTesting {ctx['model']} with a minimal request...")
        try:
            provider = build_provider(cfg, ctx["key"], transport=ctx["transport"], timeout=60)
            response = provider.generate([{"role": "user", "content": "Reply with the single word: ok"}],
                                         model=ctx["model"], max_tokens=16)
            wiz.out(f"  model replied: {response.text.strip()[:60]!r}")
            return
        except ProviderError as err:
            wiz.out(f"  test failed: {err.kind}: {err.message}")
            choice = wiz.choose("Next step", [
                ("retry", "Try the same model again"),
                ("pick", "Choose a different model"),
                ("save", "Save anyway (you can run @testmodel later)"),
            ])
            if choice == 1:
                raise _Back()
            if choice == 2:
                return


def _permissions(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nPermission policy for commands and file deletes:")
    index = wiz.choose("Policy", PERMISSION_CHOICES, default=0)
    ctx["permission_mode"] = PERMISSION_CHOICES[index][0]


def _fallbacks(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nOptional fallbacks, used only for model-specific failures (rate limits, unavailable models).")
    wiz.out("Separate several with commas. Use provider::model-id for another configured provider.")
    text = wiz.ask("Fallback models (Enter to skip)", "")
    ctx["fallbacks"] = [x.strip() for x in text.split(",") if x.strip()]


def _git(wiz: Wizard, ctx: dict) -> None:
    ctx["git"] = wiz.confirm("\nEnable Git integration (status, diff, branches, undo-aware checkpoints)?", True)


def _browser(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nBrowser testing: not available in this release; it is skipped. See docs/STATUS.md.")


def _review(wiz: Wizard, ctx: dict) -> None:
    wiz.out("\nSummary")
    wiz.out(f"  provider     : {ctx['name']} ({ctx['kind']})")
    if ctx.get("base_url"):
        wiz.out(f"  base URL     : {ctx['base_url']}")
    wiz.out(f"  model        : {ctx['model']}")
    wiz.out(f"  key storage  : {ctx['backend']}")
    wiz.out(f"  permissions  : {ctx['permission_mode']}")
    wiz.out(f"  fallbacks    : {', '.join(ctx['fallbacks']) or 'none'}")
    wiz.out(f"  git          : {'enabled' if ctx['git'] else 'disabled'}")
    choice = wiz.choose("Save this configuration?", [
        ("save", "Save"), ("back", "Go back and change something"), ("cancel", "Cancel without saving"),
    ])
    if choice == 1:
        raise _Back()
    if choice == 2:
        raise _Quit()
    _persist(ctx, wiz)


def _persist(ctx: dict, wiz: Wizard) -> None:
    home: AgentHome = ctx["home"]
    home.ensure()
    store = ConfigStore(home)
    store.set_provider(ctx["name"], ctx["kind"], model=ctx["model"], base_url=ctx["base_url"])
    general = store.data["general"]
    general["default_provider"] = ctx["name"]
    general["coding_model"] = ctx["model"]
    general["chat_model"] = ""
    general["permission_mode"] = ctx["permission_mode"]
    general["fallback_models"] = ctx["fallbacks"]
    general["setup_complete"] = True
    store.data["git"]["enabled"] = bool(ctx["git"])
    try:
        store.save()
    except ConfigError as exc:
        raise ConfigError(f"could not save configuration: {exc}") from exc
    creds = CredentialStore(home)
    try:
        if ctx["backend"] == "file":
            creds.set(ctx["name"], ctx["key"], "file")
        elif ctx["backend"] == "keyring":
            creds.set(ctx["name"], ctx["key"], "keyring")
    except CredentialError as exc:
        wiz.out(f"  warning: {exc}")
    wiz.out(f"\nSaved {home.config_file}")
    if ctx["backend"] == "environment":
        wiz.out(f"Export the key before running: export {ENV_VARS.get(ctx['name'], 'WORKFLASKCODE_KEY_' + ctx['name'].upper())}=...")
    wiz.out("Run `workflaskcode doctor` to verify, then `workflaskcode` to start.")
    _ = sys  # keep import explicit for frozen builds
