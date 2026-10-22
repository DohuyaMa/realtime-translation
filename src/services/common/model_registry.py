# src/services/common/model_registry.py
"""Pinned model registry (spec §4.7).

Every model a service may load is listed here with an immutable HF revision and
the size + sha256 of each file. RT_MODEL accepts only these aliases or an
absolute directory path. Values were produced by scripts/model_tools/pin_model.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileSpec:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    service: str  # "asr" | "tts"
    repo_id: str
    revision: str
    license: str
    files: tuple[FileSpec, ...]

    @property
    def allow_patterns(self) -> list[str]:
        return [f.path for f in self.files]


class UnknownModel(LookupError):
    """Raised for an alias that is not in the registry."""


_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        alias="whisper-small",
        service="asr",
        repo_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        license="MIT",
        files=(
            FileSpec(
                "config.json",
                2370,
                "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
            ),
            FileSpec(
                "model.bin",
                483546902,
                "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
            ),
            FileSpec(
                "tokenizer.json",
                2203239,
                "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            ),
            FileSpec(
                "vocabulary.txt",
                459861,
                "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
            ),
        ),
    ),
    ModelSpec(
        alias="whisper-large-v3-turbo",
        service="asr",
        repo_id="dropbox-dash/faster-whisper-large-v3-turbo",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        license="MIT",
        files=(
            FileSpec(
                "config.json",
                2263,
                "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
            ),
            FileSpec(
                "model.bin",
                1617884929,
                "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
            ),
            FileSpec(
                "preprocessor_config.json",
                340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            FileSpec(
                "tokenizer.json",
                2710337,
                "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
            ),
            FileSpec(
                "vocabulary.json",
                1068114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
    ),
    ModelSpec(
        alias="kokoro-82m-v1.0-onnx",
        service="tts",
        repo_id="onnx-community/Kokoro-82M-v1.0-ONNX",
        revision="1939ad2a8e416c0acfeecc08a694d14ef25f2231",
        license="Apache-2.0",
        files=(
            FileSpec(
                "onnx/model.onnx",
                325532232,
                "8fbea51ea711f2af382e88c833d9e288c6dc82ce5e98421ea61c058ce21a34cb",
            ),
            FileSpec(
                "onnx/model_fp16.onnx",
                163234740,
                "ba4527a874b42b21e35f468c10d326fdff3c7fc8cac1f85e9eb6c0dfc35c334a",
            ),
            FileSpec(
                "onnx/model_quantized.onnx",
                92361116,
                "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478",
            ),
            FileSpec(
                "tokenizer.json",
                3497,
                "77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34",
            ),
            FileSpec(
                "voices/af_alloy.bin",
                522240,
                "c4a6b876047fd7fb472edf4ebd63cfac7c3b958a7cae7c106e8f038ca6308c45",
            ),
            FileSpec(
                "voices/af_aoede.bin",
                522240,
                "4a004c33430762e2461eedb2013fad808ef4ab3121f5300f554476caf58d8361",
            ),
            FileSpec(
                "voices/af_bella.bin",
                522240,
                "f69d836209b78eb8c66e75e3cda491e26ea838a3674257e9d4e5703cbaf55c8b",
            ),
            FileSpec(
                "voices/af_heart.bin",
                522240,
                "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
            ),
            FileSpec(
                "voices/af_jessica.bin",
                522240,
                "a240a5e3c15b43563d6e923bdca8ef5613a23471d9b77653694012435df23bd8",
            ),
            FileSpec(
                "voices/af_kore.bin",
                522240,
                "9be5221b6a941c04b561959b8ff0b06e809444dcc4ab7e75a7b23606f691819e",
            ),
            FileSpec(
                "voices/af_nicole.bin",
                522240,
                "cd2191ab31b914ed7b318416b0e4440fdf392ddad9106a060819aa600a64f59a",
            ),
            FileSpec(
                "voices/af_nova.bin",
                522240,
                "18778272caa0d0eebaea251c35fd635f038434f9eee5e691d02a174bd328414f",
            ),
            FileSpec(
                "voices/af_river.bin",
                522240,
                "00a2bcf82b1d86e8f19902ede58c65ccf6c0e43b44b7d74fad54e5d8933c9c30",
            ),
            FileSpec(
                "voices/af_sarah.bin",
                522240,
                "4409fbc125afabacc615d94db5398d847006a737b0247d6892b7a9a0007a2f0a",
            ),
            FileSpec(
                "voices/af_sky.bin",
                522240,
                "4435255c9744f3f31659e0d714ab7689bf65d9e77ec1cce060f083912614f0b9",
            ),
            FileSpec(
                "voices/am_adam.bin",
                522240,
                "162b035ed91cfc48b6046982184c645f72edcdd1b82843347f605d7bf7b15716",
            ),
            FileSpec(
                "voices/am_echo.bin",
                522240,
                "3968b92c3c4cd1c4416dbded36c13eaa388a90d5788d02a13e4d781f5f8cf3c3",
            ),
            FileSpec(
                "voices/am_eric.bin",
                522240,
                "e8b5be17edd1e3636901ce7598baafe2dc8dd8ff707a0c23bf9e461add7e2832",
            ),
            FileSpec(
                "voices/am_fenrir.bin",
                522240,
                "c27989f741f7ee34d273a39d8a595cc0837d35f5ced9a29b7cc162614616df43",
            ),
            FileSpec(
                "voices/am_liam.bin",
                522240,
                "52403be32fd047c6a44517cb0bcd6b134f2a18baa73e70ef41651e0eab921ade",
            ),
            FileSpec(
                "voices/am_michael.bin",
                522240,
                "1d1f21dd8da39c30705cd4c75d039d265e9bc4a2a93ed09bc9e1b1225eb95ba1",
            ),
            FileSpec(
                "voices/am_onyx.bin",
                522240,
                "da5d135b424164916d75a68ffb4c2abce3d7d5ccc82dd1ee6cf447ce286145e6",
            ),
            FileSpec(
                "voices/am_puck.bin",
                522240,
                "fcf73c989033e9233e0b98713eca600c8c74dcc1614b37009d5450ff4a2274a0",
            ),
            FileSpec(
                "voices/am_santa.bin",
                522240,
                "61150cf726ab6c5ed7a99f90a304f91f5a72c00c592e89ec94e5df11c319227a",
            ),
            FileSpec(
                "voices/bf_alice.bin",
                522240,
                "08afa6ba24da61ea5e8efa139e5aadc938d83f0a6da5a900adaf763ac1da5573",
            ),
            FileSpec(
                "voices/bf_emma.bin",
                522240,
                "669fe0647f9dd04fcab92f1439a40eeb4c8b4ab1f82e4996fe3d918ce4a63b73",
            ),
            FileSpec(
                "voices/bf_isabella.bin",
                522240,
                "3754352c4aaa46d17f27654ab7518d65b62ad6163a0f55a5f4330c2da2c4e94f",
            ),
            FileSpec(
                "voices/bf_lily.bin",
                522240,
                "5e0ee32ebe64a467124976b14e69590746f1c4ce41a12b587a50c862edfea335",
            ),
            FileSpec(
                "voices/bm_daniel.bin",
                522240,
                "6b3194bbceffb746733cbc22c8f593dd44e401a71d53895a2dca891bc595a1e8",
            ),
            FileSpec(
                "voices/bm_fable.bin",
                522240,
                "f889083196807b4adb15e9204252165f503b8d33d3982e681c52443c49d798f1",
            ),
            FileSpec(
                "voices/bm_george.bin",
                522240,
                "c4b235a4c1f2cd3b939fed08b899ce9385638b763f7b73a59616c4fc9bd6c9bc",
            ),
            FileSpec(
                "voices/bm_lewis.bin",
                522240,
                "b8f671cef828c30e66fdf0b0756a76bba58f6bb3398cbbf27058642acbcedb97",
            ),
        ),
    ),
)

REGISTRY: dict[str, ModelSpec] = {spec.alias: spec for spec in _SPECS}


def get_spec(alias: str) -> ModelSpec:
    try:
        return REGISTRY[alias]
    except KeyError:
        raise UnknownModel(
            f"unknown model alias {alias!r}; known: {', '.join(sorted(REGISTRY))}"
        ) from None


def specs_for(service: str) -> list[ModelSpec]:
    return [spec for spec in _SPECS if spec.service == service]
