"""Silero VAD e decodificador de audio copiados do faster-whisper 1.2.1 (licenca MIT,
em LICENCA-faster-whisper.txt; o modelo assets/silero_vad_v6.onnx vem no mesmo pacote).

So `vad.py` e `audio.py`, sem o resto do faster-whisper: o `__init__` dele importa o
CTranslate2, que o Smart App Control do Windows bloqueia. Estes dois precisam so de
numpy, onnxruntime e av (extra `vad-local` do pyproject). `audio.py` e copia exata;
`vad.py` difere so no `get_assets_path`. Usado pelo `vad_local.py` quando
`[transcricao] vad = "local"` (computador sem a distro).
"""
