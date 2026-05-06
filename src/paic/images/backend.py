"""Image-generation backend.

Single-class wrapper around OpenAI-compatible ``/v1/images/{generations,
edits,variations}`` endpoints. Targeted at relays (mytoken.top, OpenRouter,
self-hosted proxies) but works against the official ``api.openai.com`` too —
``base_url=None`` falls through to the SDK default.

Capability matrix per model (kept here so the doctor row + tools can short-
circuit before hitting an API):

==================  ============  ========  =========
Model               generate      edit      variant
==================  ============  ========  =========
gpt-image-1         ✓             ✓         ✗
dall-e-3            ✓             ✗         ✗
dall-e-2            ✓             ✓         ✓
==================  ============  ========  =========

For Phase 1 we use ``gpt-image-1`` as the default since edit support is the
key feature; variants are simulated by re-running ``generate`` with the same
prompt + a small jitter clause appended.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

from paic.config import ProviderImagesConfig, load_config


class ImageBackendUnavailable(RuntimeError):
    """Raised when the image backend can't be used (disabled / missing key /
    SDK not installed / model doesn't support requested operation)."""


# Models that PAI-C knows how to call. Anything else is allowed but treated
# as if it supports only ``generate`` — the relay decides; we just refuse to
# claim edit/variant support upfront.
#
# ``_EDIT_CAPABLE_PREFIXES`` covers third-party relay aliases (mytoken.top
# etc. expose names like ``gpt-image-2-4k`` / ``gpt-image-2-hd``) so we don't
# have to enumerate every quality/size suffix the relay invents.
_EDIT_CAPABLE_EXACT: frozenset[str] = frozenset({"gpt-image-1", "gpt-image-2", "dall-e-2"})
_EDIT_CAPABLE_PREFIXES: tuple[str, ...] = ("gpt-image-2-",)
_VARIANT_CAPABLE_EXACT: frozenset[str] = frozenset({"dall-e-2"})


def _supports_edit(model: str) -> bool:
    return model in _EDIT_CAPABLE_EXACT or model.startswith(_EDIT_CAPABLE_PREFIXES)


def _supports_variant(model: str) -> bool:
    return model in _VARIANT_CAPABLE_EXACT


@dataclass
class GeneratedImage:
    png_bytes: bytes
    revised_prompt: str | None = None  # gpt-image-1 / dall-e-3 may rewrite


class OpenAICompatibleImageBackend:
    """Calls ``/v1/images/{generations,edits,variations}`` via the openai SDK."""

    def __init__(self, cfg: ProviderImagesConfig, *, api_key: str | None):
        self._cfg = cfg
        self._api_key = api_key
        self._client: Any = None

    @property
    def model(self) -> str:
        return self._cfg.model

    @property
    def supports_edit(self) -> bool:
        return _supports_edit(self._cfg.model)

    @property
    def supports_variant(self) -> bool:
        return _supports_variant(self._cfg.model)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ImageBackendUnavailable(
                "openai SDK is not installed (run `uv sync`)"
            ) from exc
        if not self._api_key:
            raise ImageBackendUnavailable(
                f"providers.images.api_key_env={self._cfg.api_key_env} but the "
                "environment variable is not set"
            )
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._cfg.base_url:
            kwargs["base_url"] = self._cfg.base_url
        self._client = OpenAI(**kwargs)
        return self._client

    # -- decode helpers -----------------------------------------------------
    @staticmethod
    def _decode(item: Any) -> bytes:
        """Pull PNG bytes out of an SDK response item.

        SDK shape: each ``data`` element has ``b64_json`` (preferred) OR
        ``url`` (rare for relays). When only a URL is returned we fetch it
        directly so callers always get raw bytes back.
        """
        b64 = getattr(item, "b64_json", None)
        if b64:
            return base64.b64decode(b64)
        url = getattr(item, "url", None)
        if url:
            import httpx

            with httpx.Client(timeout=60.0) as cli:
                resp = cli.get(url)
                resp.raise_for_status()
                return resp.content
        raise ImageBackendUnavailable(
            "image API returned neither b64_json nor url — check relay output"
        )

    # -- public ops ---------------------------------------------------------
    def generate(
        self, prompt: str, *, n: int = 1, size: str | None = None,
        quality: str | None = None,
    ) -> list[GeneratedImage]:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "prompt": prompt,
            "n": n,
            "size": size or self._cfg.size,
        }
        # gpt-image-1 returns b64 by default but accepts a quality knob;
        # dall-e-3 quality is "standard" | "hd" — pass through verbatim.
        if quality or self._cfg.quality:
            kwargs["quality"] = quality or self._cfg.quality
        try:
            resp = client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001 — relay errors vary
            raise ImageBackendUnavailable(
                f"image generation failed via {self._cfg.base_url or 'openai'}: {exc}"
            ) from exc
        return [
            GeneratedImage(
                png_bytes=self._decode(item),
                revised_prompt=getattr(item, "revised_prompt", None),
            )
            for item in resp.data
        ]

    def edit(
        self, image_bytes: bytes, prompt: str, *,
        mask_bytes: bytes | None = None, size: str | None = None,
    ) -> GeneratedImage:
        if not self.supports_edit:
            raise ImageBackendUnavailable(
                f"model '{self._cfg.model}' does not support image edits "
                f"(supported: {sorted(_EDIT_CAPABLE_EXACT)} "
                f"or any model whose name starts with one of "
                f"{list(_EDIT_CAPABLE_PREFIXES)})"
            )
        client = self._ensure_client()
        # The SDK accepts file-like objects with a ``.name`` attribute so the
        # multipart upload knows the extension. BytesIO works.
        import io

        img_io = io.BytesIO(image_bytes)
        img_io.name = "image.png"  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "prompt": prompt,
            "image": img_io,
            "size": size or self._cfg.size,
        }
        if mask_bytes:
            mask_io = io.BytesIO(mask_bytes)
            mask_io.name = "mask.png"  # type: ignore[attr-defined]
            kwargs["mask"] = mask_io
        try:
            resp = client.images.edit(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ImageBackendUnavailable(
                f"image edit failed via {self._cfg.base_url or 'openai'}: {exc}"
            ) from exc
        item = resp.data[0]
        return GeneratedImage(
            png_bytes=self._decode(item),
            revised_prompt=getattr(item, "revised_prompt", None),
        )

    def variant(
        self, image_bytes: bytes, *, n: int = 2, size: str | None = None,
        prompt_hint: str | None = None,
    ) -> list[GeneratedImage]:
        """Produce N variations.

        For models with native ``/variations`` (dall-e-2) we call it. For
        gpt-image-1 / dall-e-3 the API has no variations endpoint, so we
        fall back to ``edit`` with a "create a fresh artistic variation"
        prompt — close enough for paper-figure refinement.
        """
        if self.supports_variant:
            client = self._ensure_client()
            import io

            img_io = io.BytesIO(image_bytes)
            img_io.name = "image.png"  # type: ignore[attr-defined]
            try:
                resp = client.images.create_variation(
                    model=self._cfg.model,
                    image=img_io,
                    n=n,
                    size=size or self._cfg.size,
                )
            except Exception as exc:  # noqa: BLE001
                raise ImageBackendUnavailable(
                    f"image variant failed via {self._cfg.base_url or 'openai'}: {exc}"
                ) from exc
            return [
                GeneratedImage(
                    png_bytes=self._decode(item),
                    revised_prompt=getattr(item, "revised_prompt", None),
                )
                for item in resp.data
            ]

        if self.supports_edit:
            # Fallback: edit with an "alternative variation" instruction.
            base = (
                "Create an artistic variation of this image — same subject "
                "and overall composition, different colors / lighting / "
                "stylistic details so the user can compare options."
            )
            instruction = f"{base}\n\n{prompt_hint}" if prompt_hint else base
            return [self.edit(image_bytes, instruction, size=size) for _ in range(n)]

        raise ImageBackendUnavailable(
            f"model '{self._cfg.model}' supports neither variations nor edits"
        )


def get_default_image_backend(cfg=None) -> OpenAICompatibleImageBackend:
    """Build a backend from the current global config.

    Raises :class:`ImageBackendUnavailable` if ``providers.images.enabled``
    is false — callers should catch this and surface ``error: images_disabled``
    with a config-pointer hint.
    """
    cfg = cfg or load_config()
    images_cfg = cfg.providers_images
    if not images_cfg.enabled:
        raise ImageBackendUnavailable(
            "providers.images.enabled=false in ~/.paic/config.yaml — "
            "/paic-figure is opt-in. Enable + set api_key_env to use it."
        )
    api_key = os.environ.get(images_cfg.api_key_env)
    return OpenAICompatibleImageBackend(images_cfg, api_key=api_key)
